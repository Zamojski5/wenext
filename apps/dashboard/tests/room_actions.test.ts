/**
 * The three buttons of the Room, and what each answer of the control plane means for them.
 *
 * The cases that matter: a 409 for a project with no signed scope gets the sentence that sends the
 * founder to "Obecność", a 409 for something else does NOT get that sentence (a wrong remedy is
 * worse than a generic refusal), and the Room's words are used only for a person who came from the
 * Room.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { allowedCardRoute } from "../lib/card-routes";
import {
  isRoomReturn,
  roomActionOf,
  roomRefusalKey,
  roomSuccessKey,
} from "../lib/room-actions";
import { STUDIO_INFO } from "../lib/studio-info";

const WS = "aaaaaaaa-0000-4000-8000-000000000001";
const PR = "bbbbbbbb-0000-4000-8000-000000000001";
const API = `/api/workspaces/${WS}/projects/${PR}`;

// The sentences `project_approvals.py` really sends, copied from the route, not paraphrased.
const NO_SCOPE =
  "projekt nie ma żywego powierzenia, więc nie ma zakresu, w którym ta publikacja miałaby się " +
  "odbyć. Podpisz powierzenie i zatwierdź ponownie";
const BUDGET = "budżet wyczerpany, limit publikacji na dobę";
const WINDOW = "okno zgody tej propozycji minęło; poproś agenta o nową wersję";
const WRONG_STATE =
  "propozycja jest w stanie APPROVED, a do zatwierdzenia nadaje się tylko ta, która czeka na zgodę";
const STALE =
  "ta wersja szkicu nie czeka już na zgodę: ktoś ją w międzyczasie zatwierdził albo poprawił. " +
  "Odśwież rozmowę i zdecyduj o najnowszej.";

describe("which button a route is", () => {
  it("recognises_the_three_routes_and_nothing_else", () => {
    expect(roomActionOf(`${API}/approvals/rev7/approve`)).toBe("approve");
    expect(roomActionOf(`${API}/approvals/rev7/revise`)).toBe("revise");
    expect(roomActionOf(`${API}/opportunities/opp1/dismiss`)).toBe("dismiss");
    expect(roomActionOf(`${API}/delegation`)).toBeNull();
    expect(roomActionOf("/api/capsule/session")).toBeNull();
    expect(roomActionOf(`${API}/approvals/rev7/approve/extra`)).toBeNull();
  });

  it("all_three_pass_the_existing_card_route_check_with_no_new_entry", () => {
    // The Room adds no route and no entry to `CARD_ROUTES`: it uses the ones cards already use.
    for (const route of [
      `${API}/approvals/rev7/approve`,
      `${API}/approvals/rev7/revise`,
      `${API}/opportunities/opp1/dismiss`,
    ]) {
      expect(allowedCardRoute(route, WS), route).toBe(true);
      // And a hidden field edited to another workspace is still refused.
      expect(allowedCardRoute(route, "bbbb"), route).toBe(false);
    }
  });

  it("the_return_path_decides_whether_these_are_the_Rooms_words", () => {
    expect(isRoomReturn(`/w/${WS}/p/${PR}/room`)).toBe(true);
    expect(isRoomReturn(`/w/${WS}/p/${PR}/room?info=room_published`)).toBe(true);
    expect(isRoomReturn(`/w/${WS}/p/${PR}/c/11111111-0000-4000-8000-000000000001`)).toBe(false);
    expect(isRoomReturn(`/w/${WS}/p/${PR}/publications`)).toBe(false);
    expect(isRoomReturn("/")).toBe(false);
  });
});

describe("a refused approval", () => {
  it("no_signed_scope_sends_the_founder_to_sign_it", () => {
    expect(roomRefusalKey("approve", 409, NO_SCOPE)).toBe("room_scope_unsigned");
    const sentence = STUDIO_INFO["room_scope_unsigned"]?.text ?? "";
    expect(sentence).toContain("Obecność");
    expect(sentence).toContain("Z przeglądem");
    expect(sentence).toContain("podpisanego zakresu");
    // Nothing went out, and the sentence says so instead of hoping it is understood.
    expect(sentence).toContain("Nic nie opublikowano");
  });

  it("the_other_409s_do_not_get_the_scope_remedy", () => {
    expect(roomRefusalKey("approve", 409, BUDGET)).toBe("room_budget_spent");
    expect(roomRefusalKey("approve", 409, WINDOW)).toBe("room_window_passed");
    expect(roomRefusalKey("approve", 409, WRONG_STATE)).toBe("room_draft_stale");
    expect(roomRefusalKey("approve", 409, STALE)).toBe("room_draft_stale");
    for (const detail of [BUDGET, WINDOW, WRONG_STATE, STALE]) {
      expect(roomRefusalKey("approve", 409, detail)).not.toBe("room_scope_unsigned");
    }
  });

  it("anything_unrecognised_is_the_generic_refusal_never_a_guess", () => {
    expect(roomRefusalKey("approve", 409, "coś zupełnie nowego")).toBe("room_refused");
    expect(roomRefusalKey("approve", 500, NO_SCOPE)).toBe("room_refused");
    expect(roomRefusalKey("approve", 404, "")).toBe("room_refused");
    expect(roomRefusalKey("dismiss", 409, NO_SCOPE)).toBe("room_refused");
    // An unchanged correction is its own case, not "refresh the room": see the next test.
    expect(roomRefusalKey("revise", 409, "poprawka nie zmienia treści")).toBe("room_revise_unchanged");
    expect(roomRefusalKey("approve", 409, "poprawka nie zmienia treści")).toBe("room_refused");
    expect(roomRefusalKey("revise", 409, STALE)).toBe("room_draft_stale");
  });

  it("saving_a_correction_that_changed_nothing_says_so_instead_of_telling_to_refresh", () => {
    const sentence = STUDIO_INFO["room_revise_unchanged"]?.text ?? "";
    expect(sentence).toContain("nie zmienia treści");
    expect(sentence).toContain("nic nie zapisano");
    expect(sentence).not.toContain("Odśwież");
  });

  it("an_accepted_action_never_says_published", () => {
    expect(roomSuccessKey("approve")).toBe("room_published");
    expect(roomSuccessKey("revise")).toBe("room_revised");
    expect(roomSuccessKey("dismiss")).toBe("room_dismissed");
    // "Poszło do publikatora" is true; "Opublikowano" would be a claim about a platform nobody read.
    const sentence = STUDIO_INFO["room_published"]?.text ?? "";
    expect(sentence).toContain("Poszło do publikatora");
    expect(sentence).toContain("dopiero wtedy");
  });
});

describe("the messages", () => {
  const KEYS = [
    "room_published",
    "room_revised",
    "room_dismissed",
    "room_scope_unsigned",
    "room_budget_spent",
    "room_window_passed",
    "room_draft_stale",
    "room_revise_unchanged",
    "room_refused",
  ];

  it("every_key_the_room_can_produce_has_a_sentence_without_a_long_dash", () => {
    expect(KEYS.length).toBe(9);
    const dashes = new RegExp(`[${String.fromCodePoint(0x2014)}${String.fromCodePoint(0x2013)}]`);
    for (const key of KEYS) {
      const message = STUDIO_INFO[key];
      expect(message, key).toBeDefined();
      expect(message?.text.length ?? 0, key).toBeGreaterThan(20);
      expect(dashes.test(message?.text ?? ""), key).toBe(false);
    }
  });

  it("a_refusal_is_never_painted_as_plain_news", () => {
    for (const key of KEYS.filter((one) => !["room_published", "room_revised", "room_dismissed"].includes(one))) {
      expect(STUDIO_INFO[key]?.tone, key).toBe("absent");
    }
  });
});

describe("the handler", () => {
  const source = fs.readFileSync(
    path.resolve(import.meta.dirname, "..", "app", "studio", "card", "route.ts"),
    "utf8",
  );

  it("decides_the_rooms_words_before_the_generic_refusal", () => {
    // The generic `card_refused` points at "the card in the conversation"; reaching it first would
    // answer a Room click with a sentence about a place the person is not in.
    const room = source.indexOf("roomRefusalKey(");
    const generic = source.indexOf('backTo(back, "card_refused")');
    expect(room).toBeGreaterThan(-1);
    expect(generic).toBeGreaterThan(-1);
    expect(room).toBeLessThan(generic);
    expect(source).toContain("isRoomReturn(back)");
  });
});
