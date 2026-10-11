/**
 * The Room as the founder sees it, rendered from the same fixture the parser test reads.
 *
 * `renderToStaticMarkup` loads no CSS, so this file pins STRUCTURE and CLAIMS, not looks (the looks
 * are in `output/pokoj-2026-10-10/`): what each button posts and to where, that a number nobody
 * measured is never drawn as one, that a draft is shown word for word, and that nothing on the
 * screen speaks in the vocabulary of success.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  DraftsBlock,
  EmptyRoom,
  OpportunitiesBlock,
  ResultsBlock,
  RoomHero,
  ScopeNote,
} from "../components/studio/room";
import { parseRoom, type Room } from "../lib/room-schema";
import { PROJECT_ID, WORKSPACE_ID } from "./studio-fixtures";
import { renderToDocument } from "./render";
import { successIndicators } from "./success-indicators";

const NOW = new Date("2026-10-10T13:32:00+00:00");
const HERE = `/w/${WORKSPACE_ID}/p/${PROJECT_ID}/room`;
const ASSIGNED = [
  { platform: "REDDIT", external_account_id: "agentwatch_dev" },
  { platform: "X", external_account_id: "agentwatch_x" },
];

function room(name = "room-rich.json"): Room {
  const body = JSON.parse(
    fs.readFileSync(path.resolve(import.meta.dirname, "fixtures", name), "utf8"),
  ) as unknown;
  const result = parseRoom(body);
  if (!result.ok) throw new Error(result.reason);
  return result.value;
}

function whole(value: Room = room()) {
  return renderToDocument(
    <main>
      <RoomHero now={NOW} refreshHref={HERE} room={value} />
      <DraftsBlock
        assigned={ASSIGNED}
        now={NOW}
        projectId={PROJECT_ID}
        returnTo={HERE}
        room={value}
        scope={null}
        workspaceId={WORKSPACE_ID}
      />
      <OpportunitiesBlock
        now={NOW}
        projectId={PROJECT_ID}
        returnTo={HERE}
        room={value}
        workspaceId={WORKSPACE_ID}
      />
      <ResultsBlock now={NOW} results={value.results} />
    </main>,
  );
}

function fields(form: Element): Record<string, string> {
  const found: Record<string, string> = {};
  for (const input of Array.from(form.querySelectorAll("input, textarea"))) {
    const name = input.getAttribute("name");
    if (name !== null) found[name] = (input as HTMLInputElement).value ?? input.textContent ?? "";
  }
  return found;
}

describe("the promise", () => {
  it("leads_with_the_live_count_and_says_when_it_was_read", () => {
    const rendered = whole();
    const heading = rendered.querySelector("h1");
    expect(heading?.textContent).toBe("8 osób właśnie pisze o problemie, który rozwiązuje Twój kod.");
    expect(heading?.querySelector("strong")?.textContent).toBe("8 osób");
    // The moment of the READ, in Warsaw time: the contract carries no refresh timestamp, so this
    // is the page's own moment and the line says it that way.
    expect(rendered.querySelector(".st-room-hero__eyebrow")?.textContent).toBe("Odczyt o 15:32");
    expect(rendered.querySelector(".st-room-hero__meta")?.textContent).toContain(
      "4 odpowiedzi czekają na Twoją zgodę",
    );
    expect(rendered.querySelector(".st-room-hero__refresh")?.getAttribute("href")).toBe(HERE);
  });

  it("nobody_open_never_says_zero_people", () => {
    const empty = room("room-empty.json");
    const rendered = renderToDocument(<RoomHero now={NOW} refreshHref={HERE} room={empty} />);
    expect(rendered.querySelector("h1")?.textContent).toBe(
      "Teraz nikt nowy nie pisze o Twoim problemie.",
    );
    expect(rendered.body.textContent).not.toMatch(/\b0 osób\b/);
  });
});

describe("opportunities", () => {
  it("shows_the_open_people_and_folds_the_answered_and_the_skipped", () => {
    const rendered = whole();
    const cards = Array.from(rendered.querySelectorAll("[data-opportunity]"));
    expect(cards.length).toBe(10);
    // The open ones are the cards NOT inside a fold.
    const open = cards.filter((one) => one.closest("details") === null);
    expect(open.length).toBe(8);
    expect(rendered.querySelector('details[data-fold="answered"] summary')?.textContent).toBe(
      "Odpowiedziano (1)",
    );
    expect(rendered.querySelector('details[data-fold="skipped"] summary')?.textContent).toBe(
      "Pominięte (1)",
    );
    // A folded card is not skippable again: it has no "Pomiń".
    expect(rendered.querySelectorAll('details[data-fold] button').length).toBe(0);
  });

  it("a_card_carries_what_the_founder_needs_to_decide", () => {
    const rendered = whole();
    const card = rendered.querySelector('[data-opportunity="d1000000-0000-4000-8000-000000000001"]');
    expect(card?.getAttribute("data-platform")).toBe("REDDIT");
    expect(card?.querySelector(".st-room-card__where")?.textContent).toBe("r/LocalLLaMA");
    expect(card?.querySelector("time")?.textContent).toBe("3 h temu");
    expect(card?.querySelector(".st-room-card__author")?.textContent).toBe("u/ml_tinkerer");
    expect(card?.querySelector(".st-room-card__title")?.textContent).toContain("keeps looping");
    expect(card?.querySelector(".st-room-card__excerpt")?.textContent).toContain("Third night");
    const why = Array.from(card?.querySelectorAll(".st-room-why dt") ?? []).map((one) => one.textContent);
    expect(why).toEqual(["Dlaczego ta osoba", "Proponowany kąt"]);
    expect(card?.querySelector(".st-room-state")?.textContent).toBe("Odpowiedź gotowa");
  });

  it("an_unmeasured_score_is_brak_pomiaru_and_never_a_zero", () => {
    const rendered = whole();
    const inbox = Array.from(rendered.querySelectorAll("[data-opportunity]")).find((one) =>
      one.textContent?.includes("inbox_zero_dad"),
    );
    const metrics = Array.from(inbox?.querySelectorAll(".st-room-metrics li") ?? []).map((one) =>
      one.textContent,
    );
    expect(metrics).toEqual(["wynikbrak pomiaru", "komentarze12"]);
    const bare = Array.from(rendered.querySelectorAll("[data-opportunity]")).find((one) =>
      one.textContent?.includes("devon_ships"),
    );
    expect(bare?.querySelectorAll('.st-room-metrics li[data-measured="0"]').length).toBe(2);
    expect(bare?.querySelector(".st-room-metrics")?.textContent).not.toMatch(/\b0\b/);
  });

  it("a_post_with_no_title_is_its_own_text_not_a_placeholder", () => {
    const rendered = whole();
    const bare = Array.from(rendered.querySelectorAll("[data-opportunity]")).find((one) =>
      one.textContent?.includes("devon_ships"),
    );
    const title = bare?.querySelector(".st-room-card__title");
    expect(title?.getAttribute("data-untitled")).toBe("1");
    expect(title?.textContent).toContain("Looking for a way to replay an agent run");
    expect(bare?.querySelector(".st-room-card__excerpt")).toBeNull();
    expect(rendered.body.textContent).not.toContain("Otwórz wątek");
  });

  it("links_to_strangers_open_in_a_new_tab_without_an_opener", () => {
    const rendered = whole();
    const external = Array.from(rendered.querySelectorAll('a[target="_blank"]'));
    expect(external.length).toBeGreaterThan(10);
    for (const link of external) {
      expect(link.getAttribute("rel"), link.getAttribute("href") ?? "").toContain("noopener");
      expect(link.getAttribute("href") ?? "").toMatch(/^https?:\/\//);
    }
  });

  it("skip_posts_to_the_dismiss_route_through_the_card_handler", () => {
    const rendered = whole();
    const card = rendered.querySelector('[data-opportunity="d1000000-0000-4000-8000-000000000002"]');
    const form = card?.querySelector("form.st-room-skip");
    expect(form?.getAttribute("action")).toBe("/studio/card");
    expect(form?.getAttribute("method")).toBe("post");
    expect(fields(form as Element)).toEqual({
      workspace_id: WORKSPACE_ID,
      project_id: PROJECT_ID,
      akcja: "pomin",
      metoda: "POST",
      sciezka: `/api/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/opportunities/d1000000-0000-4000-8000-000000000002/dismiss`,
      payload: "{}",
      return_to: HERE,
    });
    expect(form?.querySelector("button")?.textContent).toBe("Pomiń");
  });

  it("a_card_with_a_draft_links_down_to_it", () => {
    const rendered = whole();
    const card = rendered.querySelector('[data-opportunity="d1000000-0000-4000-8000-000000000001"]');
    const jump = Array.from(card?.querySelectorAll("a") ?? []).find((one) =>
      one.textContent?.includes("Zobacz odpowiedź"),
    );
    expect(jump?.getAttribute("href")).toBe("#szkic-e1000000-0000-4000-8000-000000000001");
    expect(rendered.getElementById("szkic-e1000000-0000-4000-8000-000000000001")).not.toBeNull();
  });
});

describe("a card that points at a draft", () => {
  it("links_down_only_when_that_draft_is_on_the_page", () => {
    // The opportunity says it has a draft, the page has none: a link to an anchor that is not
    // there would scroll nowhere and look like a broken button.
    const value = room();
    const without: Room = { ...value, drafts: [] };
    const rendered = whole(without);
    const card = rendered.querySelector('[data-opportunity="d1000000-0000-4000-8000-000000000001"]');
    expect(card?.querySelector(".st-room-state")?.textContent).toBe("Odpowiedź gotowa");
    expect(
      Array.from(card?.querySelectorAll("a") ?? []).some((one) => one.textContent?.includes("Zobacz odpowiedź")),
    ).toBe(false);
  });
});

describe("drafts, drawn as the post", () => {
  it("a_reddit_reply_sits_under_the_quoted_thread_and_is_signed_with_the_assigned_account", () => {
    const rendered = whole();
    const draft = rendered.getElementById("szkic-e1000000-0000-4000-8000-000000000001");
    expect(draft?.getAttribute("data-platform")).toBe("REDDIT");
    expect(draft?.querySelector(".st-room-quote__title")?.textContent).toContain("keeps looping");
    expect(draft?.querySelector(".st-room-quote__title")?.getAttribute("href")).toMatch(/^https:\/\/www\.reddit\.com/);
    expect(draft?.querySelector(".st-room-quote__by")?.textContent).toBe("r/LocalLLaMA · u/ml_tinkerer");
    expect(draft?.querySelector(".st-room-comment .st-room-preview__line b")?.textContent).toBe("u/agentwatch_dev");
    expect(draft?.querySelector(".st-room-avatar")?.textContent).toBe("AG");
    expect(draft?.querySelector(".st-room-draft__head")?.textContent).toContain("Odpowiedź na Reddit");
  });

  it("the_preview_is_the_draft_word_for_word", () => {
    const value = room();
    const rendered = whole(value);
    for (const draft of value.drafts) {
      const shown = rendered
        .getElementById(`szkic-${draft.revision_id}`)
        ?.querySelector(".st-room-preview__text");
      // Not softened, not trimmed: what is on screen is what goes out.
      expect(shown?.textContent, draft.revision_id).toBe(draft.text);
    }
  });

  it("an_x_reply_and_an_own_reddit_post_each_have_their_own_shape", () => {
    const rendered = whole();
    const x = rendered.getElementById("szkic-e1000000-0000-4000-8000-000000000003");
    expect(x?.getAttribute("data-platform")).toBe("X");
    expect(x?.querySelector(".st-room-xreplying")?.textContent).toBe("W odpowiedzi do @sarah_builds");
    expect(x?.querySelector(".st-room-xpost b")?.textContent).toBe("@agentwatch_x");
    expect(x?.querySelectorAll(".st-room-chrome svg").length).toBe(5);

    const own = rendered.getElementById("szkic-e1000000-0000-4000-8000-000000000004");
    expect(own?.getAttribute("data-kind")).toBe("OWN_POST");
    expect(own?.querySelector(".st-room-preview__title")?.textContent).toContain("checklist for stuck runs");
    expect(own?.querySelector(".st-room-quote")).toBeNull();
    expect(own?.querySelector(".st-room-draft__head")?.textContent).toContain("Własny post na Reddit");
  });

  it("the_platform_chrome_carries_no_numbers", () => {
    // A vote count on a draft would be a measurement about a post that does not exist.
    const rendered = whole();
    for (const chrome of Array.from(rendered.querySelectorAll(".st-room-chrome"))) {
      expect(chrome.textContent ?? "", "chrome").not.toMatch(/\d/);
      expect(chrome.getAttribute("aria-hidden")).toBe("true");
    }
  });

  it("publish_posts_the_approve_route_of_exactly_this_revision", () => {
    const rendered = whole();
    const draft = rendered.getElementById("szkic-e1000000-0000-4000-8000-000000000001");
    const form = Array.from(draft?.querySelectorAll("form") ?? []).find(
      (one) => fields(one)["akcja"] === "opublikuj",
    );
    expect(form?.getAttribute("action")).toBe("/studio/card");
    expect(fields(form as Element)).toEqual({
      workspace_id: WORKSPACE_ID,
      project_id: PROJECT_ID,
      akcja: "opublikuj",
      metoda: "POST",
      sciezka: `/api/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/approvals/e1000000-0000-4000-8000-000000000001/approve`,
      payload: "{}",
      return_to: HERE,
    });
    expect(form?.querySelector("button")?.textContent).toBe("Opublikuj");
  });

  it("revise_posts_the_revise_route_with_the_text_in_a_field_named_text", () => {
    const rendered = whole();
    const draft = rendered.getElementById("szkic-e1000000-0000-4000-8000-000000000001");
    const form = Array.from(draft?.querySelectorAll("form") ?? []).find(
      (one) => fields(one)["akcja"] === "popraw",
    );
    const sent = fields(form as Element);
    expect(sent["sciezka"]).toBe(
      `/api/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/approvals/e1000000-0000-4000-8000-000000000001/revise`,
    );
    // `text`, not `tresc`: the approval API's own name, and the handler copies only that field.
    expect(sent["text"]).toBe(room().drafts[0]?.text);
    const area = form?.querySelector("textarea");
    expect(area?.getAttribute("name")).toBe("text");
    expect(area?.getAttribute("maxlength")).toBe("4000");
    expect(area?.hasAttribute("required")).toBe(true);
    expect(draft?.querySelector('[data-pop^="room-edit-"] summary')?.textContent).toBe("Popraw");
  });

  it("every_draft_with_a_waiting_state_has_both_buttons_and_an_expiry", () => {
    const rendered = whole();
    const drafts = Array.from(rendered.querySelectorAll("[data-draft]"));
    expect(drafts.length).toBe(4);
    const notes = drafts.map((one) => one.querySelector(".st-room-draft__note")?.textContent ?? "");
    expect(notes[0]).toBe("Wyjdzie z konta u/agentwatch_dev, po Twoim kliknięciu. Wygasa za 22 h.");
    expect(notes[1]).toContain("Wygasa za 5 h.");
    for (const one of drafts) {
      expect(one.querySelectorAll('button[type="submit"]').length).toBeGreaterThanOrEqual(2);
    }
  });

  it("a_draft_whose_window_passed_cannot_be_published_and_says_what_to_do", () => {
    const value = room();
    const first = value.drafts[0];
    if (first === undefined) throw new Error("fixture");
    const expired: Room = {
      ...value,
      drafts: [{ ...first, expires_at: "2026-10-10T12:00:00+00:00" }],
    };
    const rendered = whole(expired);
    const draft = rendered.querySelector("[data-draft]");
    expect(draft?.querySelectorAll('input[name="akcja"][value="opublikuj"]').length).toBe(0);
    expect(draft?.querySelector(".st-room-draft__note")?.textContent).toContain("Okno zgody minęło");
    expect(draft?.textContent).toContain("Poproś agenta w Codexie o nową wersję");
  });

  it("a_draft_past_consent_shows_its_state_and_no_buttons", () => {
    const value = room();
    const first = value.drafts[0];
    if (first === undefined) throw new Error("fixture");
    const queued: Room = { ...value, drafts: [{ ...first, state: "PUBLISHING" }] };
    const draft = whole(queued).querySelector("[data-draft]");
    expect(draft?.querySelector(".st-room-state")?.textContent).toBe("W kolejce do publikacji");
    expect(draft?.querySelectorAll("button").length).toBe(0);
  });

  it("without_an_assigned_account_the_handle_is_a_placeholder_and_says_so", () => {
    const rendered = renderToDocument(
      <DraftsBlock
        assigned={[]}
        now={NOW}
        projectId={PROJECT_ID}
        returnTo={HERE}
        room={room()}
        scope={null}
        workspaceId={WORKSPACE_ID}
      />,
    );
    expect(rendered.querySelector(".st-room-comment .st-room-preview__line b")?.textContent).toBe(
      "u/twoje_konto",
    );
    expect(rendered.querySelector(".st-room-draft__note")?.textContent).toContain(
      "Wyjdzie z konta przypisanego do projektu",
    );
    expect(rendered.querySelector(".st-room-avatar")?.textContent).toBe("TW");
  });

  it("no_drafts_no_section", () => {
    const rendered = renderToDocument(
      <div>
        <DraftsBlock
          assigned={ASSIGNED}
          now={NOW}
          projectId={PROJECT_ID}
          returnTo={HERE}
          room={room("room-empty.json")}
          scope={<p>nie powinno się pojawić</p>}
          workspaceId={WORKSPACE_ID}
        />
      </div>,
    );
    expect(rendered.body.textContent).toBe("");
  });
});

describe("the scope note", () => {
  it("links_to_the_presence_page_before_and_after_a_refused_click", () => {
    const presence = `/w/${WORKSPACE_ID}/p/${PROJECT_ID}/presence`;
    const before = renderToDocument(<ScopeNote presenceHref={presence} refused={false} />);
    expect(before.querySelector("a")?.getAttribute("href")).toBe(presence);
    expect(before.body.textContent).toContain("podpisany zakres");
    expect(before.body.textContent).toContain("Z przeglądem");
    expect(before.querySelector("[data-refused]")).toBeNull();

    const after = renderToDocument(<ScopeNote presenceHref={presence} refused />);
    expect(after.querySelector("a")?.getAttribute("href")).toBe(presence);
    expect(after.querySelector("[data-refused]")).not.toBeNull();
    expect(after.body.textContent).toContain("brak podpisanego zakresu");
    expect(after.body.textContent).toContain("Z przeglądem");
  });

  it("is_drawn_inside_the_drafts_block_where_it_is_passed", () => {
    const rendered = renderToDocument(
      <DraftsBlock
        assigned={ASSIGNED}
        now={NOW}
        projectId={PROJECT_ID}
        returnTo={HERE}
        room={room()}
        scope={<ScopeNote presenceHref="/p" refused={false} />}
        workspaceId={WORKSPACE_ID}
      />,
    );
    const section = rendered.querySelector("section");
    expect(section?.querySelector(".st-room-scope")).not.toBeNull();
    // Before the first draft, so it is read before the button it explains.
    const children = Array.from(section?.children ?? []);
    const note = children.findIndex((one) => one.classList.contains("st-room-scope"));
    const list = children.findIndex((one) => one.classList.contains("st-room-list"));
    expect(note).toBeGreaterThan(-1);
    expect(note).toBeLessThan(list);
  });
});

describe("results", () => {
  it("shows_four_big_numbers_for_the_window", () => {
    const rendered = whole();
    const tiles = Array.from(rendered.querySelectorAll(".st-room-stats li")).map((one) => ({
      stat: one.getAttribute("data-stat"),
      value: one.querySelector(".st-room-stat__value")?.textContent,
      label: one.querySelector(".st-room-stat__label")?.textContent,
    }));
    expect(tiles).toEqual([
      { stat: "published", value: "5", label: "Opublikowane" },
      { stat: "replies", value: "9", label: "Odpowiedzi" },
      { stat: "votes", value: "87", label: "Głosy" },
      { stat: "removed", value: "1", label: "Usunięte" },
    ]);
    expect(rendered.querySelector("#room-results")?.closest(".st-room-section__head")?.textContent).toContain(
      "ostatnie 7 dni",
    );
  });

  it("unmeasured_tiles_say_brak_pomiaru_and_a_measured_zero_says_zero", () => {
    const value = room();
    const mixed: Room = {
      ...value,
      results: { ...value.results, published: null, removed: 0, items: [] },
    };
    const rendered = renderToDocument(<ResultsBlock now={NOW} results={mixed.results} />);
    const by = (stat: string) =>
      rendered.querySelector(`[data-stat="${stat}"]`)?.querySelector(".st-room-stat__value");
    expect(by("published")?.textContent).toBe("brak pomiaru");
    expect(by("published")?.closest("li")?.getAttribute("data-measured")).toBe("0");
    expect(by("removed")?.textContent).toBe("0");
    expect(by("removed")?.closest("li")?.getAttribute("data-measured")).toBe("1");
  });

  it("the_list_keeps_visible_removed_and_unconfirmed_apart", () => {
    const rendered = whole();
    const rows = Array.from(rendered.querySelectorAll(".st-room-result"));
    expect(rows.map((one) => one.getAttribute("data-visibility"))).toEqual([
      "visible",
      "visible",
      "unconfirmed",
      "removed",
    ]);
    const tags = rows.map((one) => one.querySelector(".st-tag")?.textContent);
    expect(tags).toEqual([
      "widoczny",
      "widoczny",
      "skutek niepotwierdzony",
      "usunięty z platformy",
    ]);
    // Two unmeasured numbers are one sentence, not "głosy: brak pomiaru · odpowiedzi: brak pomiaru".
    expect(rows[2]?.querySelector("small")?.textContent).toBe(
      "opublikowano 20 h temu · głosy i odpowiedzi: brak pomiaru",
    );
    expect(rows[0]?.querySelector("small")?.textContent).toBe(
      "opublikowano wczoraj · 41 głosów · 6 odpowiedzi",
    );
  });

  it("an_empty_window_still_says_what_will_appear_instead_of_drawing_zeros", () => {
    const rendered = renderToDocument(
      <ResultsBlock now={NOW} results={room("room-empty.json").results} />,
    );
    expect(rendered.querySelector(".st-empty")?.textContent).toBe(
      "Wyniki pojawią się tutaj po pierwszej publikacji.",
    );
    expect(
      Array.from(rendered.querySelectorAll('.st-room-stats [data-measured="0"]')).length,
    ).toBe(4);
  });
});

describe("the empty room", () => {
  it("still_makes_the_promise_and_says_what_to_write_in_codex", () => {
    const rendered = renderToDocument(<EmptyRoom projectName="agentwatch" />);
    expect(rendered.querySelector("h1")?.textContent).toBe(
      "Tu pojawią się ludzie, którzy potrzebują tego, co zbudowałeś.",
    );
    expect(rendered.querySelector(".st-room-prompt__text")?.textContent).toBe(
      "znajdź moich pierwszych użytkowników",
    );
    expect(rendered.body.textContent).toContain("agentwatch");
    expect(rendered.querySelectorAll(".st-room-steps li").length).toBe(3);
    // Nothing to click: no form, no button, nothing that could be mistaken for an action.
    expect(rendered.querySelectorAll("form, button").length).toBe(0);
  });
});

describe("what the whole room never says", () => {
  it("has_no_success_vocabulary_in_text_classes_or_icons", () => {
    expect(successIndicators(whole())).toEqual([]);
    expect(successIndicators(renderToDocument(<EmptyRoom projectName="p" />))).toEqual([]);
  });

  it("has_no_long_dash_anywhere_in_what_it_renders", () => {
    const dashes = new RegExp(`[${String.fromCodePoint(0x2014)}${String.fromCodePoint(0x2013)}]`);
    expect(dashes.test(whole().documentElement.outerHTML)).toBe(false);
    expect(dashes.test(renderToDocument(<EmptyRoom projectName="p" />).documentElement.outerHTML)).toBe(false);
  });

  it("has_no_script_and_no_inline_handler", () => {
    const html = whole().documentElement.outerHTML;
    expect(html).not.toMatch(/<script/i);
    expect(html).not.toMatch(/\son[a-z]+=/i);
  });

  it("the_words_of_the_model_about_a_person_get_the_dash_rule_but_the_draft_does_not", () => {
    const value = room();
    const first = value.opportunities[0];
    const draft = value.drafts[0];
    if (first === undefined || draft === undefined) throw new Error("fixture");
    const dash = String.fromCodePoint(0x2014);
    const poisoned: Room = {
      ...value,
      opportunities: [{ ...first, why: `Dobry powód ${dash} bo tak`, angle: `Kąt${dash} bez spacji` }],
      drafts: [{ ...draft, text: `Draft with ${dash} a dash` }],
    };
    const rendered = whole(poisoned);
    const why = rendered.querySelector(".st-room-why")?.textContent ?? "";
    expect(why).not.toContain(dash);
    expect(why).toContain("Dobry powód, bo tak");
    // The draft is what goes out. Showing it softened would show text that is not the publication.
    expect(rendered.querySelector(".st-room-preview__text")?.textContent).toBe(`Draft with ${dash} a dash`);
  });
});
