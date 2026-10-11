/**
 * The conversations screens — BUILT ON FIXTURES AHEAD OF THEIR API, and tested as such.
 *
 * What these tests can prove is what the renderer does with a row, plus the two rules the shape
 * carries: an `Observed[int]` is never rendered as a zero, and an unknown conversation state is a
 * FAILED read rather than a fourth pill. What they cannot prove is that anything reads a database —
 * nothing does yet, on purpose (docs/DASHBOARD-DESIGN.md §4: the contract is proposed, the backend
 * has not confirmed it, and a screen wired to nothing would render "brak danych" over a read that
 * never happened).
 *
 * The parser is tested here too, because it is the only place the contract is written down in code
 * and because the e2e suite cannot reach it until the routes exist.
 */
import { describe, expect, it } from "vitest";

import {
  ConversationView,
} from "../components/views/conversation-view";
import { ConversationsView } from "../components/views/conversations-view";
import type { CapsuleStatus } from "../lib/capsule-schema";
import type { Read } from "../lib/control-plane";
import {
  parseConversation,
  parseConversationDetail,
  type ConversationDetail,
  type ConversationSummary,
} from "../lib/conversation-schema";
import { NOW, ONE_MINUTE_AGO } from "./fixtures";
import { renderToDocument } from "./render";
import { successIndicators } from "./success-indicators";

const CONVERSATION: ConversationSummary = {
  conversation_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  community_ref: "REDDIT:r/kubernetes",
  community_display_name: "Reddit · r/kubernetes",
  thread_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  thread_ref: "REDDIT:r/kubernetes:1abc2d",
  thread_title: "How do you test operators end-to-end?",
  thread_url: "https://www.reddit.com/r/kubernetes/comments/1abc2d/",
  state: "REPLIED",
  our_account: "u/artodev",
  our_message_at: "2026-09-02T18:12:03.000Z",
  last_message_at: ONE_MINUTE_AGO,
  last_message_author: "u/mkols",
  last_message_excerpt: "And does it handle the operator's own CRDs?",
  unread_count: 2,
  reply_count: 4,
  seen_at: "2026-09-03T06:00:00.000Z",
  score: { value: 12, unknownReason: null },
};

const DETAIL: ConversationDetail = {
  ...CONVERSATION,
  messages: [
    {
      message_id: "m-1",
      external_id: "t3_1abc2d",
      author: "u/mkols",
      is_ours: false,
      is_thread_author: true,
      body: "Krótkie pytanie o testy operatorów.",
      created_at_source: "2026-09-02T16:02:00.000Z",
      observed_at: ONE_MINUTE_AGO,
      depth: 0,
      parent_external_id: null,
      score: { value: 3, unknownReason: null },
      is_new: false,
    },
    {
      message_id: "m-2",
      external_id: "t1_ours",
      author: "u/artodev",
      is_ours: true,
      is_thread_author: false,
      body: `Nasza odpowiedź. ${"x".repeat(400)}`,
      created_at_source: "2026-09-02T18:12:03.000Z",
      observed_at: ONE_MINUTE_AGO,
      depth: 1,
      parent_external_id: "t3_1abc2d",
      score: { value: null, unknownReason: "NOT_VISIBLE" },
      is_new: false,
    },
    {
      message_id: "m-3",
      external_id: "t1_reply",
      author: "u/mkols",
      is_ours: false,
      is_thread_author: true,
      body: "And does it handle the operator's own CRDs?",
      created_at_source: ONE_MINUTE_AGO,
      observed_at: ONE_MINUTE_AGO,
      depth: 2,
      parent_external_id: "t1_ours",
      score: { value: 1, unknownReason: null },
      is_new: true,
    },
  ],
};

const SIGNED_IN: Read<CapsuleStatus> = {
  ok: true,
  value: {
    status: "SIGNED_IN",
    configured: true,
    href: null,
    account: "u/artodev",
    since: ONE_MINUTE_AGO,
    mode: "HEADLESS",
    display_only: true,
    human_steer_open: false,
    session: { logged_in: true, username: "u/artodev", threads: null, error: null },
    viewer_ready: false,
    error: null,
  },
};
const UNREAD: Read<never> = { ok: false, unauthorized: false, reason: "control plane nie odpowiada" };

const textOf = (element: Element | Document | null) =>
  ((element as Element | null)?.textContent ?? "").replace(/\s+/g, " ").trim();

/** The contract as JSON, exactly as §4 of the design document proposes it. */
function rawConversation(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    conversation_id: CONVERSATION.conversation_id,
    community_ref: CONVERSATION.community_ref,
    community_display_name: CONVERSATION.community_display_name,
    thread_id: CONVERSATION.thread_id,
    thread_ref: CONVERSATION.thread_ref,
    thread_title: CONVERSATION.thread_title,
    thread_url: CONVERSATION.thread_url,
    state: "REPLIED",
    our_account: CONVERSATION.our_account,
    our_message_at: CONVERSATION.our_message_at,
    last_message_at: CONVERSATION.last_message_at,
    last_message_author: CONVERSATION.last_message_author,
    last_message_excerpt: CONVERSATION.last_message_excerpt,
    unread_count: 2,
    reply_count: 4,
    seen_at: CONVERSATION.seen_at,
    score: 12,
    score_unknown_reason: null,
    ...overrides,
  };
}

describe("ConversationsView", () => {
  it("a_conversation_row_shows_author_title_excerpt_state_and_the_unread_dot", () => {
    const document_ = renderToDocument(
      <ConversationsView capsule={SIGNED_IN} conversations={{ ok: true, value: [CONVERSATION] }} now={NOW} />,
    );
    const row = document_.querySelector(`[data-conversation="${CONVERSATION.conversation_id}"]`);

    expect(row).not.toBeNull();
    expect(row?.getAttribute("data-unread")).toBe("tak");
    expect(row?.getAttribute("data-state-name")).toBe("REPLIED");
    const text = textOf(row);
    expect(text).toContain("u/mkols");
    expect(text).toContain(CONVERSATION.thread_title);
    expect(text).toContain(CONVERSATION.last_message_excerpt);
    // The human words on the surface, the enum only in the attribute.
    expect(text).toContain("ktoś odpisał");
    expect(text).not.toContain("REPLIED");
    // The link goes to the conversation screen, and it is the only link in the row.
    expect(row?.querySelectorAll("a").length).toBe(1);
    expect(row?.querySelector("a")?.getAttribute("href")).toBe(
      `/conversations/${CONVERSATION.conversation_id}`,
    );
    expect(successIndicators(document_)).toEqual([]);
  });

  it("an_unknown_score_is_a_dash_and_never_a_zero", () => {
    // L5241 point 5 in the one place a metric can be missing: the platform did not show it.
    const document_ = renderToDocument(
      <ConversationsView
        capsule={SIGNED_IN}
        conversations={{
          ok: true,
          value: [{ ...CONVERSATION, score: { value: null, unknownReason: "NOT_VISIBLE" } }],
        }}
        now={NOW}
      />,
    );
    const row = textOf(document_.querySelector("[data-conversation]"));
    expect(row).toContain("punkty nieznane");
    expect(row).not.toContain("0 pkt");
  });

  it("a_failed_read_of_either_half_does_not_empty_the_other", () => {
    const document_ = renderToDocument(
      <ConversationsView capsule={UNREAD} conversations={{ ok: true, value: [CONVERSATION] }} now={NOW} />,
    );
    expect(document_.querySelector('[data-state="brak odpowiedzi"]')).not.toBeNull();
    // The list is still there — one failed read is not two.
    expect(document_.querySelectorAll("[data-conversation]").length).toBe(1);
  });

  it("an_empty_list_is_one_sentence_and_one_action", () => {
    const document_ = renderToDocument(
      <ConversationsView capsule={SIGNED_IN} conversations={{ ok: true, value: [] }} now={NOW} />,
    );
    const body = textOf(document_.body);
    expect(body).toContain("Jeszcze nigdzie się nie odezwałeś.");
    expect(body).toContain("Rozmowy: brak.");
    expect(document_.querySelector('a[href="/community"]')).not.toBeNull();
    expect(successIndicators(document_)).toEqual([]);
  });
});

describe("ConversationView", () => {
  it("the_exchange_is_cards_our_message_is_marked_and_long_bodies_are_cut", () => {
    const document_ = renderToDocument(<ConversationView detail={DETAIL} now={NOW} />);
    const messages = Array.from(document_.querySelectorAll("[data-message]"));

    expect(messages.length).toBe(3);
    expect(messages[1]?.getAttribute("data-ours")).toBe("tak");
    expect(textOf(messages[1] ?? null)).toContain("u/artodev (Ty)");
    expect(messages[2]?.getAttribute("data-new")).toBe("tak");
    expect(textOf(messages[0] ?? null)).toContain("autor wątku");

    // The long body is cut on the surface and complete in the DOM, inside the disclosure.
    const ours = textOf(messages[1] ?? null);
    expect(ours).toContain("…");
    expect(messages[1]?.querySelector("details")).not.toBeNull();
    expect(messages[1]?.textContent ?? "").toContain("x".repeat(400));

    // The evidence link and the "seen" form; nothing that publishes.
    expect(document_.querySelector(`a[href="${DETAIL.thread_url}"]`)).not.toBeNull();
    expect(
      Array.from(document_.querySelectorAll("form")).map((form) => form.getAttribute("action")),
    ).toEqual([`/conversations/${DETAIL.conversation_id}/seen`]);
    expect(successIndicators(document_)).toEqual([]);
  });

  it("a_missing_score_says_it_was_not_read_and_a_404_is_its_own_screen", () => {
    const withDetail = renderToDocument(<ConversationView detail={DETAIL} now={NOW} />);
    expect(withDetail.querySelector('[data-missing="tak"]')).not.toBeNull();
    expect(textOf(withDetail.querySelector('[data-missing="tak"]'))).toContain("NOT_VISIBLE");

    const missing = renderToDocument(<ConversationView detail={null} now={NOW} />);
    expect(missing.querySelector('[data-state="nie ma takiej rozmowy"]')).not.toBeNull();
    expect(missing.querySelectorAll("[data-message]").length).toBe(0);

    const failed = renderToDocument(<ConversationView detail={undefined} now={NOW} reason="503" />);
    expect(failed.querySelector('[data-state="brak odpowiedzi"]')).not.toBeNull();
    expect(textOf(failed.body)).toContain("503");
  });

  it("the_reply_button_appears_only_when_a_route_is_given", () => {
    // A button that does nothing teaches the operator that buttons do nothing; the reply flow is
    // the next outcome, so until it has a route there is no control for it.
    const without = renderToDocument(<ConversationView detail={DETAIL} now={NOW} />);
    expect(textOf(without.body)).not.toContain("Odpowiedz");

    const with_ = renderToDocument(<ConversationView detail={DETAIL} now={NOW} replyHref="/conversations/x/reply" />);
    expect(with_.querySelector('a[href="/conversations/x/reply"]')).not.toBeNull();
  });
});

describe("the conversation contract", () => {
  it("a_complete_row_parses_and_keeps_the_observed_pair", () => {
    const parsed = parseConversation(rawConversation());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.score).toEqual({ value: 12, unknownReason: null });
    expect(parsed.value.unread_count).toBe(2);
  });

  it("a_missing_field_fails_the_whole_read_and_names_it", () => {
    const cases = ["thread_title", "unread_count", "seen_at", "last_message_excerpt"] as const;
    expect(cases.length).toBe(4);
    for (const field of cases) {
      const row = rawConversation();
      delete row[field];
      const parsed = parseConversation(row);
      expect(parsed.ok, field).toBe(false);
      if (!parsed.ok) expect(parsed.reason, field).toContain(field);
    }
  });

  it("an_unknown_state_is_a_failed_read_not_a_fourth_pill", () => {
    const parsed = parseConversation(rawConversation({ state: "SOMETHING_NEW" }));
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.reason).toContain("SOMETHING_NEW");
  });

  it("the_score_pair_refuses_both_and_neither", () => {
    // Exactly one half, mirroring the CHECK in migration 0004. Both set, or neither, is broken.
    const both = parseConversation(rawConversation({ score: 5, score_unknown_reason: "NOT_VISIBLE" }));
    const neither = parseConversation(rawConversation({ score: null, score_unknown_reason: null }));
    const onlyReason = parseConversation(rawConversation({ score: null, score_unknown_reason: "NOT_VISIBLE" }));

    expect(both.ok).toBe(false);
    expect(neither.ok).toBe(false);
    expect(onlyReason.ok).toBe(true);
    if (onlyReason.ok) expect(onlyReason.value.score).toEqual({ value: null, unknownReason: "NOT_VISIBLE" });
  });

  it("a_detail_without_a_messages_list_is_a_broken_read_not_an_empty_conversation", () => {
    const withoutList = parseConversationDetail(rawConversation());
    expect(withoutList.ok).toBe(false);
    if (!withoutList.ok) expect(withoutList.reason).toContain("messages");

    const empty = parseConversationDetail(rawConversation({ messages: [] }));
    expect(empty.ok).toBe(true);
    if (empty.ok) expect(empty.value.messages).toEqual([]);
  });
});
