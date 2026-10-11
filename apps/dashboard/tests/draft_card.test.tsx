/**
 * The draft comment card: the pause before anything this system writes reaches a platform.
 *
 * THE THREE THINGS THIS FILE REFUSES TO LET REGRESS, all of them rules the dashboard must obey
 * without holding a copy of them:
 *
 *   1. **Nothing publishes without a click.** No state renders a form that could submit itself, and
 *      the states that are a REPORT (submitting, published, rejected) render no decision buttons at
 *      all. A card that offered "Publikuj" while a submit was in flight would invite the double
 *      send that L2673 exists to prevent.
 *   2. **The consent names an exact text.** Every decision carries the revision id and its
 *      `payload_sha256`, so the approval reaching the backend is for the text that was on screen
 *      (L5218-5219). The card does not compute the hash; it carries the one it displayed.
 *   3. **A retry is the backend's verdict.** `may_retry: false` means the comment may already exist
 *      on the platform, and no button may appear. `true` is the only case with a retry control.
 *
 * The parser is tested here too, including the two shapes that must fail loudly: PUBLISHED with no
 * evidence, and a failure with no `may_retry`.
 */
import { describe, expect, it } from "vitest";

import { DraftCard } from "../components/views/draft-card";
import type { Read } from "../lib/control-plane";
import { DRAFT_STATES, parseDraft, type Draft } from "../lib/draft-schema";
import { NOW, ONE_MINUTE_AGO } from "./fixtures";
import { renderToDocument } from "./render";
import { successIndicators } from "./success-indicators";

const HASH = "a".repeat(64);
const OLDER_HASH = "b".repeat(64);

function draft(overrides: Partial<Draft> = {}): Read<Draft> {
  return {
    ok: true,
    value: {
      draft_id: "11111111-1111-4111-8111-111111111111",
      thread_id: "22222222-2222-4222-8222-222222222222",
      thread_ref: "REDDIT:r/test:1w6vtqn",
      thread_title: "test",
      thread_url: "https://www.reddit.com/r/test/comments/1w6vtqn/test/",
      community_display_name: "Reddit, r/test",
      account: "u/artodev",
      state: "AWAITING_APPROVAL",
      current_revision: {
        revision_id: "r-2",
        body: "Krótka odpowiedź w wątku testowym.",
        payload_sha256: HASH,
        author: "AGENT",
        created_at: ONE_MINUTE_AGO,
      },
      revisions: [],
      reasoning: "Wątek testowy założyciela, więc komentarz jest krótki i nie udaje eksperta.",
      voice_version: 1,
      user_profile_version: 3,
      model: "gpt-5.6-terra",
      prompt_name: "draft_comment",
      prompt_sha256: "c".repeat(64),
      created_at: ONE_MINUTE_AGO,
      approved_at: null,
      submitted_at: null,
      published: null,
      failure: null,
      rate_limit: { used_today: 1, limit_per_day: 3, resets_at: "2026-09-05T00:00:00.000Z" },
      ...overrides,
    },
  };
}

/** The JSON the backend would send, exactly as §4b proposes it. */
function rawDraft(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const value = draft().ok ? (draft() as { value: Draft }).value : ({} as Draft);
  return {
    draft_id: value.draft_id,
    thread_id: value.thread_id,
    thread_ref: value.thread_ref,
    thread_title: value.thread_title,
    thread_url: value.thread_url,
    community_display_name: value.community_display_name,
    account: value.account,
    state: "AWAITING_APPROVAL",
    current_revision: {
      revision_id: "r-2",
      body: value.current_revision.body,
      payload_sha256: HASH,
      author: "AGENT",
      created_at: ONE_MINUTE_AGO,
    },
    revisions: [],
    reasoning: value.reasoning,
    voice_version: 1,
    user_profile_version: 3,
    model: value.model,
    prompt_name: value.prompt_name,
    prompt_sha256: value.prompt_sha256,
    created_at: value.created_at,
    approved_at: null,
    submitted_at: null,
    published: null,
    failure: null,
    rate_limit: null,
    ...overrides,
  };
}

const textOf = (element: Element | Document | null) =>
  ((element as Element | null)?.textContent ?? "").replace(/\s+/g, " ").trim();

describe("DraftCard", () => {
  it("a_draft_awaiting_a_decision_shows_the_text_three_buttons_and_the_exact_consent", () => {
    const document_ = renderToDocument(<DraftCard draft={draft()} now={NOW} />);
    const card = document_.querySelector("[data-draft]");

    expect(card?.getAttribute("data-state-name")).toBe("AWAITING_APPROVAL");
    expect(textOf(card)).toContain("czeka na Twoją zgodę");

    // The thread is a link the operator can check, opened without handing over the opener.
    const link = document_.querySelector('a[href="https://www.reddit.com/r/test/comments/1w6vtqn/test/"]');
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");

    // ONE form, three named decisions, and the text in an editable field.
    const forms = Array.from(document_.querySelectorAll("form"));
    expect(forms.length).toBe(1);
    const buttons = Array.from(forms[0]?.querySelectorAll('button[name="decision"]') ?? []).map((b) =>
      b.getAttribute("value"),
    );
    expect(buttons).toEqual(["publish", "revise", "reject"]);
    expect(document_.querySelector("textarea[name='body']")?.textContent).toBe(
      "Krótka odpowiedź w wątku testowym.",
    );

    // THE CONSENT. Both values travel with the decision, so the backend can refuse a stale form.
    expect(document_.querySelector('input[name="revision_id"]')?.getAttribute("value")).toBe("r-2");
    expect(document_.querySelector('input[name="payload_sha256"]')?.getAttribute("value")).toBe(HASH);
    expect(successIndicators(document_)).toEqual([]);
  });

  it("no_state_publishes_by_itself_and_the_reporting_states_offer_no_decision", () => {
    // Parametrised over every state: a card that grew a decision button in "submitting" would
    // invite the double send L2673 exists to prevent, and one in "published" would offer to say
    // the same thing twice.
    expect(DRAFT_STATES.length).toBe(7);
    const decidable = ["DRAFT", "AWAITING_APPROVAL"];

    for (const state of DRAFT_STATES) {
      const extra: Partial<Draft> =
        state === "PUBLISHED"
          ? { published: { external_id: "t1_x", url: "https://www.reddit.com/r/test/comments/1w6vtqn/test/c", observed_at: ONE_MINUTE_AGO } }
          : state === "FAILED"
            ? { failure: { reason: "429 z platformy", at: ONE_MINUTE_AGO, may_retry: false } }
            : {};
      const document_ = renderToDocument(<DraftCard draft={draft({ state, ...extra })} now={NOW} />);
      const decisions = document_.querySelectorAll('button[name="decision"]');

      expect(document_.querySelectorAll("[data-draft]").length, state).toBe(1);
      if (decidable.includes(state)) {
        expect(decisions.length, state).toBe(3);
      } else {
        expect(decisions.length, state).toBe(0);
      }
      // Nothing that submits itself: no inline handlers anywhere, in any state.
      expect(document_.documentElement.innerHTML, state).not.toMatch(/\son(click|submit|load|change)=/i);
      expect(successIndicators(document_), state).toEqual([]);
    }
  });

  it("submitting_says_to_wait_and_when_to_look_instead_of_offering_a_button", () => {
    const document_ = renderToDocument(<DraftCard draft={draft({ state: "SUBMITTING", submitted_at: ONE_MINUTE_AGO })} now={NOW} />);
    const text = textOf(document_.body);

    expect(text).toContain("publikacja w toku");
    expect(text).toContain("Nie klikaj drugi raz");
    expect(text).toContain("Odśwież ten ekran za minutę");
    expect(document_.querySelectorAll("button").length).toBe(0);
  });

  it("published_points_at_the_comment_and_never_wears_a_success_colour", () => {
    const document_ = renderToDocument(
      <DraftCard
        draft={draft({
          state: "PUBLISHED",
          approved_at: ONE_MINUTE_AGO,
          submitted_at: ONE_MINUTE_AGO,
          published: { external_id: "t1_abc", url: "https://www.reddit.com/r/test/comments/1w6vtqn/test/c", observed_at: ONE_MINUTE_AGO },
        })}
        now={NOW}
      />,
    );
    const proof = document_.querySelector('a[href$="/c"]');

    expect(proof).not.toBeNull();
    expect(proof?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(textOf(document_.body)).toContain("opublikowany");
    // Published is a fact, not praise: the pill is neutral and no success vocabulary appears.
    expect(document_.querySelector('[data-draft] .pill')?.getAttribute("data-tone")).toBe("neutral");
    expect(successIndicators(document_)).toEqual([]);
  });

  it("a_retry_appears_only_when_the_backend_permits_it", () => {
    // THE distinction this card shares with the job detail screen: after a submit whose outcome
    // nobody measured, the comment may already exist, so there is no control at all.
    const forbidden = renderToDocument(
      <DraftCard draft={draft({ state: "FAILED", failure: { reason: "UNKNOWN po wysłaniu", at: ONE_MINUTE_AGO, may_retry: false } })} now={NOW} />,
    );
    const allowed = renderToDocument(
      <DraftCard draft={draft({ state: "FAILED", failure: { reason: "503 z gatewaya", at: ONE_MINUTE_AGO, may_retry: true } })} now={NOW} />,
    );

    expect(forbidden.querySelectorAll("button").length).toBe(0);
    expect(textOf(forbidden.body)).toContain("NIE zezwala na ponowienie");
    expect(textOf(forbidden.body)).toContain("UNKNOWN po wysłaniu");

    const retry = Array.from(allowed.querySelectorAll("button"));
    expect(retry.length).toBe(1);
    expect((retry[0]?.textContent ?? "").trim()).toBe("Spróbuj opublikować jeszcze raz");
    // Even the retry carries the consent for the exact text.
    expect(allowed.querySelector('input[name="payload_sha256"]')?.getAttribute("value")).toBe(HASH);
  });

  it("earlier_versions_are_kept_and_folded_away", () => {
    const document_ = renderToDocument(
      <DraftCard
        draft={draft({
          revisions: [
            { revision_id: "r-1", body: "Pierwsza wersja agenta.", payload_sha256: OLDER_HASH, author: "AGENT", created_at: ONE_MINUTE_AGO },
            { revision_id: "r-2", body: "Krótka odpowiedź w wątku testowym.", payload_sha256: HASH, author: "OPERATOR", created_at: ONE_MINUTE_AGO },
          ],
        })}
        now={NOW}
      />,
    );
    // The older one is in the document, behind a summary; the current one is in the field, not
    // repeated in the list.
    expect(textOf(document_.body)).toContain("Wcześniejsze wersje (1)");
    expect(document_.querySelector('[data-revision="r-1"]')).not.toBeNull();
    expect(document_.querySelector('[data-revision="r-2"]')).toBeNull();
  });

  it("the_technical_details_and_the_rate_limit_are_one_click_away", () => {
    const document_ = renderToDocument(<DraftCard draft={draft()} now={NOW} />);
    const details = Array.from(document_.querySelectorAll("details")).map((d) => textOf(d));
    const joined = details.join(" ");

    expect(joined).toContain("Dlaczego agent tak napisał");
    expect(joined).toContain("Karta głosu: wersja 1");
    expect(joined).toContain("Profil: wersja 3");
    expect(joined).toContain("Zostało 2 wypowiedzi");
    // None of it is on the surface: the card's own text has no hash and no model name.
    const surface = textOf(document_.body).replace(joined, "");
    expect(surface).not.toContain("draft_comment");
    expect(surface).not.toContain(HASH.slice(0, 12));
  });

  it("a_failed_read_is_brak_odpowiedzi_with_the_reason_and_no_buttons", () => {
    const document_ = renderToDocument(
      <DraftCard draft={{ ok: false, unauthorized: false, reason: "control plane odpowiedział 503" }} now={NOW} />,
    );
    expect(document_.querySelector('[data-state="brak odpowiedzi"]')).not.toBeNull();
    expect(textOf(document_.body)).toContain("control plane odpowiedział 503");
    expect(document_.querySelectorAll("button").length).toBe(0);
  });
});

describe("the draft contract", () => {
  it("a_complete_draft_parses_and_keeps_the_hash_of_the_exact_text", () => {
    const parsed = parseDraft(rawDraft());
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.current_revision.payload_sha256).toBe(HASH);
      expect(parsed.value.state).toBe("AWAITING_APPROVAL");
    }
  });

  it("a_claim_without_evidence_fails_the_read", () => {
    // PUBLISHED with nothing to open, and FAILED with no reason: both are states asserting a fact
    // the payload does not carry, which is the shape this whole package refuses.
    const published = parseDraft(rawDraft({ state: "PUBLISHED", published: null }));
    const failed = parseDraft(rawDraft({ state: "FAILED", failure: null }));
    expect(published.ok).toBe(false);
    if (!published.ok) expect(published.reason).toContain("PUBLISHED");
    expect(failed.ok).toBe(false);
    if (!failed.ok) expect(failed.reason).toContain("FAILED");
  });

  it("a_failure_without_may_retry_is_refused_rather_than_defaulted", () => {
    // `true` would be permission to repeat a side effect nobody measured; `false` would be a
    // verdict the dashboard is not entitled to make. So the read fails and says which field.
    const parsed = parseDraft(rawDraft({ state: "FAILED", failure: { reason: "x", at: ONE_MINUTE_AGO } }));
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.reason).toContain("may_retry");
  });

  it("a_hash_of_the_wrong_shape_and_an_unknown_state_are_broken_reads", () => {
    const badHash = parseDraft(rawDraft({ current_revision: { revision_id: "r", body: "x", payload_sha256: "krótki", author: "AGENT", created_at: ONE_MINUTE_AGO } }));
    const badState = parseDraft(rawDraft({ state: "MAYBE_PUBLISHED" }));
    const badAuthor = parseDraft(rawDraft({ current_revision: { revision_id: "r", body: "x", payload_sha256: HASH, author: "ROBOT", created_at: ONE_MINUTE_AGO } }));

    expect(badHash.ok).toBe(false);
    if (!badHash.ok) expect(badHash.reason).toContain("payload_sha256");
    expect(badState.ok).toBe(false);
    if (!badState.ok) expect(badState.reason).toContain("MAYBE_PUBLISHED");
    expect(badAuthor.ok).toBe(false);
    if (!badAuthor.ok) expect(badAuthor.reason).toContain("ROBOT");
  });
});
