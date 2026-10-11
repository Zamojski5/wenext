/**
 * Shared shape of the four form handlers under `app/studio/`.
 *
 * Each one resolves the operator from the dashboard cookie, runs ONE control plane write
 * server-side, and sends the browser back with a sentence chosen from a CLOSED SET
 * (`lib/studio-info.ts`). 303 See Other, like the login handler, so the browser follows with GET
 * and a refresh never repeats the write.
 *
 * WHY FOUR HANDLERS AND NOT NINE. The studio has nine writes; grouping them by the object they
 * act on keeps the mutating surface of this package small and easy to read in
 * `tests/source_scan.test.ts`, which asserts the complete list of route handlers by hand. Each
 * handler dispatches on an `action` field against a CLOSED set and REFUSES anything else — a
 * dispatch with a permissive default is how a form field becomes an instruction.
 *
 * `return_to` IS VALIDATED, NOT TRUSTED. It arrives in the POST body, which means anybody can put
 * anything in it, and a 303 to an attacker-chosen `Location` is an open redirect: a link that
 * looks like this dashboard and lands somewhere else, having been followed by a logged-in user.
 * `safeReturn` accepts only a path on this origin and falls back to `/` — the check is that the
 * value starts with exactly ONE slash, because `//evil.example` is a protocol-relative URL that a
 * naive `startsWith("/")` waves through.
 *
 * NOT a page, so `layout_routes.test.tsx` does not expect a `page.tsx` here.
 */
import { cookies } from "next/headers";

import { getSession, type Read, type WriteOutcome } from "../../lib/control-plane";
import { LOGIN_PATH } from "../../lib/routes";
import { DASHBOARD_COOKIE_NAME, controlPlaneTokenFor } from "../../lib/session";

export function seeOther(location: string): Response {
  return new Response(null, { status: 303, headers: { location, "cache-control": "no-store" } });
}

/** Back to `path` with one key from `STUDIO_INFO`. The key, never a sentence. */
export function backTo(path: string, info: string): Response {
  const separator = path.includes("?") ? "&" : "?";
  return seeOther(`${path}${separator}info=${encodeURIComponent(info)}`);
}

/**
 * A same-origin path from the form, or `/`.
 *
 * Refused: an absolute URL, a protocol-relative `//host`, a backslash (some clients normalise
 * `\\host` to `//host`), and anything not starting with a slash.
 */
export function safeReturn(form: FormData): string {
  const raw = form.get("return_to");
  if (typeof raw !== "string") return "/";
  const value = raw.trim();
  if (!value.startsWith("/")) return "/";
  if (value.startsWith("//") || value.startsWith("/\\")) return "/";
  if (value.includes("\\")) return "/";
  return value;
}

/** The control plane token for this request, or a redirect to the login screen. */
export async function tokenOrLogin(
  fallback = "/",
): Promise<{ readonly token: string } | { readonly redirect: Response }> {
  const jar = await cookies();
  const token = controlPlaneTokenFor(jar.get(DASHBOARD_COOKIE_NAME)?.value);
  if (token === undefined) {
    return { redirect: seeOther(LOGIN_PATH) };
  }
  const session = await getSession(token);
  if (!session.ok) {
    return {
      redirect: session.unauthorized
        ? seeOther(LOGIN_PATH)
        : backTo(fallback, "unreachable"),
    };
  }
  return { token };
}

/** Read the form, refusing anything that is not a form. */
export async function formOf(request: Request): Promise<FormData | null> {
  try {
    return await request.formData();
  } catch {
    return null;
  }
}

export function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

/** An unchecked checkbox is simply absent from the body, which is what makes this `false`. */
export function checkbox(form: FormData, name: string): boolean {
  return form.get(name) !== null;
}

/**
 * Map a write's outcome onto the message the user gets back.
 *
 * A timeout is NOT reported as a refusal: this side stopped waiting and the control plane may
 * have finished. `unreachable` says exactly that, and `lib/studio-info.ts` spells it out for the
 * reader.
 */
export function verdictOf(
  outcome: Read<WriteOutcome>,
  ok: string,
  path: string,
): Response {
  if (!outcome.ok) {
    return outcome.unauthorized ? seeOther(LOGIN_PATH) : backTo(path, "unreachable");
  }
  const status = outcome.value.status;
  return backTo(path, status >= 200 && status < 300 ? ok : "refused");
}
