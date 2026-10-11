/**
 * The browser gets a handle. The control plane token stays here. That is the whole test.
 *
 * D-016 and the package brief both say it: "token sesji operatora NIGDY nie trafia do
 * przeglądarki". Forwarding the control plane's own `Set-Cookie` would have been shorter and would
 * have put a live operator session token in the browser's cookie jar — where it survives in HAR
 * exports and profile backups. So the assertions below are about a NEGATIVE: the cookie value the
 * browser receives must not be the token, must not contain it, and must not be derivable from it by
 * looking.
 *
 * The end-to-end version of the same promise is
 * `e2e/dashboard_reads_postgres.e2e.test.ts::the_control_plane_session_token_never_appears_in_a_dashboard_response`,
 * which scans every byte of every response of a real server. This file covers the mechanism; that
 * one covers the wiring.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  DASHBOARD_COOKIE_NAME,
  DASHBOARD_SESSION_TTL_MS,
  clearDashboardSessions,
  clearedDashboardSessionCookie,
  closeDashboardSession,
  controlPlaneTokenFor,
  dashboardSessionCookie,
  dashboardSessionCount,
  openDashboardSession,
} from "../lib/session";

const TOKEN = "token-control-plane-Xy9-nie-do-przegladarki";

beforeEach(() => {
  clearDashboardSessions();
});

afterEach(() => {
  clearDashboardSessions();
});

describe("the dashboard session", () => {
  it("the_handle_given_to_the_browser_is_not_the_control_plane_token", () => {
    const handle = openDashboardSession(TOKEN);

    expect(handle).not.toBe(TOKEN);
    expect(handle).not.toContain(TOKEN);
    expect(TOKEN).not.toContain(handle);
    // 32 bytes as hex. Long enough that the assertion below is about randomness, not formatting.
    expect(handle).toMatch(/^[0-9a-f]{64}$/);

    const second = openDashboardSession(TOKEN);
    expect(second).not.toBe(handle);
  });

  it("the_cookie_sent_to_the_browser_carries_the_handle_and_not_the_token", () => {
    const handle = openDashboardSession(TOKEN);
    const cookie = dashboardSessionCookie(handle);

    expect(cookie).toContain(`${DASHBOARD_COOKIE_NAME}=${handle}`);
    expect(cookie).not.toContain(TOKEN);
    // Flag by flag, because each one is a line in D-016 rather than a habit.
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=Strict");
    expect(cookie).toContain("Path=/");
    expect(cookie).toContain(`Max-Age=${DASHBOARD_SESSION_TTL_MS / 1000}`);
    // NOT `Secure`: over plain HTTP on loopback (D-014) a Secure cookie is never sent back, so the
    // flag would break the session instead of protecting it. The day this is TLS, it flips.
    expect(cookie).not.toContain("Secure");
  });

  it("the_handle_resolves_to_the_token_and_an_unknown_handle_resolves_to_nothing", () => {
    const handle = openDashboardSession(TOKEN);
    expect(controlPlaneTokenFor(handle)).toBe(TOKEN);

    expect(controlPlaneTokenFor("00".repeat(32))).toBeUndefined();
    expect(controlPlaneTokenFor(undefined)).toBeUndefined();
    expect(controlPlaneTokenFor("")).toBeUndefined();
  });

  it("an_expired_handle_is_dropped_and_reported_as_absent", () => {
    const openedAt = 1_000_000;
    const handle = openDashboardSession(TOKEN, openedAt);

    // One millisecond before the TTL it still works; at the TTL it does not, and the entry is gone
    // rather than merely refused — the same shape as `SessionStore.resolve`.
    expect(controlPlaneTokenFor(handle, openedAt + DASHBOARD_SESSION_TTL_MS - 1)).toBe(TOKEN);
    expect(controlPlaneTokenFor(handle, openedAt + DASHBOARD_SESSION_TTL_MS)).toBeUndefined();
    expect(dashboardSessionCount()).toBe(0);
  });

  it("closing_a_session_returns_the_token_so_the_upstream_one_can_be_ended_too", () => {
    // A logout that only drops the local handle leaves the control plane session valid for anybody
    // holding a copy of the token — and the copy is the reason to log out.
    const handle = openDashboardSession(TOKEN);
    expect(closeDashboardSession(handle)).toBe(TOKEN);
    expect(controlPlaneTokenFor(handle)).toBeUndefined();
    expect(dashboardSessionCount()).toBe(0);

    // Closing twice is not an error and does not resurrect anything.
    expect(closeDashboardSession(handle)).toBeUndefined();
    expect(closeDashboardSession(undefined)).toBeUndefined();
  });

  it("an_empty_token_is_refused_rather_than_stored", () => {
    // A handle holding an empty token looks exactly like being logged in and can read nothing.
    expect(() => openDashboardSession("")).toThrow(/pusty token/);
    expect(dashboardSessionCount()).toBe(0);
  });

  it("the_cleared_cookie_expires_the_handle_and_names_no_token", () => {
    const cleared = clearedDashboardSessionCookie();
    expect(cleared).toContain(`${DASHBOARD_COOKIE_NAME}=`);
    expect(cleared).toContain("Max-Age=0");
    expect(cleared).toContain("HttpOnly");
    expect(cleared).not.toContain(TOKEN);
  });

  it("the_cookie_name_is_not_the_control_plane_cookie_name", () => {
    // Two different secrets under one name is how they get confused, and the entire point is that
    // they are different.
    expect(DASHBOARD_COOKIE_NAME).not.toBe("wenext_operator_session");
  });
});
