/**
 * `/community` — five sections, each honest about its own read, and the first forms that write.
 *
 * What a fixture can prove here is what the renderer does with a row; that the screen READS the
 * control plane is the e2e suite's job. So this file is about the three states per section
 * (unread with reason / measured empty / data), the verdict buttons on every judgment card, and
 * the forms posting to the four route handlers `tests/source_scan.test.ts` allows.
 */
import { describe, expect, it } from "vitest";

import {
  CommunityView,
  PLATFORMS,
  VERDICTS,
  type CommunityViewProps,
} from "../components/views/community-view";
import type { Read } from "../lib/control-plane";
import type { CapsuleStatus } from "../lib/capsule-schema";
import type {
  CommunityPicture,
  CommunitySummary,
  JudgmentSummary,
  JudgmentTotals,
  UserProfile,
} from "../lib/observe-schema";
import { NOW, ONE_MINUTE_AGO } from "./fixtures";
import { renderToDocument } from "./render";

const COMMUNITY: CommunitySummary = {
  community_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  platform: "HACKER_NEWS",
  community_key: "ask",
  ref: "HACKER_NEWS:ask",
  display_name: "Hacker News · Ask HN",
  access_mode: "PUBLIC_API_NO_ACCOUNT",
  observe_enabled: true,
  poll_interval_seconds: 600,
  last_polled_at: ONE_MINUTE_AGO,
  watermark_created_at: ONE_MINUTE_AGO,
  created_at: ONE_MINUTE_AGO,
  // 0025: nikt nie zdecydował o własnych wątkach, czyli stan, w którym zaczyna każda
  // społeczność. Trzy stany tej decyzji mierzy `own_posts_decision.test.tsx`.
  own_posts_allowed: null,
  own_posts_decided_at: null,
  counts: { threads: 12, comments: 40, snapshots: 30, judgments: 9, pictures: 1 },
};

const PICTURE: CommunityPicture = {
  picture_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  community_id: COMMUNITY.community_id,
  version: 2,
  window_start: "2026-08-30T12:00:00.000Z",
  window_end: ONE_MINUTE_AGO,
  thread_count: 12,
  invalid_ref_count: 1,
  model: "gpt-5.6-sol",
  effort: "high",
  prompt_name: "community_picture",
  prompt_sha256: "7570f8d1c89ea3954eb55ac0384f6f3aaadcd15faa06eaed657441c15ce6a928",
  created_at: ONE_MINUTE_AGO,
  content: {
    summary: "Dzień pod znakiem pytań o MCP w produkcji.",
    topics: [{ name: "MCP", why_it_matters: "wraca co tydzień", thread_refs: ["HACKER_NEWS:ask:1"] }],
    recurring_problems: [],
    what_gets_conversation: "konkretne pytania z liczbami",
    what_gets_ignored: "linki bez komentarza",
    durable_culture: [{ observation: "alergia na promocję", confidence: 0.35, thread_refs: ["HACKER_NEWS:ask:2"] }],
    current_state: [{ observation: "release X", thread_refs: ["HACKER_NEWS:ask:1", "HACKER_NEWS:ask:3"] }],
    where_user_could_contribute: [{ thread_ref: "HACKER_NEWS:ask:1", why: "pytanie otwarte" }],
  },
};

const PROFILE: UserProfile = {
  profile_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  version: 3,
  knows_deeply: "kolejki na Postgresie",
  positions: "nudna technologia wygrywa",
  would_never_claim: "benchmarki, których nie robił",
  voice_samples: "Krótko.",
  created_at: ONE_MINUTE_AGO,
};

const TOTALS: JudgmentTotals = {
  decision_ENGAGE: 2,
  decision_IGNORE: 7,
  decision_WAIT: 0,
  feedback_AGREE: 1,
  feedback_DISAGREE: 0,
  feedback_IRRELEVANT: 0,
};

function judgment(overrides: Partial<JudgmentSummary> = {}): JudgmentSummary {
  return {
    judgment_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    thread_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    thread_ref: "HACKER_NEWS:ask:1",
    thread_title: "Ask HN: Who is using MCP in production?",
    thread_url: "https://news.ycombinator.com/item?id=1",
    community_display_name: "Hacker News · Ask HN",
    decision: "ENGAGE",
    reasoning: "Autor pyta dokładnie o to, co użytkownik robił przez rok.",
    what_user_could_add: "Liczby z roku produkcji.",
    confidence: 0.71,
    user_profile_version: 3,
    model: "gpt-5.6-terra",
    created_at: ONE_MINUTE_AGO,
    latest_feedback: null,
    latest_feedback_note: null,
    ...overrides,
  };
}

const UNREAD: Read<never> = { ok: false, unauthorized: false, reason: "control plane nie odpowiada" };

const CAPSULE: CapsuleStatus = {
  status: "SIGNED_OUT",
  configured: false,
  href: null,
  account: null,
  since: null,
  mode: null,
  display_only: false,
  human_steer_open: false,
  session: null,
  viewer_ready: false,
  error: null,
};

function props(overrides: Partial<CommunityViewProps> = {}): CommunityViewProps {
  return {
    now: NOW,
    capsule: { ok: true, value: CAPSULE },
    communities: { ok: true, value: [COMMUNITY] },
    pictures: [{ community: COMMUNITY, picture: { ok: true, value: PICTURE } }],
    profile: { ok: true, value: PROFILE },
    engage: { ok: true, value: { rows: [judgment()], truncated: false, totals: TOTALS } },
    recentIgnored: {
      ok: true,
      value: {
        rows: [judgment({ judgment_id: "ffffffff-ffff-4fff-8fff-ffffffffffff", decision: "IGNORE", what_user_could_add: null })],
        truncated: true,
        totals: TOTALS,
      },
    },
    flash: null,
    ...overrides,
  };
}

const textOf = (element: Element | null) => (element?.textContent ?? "").replace(/\s+/g, " ").trim();

describe("CommunityView", () => {
  it("renders_every_section_with_data_and_the_evidence_links", () => {
    const document_ = renderToDocument(<CommunityView {...props()} />);
    expect(document_.querySelector('[data-screen="community"]')).not.toBeNull();
    const community = document_.querySelector(`[data-community="${COMMUNITY.community_id}"]`);
    expect(textOf(community)).toContain("12 wątków");  // 12 → forma mnoga „wątków"
    expect(textOf(community)).toContain("40 komentarzy");

    const picture = document_.querySelector(`[data-picture="${PICTURE.picture_id}"]`);
    // The pill says WHEN, not which version; the version is in „Szczegóły obrazu”.
    expect(textOf(picture)).toContain("obraz z");
    expect(textOf(picture)).toContain("wersja 2");
    expect(textOf(picture)).toContain("Dzień pod znakiem pytań o MCP");
    expect(textOf(picture)).toContain("alergia na promocję");
    // The word on the surface, the number in the attribute.
    expect(textOf(picture)).toContain("pewność niska");
    expect(picture?.querySelector('[data-confidence="0.35"]')).not.toBeNull();
    expect(textOf(picture)).toContain("dowody: HACKER_NEWS:ask:2");
    expect(textOf(picture)).toContain("Odrzucono 1 obserwację");  // odmiana dla 1

    const cards = Array.from(document_.querySelectorAll("[data-judgment]"));
    expect(cards.length).toBe(2);
    const engage = cards.find((card) => card.getAttribute("data-decision") === "ENGAGE");
    expect(engage).toBeDefined();
    expect(engage?.querySelector("a.judgment__thread")?.getAttribute("href")).toBe("https://news.ycombinator.com/item?id=1");
    expect(textOf(engage ?? null)).toContain("Co mógłbyś dodać: Liczby z roku produkcji.");
    expect(engage?.querySelector('[data-feedback="none"]')).not.toBeNull();

    // The totals are six tiles (docs/DASHBOARD-DESIGN.md §2 pkt 5), each addressable by the field
    // it shows — so the assertion is per number, not on one sentence's punctuation.
    const totals = document_.querySelector('[data-totals="tak"]');
    expect(totals).not.toBeNull();
    // Human labels on the surface (docs/DASHBOARD-DESIGN.md §1 zasada nr 1); the enum lives in
    // `data-total`, which is what the e2e suite reads the numbers by.
    expect(textOf(totals?.querySelector('[data-total="decision_ENGAGE"]') ?? null)).toBe("2 warto się odezwać");
    expect(textOf(totals?.querySelector('[data-total="decision_IGNORE"]') ?? null)).toBe("7 pominięte");
    expect(textOf(totals?.querySelector('[data-total="decision_WAIT"]') ?? null)).toBe("0 poczekaj");
    expect(textOf(totals?.querySelector('[data-total="feedback_AGREE"]') ?? null)).toContain("1");
    expect(textOf(document_.querySelector('[data-profile="present"]'))).toContain("Profil v3");
    expect(textOf(document_.body)).toContain("może być więcej osądów");
  });

  it("every_judgment_card_carries_the_three_verdict_buttons_posting_to_the_feedback_handler", () => {
    const document_ = renderToDocument(<CommunityView {...props()} />);
    expect(VERDICTS.length).toBe(3);
    for (const card of Array.from(document_.querySelectorAll("[data-judgment]"))) {
      const form = card.querySelector("form");
      expect(form?.getAttribute("action")).toBe("/community/feedback");
      expect(form?.getAttribute("method")).toBe("post");
      expect(form?.querySelector('input[name="judgment_id"]')?.getAttribute("value")).toBe(card.getAttribute("data-judgment"));
      const buttons = Array.from(form?.querySelectorAll('button[name="verdict"]') ?? []).map((b) => b.getAttribute("value"));
      expect(buttons).toEqual(VERDICTS.map((v) => v.value));
    }
  });

  it("a_recorded_verdict_is_shown_next_to_the_judgment", () => {
    const document_ = renderToDocument(
      <CommunityView
        {...props({
          engage: {
            ok: true,
            value: { rows: [judgment({ latest_feedback: "AGREE", latest_feedback_note: "tak" })], truncated: false, totals: TOTALS },
          },
        })}
      />,
    );
    const feedback = document_.querySelector('[data-feedback="AGREE"]');
    expect(textOf(feedback)).toContain("Twoja odpowiedź: AGREE. Notatka: tak");
  });

  it("a_failed_read_in_one_section_does_not_empty_the_others", () => {
    const document_ = renderToDocument(<CommunityView {...props({ communities: UNREAD, engage: UNREAD })} />);
    const body = textOf(document_.body);
    expect(body).toContain("control plane nie odpowiada");
    // The judgments section failed, the IGNORE section did not: the IGNORE card is still there.
    expect(document_.querySelectorAll("[data-judgment]").length).toBe(1);
    // A failed community read means no picture entries — and the picture section says so.
    expect(document_.querySelector('[data-totals="tak"]')).toBeNull();
    expect(textOf(document_.querySelector('[data-profile="present"]'))).toContain("Profil v3");
  });

  it("measured_empty_states_are_sentences_not_blank_lists", () => {
    const document_ = renderToDocument(
      <CommunityView
        {...props({
          communities: { ok: true, value: [] },
          pictures: [],
          profile: { ok: true, value: null },
          engage: { ok: true, value: { rows: [], truncated: false, totals: { ...TOTALS, decision_ENGAGE: 0 } } },
          recentIgnored: { ok: true, value: { rows: [], truncated: false, totals: TOTALS } },
        })}
      />,
    );
    const body = textOf(document_.body);
    expect(body).toContain("Obserwowane community: brak");
    expect(body).toContain("Bez obserwowanego community nie ma czego rysować");
    expect(body).toContain("Brak profilu");
    expect(body).toContain("Osądy ENGAGE: brak");
    // Every empty state offers exactly one action (docs/DASHBOARD-DESIGN.md §1 zasada nr 1).
    expect(document_.querySelector('a[href="#dodaj-community"]')).not.toBeNull();
    expect(document_.querySelector('a[href="#profil"]')).not.toBeNull();
    expect(document_.querySelectorAll("[data-judgment]").length).toBe(0);
    expect(document_.querySelector('[data-profile="none"]')).not.toBeNull();
  });

  it("a_community_without_a_picture_yet_says_when_the_first_one_comes", () => {
    const document_ = renderToDocument(
      <CommunityView {...props({ pictures: [{ community: COMMUNITY, picture: { ok: true, value: null } }] })} />,
    );
    expect(textOf(document_.querySelector('[data-picture="none"]'))).toContain("jeszcze bez obrazu");
  });

  it("the_enable_profile_and_resume_forms_post_to_their_handlers", () => {
    const document_ = renderToDocument(<CommunityView {...props()} />);
    const actions = Array.from(document_.querySelectorAll("form")).map((form) => form.getAttribute("action"));
    expect(actions).toContain("/community/enable");
    expect(actions).toContain("/community/profile");
    expect(actions).toContain("/community/resume");
    expect(PLATFORMS.map((p) => p.value)).toEqual(["HACKER_NEWS", "REDDIT"]);
    const select = document_.querySelector('select[name="platform"]');
    expect(Array.from(select?.querySelectorAll("option") ?? []).map((o) => o.getAttribute("value"))).toEqual(["HACKER_NEWS", "REDDIT"]);
    // The profile form is prefilled with the current version — a new version starts from the old.
    expect(textOf(document_.querySelector('textarea[name="knows_deeply"]'))).toBe("kolejki na Postgresie");
  });

  it("a_flash_sentence_is_shown_when_given_and_absent_otherwise", () => {
    const with_ = renderToDocument(<CommunityView {...props({ flash: "Profil zapisany." })} />);
    expect(textOf(with_.querySelector('[data-flash="tak"]'))).toBe("Profil zapisany.");
    const without = renderToDocument(<CommunityView {...props()} />);
    expect(without.querySelector('[data-flash="tak"]')).toBeNull();
  });
});
