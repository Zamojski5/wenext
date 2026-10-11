/**
 * The browser's session, which is NOT the control plane's session.
 *
 * D-016 and this package's brief say the same thing in different words: "token sesji operatora
 * NIGDY nie trafia do przeglądarki". Forwarding the control plane's `Set-Cookie` straight through
 * would satisfy the letter of "HttpOnly" and break that sentence anyway — the token would then sit
 * in the browser's cookie jar, travel on every request to this server, appear in any HAR the
 * founder exports, and survive in a profile backup. So this process keeps the token and hands the
 * browser an unrelated, opaque HANDLE.
 *
 *   browser  ──  wenext_dashboard_handle=<32 random bytes>  ──▶  Next.js server
 *   Next.js  ──  wenext_operator_session=<control plane token>  ──▶  control plane
 *
 * The map is keyed by the SHA-256 of the handle, exactly like `control_plane.auth.SessionStore`:
 * the raw handle is never stored, so a memory dump or a `repr` in a traceback hands out nothing
 * usable. The value it protects is a live control plane token, which makes this the one place in
 * the dashboard where storing a raw secret would be a real leak.
 *
 * TWO LIMITS, WRITTEN DOWN RATHER THAN HIDDEN — both measured, neither guessed:
 *
 *   1. **The map is in memory, so restarting the Next.js process logs the operator out.** There is
 *      no session table for the dashboard in migrations 0001-0003 and inventing one is a
 *      migration, not a module. `control_plane.auth.SessionStore` documents the identical limit
 *      for the same reason, and F1's DoD asks that a restart not lose a TASK (L5212) — it says
 *      nothing about a session, and a login is one password away.
 *   2. **One process only.** Two dashboard replicas would not share this map. The stack runs one
 *      (`docker-compose.yml`, service `dashboard`), and a second replica is a decision, not a
 *      `--scale` flag.
 *
 * NOT MEASURED, AND SAID SO PLAINLY: nothing here expires the handle on a schedule. The TTL below
 * is checked when a handle is RESOLVED, so a stale entry can sit in the map until somebody
 * presents it — bounded by the process lifetime, not swept. There is no reaper for this map and
 * no test claiming one.
 */
import { createHash, randomBytes } from "node:crypto";

/**
 * Name of the cookie the browser holds. Deliberately NOT
 * `control_plane.auth.SESSION_COOKIE_NAME`: two different secrets under one name is how they get
 * confused, and the whole point here is that they are different.
 */
export const DASHBOARD_COOKIE_NAME = "wenext_dashboard_handle";

/**
 * How long a handle stays usable. Mirrors `control_plane.auth.SESSION_TTL` (8 h, absolute, not
 * sliding). The control plane remains the authority: when it stops recognising the token, the
 * guard sends the operator to the login screen no matter what this clock says.
 */
export const DASHBOARD_SESSION_TTL_MS = 8 * 60 * 60 * 1000;

/** 32 bytes of `randomBytes`, hex. The same order of magnitude as `secrets.token_urlsafe(32)`. */
const HANDLE_BYTES = 32;

interface StoredSession {
  readonly controlPlaneToken: string;
  readonly openedAtMs: number;
}

/**
 * ON `globalThis`, AND THAT IS NOT A STYLE CHOICE — IT IS A BUG THAT WAS MEASURED AND FIXED.
 *
 * The first version of this file held the map in a module-level `const`, on the reasoning that
 * Next.js keeps one module instance per server process (which is what
 * `control_plane.auth.SESSIONS` relies on, correctly, in Python). In a PRODUCTION Next.js build
 * that reasoning is wrong: `app/login/submit/route.ts` and `app/jobs/page.tsx` are compiled into
 * SEPARATE bundles, each with its own module registry, so each got its own `Map`. The handle minted
 * by the route handler was therefore unknown to every page, and the dashboard redirected to the
 * login screen immediately after a successful login — forever.
 *
 * It was invisible in the unit suite, where `tests/session.test.ts` imports one module instance and
 * every assertion passes. It showed up the first time the end-to-end suite drove a real `next
 * start`: ten of eleven cases failed with 307 to `/login`. That is precisely why the end-to-end run
 * exists, and precisely why "the components render correctly" is not evidence that the product
 * works.
 *
 * `globalThis` is per PROCESS, which is the scope the session actually has. The symbol is namespaced
 * so nothing else can collide with it.
 */
const STORE_KEY = Symbol.for("wenext.dashboard.sessions");

type GlobalWithSessions = typeof globalThis & {
  [STORE_KEY]?: Map<string, StoredSession>;
};

const globalScope = globalThis as GlobalWithSessions;
const SESSIONS: Map<string, StoredSession> = (globalScope[STORE_KEY] ??= new Map<
  string,
  StoredSession
>());

function keyOf(handle: string): string {
  return createHash("sha256").update(handle, "utf8").digest("hex");
}

/** Mint a handle for a control plane token. Returns the handle — the ONLY value the browser gets. */
export function openDashboardSession(controlPlaneToken: string, nowMs = Date.now()): string {
  if (controlPlaneToken === "") {
    throw new Error(
      "pusty token control plane. Sesja panelu bez tokenu byłaby uchwytem, który wygląda jak " +
        "zalogowanie i nie pozwala odczytać niczego",
    );
  }
  const handle = randomBytes(HANDLE_BYTES).toString("hex");
  SESSIONS.set(keyOf(handle), { controlPlaneToken, openedAtMs: nowMs });
  return handle;
}

/**
 * The control plane token this handle stands for, or `undefined`.
 *
 * An expired entry is DELETED and reported as absent, not returned — the same shape as
 * `SessionStore.resolve`. `undefined` also covers "no cookie at all", and the caller must treat
 * both as "not logged in": there is nothing to distinguish for a caller who has no session.
 */
export function controlPlaneTokenFor(
  handle: string | undefined,
  nowMs = Date.now(),
): string | undefined {
  if (handle === undefined || handle === "") {
    return undefined;
  }
  const key = keyOf(handle);
  const stored = SESSIONS.get(key);
  if (stored === undefined) {
    return undefined;
  }
  if (nowMs - stored.openedAtMs >= DASHBOARD_SESSION_TTL_MS) {
    SESSIONS.delete(key);
    return undefined;
  }
  return stored.controlPlaneToken;
}

/**
 * Forget this handle and return the token it held, so the caller can end the session UPSTREAM too.
 *
 * Dropping only the local entry would leave a live control plane session for anybody holding a
 * copy of the token — and the copy is the entire reason to log out (`api/session.py` says the same
 * about the cookie).
 */
export function closeDashboardSession(handle: string | undefined): string | undefined {
  if (handle === undefined || handle === "") {
    return undefined;
  }
  const key = keyOf(handle);
  const stored = SESSIONS.get(key);
  SESSIONS.delete(key);
  return stored?.controlPlaneToken;
}

/** How many handles the map holds. For the tests, and for nothing else. */
export function dashboardSessionCount(): number {
  return SESSIONS.size;
}

/** Drop every handle. For the tests, so one case cannot authenticate the next one. */
export function clearDashboardSessions(): void {
  SESSIONS.clear();
}

/**
 * The `Set-Cookie` value that gives the browser its handle.
 *
 * Flag by flag, and each one from D-016:
 *   - `HttpOnly` — browser script must not read it. It is a handle, not the token, but a handle
 *     readable by script is still a session anybody's injected code can lift.
 *   - `SameSite=Strict` — nothing else on this host may drive the panel.
 *   - no `Secure` — the stack publishes plain HTTP on the loopback interface (D-014), and a
 *     `Secure` cookie would never come back: the session would silently stop working. It becomes
 *     `Secure` the day this is served over TLS, which is the day D-014's assumption expires.
 *   - `Path=/` — every screen is behind the boundary.
 */
export function dashboardSessionCookie(handle: string): string {
  return [
    `${DASHBOARD_COOKIE_NAME}=${handle}`,
    "Path=/",
    `Max-Age=${Math.floor(DASHBOARD_SESSION_TTL_MS / 1000)}`,
    "HttpOnly",
    "SameSite=Strict",
  ].join("; ");
}

/** The `Set-Cookie` value that asks the browser to drop the handle. */
export function clearedDashboardSessionCookie(): string {
  return [
    `${DASHBOARD_COOKIE_NAME}=`,
    "Path=/",
    "Max-Age=0",
    "HttpOnly",
    "SameSite=Strict",
  ].join("; ");
}
