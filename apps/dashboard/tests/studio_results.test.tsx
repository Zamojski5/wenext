/**
 * The results block, which is where this product either tells the truth about its work or does not.
 *
 * ZAKRES-FUNKCJONALNY §6 has eight rules about numbers. Four of them can be broken by this one
 * component and all four are tested here:
 *
 *   * a failed read is not an empty workspace (rule 2, and the whole point of `Read<T>`);
 *   * an empty workspace is an empty workspace, said in words, not a screen full of zeros;
 *   * "wysłane" and "potwierdzone jako widoczne" are two numbers and are never summed (rule 7);
 *   * `still_present === null` means NOBODY LOOKED and renders as "skutek niepotwierdzony",
 *     never as removed and never as visible (the state §5 calls obligatory).
 *
 * The last one is not hypothetical: two real comments were taken down by automatic moderation
 * within an hour while the author's own read still reported them present (§10).
 */
import { describe, expect, it } from "vitest";

import { ResultsBlock } from "../components/studio/results";
import { results } from "./studio-fixtures";
import { renderToDocument } from "./render";

const COMMUNITY = {
  community_id: "11111111-0000-4000-8000-000000000001",
  platform: "REDDIT",
  community_key: "kubernetes",
  display_name: "r/kubernetes",
  access_mode: "PUBLIC_API_NO_ACCOUNT",
  observe_enabled: true,
  last_polled_at: "2026-09-19T07:30:00+00:00",
  thread_count: 12,
};

function publication(
  stillPresent: boolean | null,
  observedAt: string | null,
  kind: "REPLY" | "OWN_POST" = "REPLY",
) {
  return {
    request_id: "22222222-0000-4000-8000-000000000001",
    status: "PUBLISHED",
    kind,
    target_url: "https://reddit.com/r/kubernetes/comments/x",
    permalink: null,
    published_at: "2026-09-19T06:00:00+00:00",
    queued_at: "2026-09-19T05:59:00+00:00",
    still_present: stillPresent,
    observed_at: observedAt,
    removal_scope: null,
    platform: null,
    detail: null,
  };
}

describe("ResultsBlock", () => {
  it("the_operator_panel_gets_no_correction_form_because_it_only_reads", () => {
    // No `workspaceId`, no form. The same component serves a screen with no handler behind it,
    // and a button posting nowhere is worse than no button.
    const rendered = renderToDocument(
      <ResultsBlock results={results({ publications: [publication(true, "2026-09-19T07:00:00+00:00")] })} />,
    );
    expect(rendered.body.innerHTML).not.toContain("/studio/knowledge");
  });

  it("in_the_studio_each_publication_carries_the_four_short_corrections", () => {
    const rendered = renderToDocument(
      <ResultsBlock
        projectId="bbbbbbbb-0000-4000-8000-000000000001"
        results={results({ publications: [publication(true, "2026-09-19T07:00:00+00:00")] })}
        returnTo="/w/aaaa/p/bbbb"
        workspaceId="aaaaaaaa-0000-4000-8000-000000000001"
      />,
    );
    const markup = rendered.body.innerHTML;
    expect(markup).toContain('action="/studio/knowledge"');
    expect(markup).toContain('value="feedback"');
    for (const verdict of ["TOO_LONG", "NOT_LIKE_THIS", "GOOD", "DONT_REPLY_HERE"]) {
      expect(markup, verdict).toContain(`value="${verdict}"`);
    }
    // The correction is tied to THIS publication; without the id it would be a note about
    // nothing in particular.
    expect(markup).toContain('name="request_id"');
    expect(markup).toContain("22222222-0000-4000-8000-000000000001");
  });

  it("a_failed_read_is_not_an_empty_workspace", () => {
    const rendered = renderToDocument(<ResultsBlock reason="control plane odpowiedział 500" />);
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("Nie odczytano rezultatów");
    expect(text).toContain("control plane odpowiedział 500");
    // And it does NOT claim a measurement. No zero anywhere on a screen that measured nothing.
    expect(text).not.toContain("wysłane publikacje");
  });

  it("an_empty_workspace_says_the_read_succeeded_and_found_nothing", () => {
    const rendered = renderToDocument(<ResultsBlock results={results()} />);
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("Odczyt się udał");
    expect(text).not.toContain("Nie odczytano");
  });

  it("sent_and_confirmed_are_two_numbers_and_neither_is_the_other", () => {
    const rendered = renderToDocument(
      <ResultsBlock
        results={results({
          communities: [COMMUNITY],
          publications: [publication(true, "2026-09-19T09:00:00+00:00")],
          totals: { communities: 1, threads: 12, published: 4, confirmed_visible: 1 },
        })}
      />,
    );
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("wysłane publikacje: 4");
    expect(text).toContain("potwierdzone jako widoczne: 1");
    // The pair, not a sum and not a single "sukcesy" counter.
    expect(text).not.toContain("sukcesy");
  });

  it("a_publication_nobody_checked_is_neither_visible_nor_removed", () => {
    const rendered = renderToDocument(
      <ResultsBlock
        results={results({
          publications: [publication(null, null)],
          totals: { communities: 0, threads: 0, published: 1, confirmed_visible: 0 },
        })}
      />,
    );
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("skutek niepotwierdzony");
    expect(text).not.toContain("potwierdzona jako widoczna");
    expect(text).not.toContain("niewidoczna publicznie");
    // And the timestamp of the reading that did not happen says so in words, never as a dash.
    expect(text).toContain("brak odczytu");
  });

  it("a_publication_that_disappeared_is_marked_as_such_and_not_counted", () => {
    const rendered = renderToDocument(
      <ResultsBlock
        results={results({
          publications: [publication(false, "2026-09-19T09:00:00+00:00")],
          totals: { communities: 0, threads: 0, published: 1, confirmed_visible: 0 },
        })}
      />,
    );
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("niewidoczna publicznie");
    expect(text).toContain("potwierdzone jako widoczne: 0");
  });

  it("says_whether_our_reply_or_the_whole_thread_under_it_was_removed", () => {
    // 26.09: moderators removed threads under 19 of our replies. "Niewidoczna" alone read as
    // "your reply was removed", which the platform did not say.
    const gone = (removal_scope: "COMMENT" | "PARENT_POST") => ({
      ...publication(false, "2026-09-26T09:00:00+00:00"),
      removal_scope,
    });
    const rendered = renderToDocument(
      <ResultsBlock results={results({ publications: [gone("PARENT_POST"), { ...gone("COMMENT"), request_id: "22222222-0000-4000-8000-000000000002" }] })} />,
    );
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("usunięto cały wątek, nie naszą odpowiedź");
    expect(text).toContain("usunięto naszą odpowiedź");
  });

  it("every_community_carries_the_time_of_its_last_reading", () => {
    // Rule 1: a number with no date of the last reading has no business being on the screen.
    const rendered = renderToDocument(
      <ResultsBlock
        results={results({
          communities: [COMMUNITY],
          totals: { communities: 1, threads: 12, published: 0, confirmed_visible: 0 },
        })}
      />,
    );
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("12 wątków");
    expect(text).toContain("2026-09-19 07:30");
  });

  it("a_community_never_polled_says_brak_odczytu_rather_than_showing_nothing", () => {
    const rendered = renderToDocument(
      <ResultsBlock
        results={results({
          communities: [{ ...COMMUNITY, last_polled_at: null }],
          totals: { communities: 1, threads: 0, published: 0, confirmed_visible: 0 },
        })}
      />,
    );
    expect(rendered.body.textContent).toContain("brak odczytu");
  });

  it("tells_our_own_thread_apart_from_a_reply_under_somebody_elses", () => {
    // 0023: dwa różne działania wobec społeczności. Bez etykiety oba wiersze wyglądały tak samo,
    // a założenie własnego wątku ocenia się inaczej niż dopisanie się do cudzej rozmowy.
    const reply = renderToDocument(
      <ResultsBlock results={results({ publications: [publication(true, null, "REPLY")] })} />,
    );
    expect(reply.body.textContent).toContain("odpowiedź");
    expect(reply.body.textContent).not.toContain("nasz wątek");

    const own = renderToDocument(
      <ResultsBlock results={results({ publications: [publication(true, null, "OWN_POST")] })} />,
    );
    expect(own.body.textContent).toContain("nasz wątek");
    // Słowa z bazy zostają w bazie: na ekranie nie ma ani `REPLY`, ani `OWN_POST` jako tekstu.
    expect(own.body.textContent).not.toContain("OWN_POST");
    expect(own.querySelector("[data-publication-kind]")?.getAttribute("data-publication-kind")).toBe(
      "OWN_POST",
    );
  });

  it("opens_the_exact_published_comment_when_execution_has_a_permalink", () => {
    const permalink = "https://www.reddit.com/r/LLMDevs/comments/thread/comment/reply/";
    const rendered = renderToDocument(
      <ResultsBlock
        results={results({ publications: [{ ...publication(false, null), permalink }] })}
      />,
    );
    expect(rendered.querySelector("a")?.getAttribute("href")).toBe(permalink);
  });
});
