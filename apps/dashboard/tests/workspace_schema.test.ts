/**
 * The studio's parsers, and the one thing they must never do: turn a broken read into a false.
 *
 * Every field this screen shows is a claim about the user's own setup. `active` defaulting to
 * `false` would show an attached account as detached; `support.comment` defaulting to `false`
 * would tell the user the product cannot reply on Reddit; `still_present` defaulting to `false`
 * would report a live comment as removed. So a missing or wrongly typed field fails the WHOLE
 * read, and the screen says the read did not happen.
 *
 * `support: null` is the ONE exception and it is not laxness: it is the control plane saying
 * "this row names a platform the capability matrix no longer knows". The screen renders that as
 * "integracja niedostępna", which is the safe direction. Anything else in that field — a string,
 * a number, an array — is a broken read.
 */
import { describe, expect, it } from "vitest";

import {
  parseAccount,
  parseAssignment,
  parseAttachment,
  parsePersonProfile,
  parsePlatformSupport,
  parseProject,
  parseResultPublication,
  parseWorkspace,
} from "../lib/workspace-schema";

const SUPPORT = {
  platform: "REDDIT",
  label: "Reddit",
  integrated: true,
  read: true,
  comment: true,
  own_post: false,
  effect_readback: true,
  audience_metrics: false,
  note: null,
};

const WORKSPACE = {
  workspace_id: "aaaa",
  name: "Studio",
  created_at: "2026-09-19T08:00:00+00:00",
};

const PROJECT = {
  project_id: "bbbb",
  workspace_id: "aaaa",
  name: "Obecność devops",
  goal: "",
  mode: "AUTONOMOUS",
  paused: false,
  paused_at: null,
  archived: false,
  created_at: "2026-09-19T08:00:00+00:00",
};

const PROFILE = {
  person_profile_id: "cccc",
  workspace_id: "aaaa",
  name: "Anna",
  kind: "PERSON",
  created_at: "2026-09-19T08:00:00+00:00",
  archived: false,
};

const ACCOUNT = {
  social_account_id: "dddd",
  platform: "REDDIT",
  external_account_id: "Demo_Maintainer",
  state: "READY",
  created_at: "2026-09-19T08:00:00+00:00",
  person_profile_id: "cccc",
  attached_at: "2026-09-19T08:00:00+00:00",
  support: SUPPORT,
};

const ATTACHMENT = {
  attachment_id: "eeee",
  person_profile_id: "cccc",
  social_account_id: "dddd",
  platform: "REDDIT",
  external_account_id: "Demo_Maintainer",
  attached_at: "2026-09-19T08:00:00+00:00",
  detached_at: null,
  active: true,
};

const ASSIGNMENT = {
  support: SUPPORT,
  assignment_id: "ffff",
  project_id: "bbbb",
  social_account_id: "dddd",
  platform: "REDDIT",
  external_account_id: "Demo_Maintainer",
  assigned_at: "2026-09-19T08:00:00+00:00",
  unassigned_at: null,
  active: true,
};

const PUBLICATION = {
  request_id: "gggg",
  status: "PUBLISHED",
  // 0023: rodzaj jest wymagany, bo nieznany rodzaj psuje cały odczyt zamiast udawać odpowiedź.
  kind: "REPLY",
  target_url: "https://reddit.com/x",
  permalink: null,
  published_at: "2026-09-19T08:00:00+00:00",
  queued_at: "2026-09-19T07:59:00+00:00",
  still_present: null,
  observed_at: null,
};

/** Every parser with a row it must accept, so the refusal cases below mean something. */
const GOOD: readonly [string, (row: Record<string, unknown>) => unknown, Record<string, unknown>][] =
  [
    ["workspace", parseWorkspace, WORKSPACE],
    ["project", parseProject, PROJECT],
    ["profile", parsePersonProfile, PROFILE],
    ["account", parseAccount, ACCOUNT],
    ["attachment", parseAttachment, ATTACHMENT],
    ["assignment", parseAssignment, ASSIGNMENT],
    ["support", parsePlatformSupport, SUPPORT],
    ["publication", parseResultPublication, PUBLICATION],
  ];

describe("the studio parsers", () => {
  it("accept_a_complete_row", () => {
    expect(GOOD.length).toBe(8);
    for (const [name, parse, row] of GOOD) {
      const result = parse(row) as { ok: boolean; reason?: string };
      expect(result.ok, `${name}: ${result.reason ?? ""}`).toBe(true);
    }
  });

  it("fail_the_whole_read_when_any_single_field_is_missing", () => {
    // Field by field, for every parser: a screen is only as honest as its strictest field.
    for (const [name, parse, row] of GOOD) {
      for (const key of Object.keys(row)) {
        const damaged = { ...row };
        delete damaged[key];
        const result = parse(damaged) as { ok: boolean };
        expect(result.ok, `${name} bez pola ${key} przeszło parsowanie`).toBe(false);
      }
    }
  });

  it("a_boolean_field_arriving_as_a_string_is_a_broken_read_not_a_false", () => {
    // The tempting version — `Boolean(row.active)` — would read `"false"` as true and a missing
    // field as false. Both are wrong in a way nobody sees on the screen.
    expect((parseAttachment({ ...ATTACHMENT, active: "true" }) as { ok: boolean }).ok).toBe(false);
    expect((parseProject({ ...PROJECT, paused: "nie" }) as { ok: boolean }).ok).toBe(false);
  });

  it("support_may_be_null_and_nothing_else", () => {
    const withoutMatrix = parseAccount({ ...ACCOUNT, support: null }) as {
      ok: boolean;
      value?: { support: unknown };
    };
    expect(withoutMatrix.ok).toBe(true);
    expect(withoutMatrix.value?.support).toBe(null);

    for (const broken of ["REDDIT", 1, [], true]) {
      const result = parseAccount({ ...ACCOUNT, support: broken }) as { ok: boolean };
      expect(result.ok, String(broken)).toBe(false);
    }
  });

  it("an_unknown_publication_kind_breaks_the_read_instead_of_passing_as_a_reply", () => {
    // Domyślenie się tutaj pokazałoby założony przez nas wątek jako dopisek do cudzej rozmowy.
    for (const kind of ["", "POST", "own_post", 7]) {
      const result = parseResultPublication({ ...PUBLICATION, kind }) as { ok: boolean };
      expect(result.ok, `rodzaj ${String(kind)}`).toBe(false);
    }
    expect((parseResultPublication({ ...PUBLICATION, kind: "OWN_POST" }) as { ok: boolean }).ok).toBe(
      true,
    );
  });

  it("still_present_keeps_its_three_values", () => {
    // `null` is NOBODY LOOKED and it has to survive parsing as itself. Collapsing it to `false`
    // would report an unchecked publication as removed.
    for (const value of [true, false, null]) {
      const result = parseResultPublication({ ...PUBLICATION, still_present: value }) as {
        ok: boolean;
        value?: { still_present: boolean | null };
      };
      expect(result.ok, String(value)).toBe(true);
      expect(result.value?.still_present).toBe(value);
    }
    expect(
      (parseResultPublication({ ...PUBLICATION, still_present: "tak" }) as { ok: boolean }).ok,
    ).toBe(false);
  });

  it("a_nullable_timestamp_absent_from_the_payload_is_a_failure_not_a_null", () => {
    // "The control plane did not send this field" and "the control plane said there is no such
    // moment" are different facts; only the second one may render as "brak odczytu".
    const damaged = { ...ATTACHMENT };
    delete (damaged as Record<string, unknown>)["detached_at"];
    expect((parseAttachment(damaged) as { ok: boolean }).ok).toBe(false);
    expect((parseAttachment({ ...ATTACHMENT, detached_at: null }) as { ok: boolean }).ok).toBe(
      true,
    );
  });

  it("the_reason_names_the_fields_that_were_wrong", () => {
    const result = parseProject({ project_id: "bbbb" }) as { ok: boolean; reason?: string };
    expect(result.ok).toBe(false);
    expect(result.reason).toContain("mode");
    expect(result.reason).toContain("zepsuty odczyt");
  });
});
