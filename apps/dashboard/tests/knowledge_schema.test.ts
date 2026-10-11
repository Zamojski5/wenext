/**
 * `lib/knowledge-schema.ts`: the parser that must never turn a missing field into a reassuring one.
 *
 * The three fields under test are `status`, `fetched_at` and `content_hash`. Together they are
 * the whole of provenance, and the failure mode being ruled out is a parser that defaults any of
 * them: a material rendered as read when nobody read it, or a finding rendered as standing on a
 * material that is not there.
 */
import { describe, expect, it } from "vitest";

import {
  ageNote,
  experienceOf,
  itemKindLabel,
  outcomeLabel,
  parseConflict,
  parseItem,
  parseSource,
  sourceKindLabel,
  statedByLabel,
  statusNote,
  verdictOf,
} from "../lib/knowledge-schema";

function source(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    source_id: "11111111-0000-4000-8000-000000000001",
    project_id: null,
    kind: "WEB_PAGE",
    locator: "https://example.test/artykul",
    title: "Kolejki bez ack",
    fetched_at: "2026-09-19T08:00:00+00:00",
    content_hash: "sha256:" + "a".repeat(64),
    status: "FETCHED",
    unavailable_reason: null,
    has_raw: true,
    created_at: "2026-09-19T08:00:00+00:00",
    ...overrides,
  };
}

function item(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    item_id: "22222222-0000-4000-8000-000000000001",
    project_id: null,
    person_profile_id: null,
    kind: "FACT",
    statement: "Kolejka bez ack gubi wiadomości",
    stated_by: "SOURCE",
    source_span: null,
    about: null,
    payload: {},
    valid_from: "2026-09-19T08:00:00+00:00",
    superseded_by: null,
    retracted_at: null,
    retracted_reason: null,
    in_force: true,
    stale: false,
    stale_reason: null,
    source_age_days: 2,
    source: source(),
    ...overrides,
  };
}

function conflict(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    about: "pora publikacji",
    kind: "INTERPRETATION",
    certain: true,
    advice: "Nie wybieraj jednego po cichu.",
    items: [
      {
        item_id: "33333333-0000-4000-8000-000000000001",
        statement: "publikujemy rano",
        stated_by: "AGENT",
        created_at: "2026-09-18T08:00:00+00:00",
      },
      {
        item_id: "33333333-0000-4000-8000-000000000002",
        statement: "publikujemy wieczorem",
        stated_by: "USER",
        created_at: "2026-09-19T08:00:00+00:00",
      },
    ],
    ...overrides,
  };
}

describe("parseSource", () => {
  it("a_material_read_carries_its_date_and_its_hash", () => {
    const parsed = parseSource(source());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.fetched_at).toBe("2026-09-19T08:00:00+00:00");
    expect(parsed.value.content_hash?.startsWith("sha256:")).toBe(true);
  });

  it("an_unreadable_address_keeps_its_reason_and_gets_no_invented_hash", () => {
    const parsed = parseSource(
      source({
        status: "UNAVAILABLE",
        content_hash: null,
        has_raw: false,
        unavailable_reason: "serwer odpowiedział 404",
      }),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.content_hash).toBeNull();
    expect(statusNote(parsed.value)).toBe("serwer odpowiedział 404");
  });

  it.each(["status", "fetched_at", "content_hash", "has_raw", "locator"])(
    "a_missing_%s_is_a_broken_read_and_not_a_default",
    (field) => {
      const row = source();
      delete row[field];
      const parsed = parseSource(row);
      expect(parsed.ok).toBe(false);
      if (parsed.ok) return;
      expect(parsed.reason).toContain(field);
      expect(parsed.reason).toContain("zepsuty odczyt");
    },
  );

  it("a_material_nobody_has_read_yet_is_null_and_not_a_date", () => {
    const parsed = parseSource(source({ fetched_at: null, content_hash: null, status: "STALE" }));
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.fetched_at).toBeNull();
    expect(statusNote(parsed.value)).toContain("odświeżyć");
  });
});

describe("parseItem", () => {
  it("a_finding_arrives_with_the_material_it_stands_on", () => {
    const parsed = parseItem(item());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.source?.locator).toBe("https://example.test/artykul");
  });

  it("a_finding_from_the_conversation_has_a_null_material_which_is_not_the_same_as_absent", () => {
    const withNull = parseItem(item({ source: null, kind: "INTERPRETATION" }));
    expect(withNull.ok).toBe(true);
    if (withNull.ok) expect(withNull.value.source).toBeNull();

    const row = item();
    delete row["source"];
    const absent = parseItem(row);
    expect(absent.ok).toBe(false);
    if (!absent.ok) expect(absent.reason).toContain("source");
  });

  it("a_broken_material_inside_a_finding_breaks_the_finding", () => {
    const broken = source();
    delete broken["content_hash"];
    const parsed = parseItem(item({ source: broken }));
    expect(parsed.ok).toBe(false);
  });

  it("a_withdrawn_finding_keeps_its_reason_and_says_it_is_not_in_force", () => {
    const parsed = parseItem(
      item({
        in_force: false,
        retracted_at: "2026-09-19T09:00:00+00:00",
        retracted_reason: "to był domysł",
      }),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.in_force).toBe(false);
    expect(parsed.value.retracted_reason).toBe("to był domysł");
  });
});

describe("staleness on a finding", () => {
  it("a_shaky_finding_keeps_its_flag_and_its_reason", () => {
    const parsed = parseItem(
      item({
        stale: true,
        stale_reason: "materiał czytany 95 dni temu, próg to 30",
        source_age_days: 95,
      }),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.stale).toBe(true);
    expect(parsed.value.stale_reason).toContain("95 dni");
    expect(parsed.value.source_age_days).toBe(95);
  });

  it("a_missing_stale_flag_is_a_broken_read_and_never_false", () => {
    // "Nothing looks out of date" and "nobody said" must not render the same. Defaulting this
    // to false would make every finding of a broken payload look freshly checked.
    const row = item();
    delete row["stale"];
    const parsed = parseItem(row);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.reason).toContain("stale");
  });

  it("an_age_that_is_not_a_number_is_a_broken_read", () => {
    const parsed = parseItem(item({ source_age_days: "dawno" }));
    expect(parsed.ok).toBe(false);
  });

  it("an_age_of_null_is_allowed_because_a_material_may_never_have_been_read", () => {
    const parsed = parseItem(item({ source_age_days: null }));
    expect(parsed.ok).toBe(true);
    if (parsed.ok) expect(parsed.value.source_age_days).toBeNull();
    expect(ageNote(null)).toBe("nie przeczytano");
    expect(ageNote(0.2)).toBe("dziś");
    expect(ageNote(95)).toBe("95 dni temu");
  });
});

describe("parseConflict", () => {
  it("both_sides_of_a_disagreement_come_through_with_their_dates", () => {
    const parsed = parseConflict(conflict());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.items).toHaveLength(2);
    expect(parsed.value.items[1]?.created_at).toBe("2026-09-19T08:00:00+00:00");
    expect(parsed.value.certain).toBe(true);
  });

  it("a_conflict_that_lost_a_side_is_a_broken_read_not_an_agreement", () => {
    const parsed = parseConflict(conflict({ items: [conflict().items as never[]].flat().slice(0, 1) }));
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.reason).toContain("zepsuty odczyt");
  });

  it.each(["about", "advice", "certain", "items"])(
    "a_missing_%s_is_a_broken_read",
    (field) => {
      const row = conflict();
      delete row[field];
      const parsed = parseConflict(row);
      expect(parsed.ok).toBe(false);
    },
  );
});

describe("experienceOf", () => {
  function measured(overrides: Record<string, unknown> = {}): Record<string, unknown> {
    return item({
      kind: "EXPERIENCE",
      stated_by: "AGENT",
      source: null,
      statement: "r/devops: HELP, 800 znaków. Wynik: cisza.",
      payload: {
        situation: { community: "r/devops", move: "NUMBER", decision_kind: "HELP" },
        decision: { account: "konto", length_chars: 800 },
        outcome: {
          kind: "NO_REACTION",
          sentence: "po 26 godzinach nikt nie zareagował",
          measured_at: "2026-09-19T10:00:00+00:00",
          hours_after_publication: 26.5,
        },
        request_id: "44444444-0000-4000-8000-000000000001",
      },
      ...overrides,
    });
  }

  it("the_three_parts_come_through_with_the_publication_they_are_about", () => {
    const parsed = parseItem(measured());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const experience = experienceOf(parsed.value);
    expect(experience).not.toBeNull();
    expect(experience?.community).toBe("r/devops");
    expect(experience?.account).toBe("konto");
    expect(experience?.outcome).toBe("NO_REACTION");
    expect(experience?.hoursAfter).toBe(26.5);
    expect(experience?.requestId).toBe("44444444-0000-4000-8000-000000000001");
  });

  it("a_payload_without_an_outcome_is_null_rather_than_an_experience_with_a_blank", () => {
    const parsed = parseItem(
      measured({ payload: { situation: {}, decision: {}, outcome: {} } }),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    // The whole point of the row is what came of it. A blank where the result goes would read
    // as "nothing happened", which is a different finding.
    expect(experienceOf(parsed.value)).toBeNull();
  });

  it("an_ordinary_finding_is_not_an_experience", () => {
    const parsed = parseItem(item({ kind: "INTERPRETATION" }));
    expect(parsed.ok).toBe(true);
    if (parsed.ok) expect(experienceOf(parsed.value)).toBeNull();
  });

  it("every_outcome_the_backend_can_send_has_a_polish_name", () => {
    for (const outcome of ["REMOVED", "NO_REACTION", "REPLIED", "SCORED"]) {
      expect(outcomeLabel(outcome)).not.toBe(outcome);
    }
    expect(outcomeLabel("COS_NOWEGO")).toBe("COS_NOWEGO");
  });

  it("a_correction_says_which_button_the_person_pressed", () => {
    const parsed = parseItem(
      item({
        kind: "PREFERENCE",
        stated_by: "USER",
        source: null,
        payload: { verdict: "TOO_LONG", request_id: "44444444-0000-4000-8000-000000000001" },
      }),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(verdictOf(parsed.value)).toBe("TOO_LONG");
  });

  it("a_missing_payload_is_a_broken_read", () => {
    const row = item();
    delete row["payload"];
    const parsed = parseItem(row);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.reason).toContain("payload");
  });
});

describe("labels", () => {
  it("every_kind_the_backend_can_send_has_a_polish_name", () => {
    for (const kind of ["WEB_PAGE", "WEB_SEARCH", "RSS", "NEWSLETTER", "DOCUMENT", "USER_NOTE"]) {
      expect(sourceKindLabel(kind)).not.toBe(kind);
    }
    for (const kind of ["FACT", "INTERPRETATION", "PREFERENCE", "EXPERIENCE"]) {
      expect(itemKindLabel(kind)).not.toBe(kind);
    }
    for (const who of ["USER", "AGENT", "SOURCE"]) {
      expect(statedByLabel(who)).not.toBe(who);
    }
  });

  it("an_unknown_value_is_printed_as_it_came_rather_than_hidden", () => {
    expect(sourceKindLabel("NOWY_RODZAJ")).toBe("NOWY_RODZAJ");
    expect(itemKindLabel("NOWY")).toBe("NOWY");
  });

  it("a_material_from_a_source_is_not_labelled_as_the_agents_own_claim", () => {
    // A feed entry is the feed's claim, and calling it "od agenta" would be the product
    // taking credit for somebody else's sentence.
    expect(statedByLabel("SOURCE")).toBe("z materiału");
    expect(statedByLabel("AGENT")).toBe("od agenta");
  });
});
