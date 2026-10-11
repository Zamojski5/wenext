/**
 * The capsule sign-in card, and the parsers of the contract behind it.
 *
 * THE TWO RULES THIS FILE EXISTS TO PIN DOWN, both of them about things the dashboard must not
 * claim:
 *
 *   1. **The dashboard never asks for a platform password.** The human signs in inside the
 *      capsule's own browser; this card links to it and accepts a confirmation. So: no password
 *      field, in any state, ever. That is the assertion the whole sign-in design rests on.
 *   2. **"Logged in" is the API's verdict, never derived here.** `status` comes from the control
 *      plane, which computed it from a real read of the platform. The card may use `mode` and
 *      to change the pill — "the capsule is up" is not "you are logged in" (L5241 point 5).
 *
 * The parser tests cover the shapes the backend session agreed to on 2026-09-03, including the two
 * that must fail loudly: an unknown `status` and a confirmation whose `resumed_jobs` is not a
 * number. Nothing here reaches a network — the routes are the backend's and are not on disk yet.
 */
import { describe, expect, it } from "vitest";

import { CapsuleCard } from "../components/views/capsule-card";
import {
  CAPSULE_STATUSES,
  parseCapsuleConfirmation,
  parseCapsuleStatus,
  parseCapsuleTicket,
  type CapsuleStatus,
} from "../lib/capsule-schema";
import type { Read } from "../lib/control-plane";
import { NOW, ONE_MINUTE_AGO } from "./fixtures";
import { renderToDocument } from "./render";
import { successIndicators } from "./success-indicators";

function status(overrides: Partial<CapsuleStatus> = {}): Read<CapsuleStatus> {
  return {
    ok: true,
    value: {
      status: "SIGNED_OUT",
      configured: true,
      href: null,
      account: null,
      since: ONE_MINUTE_AGO,
      mode: "HEADLESS",
      display_only: false,
      human_steer_open: false,
      session: null,
      viewer_ready: false,
      error: null,
      ...overrides,
    },
  };
}

/** The JSON `GET /api/capsule` sends, exactly as agreed. */
function rawStatus(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    status: "AWAITING_HUMAN",
    configured: true,
    href: null,
    account: null,
    since: ONE_MINUTE_AGO,
    mode: "INTERACTIVE",
    display_only: false,
    human_steer_open: true,
    session: null,
    viewer_ready: true,
    error: null,
    ...overrides,
  };
}

const textOf = (element: Element | Document | null) =>
  ((element as Element | null)?.textContent ?? "").replace(/\s+/g, " ").trim();

describe("CapsuleCard", () => {
  it("no_state_of_this_card_asks_for_a_platform_password", () => {
    // THE assertion the sign-in design rests on. Parametrised over every state the card can be in,
    // including the two that are not a `status` at all — a card with no password field in three of
    // five states would be a card somebody adds one to in the fourth.
    const cases: readonly Read<CapsuleStatus>[] = [
      status({ status: "SIGNED_OUT" }),
      status({ status: "AWAITING_HUMAN", mode: "INTERACTIVE", viewer_ready: true }),
      status({ status: "SIGNED_IN", account: "u/artodev" }),
      status({ configured: false }),
      status({ error: "kapsuła nie odpowiada na 127.0.0.1:9222" }),
    ];
    expect(cases.length).toBe(5);

    for (const capsule of cases) {
      const document_ = renderToDocument(<CapsuleCard capsule={capsule} now={NOW} />);
      const label = capsule.ok ? `${capsule.value.status}/${capsule.value.configured}` : "unread";

      // The card rendered before any negative assertion means anything.
      expect(document_.querySelectorAll("[data-capsule]").length, label).toBe(1);
      expect(document_.querySelectorAll('input[type="password"]').length, label).toBe(0);
      expect(document_.querySelectorAll("input").length, label).toBe(0);
      // The card may SAY it never asks for a password (it does, when signed out); what it may
      // never do is ASK. So the assertion is about a prompt and a field, not about the word.
      expect(textOf(document_.body).toLowerCase(), label).not.toContain("podaj hasło");
      expect(textOf(document_.body).toLowerCase(), label).not.toContain("wpisz hasło");
      expect(successIndicators(document_), label).toEqual([]);
    }
  });

  it("the_three_statuses_get_their_own_pill_and_their_own_primary_button", () => {
    const cases: readonly (readonly [string, string, string])[] = [
      // state, pill, the ONE emphasised button ("" = none, because nothing is urgent)
      ["SIGNED_OUT", "niezalogowany", "Otwórz przeglądarkę agenta"],
      ["AWAITING_HUMAN", "czeka na Ciebie", "Gotowe, jestem zalogowany"],
      ["SIGNED_IN", "zalogowany", ""],
    ];
    expect(cases.length).toBe(CAPSULE_STATUSES.length);

    for (const [state, pill, primary] of cases) {
      const document_ = renderToDocument(<CapsuleCard capsule={status({ status: state })} now={NOW} />);
      const card = document_.querySelector("[data-capsule]");

      expect(card?.getAttribute("data-capsule"), state).toBe(state);
      expect(textOf(card), state).toContain(pill);
      // Both actions are always available; which one is PRIMARY depends on the state.
      const buttons = Array.from(document_.querySelectorAll("button"));
      expect(buttons.length, state).toBe(2);
      const emphasised = buttons.filter((button) => button.className.includes("btn--primary"));
      expect(emphasised.length, state).toBe(primary === "" ? 0 : 1);
      expect((emphasised[0]?.textContent ?? "").trim(), state).toBe(primary);
    }
  });

  it("the_open_form_targets_a_new_tab_and_posts_to_a_handler_of_this_dashboard", () => {
    // `target="_blank"` is how a new tab happens without JavaScript; the handler answers 303 to
    // the ticket URL inside that tab, so this screen stays where it is.
    const document_ = renderToDocument(<CapsuleCard capsule={status()} now={NOW} />);
    const forms = Array.from(document_.querySelectorAll("form"));

    expect(forms.length).toBe(2);
    const open = forms.find((form) => form.getAttribute("target") === "_blank");
    expect(open?.getAttribute("action")).toBe("/community/capsule/open");
    expect(open?.getAttribute("method")).toBe("post");

    const confirm = forms.find((form) => form.getAttribute("target") === null);
    expect(confirm?.getAttribute("action")).toBe("/community/capsule/confirm");
    expect(confirm?.getAttribute("method")).toBe("post");

    // The ticket URL is never rendered as a link: it is minted by the POST and lives ~2 minutes,
    // so a link printed on the page would be a dead link by the time anybody read it.
    expect(document_.querySelectorAll("a").length).toBe(0);
  });

  it("the_pill_is_the_api_verdict_and_is_not_derived_from_the_mode", () => {
    // A capsule that is up and interactive is NOT a signed-in session. If this ever starts reading
    // `mode`, the two documents below stop differing — and the screen starts claiming a
    // measurement the control plane did not make.
    const interactiveButSignedOut = renderToDocument(
      <CapsuleCard capsule={status({ status: "SIGNED_OUT", mode: "INTERACTIVE" })} now={NOW} />,
    );
    const headlessButSignedIn = renderToDocument(
      <CapsuleCard capsule={status({ status: "SIGNED_IN", mode: "HEADLESS", account: "u/x" })} now={NOW} />,
    );

    expect(interactiveButSignedOut.querySelector("[data-capsule]")?.getAttribute("data-capsule")).toBe("SIGNED_OUT");
    expect(textOf(interactiveButSignedOut.body)).toContain("niezalogowany");
    expect(textOf(interactiveButSignedOut.body)).not.toContain("zalogowany.");
    expect(headlessButSignedIn.querySelector("[data-capsule]")?.getAttribute("data-capsule")).toBe("SIGNED_IN");
    expect(textOf(headlessButSignedIn.body)).toContain("u/x");
  });

  it("waiting_says_whether_the_viewer_is_up_yet_without_changing_the_pill", () => {
    // `viewer_ready` may change the instruction to the human and nothing else: "open it now" and
    // "it is still starting" are different sentences, and neither is a different status.
    const ready = renderToDocument(
      <CapsuleCard capsule={status({ status: "AWAITING_HUMAN", mode: "INTERACTIVE", viewer_ready: true })} now={NOW} />,
    );
    const starting = renderToDocument(
      <CapsuleCard capsule={status({ status: "AWAITING_HUMAN", mode: "INTERACTIVE", viewer_ready: false })} now={NOW} />,
    );

    expect(textOf(ready.body)).toContain("otwarta i czeka");
    expect(textOf(starting.body)).toContain("się przygotowuje");
    for (const document_ of [ready, starting]) {
      expect(document_.querySelector("[data-capsule]")?.getAttribute("data-capsule")).toBe("AWAITING_HUMAN");
      expect(textOf(document_.body)).toContain("czeka na Ciebie");
    }
  });

  it("not_configured_offers_nothing_and_a_stated_error_offers_everything", () => {
    // Two different facts. Nothing configured: there is no capsule, so there are no buttons — a
    // button that cannot work teaches the operator that buttons do not work. A capsule that
    // reports a problem: the problem is stated, and the actions stay, because retrying is exactly
    // what a human should be able to do.
    const missing = renderToDocument(<CapsuleCard capsule={status({ configured: false })} now={NOW} />);
    expect(textOf(missing.body)).toContain("nie skonfigurowana");
    expect(missing.querySelectorAll("form").length).toBe(0);

    const broken = renderToDocument(<CapsuleCard capsule={status({ error: "gateway :8760 nie odpowiada" })} now={NOW} />);
    expect(broken.querySelector('[data-capsule-error="tak"]')).not.toBeNull();
    expect(textOf(broken.body)).toContain("gateway :8760 nie odpowiada");
    expect(broken.querySelectorAll("form").length).toBe(2);
  });

  it("a_failed_read_is_brak_odpowiedzi_with_the_reason_and_no_buttons", () => {
    const document_ = renderToDocument(
      <CapsuleCard capsule={{ ok: false, unauthorized: false, reason: "control plane odpowiedział 503" }} now={NOW} />,
    );
    expect(document_.querySelector('[data-state="brak odpowiedzi"]')).not.toBeNull();
    expect(textOf(document_.body)).toContain("control plane odpowiedział 503");
    expect(document_.querySelectorAll("button").length).toBe(0);
    expect(document_.querySelectorAll("[data-capsule]").length).toBe(0);
  });
});

describe("the capsule contract", () => {
  it("a_complete_status_parses_and_an_unknown_one_fails_the_read", () => {
    const parsed = parseCapsuleStatus(rawStatus());
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.mode).toBe("INTERACTIVE");
      expect(parsed.value.viewer_ready).toBe(true);
    }

    for (const broken of [
      rawStatus({ status: "MAYBE" }),
      rawStatus({ mode: "SOMETHING" }),
      rawStatus({ configured: "tak" }),
      rawStatus({ viewer_ready: "tak" }),
    ]) {
      const failed = parseCapsuleStatus(broken);
      expect(failed.ok, JSON.stringify(broken.status)).toBe(false);
    }

    // A missing field is named, so the screen can say WHICH field the API stopped sending.
    const withoutError = rawStatus();
    delete withoutError["error"];
    const missing = parseCapsuleStatus(withoutError);
    expect(missing.ok).toBe(false);
    if (!missing.ok) expect(missing.reason).toContain("error");
  });

  it("the_ticket_and_the_confirmation_parse_and_refuse_broken_shapes", () => {
    const ticket = parseCapsuleTicket({
      href: "http://127.0.0.1:8760/login-capsule#ticket=abc",
      ticket_expires_at: ONE_MINUTE_AGO,
      status: "AWAITING_HUMAN",
    });
    expect(ticket.ok).toBe(true);
    if (ticket.ok) expect(ticket.value.href).toContain("login-capsule");
    expect(parseCapsuleTicket({ ticket_expires_at: ONE_MINUTE_AGO, status: "AWAITING_HUMAN" }).ok).toBe(false);

    const confirmation = parseCapsuleConfirmation({
      status: "SIGNED_IN",
      account: "u/artodev",
      with_human: { logged_in: true, username: "u/artodev", threads: 25, error: null },
      display_only: { logged_in: true, username: "u/artodev", threads: 25, error: null },
      resumed_jobs: 3,
    });
    expect(confirmation.ok).toBe(true);
    if (confirmation.ok) {
      expect(confirmation.value.resumed_jobs).toBe(3);
      expect(confirmation.value.display_only.threads).toBe(25);
    }

    // A side that did not run is reported as not run — not as "measured, not logged in".
    const partial = parseCapsuleConfirmation({
      status: "SIGNED_OUT",
      account: null,
      with_human: { logged_in: false, username: null, threads: null, error: null },
      display_only: null,
      resumed_jobs: 0,
    });
    expect(partial.ok).toBe(true);
    if (partial.ok) {
      expect(partial.value.display_only.logged_in).toBeNull();
      expect(partial.value.display_only.error).toBe("nie uruchomiono");
    }

    // And the count of resumed jobs must be a number: "some" is not a measurement.
    expect(
      parseCapsuleConfirmation({
        status: "SIGNED_IN",
        account: "u/x",
        with_human: { logged_in: true, username: "u/x", threads: 1, error: null },
        display_only: { logged_in: true, username: "u/x", threads: 1, error: null },
        resumed_jobs: "kilka",
      }).ok,
    ).toBe(false);
  });
});
