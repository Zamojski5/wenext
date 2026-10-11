/**
 * The two decisions behind the capsule handlers — the ones a review caught being wrong.
 *
 * BOTH BUGS THIS FILE PINS DOWN WERE REAL, and both are of the same family: a state nobody
 * measured, reported as if somebody had.
 *
 *   1. **A timeout reported as "nothing happened."** Confirming a sign-in switches the capsule's
 *      mode and reads the platform twice. When the dashboard stops waiting after 90 s, the work
 *      upstream does not stop — the switch and the resumed jobs may both have completed. The first
 *      version put that in the same branch as a refusal and told the operator "nic nie zostało
 *      zmienione", which is a claim about a state nobody looked at.
 *   2. **An unvalidated redirect.** The open handler answers `303` to an address from a JSON body.
 *      "It came from our own API" is the reasoning behind every open redirect ever shipped, so the
 *      address is compared against one configured origin and everything else is refused.
 *
 * The functions are pure, so these run with no request, no cookie jar and no control plane.
 */
import { describe, expect, it } from "vitest";

import {
  CAPSULE_VIEWER_ORIGIN_VARIABLE,
  X_CAPSULE_VIEWER_ORIGIN_VARIABLE,
  capsulePlatform,
  capsuleViewerOrigin,
  capsuleViewerOrigins,
  confirmDecision,
  openDecision,
  viewerRedirectAllowed,
} from "../lib/capsule-flow";
import type { Read } from "../lib/control-plane";
import type { CapsuleConfirmation, CapsuleTicket } from "../lib/capsule-schema";

const ORIGIN = "http://127.0.0.1:8760";
const TICKET = `${ORIGIN}/login-capsule#ticket=abc&plg=1&ilg=1`;

function ticket(href = TICKET): Read<CapsuleTicket> {
  return { ok: true, value: { href, ticket_expires_at: "2026-09-04T10:00:00.000Z", status: "AWAITING_HUMAN" } };
}

function confirmation(status: string): Read<CapsuleConfirmation> {
  const probe = { logged_in: status === "SIGNED_IN", username: status === "SIGNED_IN" ? "u/x" : null, threads: 3, error: null };
  return {
    ok: true,
    value: { status, account: status === "SIGNED_IN" ? "u/x" : null, with_human: probe, display_only: probe, resumed_jobs: 2 },
  };
}

describe("the configured viewer origin", () => {
  it("is_read_from_the_environment_and_absent_when_unset_or_broken", () => {
    expect(capsuleViewerOrigin({ [CAPSULE_VIEWER_ORIGIN_VARIABLE]: ORIGIN })).toBe(ORIGIN);
    // A path or a trailing slash still yields the ORIGIN, so configuration style cannot break it.
    expect(capsuleViewerOrigin({ [CAPSULE_VIEWER_ORIGIN_VARIABLE]: `${ORIGIN}/login-capsule` })).toBe(ORIGIN);
    // Absent, empty, and unparsable are all "no origin" — refusing to redirect is the safe way to
    // be wrong, and the operator gets a sentence rather than a jump to a half-parsed address.
    expect(capsuleViewerOrigin({})).toBeNull();
    expect(capsuleViewerOrigin({ [CAPSULE_VIEWER_ORIGIN_VARIABLE]: "   " })).toBeNull();
    expect(capsuleViewerOrigin({ [CAPSULE_VIEWER_ORIGIN_VARIABLE]: "8760" })).toBeNull();
  });
});

describe("the X capsule's own viewer (D-051)", () => {
  const X_ORIGIN = "http://127.0.0.1:8761";
  const both = { [CAPSULE_VIEWER_ORIGIN_VARIABLE]: ORIGIN, [X_CAPSULE_VIEWER_ORIGIN_VARIABLE]: X_ORIGIN };

  it("lets_an_x_sign_in_land_on_the_x_screen_or_the_shared_one_and_reddit_only_on_the_shared", () => {
    expect(capsuleViewerOrigins("X", both)).toEqual([X_ORIGIN, ORIGIN]);
    expect(capsuleViewerOrigins("REDDIT", both)).toEqual([ORIGIN]);
    expect(capsuleViewerOrigins("X", { [CAPSULE_VIEWER_ORIGIN_VARIABLE]: ORIGIN })).toEqual([ORIGIN]);
    expect(capsuleViewerOrigins("X", {})).toEqual([]);
  });

  it("redirects_to_the_x_screen_for_x_and_refuses_it_for_reddit", () => {
    const xTicket = ticket(`${X_ORIGIN}/login-capsule#ticket=abc&plg=1&ilg=1`);
    expect(openDecision(xTicket, capsuleViewerOrigins("X", both))).toEqual({
      kind: "redirect",
      href: `${X_ORIGIN}/login-capsule#ticket=abc&plg=1&ilg=1`,
    });
    expect(openDecision(xTicket, capsuleViewerOrigins("REDDIT", both))).toEqual({
      kind: "info",
      info: "capsule_open_blocked",
    });
    expect(openDecision(ticket("http://127.0.0.1:8762/login-capsule#ticket=a"), capsuleViewerOrigins("X", both))).toEqual({
      kind: "info",
      info: "capsule_open_blocked",
    });
  });
});

describe("the redirect allowlist", () => {
  it("accepts_only_the_configured_origin_over_http_or_https", () => {
    expect(viewerRedirectAllowed(TICKET, ORIGIN)).toBe(true);
    expect(viewerRedirectAllowed(`${ORIGIN}/anything?a=b`, ORIGIN)).toBe(true);
    expect(viewerRedirectAllowed("https://viewer.example/x", "https://viewer.example")).toBe(true);
  });

  it("refuses_every_shape_an_open_redirect_arrives_in", () => {
    // Each entry is a real trick: another host, a lookalike host, a different port (a different
    // origin), a scheme that is not a page, credentials in the address, and a value that is not a
    // URL at all. The count is asserted so the list cannot quietly shrink to nothing.
    const refused: readonly string[] = [
      "http://evil.example/login-capsule",
      "http://127.0.0.1.evil.example/login-capsule",
      "http://127.0.0.1:9999/login-capsule",
      "https://127.0.0.1:8760/login-capsule",
      "javascript:alert(1)",
      "data:text/html,<h1>x</h1>",
      "file:///etc/passwd",
      "http://user:pass@127.0.0.1:8760/login-capsule",
      "//127.0.0.1:8760/login-capsule",
      "/community",
      "",
    ];
    expect(refused.length).toBe(11);
    for (const href of refused) {
      expect(viewerRedirectAllowed(href, ORIGIN), href).toBe(false);
    }
  });

  it("refuses_everything_when_no_origin_is_configured", () => {
    // Including the address that WOULD be right: without configuration there is nothing to compare
    // against, and "probably ours" is not a check.
    expect(viewerRedirectAllowed(TICKET, null)).toBe(false);
  });
});

describe("openDecision", () => {
  it("redirects_to_a_ticket_from_the_configured_origin", () => {
    expect(openDecision(ticket(), ORIGIN)).toEqual({ kind: "redirect", href: TICKET });
  });

  it("refuses_a_foreign_address_with_a_different_sentence_than_a_broken_call", () => {
    // Two different problems, two different sentences: one is a configuration mistake on this side,
    // the other is the capsule or the gateway being down. Sending the operator to the same place
    // for both would send them to the wrong place half the time.
    const blocked = openDecision(ticket("http://evil.example/x"), ORIGIN);
    const refused = openDecision(
      { ok: false, unauthorized: false, reason: "otwarcie przeglądarki agenta: kapsuła nieosiągalna" },
      ORIGIN,
    );

    expect(blocked).toEqual({ kind: "info", info: "capsule_open_blocked" });
    expect(refused).toEqual({ kind: "info", info: "capsule_open_refused" });
    expect(blocked).not.toEqual(refused);
  });

  it("sends_an_expired_session_to_the_login_screen", () => {
    expect(openDecision({ ok: false, unauthorized: true, reason: "401" }, ORIGIN)).toEqual({ kind: "login" });
  });
});

describe("capsulePlatform", () => {
  it("opens_only_the_platforms_the_agents_browser_signs_into_and_empty_means_reddit", () => {
    // The value arrives in a hidden field of the account card's form (26.09). Exact spelling, the
    // backend's: a lenient match would make the closed set a suggestion.
    expect(capsulePlatform("X")).toBe("X");
    expect(capsulePlatform("REDDIT")).toBe("REDDIT");
    expect(capsulePlatform("")).toBe("REDDIT");
    for (const refused of ["x", "reddit", "LINKEDIN", "HACKER_NEWS", " X", "X,REDDIT"]) {
      expect(capsulePlatform(refused), refused).toBeNull();
    }
  });
});

describe("confirmDecision", () => {
  it("keeps_signed_in_and_signed_out_apart", () => {
    expect(confirmDecision(confirmation("SIGNED_IN"))).toEqual({ kind: "info", info: "capsule_signed_in" });
    expect(confirmDecision(confirmation("SIGNED_OUT"))).toEqual({ kind: "info", info: "capsule_signed_out" });
  });

  it("a_timeout_is_not_reported_as_nothing_happened", () => {
    // THE test this file exists for. The dashboard stopped waiting; the capsule may already have
    // switched modes and the parked jobs may already be back in the queue. Saying "nic nie zostało
    // zmienione" would be a claim about a state nobody measured.
    const timedOut = confirmDecision({
      ok: false,
      unauthorized: false,
      timedOut: true,
      reason: "control plane nie odpowiedział w wyznaczonym czasie",
    });
    const failed = confirmDecision({ ok: false, unauthorized: false, reason: "503 kapsuła nieosiągalna" });

    expect(timedOut).toEqual({ kind: "info", info: "capsule_confirm_pending" });
    expect(failed).toEqual({ kind: "info", info: "capsule_confirm_failed" });
    expect(timedOut).not.toEqual(failed);
  });

  it("sends_an_expired_session_to_the_login_screen", () => {
    expect(confirmDecision({ ok: false, unauthorized: true, reason: "401" })).toEqual({ kind: "login" });
  });
});
