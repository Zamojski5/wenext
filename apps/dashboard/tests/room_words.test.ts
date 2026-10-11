/**
 * The sentences of the Room, one instant at a time.
 *
 * Every function here takes `now`, so the cases below fix the clock and the answer cannot depend on
 * when the suite runs. The ones that carry weight are the places the screen could claim more than
 * it knows: "właśnie" on a day-old thread, "0 osób", a zero where there was no measurement, and a
 * draft that looks sendable after its window closed.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { parseRoom, type Room, type RoomDraft } from "../lib/room-schema";
import {
  ageSentence,
  authorLabel,
  communityLabel,
  draftHandle,
  draftStateOf,
  expirySentence,
  figure,
  headlineOf,
  initialsOf,
  openOpportunities,
  summaryOf,
  warsawClock,
} from "../lib/room-words";

const NOW = new Date("2026-10-10T13:32:00+00:00");

function iso(offsetMinutes: number): string {
  return new Date(NOW.getTime() + offsetMinutes * 60_000).toISOString();
}

function rich(): Room {
  const body = JSON.parse(
    fs.readFileSync(path.resolve(import.meta.dirname, "fixtures", "room-rich.json"), "utf8"),
  ) as unknown;
  const result = parseRoom(body);
  if (!result.ok) throw new Error(result.reason);
  return result.value;
}

/** A room with `count` open opportunities created `ageMinutes` ago. */
function crowd(count: number, ageMinutes: number): Room {
  const base = rich();
  const template = base.opportunities[1];
  if (template === undefined) throw new Error("fixture");
  return {
    ...base,
    drafts: [],
    opportunities: Array.from({ length: count }, (_, index) => ({
      ...template,
      id: `o${index}`,
      state: "NOWA" as const,
      created_at: iso(-ageMinutes),
    })),
  };
}

describe("how long ago", () => {
  it("says_it_the_way_a_person_does", () => {
    expect(ageSentence(null, NOW)).toBeNull();
    expect(ageSentence(iso(-1), NOW)).toBe("przed chwilą");
    expect(ageSentence(iso(-12), NOW)).toBe("12 min temu");
    expect(ageSentence(iso(-59), NOW)).toBe("59 min temu");
    expect(ageSentence(iso(-120), NOW)).toBe("2 h temu");
    expect(ageSentence(iso(-23 * 60), NOW)).toBe("23 h temu");
    expect(ageSentence(iso(-26 * 60), NOW)).toBe("wczoraj");
    expect(ageSentence(iso(-3 * 24 * 60), NOW)).toBe("3 dni temu");
  });

  it("a_little_in_the_future_is_clock_skew_and_a_lot_is_printed_as_it_is", () => {
    expect(ageSentence(iso(2), NOW)).toBe("przed chwilą");
    // Hours ahead are a clock somebody has to fix: shown as the instant, not smoothed over.
    expect(ageSentence(iso(300), NOW)).toMatch(/października/);
  });

  it("an_unreadable_date_says_so", () => {
    expect(ageSentence("jutro", NOW)).toBe("data nieczytelna");
  });
});

describe("the approval window", () => {
  it("counts_down_near_the_end_and_names_the_day_far_from_it", () => {
    expect(expirySentence(null, NOW)).toBeNull();
    expect(expirySentence(iso(25), NOW)).toEqual({ text: "Wygasa za 25 min", expired: false });
    expect(expirySentence(iso(5 * 60 + 10), NOW)).toEqual({ text: "Wygasa za 5 h", expired: false });
    const far = expirySentence(iso(3 * 24 * 60), NOW);
    expect(far?.expired).toBe(false);
    // The wall clock of Warsaw, not UTC: a deadline is read in order to act on it.
    expect(far?.text).toBe("Wygasa 13 października, 15:32");
  });

  it("a_window_that_passed_says_so_and_is_marked_expired", () => {
    expect(expirySentence(iso(-1), NOW)).toEqual({ text: "Okno zgody minęło", expired: true });
    expect(expirySentence(iso(0), NOW)?.expired).toBe(true);
  });

  it("the_clock_is_warsaw_time", () => {
    expect(warsawClock(NOW)).toBe("15:32");
  });
});

describe("numbers", () => {
  it("null_is_brak_pomiaru_and_zero_is_zero", () => {
    expect(figure(null)).toEqual({ text: "brak pomiaru", measured: false });
    expect(figure(0)).toEqual({ text: "0", measured: true });
    expect(figure(87)).toEqual({ text: "87", measured: true });
    expect(figure(1234).text.replace(/\s/g, "")).toBe("1234");
  });
});

describe("the promise", () => {
  it("counts_only_people_who_can_still_be_answered", () => {
    const room = rich();
    // 10 opportunities: 7 NOWA or SZKIC, one answered, one skipped, and one more NOWA.
    expect(room.opportunities.length).toBe(10);
    expect(openOpportunities(room).length).toBe(8);
    expect(headlineOf(room, NOW)?.open).toBe(8);
    expect(headlineOf(room, NOW)?.count).toBe("8 osób");
  });

  it("the_verb_agrees_with_the_number_the_way_polish_needs", () => {
    expect(headlineOf(crowd(1, 60), NOW)).toMatchObject({
      count: "1 osoba",
      rest: "właśnie pisze o problemie, który rozwiązuje Twój kod.",
    });
    expect(headlineOf(crowd(2, 60), NOW)).toMatchObject({ count: "2 osoby" });
    expect(headlineOf(crowd(2, 60), NOW)?.rest).toContain("piszą");
    expect(headlineOf(crowd(5, 60), NOW)?.count).toBe("5 osób");
    expect(headlineOf(crowd(5, 60), NOW)?.rest).toContain(" pisze ");
    expect(headlineOf(crowd(12, 60), NOW)?.rest).toContain(" pisze ");
    expect(headlineOf(crowd(22, 60), NOW)?.rest).toContain("piszą");
  });

  it("right_now_is_used_only_while_it_is_true", () => {
    expect(headlineOf(crowd(3, 60), NOW)?.rest.startsWith("właśnie")).toBe(true);
    expect(headlineOf(crowd(3, 47 * 60), NOW)?.rest.startsWith("właśnie")).toBe(true);
    expect(headlineOf(crowd(3, 49 * 60), NOW)?.rest.startsWith("ostatnio")).toBe(true);
  });

  it("nobody_open_gives_no_headline_never_zero_people", () => {
    expect(headlineOf(crowd(0, 60), NOW)).toBeNull();
    const answeredOnly: Room = {
      ...crowd(2, 60),
      opportunities: crowd(2, 60).opportunities.map((one) => ({
        ...one,
        state: "ODPOWIEDZIANO" as const,
      })),
    };
    expect(headlineOf(answeredOnly, NOW)).toBeNull();
  });

  it("the_line_under_it_reports_waiting_drafts_and_the_freshest_person", () => {
    expect(summaryOf(rich(), NOW)).toEqual([
      "4 odpowiedzi czekają na Twoją zgodę",
      "najnowsza 1 h temu",
    ]);
    expect(summaryOf(crowd(1, 5), NOW)).toEqual(["najnowsza 5 min temu"]);
  });
});

describe("names", () => {
  it("reddit_gets_its_prefixes_and_x_gets_an_at_sign", () => {
    expect(communityLabel("REDDIT", "devops")).toBe("r/devops");
    expect(communityLabel("REDDIT", "r/devops")).toBe("r/devops");
    expect(communityLabel("X", null)).toBeNull();
    expect(communityLabel("REDDIT", "  ")).toBeNull();
    expect(authorLabel("REDDIT", "ml_tinkerer")).toBe("u/ml_tinkerer");
    expect(authorLabel("REDDIT", "u/ml_tinkerer")).toBe("u/ml_tinkerer");
    expect(authorLabel("X", "sarah_builds")).toBe("@sarah_builds");
    expect(authorLabel("X", "@sarah_builds")).toBe("@sarah_builds");
    expect(authorLabel("X", null)).toBeNull();
  });

  it("initials_are_two_letters_and_a_placeholder_is_Ty", () => {
    expect(initialsOf("u/agentwatch_dev")).toBe("AG");
    expect(initialsOf("@sarah_builds")).toBe("SA");
    expect(initialsOf(null)).toBe("Ty");
    expect(initialsOf("u/__")).toBe("Ty");
  });

  it("a_draft_is_signed_with_the_assigned_account_or_a_placeholder_that_says_it_is_one", () => {
    const assigned = [
      { platform: "REDDIT", external_account_id: "agentwatch_dev" },
      { platform: "X", external_account_id: "agentwatch_x" },
    ];
    expect(draftHandle("REDDIT", assigned)).toEqual({ text: "u/agentwatch_dev", real: true });
    expect(draftHandle("X", assigned)).toEqual({ text: "@agentwatch_x", real: true });
    expect(draftHandle("X", [])).toEqual({ text: "@twoje_konto", real: false });
    expect(draftHandle("REDDIT", [{ platform: "X", external_account_id: "a" }])).toEqual({
      text: "u/twoje_konto",
      real: false,
    });
  });
});

describe("what a draft's state means for the buttons", () => {
  function withState(state: string): RoomDraft {
    const draft = rich().drafts[0];
    if (draft === undefined) throw new Error("fixture");
    return { ...draft, state };
  }

  it("a_draft_waiting_for_consent_has_the_buttons", () => {
    expect(draftStateOf(withState("READY_FOR_APPROVAL"))).toEqual({
      label: "Czeka na Twoją zgodę",
      actionable: true,
    });
    // The room endpoint's own word for the same state.
    expect(draftStateOf(withState("czeka_na_zgode"))).toEqual({
      label: "Czeka na Twoją zgodę",
      actionable: true,
    });
  });

  it("states_past_consent_say_so_in_words_and_take_the_buttons_away", () => {
    for (const state of ["APPROVED", "PUBLISHING", "ISSUED", "published", "EXPIRED", "REJECTED", "SUPERSEDED"]) {
      const result = draftStateOf(withState(state));
      expect(result.actionable, state).toBe(false);
      expect(result.label, state).not.toBeNull();
    }
  });

  it("an_unknown_state_keeps_the_buttons_because_the_server_is_the_judge", () => {
    expect(draftStateOf(withState("SOMETHING_NEW"))).toEqual({ label: null, actionable: true });
  });
});
