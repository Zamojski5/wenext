/**
 * `lib/identity-schema.ts`: the parser of the one screen that answers "who can see our data".
 *
 * The failure mode being ruled out is a person who lost access rendering as a person who has it.
 * `active` and `revoked_at` are therefore required fields rather than optional ones, and a
 * payload missing either is a broken read rather than a member in good standing.
 */
import { describe, expect, it } from "vitest";

import {
  parseInvitation,
  parseMember,
  roleLabel,
  roleNote,
} from "../lib/identity-schema";

function member(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    user_id: "11111111-0000-4000-8000-000000000001",
    email: "ala@firma.test",
    role: "OWNER",
    joined_at: "2026-09-19T08:00:00+00:00",
    revoked_at: null,
    active: true,
    ...overrides,
  };
}

function invitation(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    invitation_id: "22222222-0000-4000-8000-000000000001",
    email: "gosc@firma.test",
    expires_at: "2026-09-26T08:00:00+00:00",
    accepted_at: null,
    live: true,
    ...overrides,
  };
}

describe("parseMember", () => {
  it("a_current_member_comes_through_with_their_role", () => {
    const parsed = parseMember(member());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.role).toBe("OWNER");
    expect(parsed.value.active).toBe(true);
    expect(parsed.value.revoked_at).toBeNull();
  });

  it("a_removed_member_keeps_the_moment_their_access_ended", () => {
    const parsed = parseMember(
      member({ active: false, revoked_at: "2026-09-19T10:00:00+00:00", role: "MEMBER" }),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.active).toBe(false);
    expect(parsed.value.revoked_at).toBe("2026-09-19T10:00:00+00:00");
  });

  it("the_founders_migrated_account_has_no_address_and_that_is_not_a_broken_read", () => {
    const parsed = parseMember(member({ email: null }));
    expect(parsed.ok).toBe(true);
    if (parsed.ok) expect(parsed.value.email).toBeNull();
  });

  it.each(["active", "revoked_at", "role", "email", "user_id"])(
    "a_missing_%s_is_a_broken_read_and_never_a_default",
    (field) => {
      const row = member();
      delete row[field];
      const parsed = parseMember(row);
      expect(parsed.ok).toBe(false);
      if (!parsed.ok) expect(parsed.reason).toContain(field);
    },
  );
});

describe("parseInvitation", () => {
  it("a_live_invitation_says_when_it_expires", () => {
    const parsed = parseInvitation(invitation());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.live).toBe(true);
    expect(parsed.value.accepted_at).toBeNull();
  });

  it("an_accepted_one_is_not_live_and_says_when_it_was_taken", () => {
    const parsed = parseInvitation(
      invitation({ live: false, accepted_at: "2026-09-20T08:00:00+00:00" }),
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) expect(parsed.value.accepted_at).toBe("2026-09-20T08:00:00+00:00");
  });

  it("the_payload_carries_no_token_and_the_type_has_no_field_for_one", () => {
    const parsed = parseInvitation(invitation());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    // A one-time credential has no business in a list: the type not having the field is the
    // cheapest way to keep somebody from storing it later.
    expect(Object.keys(parsed.value)).not.toContain("token");
    expect(Object.keys(parsed.value)).not.toContain("token_hash");
  });

  it("a_missing_live_flag_is_a_broken_read", () => {
    const row = invitation();
    delete row["live"];
    const parsed = parseInvitation(row);
    expect(parsed.ok).toBe(false);
  });
});

describe("role labels", () => {
  it("both_roles_read_as_polish_and_say_what_they_may_do", () => {
    expect(roleLabel("OWNER")).toBe("właściciel");
    expect(roleLabel("MEMBER")).toBe("członek");
    expect(roleNote("OWNER")).toContain("usuwa");
    expect(roleNote("MEMBER")).not.toContain("usuwa");
  });

  it("an_unknown_role_is_printed_as_it_came", () => {
    expect(roleLabel("ADMIN")).toBe("ADMIN");
  });
});
