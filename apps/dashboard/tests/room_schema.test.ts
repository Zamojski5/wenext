/**
 * The contract of `GET .../projects/{project}/room`, parsed from the SAME two files the screenshots
 * use (`tests/fixtures/room-rich.json`, `room-empty.json`), so the screen that was looked at and the
 * parser that was tested read one document, not two that drifted.
 *
 * WHAT IS PINNED, because each is a way the Room could say something nobody measured:
 *
 *   * a `null` number stays `null` (it is "brak pomiaru", and `?? 0` anywhere would turn it into a
 *     measurement);
 *   * a missing key is a broken read that NAMES the key, not a default;
 *   * a closed list is closed (`stan`, `rodzaj`);
 *   * an address that is not http(s) never reaches an `href`.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  DRAFT_KINDS,
  OPPORTUNITY_STATES,
  isWebAddress,
  parseRoom,
  visibilityOf,
  type Room,
} from "../lib/room-schema";

const FIXTURES = path.resolve(import.meta.dirname, "fixtures");

function load(name: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES, name), "utf8")) as Record<string, unknown>;
}

function parsed(name: string): Room {
  const result = parseRoom(load(name));
  if (!result.ok) throw new Error(result.reason);
  return result.value;
}

/** A deep copy of the rich fixture that a case may break in exactly one place. */
function broken(edit: (body: Record<string, any>) => void): ReturnType<typeof parseRoom> {
  const body = load("room-rich.json") as Record<string, any>;
  edit(body);
  return parseRoom(body);
}

function reason(result: ReturnType<typeof parseRoom>): string {
  if (result.ok) throw new Error("oczekiwano odczytu zepsutego");
  return result.reason;
}

describe("the exact contract", () => {
  it("parses_the_rich_fixture_row_for_row", () => {
    const room = parsed("room-rich.json");
    // Counts before contents: a parser that returned fewer rows would pass every check below it.
    expect(room.opportunities.length).toBe(10);
    expect(room.drafts.length).toBe(4);
    expect(room.results.items.length).toBe(4);
    expect(room.room_path).toMatch(/^\/w\/.+\/p\/.+\/room$/);

    const first = room.opportunities[0];
    expect(first?.platform).toBe("REDDIT");
    expect(first?.community).toBe("r/LocalLLaMA");
    expect(first?.score).toBe(214);
    expect(first?.comments).toBe(87);
    expect(first?.state).toBe("SZKIC");
    expect(first?.draft_revision).toBe(room.drafts[0]?.revision_id);
    expect(first?.why).toContain("Opisuje");
    expect(first?.angle).toContain("regułą");

    const reply = room.drafts[0];
    expect(reply?.kind).toBe("REPLY");
    expect(reply?.in_reply_to?.author).toBe("ml_tinkerer");
    expect(reply?.opportunity_id).toBe(first?.id);
    expect(reply?.state).toBe("READY_FOR_APPROVAL");
  });

  it("every_state_and_kind_of_the_contract_appears_in_the_fixture", () => {
    // The fixture is only evidence for a list it exercises.
    const room = parsed("room-rich.json");
    expect(OPPORTUNITY_STATES.length).toBe(4);
    for (const state of OPPORTUNITY_STATES) {
      expect(
        room.opportunities.some((one) => one.state === state),
        state,
      ).toBe(true);
    }
    expect(DRAFT_KINDS.length).toBe(2);
    for (const kind of DRAFT_KINDS) {
      expect(room.drafts.some((one) => one.kind === kind), kind).toBe(true);
    }
    expect(new Set(room.opportunities.map((one) => one.platform))).toEqual(
      new Set(["REDDIT", "X"]),
    );
  });

  it("an_own_post_has_no_thread_it_answers", () => {
    const own = parsed("room-rich.json").drafts.find((one) => one.kind === "OWN_POST");
    expect(own?.in_reply_to).toBeNull();
    expect(own?.opportunity_id).toBeNull();
    expect(own?.title).toContain("checklist");
  });
});

describe("null is a value", () => {
  it("an_unmeasured_score_and_comment_count_stay_null_and_are_never_zero", () => {
    const room = parsed("room-rich.json");
    const inbox = room.opportunities.find((one) => one.author === "inbox_zero_dad");
    expect(inbox?.score).toBeNull();
    expect(inbox?.comments).toBe(12);
    const testing = room.opportunities.find((one) => one.author === "ship_it_sunday");
    expect(testing?.score).toBe(77);
    expect(testing?.comments).toBeNull();
    const bare = room.opportunities.find((one) => one.author === "devon_ships");
    expect(bare?.score).toBeNull();
    expect(bare?.comments).toBeNull();
    // `toBeNull`, not "falsy": 0 is falsy too and is exactly the substitution this forbids.
    expect(bare?.score).not.toBe(0);
  });

  it("every_headline_number_of_the_empty_window_is_null_not_zero", () => {
    const results = parsed("room-empty.json").results;
    expect(results.window_days).toBe(7);
    expect(results.published).toBeNull();
    expect(results.replies).toBeNull();
    expect(results.votes).toBeNull();
    expect(results.removed).toBeNull();
    expect(results.items).toEqual([]);
  });

  it("an_empty_room_is_a_valid_room_with_empty_lists", () => {
    const room = parsed("room-empty.json");
    expect(room.opportunities).toEqual([]);
    expect(room.drafts).toEqual([]);
  });

  it("a_measured_zero_stays_zero", () => {
    // The other direction of the same rule: `0` measured is not "brak pomiaru".
    const result = broken((body) => {
      body["wyniki"]["usuniete"] = 0;
      body["okazje"][0]["wynik"] = 0;
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.results.removed).toBe(0);
      expect(result.value.opportunities[0]?.score).toBe(0);
    }
  });

  it("an_unmeasured_metric_of_a_published_item_stays_null", () => {
    const item = parsed("room-rich.json").results.items[2];
    expect(item?.votes).toBeNull();
    expect(item?.replies).toBeNull();
    expect(item?.visibility).toEqual({ kind: "unconfirmed", raw: null });
  });
});

describe("a broken read says what is broken", () => {
  it("a_missing_key_fails_the_whole_read_and_names_it", () => {
    expect(reason(broken((body) => delete body["okazje"][0]["kat"]))).toContain("okazje[0].kat");
    expect(reason(broken((body) => delete body["okazje"][2]["wynik"]))).toContain("okazje[2].wynik");
    expect(reason(broken((body) => delete body["szkice"][1]["wygasa"]))).toContain("szkice[1].wygasa");
    expect(reason(broken((body) => delete body["szkice"][0]["w_odpowiedzi_na"]))).toContain(
      "szkice[0].w_odpowiedzi_na",
    );
    expect(reason(broken((body) => delete body["wyniki"]["usuniete"]))).toContain("wyniki.usuniete");
    expect(reason(broken((body) => delete body["wyniki"]["pozycje"][1]["glosy"]))).toContain(
      "wyniki.pozycje[1].glosy",
    );
  });

  it("a_list_that_is_not_a_list_is_a_broken_read_not_an_empty_room", () => {
    expect(reason(broken((body) => (body["okazje"] = "x")))).toContain("okazje");
    expect(reason(broken((body) => (body["szkice"] = null)))).toContain("szkice");
    expect(reason(broken((body) => delete body["wyniki"]["pozycje"]))).toContain("pozycje");
    expect(reason(parseRoom(null))).toContain("nie jest obiektem");
    expect(reason(parseRoom({ okazje: [], szkice: [] }))).toContain("wyniki");
  });

  it("a_value_outside_a_closed_list_is_refused", () => {
    expect(reason(broken((body) => (body["okazje"][0]["stan"] = "ARCHIWUM")))).toContain("ARCHIWUM");
    expect(reason(broken((body) => (body["szkice"][0]["rodzaj"] = "QUOTE")))).toContain("QUOTE");
  });

  it("a_number_that_is_not_a_number_is_refused_instead_of_coerced", () => {
    expect(reason(broken((body) => (body["okazje"][0]["wynik"] = "214")))).toContain("okazje[0].wynik");
    expect(reason(broken((body) => (body["wyniki"]["glosy"] = "87")))).toContain("wyniki.glosy");
    expect(reason(broken((body) => (body["wyniki"]["okno_dni"] = 0)))).toContain("okno_dni");
  });

  it("an_address_that_is_not_http_never_reaches_an_href", () => {
    expect(reason(broken((body) => (body["okazje"][0]["url"] = "javascript:alert(1)")))).toContain(
      "okazje[0].url",
    );
    expect(
      reason(broken((body) => (body["szkice"][0]["w_odpowiedzi_na"]["url"] = "data:text/html,x"))),
    ).toContain("w_odpowiedzi_na.url");
    expect(isWebAddress("https://www.reddit.com/r/x/")).toBe(true);
    expect(isWebAddress("http://example.org")).toBe(true);
    expect(isWebAddress("javascript:alert(1)")).toBe(false);
    expect(isWebAddress("/relative")).toBe(false);
    expect(isWebAddress("")).toBe(false);
  });

  it("an_empty_text_is_not_a_draft", () => {
    expect(reason(broken((body) => (body["szkice"][0]["tekst"] = "")))).toContain("szkice[0].tekst");
  });
});

describe("visibility keeps three values apart", () => {
  it("maps_the_encodings_this_repository_already_uses_and_defaults_to_unconfirmed", () => {
    expect(visibilityOf(true)).toEqual({ kind: "visible" });
    expect(visibilityOf(false)).toEqual({ kind: "removed" });
    expect(visibilityOf("WIDOCZNA")).toEqual({ kind: "visible" });
    expect(visibilityOf("visible")).toEqual({ kind: "visible" });
    expect(visibilityOf("USUNIETA")).toEqual({ kind: "removed" });
    expect(visibilityOf("REMOVED")).toEqual({ kind: "removed" });
    // The control plane sends the raw verdict: CONFIRMED | REMOVED | UNVERIFIED.
    expect(visibilityOf("CONFIRMED")).toEqual({ kind: "visible" });
    expect(visibilityOf("UNVERIFIED")).toEqual({ kind: "unconfirmed", raw: "UNVERIFIED" });
    expect(visibilityOf(null)).toEqual({ kind: "unconfirmed", raw: null });
    // A word nobody taught this parser is unconfirmed AND is kept, never a quiet "visible".
    expect(visibilityOf("ZAWIESZONA")).toEqual({ kind: "unconfirmed", raw: "ZAWIESZONA" });
    expect(visibilityOf(7)).toEqual({ kind: "unconfirmed", raw: null });
  });

  it("the_rich_fixture_has_all_three", () => {
    const kinds = parsed("room-rich.json").results.items.map((one) => one.visibility.kind);
    expect(new Set(kinds)).toEqual(new Set(["visible", "removed", "unconfirmed"]));
  });
});

describe("the fixtures themselves", () => {
  it("contain_no_long_dash", () => {
    // U+2014 and U+2013 are never written in this product, and a fixture is where a stray one
    // would be copied from. Built from code points so this file does not carry them either.
    const dashes = new RegExp(`[${String.fromCodePoint(0x2014)}${String.fromCodePoint(0x2013)}]`);
    for (const name of ["room-rich.json", "room-empty.json"]) {
      expect(dashes.test(fs.readFileSync(path.join(FIXTURES, name), "utf8")), name).toBe(false);
    }
  });
});
