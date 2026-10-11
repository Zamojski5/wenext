/**
 * The JSON of `api/codex_connections.py`, checked before it reaches the "Połączenia Codex" screen.
 *
 * THE FIELD THAT MUST NEVER DEFAULT IS `read`. Both the model catalogue and the rate limits carry
 * one, and both mean "somebody actually asked and this is the answer". A parser that filled it in
 * would turn "nie wiemy" into "nie ma", which on this screen is the difference between a user
 * waiting for a limit to reset and a user thinking their subscription is broken.
 *
 * `used_percent`, `resets_at` and `window_minutes` are each `number | null` / `string | null` ON
 * PURPOSE and independently of one another. In the pinned app-server contract the only required
 * field of a rate-limit window is `usedPercent`; the other two are optional, so a window with a
 * percentage and no reset time is a legal answer and the screen prints it as such.
 */
import type { Read } from "./control-plane";

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function str(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" ? value : null;
}

function flag(row: Record<string, unknown>, field: string): boolean | null {
  const value = row[field];
  return typeof value === "boolean" ? value : null;
}

/** Present-and-nullable. Absence is a broken read, never a `null`. */
function nullable<T>(
  row: Record<string, unknown>,
  field: string,
  isValue: (value: unknown) => value is T,
): { readonly present: true; readonly value: T | null } | { readonly present: false } {
  if (!(field in row)) return { present: false };
  const value = row[field];
  if (value === null) return { present: true, value: null };
  if (isValue(value)) return { present: true, value };
  return { present: false };
}

const isString = (value: unknown): value is string => typeof value === "string";
const isNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

function report(what: string, missing: readonly string[]): Read<never> {
  return failure(
    `${what}: brakuje pól albo mają zły typ (${missing.join(", ")}). To zepsuty odczyt, ` +
      "nie puste dane",
  );
}

function objectAt(
  row: Record<string, unknown>,
  field: string,
  what: string,
): Read<Record<string, unknown>> {
  const value = row[field];
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return failure(`${what}: pole '${field}' nie jest obiektem`);
  }
  return { ok: true, value: value as Record<string, unknown> };
}

export interface LimitWindow {
  readonly name: string;
  /** `false` means the engine did not report this window at all. Not "zero percent used". */
  readonly present: boolean;
  readonly used_percent: number | null;
  readonly resets_at: string | null;
  readonly window_minutes: number | null;
}

export interface ConnectionLimits {
  readonly read: boolean;
  readonly reason: string | null;
  readonly at: string | null;
  readonly windows: readonly LimitWindow[];
  readonly plan_type: string | null;
}

export interface ConnectionModels {
  readonly read: boolean;
  /** `false` when the catalogue stopped before `nextCursor = null`. Routing is blocked then. */
  readonly complete: boolean;
  readonly count: number;
  readonly names: readonly string[];
  readonly fetched_at: string | null;
  readonly reason: string | null;
}

export interface LoginTicket {
  readonly verification_url: string;
  readonly user_code: string;
  readonly deadline_at: string | null;
}

export interface EngineRead {
  /**
   * `false` when this product deliberately never opens an app-server on this connection's
   * directory. Today that is the volume root, where the live pilot's turn runner already starts
   * one per turn and two on one `CODEX_HOME` are forbidden (L2159).
   */
  readonly enabled: boolean;
  readonly reason: string | null;
}

export interface ConnectionHome {
  readonly configured: boolean;
  readonly exists: boolean;
  readonly has_auth: boolean;
}

export interface CodexConnection {
  readonly connection_id: string;
  readonly label: string;
  readonly scope: string;
  readonly workspace_id: string | null;
  readonly home_ref: string;
  readonly status: string;
  readonly account_email: string | null;
  readonly plan_type: string | null;
  readonly installation_id: string | null;
  readonly models: ConnectionModels;
  readonly limits: ConnectionLimits;
  readonly login: LoginTicket | null;
  readonly last_read_at: string | null;
  readonly last_error: string | null;
  readonly created_at: string;
  readonly disconnected_at: string | null;
  readonly active: boolean;
  readonly home: ConnectionHome;
  readonly engine_read: EngineRead;
}

export interface CodexRootState {
  readonly configured: boolean;
  readonly adopted: boolean;
  readonly exists: boolean;
  readonly has_auth: boolean;
  readonly path: string | null;
}

export interface CodexConnectionsView {
  readonly connections: readonly CodexConnection[];
  readonly root: CodexRootState;
}

function parseWindow(row: Record<string, unknown>): Read<LimitWindow> {
  const missing: string[] = [];
  const name = str(row, "name");
  const present = flag(row, "present");
  const used = nullable(row, "used_percent", isNumber);
  const resets = nullable(row, "resets_at", isString);
  const minutes = nullable(row, "window_minutes", isNumber);
  if (name === null) missing.push("name");
  if (present === null) missing.push("present");
  if (!used.present) missing.push("used_percent");
  if (!resets.present) missing.push("resets_at");
  if (!minutes.present) missing.push("window_minutes");
  if (missing.length > 0) return report("okno limitu", missing);
  return {
    ok: true,
    value: {
      name: name as string,
      present: present as boolean,
      used_percent: used.present ? used.value : null,
      resets_at: resets.present ? resets.value : null,
      window_minutes: minutes.present ? minutes.value : null,
    },
  };
}

function parseLimits(row: Record<string, unknown>): Read<ConnectionLimits> {
  const wasRead = flag(row, "read");
  const reason = nullable(row, "reason", isString);
  const at = nullable(row, "at", isString);
  const planType = nullable(row, "plan_type", isString);
  const missing: string[] = [];
  if (wasRead === null) missing.push("read");
  if (!reason.present) missing.push("reason");
  if (!at.present) missing.push("at");
  if (!planType.present) missing.push("plan_type");
  if (!Array.isArray(row["windows"])) missing.push("windows");
  if (missing.length > 0) return report("limity połączenia", missing);

  const windows: LimitWindow[] = [];
  for (const entry of row["windows"] as unknown[]) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      return failure("limity połączenia: element 'windows' nie jest obiektem");
    }
    const parsed = parseWindow(entry as Record<string, unknown>);
    if (!parsed.ok) return parsed;
    windows.push(parsed.value);
  }
  return {
    ok: true,
    value: {
      read: wasRead as boolean,
      reason: reason.present ? reason.value : null,
      at: at.present ? at.value : null,
      windows,
      plan_type: planType.present ? planType.value : null,
    },
  };
}

function parseModels(row: Record<string, unknown>): Read<ConnectionModels> {
  const wasRead = flag(row, "read");
  const complete = flag(row, "complete");
  const count = typeof row["count"] === "number" ? (row["count"] as number) : null;
  const fetchedAt = nullable(row, "fetched_at", isString);
  const reason = nullable(row, "reason", isString);
  const missing: string[] = [];
  if (wasRead === null) missing.push("read");
  if (complete === null) missing.push("complete");
  if (count === null) missing.push("count");
  if (!fetchedAt.present) missing.push("fetched_at");
  if (!reason.present) missing.push("reason");
  if (!Array.isArray(row["models"])) missing.push("models");
  if (missing.length > 0) return report("katalog modeli", missing);

  const names: string[] = [];
  for (const entry of row["models"] as unknown[]) {
    if (typeof entry !== "object" || entry === null) continue;
    const identifier = (entry as Record<string, unknown>)["id"];
    if (typeof identifier === "string") names.push(identifier);
  }
  return {
    ok: true,
    value: {
      read: wasRead as boolean,
      complete: complete as boolean,
      count: count as number,
      names,
      fetched_at: fetchedAt.present ? fetchedAt.value : null,
      reason: reason.present ? reason.value : null,
    },
  };
}

/**
 * `null` means no login is open. An ABSENT field means a broken read, like every other nullable
 * field in this package: the control plane always sends `login`, so its absence is a payload
 * nobody should render, not "there is no ticket".
 */
function parseLogin(row: Record<string, unknown>): Read<LoginTicket | null> {
  if (!("login" in row)) {
    return failure("połączenie: brak pola 'login'. To zepsuty odczyt, nie brak logowania");
  }
  const value = row["login"];
  if (value === null) return { ok: true, value: null };
  if (typeof value !== "object" || Array.isArray(value)) {
    return failure("połączenie: pole 'login' nie jest obiektem ani null-em");
  }
  const ticket = value as Record<string, unknown>;
  const url = str(ticket, "verification_url");
  const code = str(ticket, "user_code");
  if (url === null || code === null) {
    // A ticket without both halves is unusable: the human needs the address AND the code.
    return failure("połączenie: otwarte logowanie bez adresu albo bez kodu");
  }
  const deadline = str(ticket, "deadline_at");
  return { ok: true, value: { verification_url: url, user_code: code, deadline_at: deadline } };
}

export function parseConnection(row: Record<string, unknown>): Read<CodexConnection> {
  const missing: string[] = [];
  const id = str(row, "connection_id");
  const label = str(row, "label");
  const scope = str(row, "scope");
  const homeRef = str(row, "home_ref");
  const status = str(row, "status");
  const createdAt = str(row, "created_at");
  const active = flag(row, "active");
  const workspaceId = nullable(row, "workspace_id", isString);
  const email = nullable(row, "account_email", isString);
  const planType = nullable(row, "plan_type", isString);
  const installation = nullable(row, "installation_id", isString);
  const lastRead = nullable(row, "last_read_at", isString);
  const lastError = nullable(row, "last_error", isString);
  const disconnectedAt = nullable(row, "disconnected_at", isString);
  if (id === null) missing.push("connection_id");
  if (label === null) missing.push("label");
  if (scope === null) missing.push("scope");
  if (homeRef === null) missing.push("home_ref");
  if (status === null) missing.push("status");
  if (createdAt === null) missing.push("created_at");
  if (active === null) missing.push("active");
  if (!workspaceId.present) missing.push("workspace_id");
  if (!email.present) missing.push("account_email");
  if (!planType.present) missing.push("plan_type");
  if (!installation.present) missing.push("installation_id");
  if (!lastRead.present) missing.push("last_read_at");
  if (!lastError.present) missing.push("last_error");
  if (!disconnectedAt.present) missing.push("disconnected_at");
  if (missing.length > 0) return report("połączenie Codex", missing);

  const models = objectAt(row, "models", "połączenie Codex");
  if (!models.ok) return models;
  const parsedModels = parseModels(models.value);
  if (!parsedModels.ok) return parsedModels;

  const limits = objectAt(row, "limits", "połączenie Codex");
  if (!limits.ok) return limits;
  const parsedLimits = parseLimits(limits.value);
  if (!parsedLimits.ok) return parsedLimits;

  const login = parseLogin(row);
  if (!login.ok) return login;

  const engineRead = objectAt(row, "engine_read", "połączenie Codex");
  if (!engineRead.ok) return engineRead;
  const readEnabled = flag(engineRead.value, "enabled");
  const readReason = nullable(engineRead.value, "reason", isString);
  if (readEnabled === null || !readReason.present) {
    return report("odczyt silnika", ["enabled", "reason"]);
  }

  const home = objectAt(row, "home", "połączenie Codex");
  if (!home.ok) return home;
  const configured = flag(home.value, "configured");
  const exists = flag(home.value, "exists");
  const hasAuth = flag(home.value, "has_auth");
  if (configured === null || exists === null || hasAuth === null) {
    return report("katalog połączenia", ["configured", "exists", "has_auth"]);
  }

  return {
    ok: true,
    value: {
      connection_id: id as string,
      label: label as string,
      scope: scope as string,
      workspace_id: workspaceId.present ? workspaceId.value : null,
      home_ref: homeRef as string,
      status: status as string,
      account_email: email.present ? email.value : null,
      plan_type: planType.present ? planType.value : null,
      installation_id: installation.present ? installation.value : null,
      models: parsedModels.value,
      limits: parsedLimits.value,
      login: login.value,
      last_read_at: lastRead.present ? lastRead.value : null,
      last_error: lastError.present ? lastError.value : null,
      created_at: createdAt as string,
      disconnected_at: disconnectedAt.present ? disconnectedAt.value : null,
      active: active as boolean,
      home: { configured, exists, has_auth: hasAuth },
      engine_read: {
        enabled: readEnabled,
        reason: readReason.present ? readReason.value : null,
      },
    },
  };
}

export function parseCodexRoot(row: Record<string, unknown>): Read<CodexRootState> {
  const configured = flag(row, "configured");
  const adopted = flag(row, "adopted");
  if (configured === null || adopted === null) {
    return report("stan korzenia wolumenu", ["configured", "adopted"]);
  }
  // The remaining fields exist only when a root is configured — the backend leaves them out
  // entirely otherwise, and that absence is itself the answer.
  return {
    ok: true,
    value: {
      configured,
      adopted,
      exists: flag(row, "exists") ?? false,
      has_auth: flag(row, "has_auth") ?? false,
      path: str(row, "path"),
    },
  };
}
