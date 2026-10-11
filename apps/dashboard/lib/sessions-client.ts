/**
 * Reads and writes of `api/sessions.py`, from the server side of the dashboard.
 *
 * WHY A SECOND CLIENT MODULE. `lib/control-plane.ts` owns the transport for everything else and
 * its `call` and `decode` are module-private. Reaching into them would mean editing a file
 * another session owns, so this module repeats the four lines of fetch and nothing else: the
 * address, the timeout and the cookie name are IMPORTED from there, so there is exactly one
 * answer to "where is the control plane" and one to "how long do we wait".
 *
 * THE TIMEOUT IS THE ORDINARY ONE, AND THAT IS DELIBERATE. None of these calls waits for a
 * model: starting a session, sending a message, interrupting and resuming all enqueue a job and
 * return. The turn happens in a worker and the screen learns about it by reading the trace. A
 * long timeout here would only hide a control plane that is not answering.
 *
 * A 401 IS NOT A FAILED READ. It means the dashboard's session expired, and the caller sends the
 * person to the login screen instead of printing "nie udało się odczytać rozmowy", which would
 * be a lie about the conversation.
 */
import {
  CONTROL_PLANE_SESSION_COOKIE,
  REQUEST_TIMEOUT_MS,
  controlPlaneUrl,
  type Read,
  type WriteOutcome,
} from "./control-plane";
import {
  parseSessionDetail,
  parseSessionList,
  type SessionDetail,
  type SessionEvent,
  type SessionRow,
} from "./session-schema";

function sessionsPath(workspaceId: string, projectId: string): string {
  return (
    `/api/workspaces/${encodeURIComponent(workspaceId)}` +
    `/projects/${encodeURIComponent(projectId)}/sessions`
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

/**
 * Every session of one project, newest first.
 *
 * `conversationsOnly` dokłada `?rodzaj=CONVERSATION`, czyli filtr PO STRONIE SERWERA z kontraktu
 * D-045. Starszy obraz api zignoruje nieznany parametr i odda wszystko, dlatego pasek przepuszcza
 * wynik jeszcze przez `conversationsOf`: serwer odsiewa, gdy umie, ekran odsiewa to, co potrafi
 * rozpoznać, a gdy pola `rodzaj` nie ma, nikt niczego nie ukrywa. Trzy zdania zamiast jednego, bo
 * inaczej w dniu wdrożenia kontraktu pasek albo pokazywałby tiki obecności, albo gubił rozmowy.
 */
export async function getSessions(
  token: string,
  workspaceId: string,
  projectId: string,
  conversationsOnly = false,
): Promise<Read<readonly SessionRow[]>> {
  const path = conversationsOnly
    ? `${sessionsPath(workspaceId, projectId)}?rodzaj=CONVERSATION`
    : sessionsPath(workspaceId, projectId);
  const raw = await call("GET", path, { token });
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: [] };
  const body = decode(raw.value, "lista sesji");
  if (!body.ok) return body;
  return parseSessionList(body.value);
}

/**
 * Ostatnie rozmowy CAŁEJ przestrzeni, do sekcji „Ostatnie" w pasku (D-045).
 *
 * `null` W `ok: true` ZNACZY „TEJ TRASY JESZCZE NIE MA" i jest osobnym stanem od pustej listy.
 * Trasa wchodzi po stronie control plane'u razem z polami rozmowy; do tego czasu odpowiada 404,
 * a sekcja po prostu się nie rysuje. Pusta lista znaczyłaby „w tej przestrzeni nie ma rozmów",
 * czyli zdanie o produkcie, a nie o wdrożeniu, i po dwóch dniach nikt by ich nie odróżnił.
 *
 * NIE SKŁADAMY TEGO Z LIST PROJEKTÓW. Dziesięć projektów to dziesięć odczytów przy każdym
 * wejściu na dowolny ekran, bo pasek jest na każdym; wenext-84 przyjęło ten argument i dokłada
 * jeden odczyt przestrzeni.
 */
export async function getRecentConversations(
  token: string,
  workspaceId: string,
  limit = 10,
): Promise<Read<readonly SessionRow[] | null>> {
  const path =
    `/api/workspaces/${encodeURIComponent(workspaceId)}/sessions` +
    `?rodzaj=CONVERSATION&limit=${encodeURIComponent(String(Math.max(1, limit)))}`;
  const raw = await call("GET", path, { token });
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const body = decode(raw.value, "ostatnie rozmowy");
  if (!body.ok) return body;
  const parsed = parseSessionList(body.value);
  if (!parsed.ok) return parsed;
  return { ok: true, value: parsed.value };
}

/**
 * One session and its trace from `afterSeq` on.
 *
 * `null` means the session is not there, which is what the control plane answers for an id from
 * another organization as well: absent, not forbidden.
 */
export async function getSessionDetail(
  token: string,
  workspaceId: string,
  projectId: string,
  sessionId: string,
  afterSeq = 0,
): Promise<Read<SessionDetail | null>> {
  let cursor = Math.max(0, afterSeq);
  let snapshotEnd: number | undefined;
  const events: SessionEvent[] = [];
  while (true) {
    const page = await getSessionPage(token, workspaceId, projectId, sessionId, cursor);
    if (!page.ok || page.value === null) return page;
    const detail = page.value;
    if (detail.session.session_id !== sessionId || detail.session.project_id !== projectId) {
      return { ok: false, unauthorized: false, reason: "Odczytano inną rozmowę niż żądana." };
    }
    // Freeze the target from the first response. A running agent can keep appending events;
    // the live stream picks those up after the last event we actually render.
    snapshotEnd ??= detail.latest_seq ?? detail.last_seq;
    const previous = cursor;
    for (const event of detail.events) {
      if (event.seq <= cursor) {
        return { ok: false, unauthorized: false, reason: "Historia rozmowy ma niespójną kolejność." };
      }
      cursor = event.seq;
      events.push(event);
    }
    if (cursor >= snapshotEnd) {
      return { ok: true, value: { ...detail, events, last_seq: Math.min(cursor, detail.latest_seq ?? cursor) } };
    }
    if (cursor === previous) {
      return { ok: false, unauthorized: false, reason: "Nie udało się odczytać całej historii rozmowy." };
    }
  }
}

async function getSessionPage(
  token: string,
  workspaceId: string,
  projectId: string,
  sessionId: string,
  afterSeq: number,
): Promise<Read<SessionDetail | null>> {
  const path =
    `${sessionsPath(workspaceId, projectId)}/${encodeURIComponent(sessionId)}` +
    `?after_seq=${encodeURIComponent(String(Math.max(0, afterSeq)))}`;
  const raw = await call("GET", path, { token });
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const body = decode(raw.value, "rozmowa");
  if (!body.ok) return body;
  const parsed = parseSessionDetail(body.value);
  if (!parsed.ok) return parsed;
  return { ok: true, value: parsed.value };
}

/**
 * The status and the control plane's own sentence, in the shape `verdictOf` already understands.
 *
 * A refusal (409: the session is finished, or already running) is a SUCCESSFUL read of an
 * unsuccessful write, so it comes back as `ok` with its status. `verdictOf` turns it into
 * "refused" for the person. Reporting it as an unreachable control plane would send them to
 * look for a network problem that is not there.
 */
function outcomeOf(raw: Read<RawResponse>, what: string): Read<WriteOutcome> {
  if (!raw.ok) return raw;
  let detail = "";
  try {
    const parsed: unknown = JSON.parse(raw.value.body);
    if (typeof parsed === "object" && parsed !== null) {
      const value = (parsed as Record<string, unknown>)["detail"];
      if (typeof value === "string") detail = value;
    }
  } catch {
    detail = "";
  }
  return {
    ok: true,
    // Widoczności skutku ta trasa nie mierzy, więc `null`, a nie `false`.
    value: { status: raw.value.status, detail: detail === "" ? what : detail, inConversation: null },
  };
}

/**
 * Rozpoczęta rozmowa: zwykły wynik zapisu plus identyfikator, jeśli dało się go odczytać.
 *
 * `sessionId` jest `null`, gdy odpowiedź go nie niosła. Wtedy przeglądarka wraca tam, skąd
 * przyszła, zamiast iść pod adres zbudowany ze zgadniętego identyfikatora: rozmowa POWSTAŁA i
 * widać ją w pasku, więc jedyne, co się traci, to jedno kliknięcie.
 */
export interface StartedSession extends WriteOutcome {
  readonly sessionId: string | null;
}

/** Entrust a goal. The first turn is queued by the control plane, not by this call. */
export async function startSession(
  token: string,
  workspaceId: string,
  projectId: string,
  goal: string,
  codexConnectionId: string | null,
): Promise<Read<StartedSession>> {
  const body: Record<string, unknown> = { goal };
  // Sent only when chosen. An absent connection is NOT "any": the session parks on its first
  // turn and says what is missing, which is the honest behaviour and the one the API expects.
  if (codexConnectionId !== null && codexConnectionId !== "") {
    body["codex_connection_id"] = codexConnectionId;
  }
  const raw = await call("POST", sessionsPath(workspaceId, projectId), { token, body });
  const outcome = outcomeOf(raw, "start sesji");
  if (!outcome.ok) return outcome;
  return { ok: true, value: { ...outcome.value, sessionId: startedId(raw) } };
}

/**
 * Identyfikator rozmowy z odpowiedzi `POST …/sessions`, albo `null`.
 *
 * Czytany OSOBNO od `outcomeOf`, bo tamta funkcja wyciąga tylko `detail` i jest wspólna dla
 * wszystkich zapisów. Tu potrzebna jest jedna wartość z ciała, i to wyłącznie po to, żeby po
 * wysłaniu pierwszego zdania człowiek wylądował w rozmowie, którą właśnie zaczął (D-045).
 */
function startedId(raw: Read<RawResponse>): string | null {
  if (!raw.ok) return null;
  try {
    const parsed: unknown = JSON.parse(raw.value.body);
    if (typeof parsed !== "object" || parsed === null) return null;
    const session = (parsed as Record<string, unknown>)["session"];
    if (typeof session !== "object" || session === null) return null;
    const id = (session as Record<string, unknown>)["session_id"];
    return typeof id === "string" && id !== "" ? id : null;
  } catch {
    return null;
  }
}

export async function sendSessionMessage(
  token: string,
  workspaceId: string,
  projectId: string,
  sessionId: string,
  text: string,
): Promise<Read<WriteOutcome>> {
  return outcomeOf(
    await call(
      "POST",
      `${sessionsPath(workspaceId, projectId)}/${encodeURIComponent(sessionId)}/message`,
      { token, body: { text } },
    ),
    "wiadomość do sesji",
  );
}

export async function interruptSession(
  token: string,
  workspaceId: string,
  projectId: string,
  sessionId: string,
): Promise<Read<WriteOutcome>> {
  return outcomeOf(
    await call(
      "POST",
      `${sessionsPath(workspaceId, projectId)}/${encodeURIComponent(sessionId)}/interrupt`,
      { token },
    ),
    "przerwanie sesji",
  );
}

/**
 * Nowa rozmowa wyrastająca z tej, która doszła do limitu (0026).
 *
 * NIE JEST WZNOWIENIEM I NIE UDAJE GO: tamta sesja została przy swoim wyczerpanym budżecie, a
 * ta zaczyna z limitem z ustawień projektu i z odwołaniem do poprzedniej. Doliczenie tur do
 * zamkniętej rozmowy zrobiłoby z budżetu liczbę, która nic nie znaczy.
 */
export async function continueSession(
  token: string,
  workspaceId: string,
  projectId: string,
  sessionId: string,
): Promise<Read<WriteOutcome>> {
  return outcomeOf(
    await call(
      "POST",
      `${sessionsPath(workspaceId, projectId)}/${encodeURIComponent(sessionId)}/continue`,
      { token },
    ),
    "nowa rozmowa",
  );
}

export async function resumeSession(
  token: string,
  workspaceId: string,
  projectId: string,
  sessionId: string,
): Promise<Read<WriteOutcome>> {
  return outcomeOf(
    await call(
      "POST",
      `${sessionsPath(workspaceId, projectId)}/${encodeURIComponent(sessionId)}/resume`,
      { token },
    ),
    "wznowienie sesji",
  );
}
