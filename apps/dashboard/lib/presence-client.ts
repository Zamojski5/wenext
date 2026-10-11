/**
 * Reads and writes of `api/presence.py` and `api/delegations.py`, from the dashboard's server.
 *
 * The transport is the one `lib/sessions-client.ts` established and for the same reason: the
 * address, the timeout and the cookie name are imported from `lib/control-plane.ts`, so there is
 * one answer to "where is the control plane" even though `call` and `decode` there are private.
 *
 * NOTHING HERE WAITS FOR A MODEL. Reading presence reads rows; pausing and revoking write a row
 * and append an interrupt event. The tick itself is a job and no route starts one.
 */
import {
  CONTROL_PLANE_SESSION_COOKIE,
  REQUEST_TIMEOUT_MS,
  controlPlaneUrl,
  type Read,
  type WriteOutcome,
} from "./control-plane";
import {
  parseDecisions,
  parsePresence,
  parseServiceStatus,
  type Decision,
  type Presence,
  type ServiceStatus,
} from "./presence-schema";

function projectPath(workspaceId: string, projectId: string, suffix: string): string {
  return (
    `/api/workspaces/${encodeURIComponent(workspaceId)}` +
    `/projects/${encodeURIComponent(projectId)}${suffix}`
  );
}

interface RawResponse {
  readonly status: number;
  readonly body: string;
}

async function call(
  method: string,
  path: string,
  options: { readonly token: string; readonly body?: unknown },
): Promise<Read<RawResponse>> {
  let base: string;
  try {
    base = controlPlaneUrl();
  } catch (error) {
    return { ok: false, unauthorized: false, reason: String(error) };
  }
  const headers: Record<string, string> = {
    accept: "application/json",
    cookie: `${CONTROL_PLANE_SESSION_COOKIE}=${options.token}`,
  };
  if (options.body !== undefined) headers["content-type"] = "application/json";
  try {
    const response = await fetch(`${base}${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    const body = await response.text();
    if (response.status === 401) {
      return { ok: false, unauthorized: true, reason: "sesja panelu wygasła" };
    }
    return { ok: true, value: { status: response.status, body } };
  } catch (error) {
    return {
      ok: false,
      unauthorized: false,
      reason: `control plane nie odpowiedział (${String(error)})`,
    };
  }
}

function decode(raw: RawResponse, what: string): Read<unknown> {
  if (raw.status !== 200 && raw.status !== 201) {
    return {
      ok: false,
      unauthorized: false,
      reason: `${what}: control plane odpowiedział ${raw.status}`,
    };
  }
  try {
    return { ok: true, value: JSON.parse(raw.body) };
  } catch (error) {
    return { ok: false, unauthorized: false, reason: `${what}: ciało nie jest JSON-em (${error})` };
  }
}

/** The state of one project: what governs it, what it spent, and every wake-up of the last day. */
export async function getPresence(
  token: string,
  workspaceId: string,
  projectId: string,
  limit = 50,
): Promise<Read<Presence>> {
  const raw = await call(
    "GET",
    `${projectPath(workspaceId, projectId, "/presence")}?limit=${encodeURIComponent(String(limit))}`,
    { token },
  );
  if (!raw.ok) return raw;
  const body = decode(raw.value, "stan obecności");
  if (!body.ok) return body;
  return parsePresence(body.value);
}

/** Proposals still waiting for a person. In REVIEW mode this is the whole workflow. */
export async function getDecisions(
  token: string,
  workspaceId: string,
  projectId: string,
): Promise<Read<readonly Decision[]>> {
  const raw = await call("GET", projectPath(workspaceId, projectId, "/decisions"), { token });
  if (!raw.ok) return raw;
  const body = decode(raw.value, "kolejka decyzji");
  if (!body.ok) return body;
  return parseDecisions(body.value);
}

/**
 * Sign a scope. The WHOLE permission travels in one call: mode, accounts, communities, actions
 * and budget together, because that is what the control plane hashes into the digest a decision
 * later cites. There is deliberately no "edit" call anywhere: changing what the agent may do
 * means revoking and signing again, so no signature can come to cover something unread.
 */
export async function signDelegation(
  token: string,
  workspaceId: string,
  projectId: string,
  scope: {
    readonly mode: string;
    readonly accounts: readonly string[];
    readonly communities: readonly string[];
    readonly actions: readonly string[];
    readonly budget: Record<string, number>;
    /**
     * The deadline, as an ISO instant in UTC (26.09). Part of the same body because it is part
     * of what the person signs: a signature without a visible end is signed in blank.
     */
    readonly expires_at: string;
  },
): Promise<Read<WriteOutcome>> {
  const raw = await call("POST", projectPath(workspaceId, projectId, "/delegation"), {
    token,
    body: scope,
  });
  if (!raw.ok) return raw;
  return outcomeOf(raw.value, "podpisanie powierzenia");
}

/**
 * Pause, resume or revoke. One function for three routes because they differ only in the last
 * path segment; the CALLER still has to name which, so "pause" can never be typed by accident
 * where "revoke" was meant.
 *
 * Returns the control plane's STATUS and its sentence, in the shape `verdictOf` understands, and
 * not the delegation: the page re-reads on the next render anyway, and a handler that parsed a
 * delegation here would have two ways to be wrong about the state.
 */
export async function changeDelegation(
  token: string,
  workspaceId: string,
  projectId: string,
  action: "pause" | "resume" | "revoke",
): Promise<Read<WriteOutcome>> {
  const raw = await call("POST", projectPath(workspaceId, projectId, `/delegation/${action}`), {
    token,
  });
  if (!raw.ok) return raw;
  return outcomeOf(raw.value, "zmiana powierzenia");
}

/**
 * Call off one scheduled return (0023).
 *
 * Takes the wake-up's own identifier, not "the next one": a person cancels the row they are
 * looking at, and a screen that cancelled whatever happened to be first would be a screen you
 * cannot trust twice.
 */
export async function cancelReturn(
  token: string,
  workspaceId: string,
  projectId: string,
  wakeupId: string,
): Promise<Read<WriteOutcome>> {
  const raw = await call(
    "POST",
    projectPath(workspaceId, projectId, `/returns/${wakeupId}/cancel`),
    { token },
  );
  if (!raw.ok) return raw;
  return outcomeOf(raw.value, "odwołanie powrotu");
}

/** The control plane's status and its sentence, in the shape `verdictOf` understands. */
function outcomeOf(raw: RawResponse, what: string): Read<WriteOutcome> {
  let detail = "";
  try {
    const parsed: unknown = JSON.parse(raw.body);
    if (typeof parsed === "object" && parsed !== null) {
      const value = (parsed as Record<string, unknown>)["detail"];
      if (typeof value === "string") detail = value;
    }
  } catch {
    detail = "";
  }
  // Widoczności skutku ta trasa nie mierzy, więc `null`, a nie `false`.
  return {
    ok: true,
    value: { status: raw.status, detail: detail === "" ? what : detail, inConversation: null },
  };
}

/**
 * Stan usługi dla paska w nagłówku studia (P7 etap B).
 *
 * `null` zamiast rzucania, gdy odczyt się nie uda: pasek stanu, który wywraca cały ekran, jest
 * gorszy od paska, którego chwilowo nie ma. Ekran, który go nie dostał, nie pisze „usługa
 * działa", tylko nie pisze nic, i to jest różnica między niewiedzą a zapewnieniem.
 */
export async function getServiceStatus(
  token: string,
  workspaceId: string,
): Promise<ServiceStatus | null> {
  const raw = await call("GET", `/api/workspaces/${encodeURIComponent(workspaceId)}/status`, {
    token,
  });
  if (!raw.ok) return null;
  const body = decode(raw.value, "stan usługi");
  if (!body.ok) return null;
  return parseServiceStatus(body.value);
}
