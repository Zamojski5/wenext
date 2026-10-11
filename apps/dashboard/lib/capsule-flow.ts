/**
 * The two decisions the capsule handlers make, as pure functions — so they can be tested without
 * a request, a cookie jar or a control plane.
 *
 * The handlers under `app/community/capsule/` are then three lines each: resolve the operator, call
 * the control plane, ask one of these what to do. Everything that could be wrong (an address that
 * does not belong to us, a timeout dressed as a failure, a refusal dressed as success) is decided
 * here and covered by `tests/capsule_flow.test.ts`.
 *
 * WHY THE REDIRECT NEEDS AN ALLOWLIST AT ALL. `app/community/capsule/open/route.ts` answers `303`
 * to an address that arrived in a JSON body. The control plane is not hostile, but "the value came
 * from our own API" is exactly the reasoning behind every open redirect ever shipped: one bug or
 * one injected row upstream and this dashboard becomes a jump host to anywhere. So the address is
 * checked against ONE configured origin, and anything else is refused with a sentence — the same
 * discipline as `lib/format.ts::linkCell`, which refuses any cell link that is not an internal path.
 *
 * WHY A TIMEOUT IS NOT A FAILURE. Confirming a sign-in switches the capsule's mode and reads the
 * platform twice; the backend measured up to a minute. When the dashboard stops waiting, the work
 * upstream DOES NOT STOP — the capsule may already have switched and the parked jobs may already
 * be back in the queue. Reporting that as "nic nie zostało zmienione" would be a claim about a
 * state nobody measured, so it gets its own sentence and sends the operator to look at the card.
 */
import type { Read } from "./control-plane";
import type { CapsuleConfirmation, CapsuleTicket } from "./capsule-schema";

/**
 * Where the capsule's viewer lives, as an ORIGIN (`http://127.0.0.1:8760`). Set from the same value
 * the gateway publishes; absent means the dashboard will not redirect anywhere at all.
 *
 * NOT prefixed `NEXT_PUBLIC_` — like every other variable in this package, it is read server-side
 * only (`tests/source_scan.test.ts` scans for the prefix).
 */
export const CAPSULE_VIEWER_ORIGIN_VARIABLE = "WENEXT_CAPSULE_VIEWER_ORIGIN";

/** The configured origin, or `null` when nobody configured one. Never throws: absence is a state. */
export function capsuleViewerOrigin(environment: Record<string, string | undefined> = process.env): string | null {
  const configured = environment[CAPSULE_VIEWER_ORIGIN_VARIABLE];
  if (configured === undefined || configured.trim() === "") {
    return null;
  }
  try {
    return new URL(configured).origin;
  } catch {
    // A misconfigured origin is treated as no origin: refusing to redirect is the safe direction,
    // and the operator sees a sentence rather than a jump to a half-parsed address.
    return null;
  }
}

/**
 * May the dashboard send a browser to `href`?
 *
 * Four conditions, and all four have to hold: an origin is configured, the address parses, its
 * scheme is HTTP(S), and its origin is exactly the configured one. Credentials in the URL are
 * refused too — `http://user:pass@host/` has the right origin and is still not an address this
 * dashboard hands to a browser.
 */
export function viewerRedirectAllowed(href: string, origin: string | null): boolean {
  if (origin === null) return false;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return false;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false;
  if (url.username !== "" || url.password !== "") return false;
  return url.origin === origin;
}

/**
 * The platforms whose sign-in a studio account card may open in the agent's browser (26.09).
 *
 * A CLOSED SET, checked by `app/studio/capsule/open/route.ts` although the card chooses the value
 * itself: it arrives in a hidden form field, and a form field becomes an instruction only after it
 * has been checked. The spelling is the backend's (`{"platform": "X"}`), matched exactly.
 */
export const CAPSULE_PLATFORMS = ["REDDIT", "X"] as const;
export type CapsulePlatform = (typeof CAPSULE_PLATFORMS)[number];

/**
 * The X capsule's viewer origin (D-051). The X account has a capsule of its own, running Chromium
 * because Google refuses a person's sign-in in Chrome for Testing, and its screen is served by a
 * second gateway on its own origin. Same rule as above: read server-side only, absent is a state.
 */
export const X_CAPSULE_VIEWER_ORIGIN_VARIABLE = "WENEXT_X_CAPSULE_VIEWER_ORIGIN";

/**
 * The origins a sign-in for `platform` may be sent to. Reddit: the one capsule viewer. X: its own
 * viewer AND that one, because whether X has a capsule of its own is the control plane's setting
 * (`WENEXT_X_BROWSER_READER_URL`), and until it does, X signs in on the shared screen. Both are
 * configured origins; nothing from the response widens the list.
 */
export function capsuleViewerOrigins(
  platform: CapsulePlatform,
  environment: Record<string, string | undefined> = process.env,
): readonly string[] {
  const shared = capsuleViewerOrigin(environment);
  const own =
    platform === "X"
      ? capsuleViewerOrigin({
          [CAPSULE_VIEWER_ORIGIN_VARIABLE]: environment[X_CAPSULE_VIEWER_ORIGIN_VARIABLE],
        })
      : null;
  return [own, shared].filter((origin): origin is string => origin !== null);
}

/**
 * The platform to open, or `null` for anything outside the set.
 *
 * EMPTY MEANS REDDIT, and only empty does: account cards from before 26.09 carry no platform in
 * their `polacz` payload, and every one of them was a Reddit card.
 */
export function capsulePlatform(requested: string): CapsulePlatform | null {
  const value = requested === "" ? "REDDIT" : requested;
  return CAPSULE_PLATFORMS.find((one) => one === value) ?? null;
}

/** What the open handler does next: send the browser somewhere, or say why it will not. */
export type OpenDecision =
  | { readonly kind: "redirect"; readonly href: string }
  | { readonly kind: "login" }
  | { readonly kind: "info"; readonly info: string };

/**
 * The open decision. A refused address and an unreachable control plane are DIFFERENT sentences,
 * because they send the operator to different places: one is a configuration problem on this side,
 * the other is the capsule or the gateway being down.
 */
export function openDecision(
  read: Read<CapsuleTicket>,
  origin: string | null | readonly string[],
): OpenDecision {
  if (!read.ok) {
    if (read.unauthorized) return { kind: "login" };
    return { kind: "info", info: "capsule_open_refused" };
  }
  const allowed = origin === null ? [] : typeof origin === "string" ? [origin] : origin;
  if (!allowed.some((one) => viewerRedirectAllowed(read.value.href, one))) {
    return { kind: "info", info: "capsule_open_blocked" };
  }
  return { kind: "redirect", href: read.value.href };
}

/** What the confirm handler says. Four outcomes, and none of them may impersonate another. */
export type ConfirmDecision =
  | { readonly kind: "login" }
  | { readonly kind: "info"; readonly info: string };

export function confirmDecision(read: Read<CapsuleConfirmation>): ConfirmDecision {
  if (!read.ok) {
    if (read.unauthorized) return { kind: "login" };
    // THE DISTINCTION THIS FUNCTION EXISTS FOR. A timeout means the dashboard stopped waiting, not
    // that nothing happened: the switch and the resume may both have completed upstream. Saying
    // "nic nie zostało zmienione" there would be a claim about a state nobody measured.
    if (read.timedOut === true) return { kind: "info", info: "capsule_confirm_pending" };
    return { kind: "info", info: "capsule_confirm_failed" };
  }
  return {
    kind: "info",
    info: read.value.status === "SIGNED_IN" ? "capsule_signed_in" : "capsule_signed_out",
  };
}
