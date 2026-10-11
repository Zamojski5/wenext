/**
 * A delegation's deadline (26.09): read from the backend, said next to the state, and chosen from
 * a closed list when the person signs on the presence screen.
 *
 * THREE THINGS THIS FILE PINS DOWN:
 *
 *   1. **the parser tolerates the new fields and their absence.** `expires_at` and the state
 *      `wygasła` are new; an older control plane sends neither, and that must read as "no deadline
 *      known", not as a broken read;
 *   2. **"Wygasło" follows the backend's state, not this server's clock.** The backend is what
 *      stops the agent, so it is the one definition of expired;
 *   3. **the form and the handler share one list of deadlines**, and the handler refuses anything
 *      else instead of choosing a default the person never saw.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  DEFAULT_DELEGATION_EXPIRY_HOURS,
  DELEGATION_EXPIRED,
  DELEGATION_EXPIRY_CHOICES,
  deadlineSentence,
  delegationExpiresAt,
  delegationHasExpired,
  parseDelegation,
  parsePresence,
  stateSentence,
} from "../lib/presence-schema";

const APP_ROOT = path.resolve(import.meta.dirname, "..");

function delegation(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    delegation_id: "d1",
    mode: "AUTONOMOUS",
    state: "aktywna",
    live: true,
    scope: { accounts: ["a1"], communities: ["r/SaaS"], actions: ["REPLY"] },
    limits: {},
    scope_sha256: "abc",
    signed_at: "2026-09-26T10:00:00+00:00",
    paused_at: null,
    revoked_at: null,
    ...overrides,
  };
}

describe("reading the deadline", () => {
  it("keeps_expires_at_and_the_expired_state", () => {
    const parsed = parseDelegation(
      delegation({ state: "wygasła", live: false, expires_at: "2026-10-03T12:00:00+00:00" }),
    );
    expect(parsed?.expires_at).toBe("2026-10-03T12:00:00+00:00");
    expect(parsed?.state).toBe(DELEGATION_EXPIRED);
    expect(parsed?.live).toBe(false);
  });

  it("an_older_control_plane_without_the_field_reads_as_no_deadline_known", () => {
    const read = parsePresence({
      state: "czuwa",
      delegation: delegation(),
      budget: null,
      wakeups: [],
      planned: [],
    });
    expect(read.ok).toBe(true);
    if (!read.ok) return;
    expect(read.value.delegation?.expires_at).toBeNull();
    // A value of the wrong type is not a deadline either.
    expect(parseDelegation(delegation({ expires_at: 1234 }))?.expires_at).toBeNull();
  });
});

describe("the sentence next to the state", () => {
  it("an_active_delegation_says_until_when_on_the_warsaw_clock", () => {
    expect(
      deadlineSentence({ state: "aktywna", expires_at: "2026-10-03T12:00:00+00:00" }),
    ).toBe("Ważne do 3 października 2026, 14:00.");
    // A paused one keeps its deadline too: pausing does not extend it.
    expect(
      deadlineSentence({ state: "wstrzymana", expires_at: "2026-12-01T08:05:00Z" }),
    ).toBe("Ważne do 1 grudnia 2026, 09:05.");
  });

  it("the_signature_beside_the_deadline_is_on_the_same_clock", () => {
    // Measured 27.09 on the screen: "Podpisano 2026-09-27 00:46. Ważne do 1 października 2026,
    // 01:45." The first in UTC, the second in Warsaw, one sentence, no zone named.
    const page = fs.readFileSync(
      path.join(APP_ROOT, "app/(studio)/w/[workspaceId]/p/[projectId]/presence/page.tsx"),
      "utf8",
    );
    expect(page).toMatch(/Podpisano \$\{warsawMoment\(\s*delegation\.signed_at/);
  });

  it("an_expired_delegation_says_since_when_even_without_the_moment", () => {
    expect(
      deadlineSentence({ state: "wygasła", expires_at: "2026-10-03T12:00:00+00:00" }),
    ).toBe("Wygasło 3 października 2026, 14:00.");
    expect(deadlineSentence({ state: "wygasła", expires_at: null })).toBe("Wygasło.");
  });

  it("no_deadline_and_not_expired_says_nothing", () => {
    expect(deadlineSentence({ state: "aktywna", expires_at: null })).toBeNull();
  });

  it("the_backend_state_decides_not_this_servers_clock", () => {
    // A deadline long past with the backend still saying `aktywna`: the screen repeats the
    // deadline and does not declare the delegation expired on its own authority.
    expect(
      deadlineSentence({ state: "aktywna", expires_at: "2020-01-01T00:00:00Z" }),
    ).toBe("Ważne do 1 stycznia 2020, 01:00.");
  });
});

describe("choosing a deadline when signing", () => {
  it("the_list_is_closed_and_the_default_is_on_it", () => {
    expect(DELEGATION_EXPIRY_CHOICES.map((one) => one.hours)).toEqual([24, 72, 168, 336]);
    expect(DELEGATION_EXPIRY_CHOICES.map((one) => one.label)).toEqual([
      "1 dzień",
      "3 dni",
      "7 dni",
      "14 dni",
    ]);
    expect(DEFAULT_DELEGATION_EXPIRY_HOURS).toBe(168);
  });

  it("expires_at_is_now_plus_the_hours_as_an_utc_instant", () => {
    const now = new Date("2026-09-26T21:30:00.000Z");
    expect(delegationExpiresAt(168, now)).toBe("2026-10-03T21:30:00.000Z");
    expect(delegationExpiresAt(24, now)).toBe("2026-09-27T21:30:00.000Z");
  });

  it("anything_off_the_list_is_refused_rather_than_defaulted", () => {
    const now = new Date("2026-09-26T21:30:00.000Z");
    for (const hours of [0, 1, 100, 8760, Number.NaN, -24]) {
      expect(delegationExpiresAt(hours, now), String(hours)).toBeNull();
    }
  });

  it("the_form_renders_the_list_and_the_handler_sends_expires_at_from_it", () => {
    // Read on the sources: the page is a server component behind the auth gate, and what matters
    // here is that both sides use the ONE list, under the ONE field name.
    const page = fs.readFileSync(
      path.join(APP_ROOT, "app/(studio)/w/[workspaceId]/p/[projectId]/presence/page.tsx"),
      "utf8",
    );
    expect(page).toContain('name="expires_in_hours"');
    expect(page).toContain("DELEGATION_EXPIRY_CHOICES.map");
    expect(page).toContain("defaultValue={String(DEFAULT_DELEGATION_EXPIRY_HOURS)}");
    const handler = fs.readFileSync(path.join(APP_ROOT, "app/studio/presence/route.ts"), "utf8");
    expect(handler).toContain('delegationExpiresAt(Number(field(form, "expires_in_hours")), new Date())');
    expect(handler).toContain("expires_at: expiresAt");
    // Refused, not defaulted, when the value is off the list.
    expect(handler).toContain('if (expiresAt === null) return backTo(back, "refused");');
  });

  it("an_expired_delegation_is_offered_a_new_signature_and_no_resume_but_keeps_revoke", () => {
    // "Wznów" on an expired signature would promise that a click restores the work; the way back
    // is a new signature, which the backend accepts without revoking the expired one first. Read
    // on the page source for the same reason as above.
    const page = fs.readFileSync(
      path.join(APP_ROOT, "app/(studio)/w/[workspaceId]/p/[projectId]/presence/page.tsx"),
      "utf8",
    );
    expect(page).toContain("const expired = delegationHasExpired(presence.value);");
    // The sign block, manual form included, stands for "never entrusted" AND for "expired".
    const sign = page.indexOf("{delegation === null || expired ? (");
    expect(sign).toBeGreaterThan(-1);
    expect(page.indexOf('data-block="sign"')).toBeGreaterThan(sign);
    expect(page.indexOf('name="expires_in_hours"')).toBeGreaterThan(sign);
    // The expired signature's own block still renders, below the offer.
    const block = page.indexOf('{delegation === null ? null : (');
    expect(block).toBeGreaterThan(sign);
    expect(page.indexOf('data-block="delegation"')).toBeGreaterThan(block);
    // No pause and no resume when expired, and "Odwołaj" after them, outside that condition.
    const actions = page.indexOf("{expired ? null : delegation.live ? (");
    expect(actions).toBeGreaterThan(block);
    expect(page.indexOf('value="resume"')).toBeGreaterThan(actions);
    expect(page.indexOf('value="revoke"')).toBeGreaterThan(page.indexOf('value="resume"'));
  });
});

describe("the project's own word for an expired delegation", () => {
  it("wygasly_is_a_known_state_with_its_own_sentence", () => {
    const read = parsePresence({
      state: "wygasły",
      delegation: delegation({ state: "wygasła", live: false, expires_at: "2026-10-03T12:00:00+00:00" }),
      budget: null,
      wakeups: [],
      planned: [],
    });
    // Before 26.09 an unknown word failed the whole read and the page said so instead of a state.
    expect(read.ok).toBe(true);
    if (!read.ok) return;
    expect(read.value.state).toBe("wygasły");
    expect(stateSentence(read.value)).toBe(
      "Powierzenie wygasło; praca w tle stoi, dalsza praca wymaga nowego podpisu.",
    );
  });

  it("either_word_means_the_screen_offers_a_new_signature", () => {
    const expiredDelegation = parseDelegation(delegation({ state: "wygasła", live: false }));
    const activeDelegation = parseDelegation(delegation());
    expect(delegationHasExpired({ state: "wygasły", delegation: null })).toBe(true);
    expect(delegationHasExpired({ state: "czuwa", delegation: expiredDelegation })).toBe(true);
    expect(delegationHasExpired({ state: "wygasły", delegation: expiredDelegation })).toBe(true);
    expect(delegationHasExpired({ state: "czuwa", delegation: activeDelegation })).toBe(false);
    expect(delegationHasExpired({ state: "wstrzymany", delegation: activeDelegation })).toBe(false);
    expect(delegationHasExpired({ state: "niepowierzony", delegation: null })).toBe(false);
  });

  it("an_unknown_word_still_fails_the_read_rather_than_guessing", () => {
    // Accepting `wygasły` is a decision about one word, not a loosening of the parser.
    const read = parsePresence({ state: "wygasla", delegation: null, budget: null, wakeups: [], planned: [] });
    expect(read.ok).toBe(false);
  });
});
