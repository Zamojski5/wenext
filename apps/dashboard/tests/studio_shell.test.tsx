/**
 * The studio shell: what the founder accepted as the direction, asserted rather than eyeballed.
 *
 * Four of WIZJA-WORKSPACE's sentences are testable from the markup, and these are the tests:
 * projects on the left, the entry to "Profile i konta" at the same level (it concerns all
 * projects), the operator panel reachable but not the front door, and the mark with no wordmark
 * next to it — the product's name is going to change (D-039).
 *
 * The fifth — that nothing animates while no model is running — is asserted by its absence: the
 * shell renders no element claiming activity, and `components/studio/composer.tsx` states in
 * words that sessions arrive in P2.
 */
import { describe, expect, it } from "vitest";

import { StudioShell, projectOf, returnPathFor } from "../components/studio/shell";
import type { StudioActive } from "../components/studio/shell";
import {
  OTHER_WORKSPACE_ID,
  PROJECT_ID,
  SESSION_ID,
  WORKSPACE_ID,
  frame,
  project,
  session,
} from "./studio-fixtures";
import { renderToDocument } from "./render";

function hrefs(document_: Document, selector: string): string[] {
  return Array.from(document_.querySelectorAll(selector)).map(
    (element) => element.getAttribute("href") ?? "",
  );
}

describe("StudioShell", () => {
  it("the_projects_of_the_workspace_are_on_the_left_and_link_into_themselves", () => {
    const projects = [
      project({ project_id: PROJECT_ID, name: "Obecność devops" }),
      project({ project_id: "bbbbbbbb-0000-4000-8000-000000000002", name: "Research" }),
    ];
    const rendered = renderToDocument(
      <StudioShell active={{ kind: "workspace" }} crumb="Studio" frame={frame({ projects })}>
        <p>treść</p>
      </StudioShell>,
    );

    // Count before contents: an empty list would satisfy every assertion below.
    const links = hrefs(rendered, ".st__list .st__item");
    expect(links.length).toBe(2);
    expect(links).toContain(`/w/${WORKSPACE_ID}/p/${PROJECT_ID}`);
    expect(rendered.body.textContent).toContain("Obecność devops");
    expect(rendered.body.textContent).toContain("Research");
  });

  it("the_open_project_is_the_only_one_marked_as_current", () => {
    // `aria-current` is passed in, not guessed from the URL: a layout cannot see which project is
    // open, and a sidebar that is sometimes wrong about where you are is worse than useless.
    const projects = [
      project({ project_id: PROJECT_ID }),
      project({ project_id: "bbbbbbbb-0000-4000-8000-000000000002", name: "Research" }),
    ];
    const rendered = renderToDocument(
      <StudioShell
        active={{ kind: "project", projectId: PROJECT_ID }}
        crumb="Studio"
        frame={frame({ projects })}
      >
        <p>treść</p>
      </StudioShell>,
    );
    const current = Array.from(rendered.querySelectorAll('.st__list [aria-current="page"]'));
    expect(current.length).toBe(1);
    expect(current[0]?.getAttribute("href")).toBe(`/w/${WORKSPACE_ID}/p/${PROJECT_ID}`);
  });

  it("people_and_codex_sit_at_the_workspace_level_and_operations_leaves_the_studio", () => {
    const rendered = renderToDocument(
      <StudioShell active={{ kind: "people" }} crumb="Studio" frame={frame()}>
        <p>treść</p>
      </StudioShell>,
    );
    // DWIE POZYCJE W PASKU, DWIE W DOKU (odbiór 1, różnica nr 16). Referencja ma na dole paska
    // dwie pozycje globalne i dok z awatarem, a nie cztery pozycje, z których dwie są bez ikon.
    // „Przestrzeń" i „Operacje" przeniosły się pod awatar, bo to są wejścia do KONTA i do
    // panelu sprzed pivotu, a nie miejsca, w których pracuje się nad projektem.
    const navigation = hrefs(rendered, ".st__nav a");
    expect(navigation).toEqual([`/w/${WORKSPACE_ID}/people`, `/w/${WORKSPACE_ID}/codex`]);
    expect(hrefs(rendered, '[data-pop="account-dock"] a')).toEqual([
      `/w/${WORKSPACE_ID}/organization`,
      "/operations",
    ]);
    // Wylogowanie zostaje JEDNYM formularzem i mieszka przy awatarze, bo to jest wyjście z
    // konta, a nie pozycja nawigacji.
    const logout = Array.from(rendered.querySelectorAll('form[action="/logout"]'));
    expect(logout.length).toBe(1);
    expect(logout[0]?.closest('[data-pop="account-dock"]')).not.toBeNull();
    // The current entry is marked, and only one of them.
    const current = Array.from(rendered.querySelectorAll('.st__nav [aria-current="page"]'));
    expect(current.length).toBe(1);
    expect(current[0]?.getAttribute("data-nav")).toBe("people");
  });

  it("every_workspace_of_the_operator_is_reachable_from_the_switch", () => {
    const rendered = renderToDocument(
      <StudioShell active={{ kind: "workspace" }} crumb="Studio" frame={frame()}>
        <p>treść</p>
      </StudioShell>,
    );
    const inSwitch = hrefs(rendered, '[data-pop="workspace-switch"] a');
    expect(inSwitch.length).toBe(2);
    expect(inSwitch).toContain(`/w/${WORKSPACE_ID}`);
    expect(inSwitch).toContain(`/w/${OTHER_WORKSPACE_ID}`);
  });

  it("the_mark_carries_no_wordmark", () => {
    // D-039: a small standalone symbol, no "Wenext" next to it. The product's name is going to
    // change, and a wordmark is the thing that would have to be found and removed everywhere.
    const rendered = renderToDocument(
      <StudioShell active={{ kind: "workspace" }} crumb="Studio" frame={frame()}>
        <p>treść</p>
      </StudioShell>,
    );
    expect(rendered.querySelector(".st__mark")).not.toBeNull();
    expect((rendered.body.textContent ?? "").toLowerCase()).not.toContain("wenext");
  });

  it("every_form_in_the_shell_posts_and_carries_a_same_origin_return_path", () => {
    // A studio write must never be reachable by GET (a prefetch would perform it), and the
    // `return_to` it carries must be a path on this origin — the handler validates it again,
    // and this is the half that makes sure nothing ever sends something else.
    const rendered = renderToDocument(
      <StudioShell
        active={{ kind: "project", projectId: PROJECT_ID }}
        crumb="Studio"
        frame={frame()}
      >
        <p>treść</p>
      </StudioShell>,
    );
    const forms = Array.from(rendered.querySelectorAll("form"));
    expect(forms.length).toBeGreaterThan(0);
    for (const form of forms) {
      expect(form.getAttribute("method")).toBe("post");
      const returnTo = form.querySelector('input[name="return_to"]');
      if (returnTo === null) {
        // The logout is the one form with nothing to return to.
        expect(form.getAttribute("action")).toBe("/logout");
        continue;
      }
      const value = returnTo.getAttribute("value") ?? "";
      expect(value.startsWith("/")).toBe(true);
      expect(value.startsWith("//")).toBe(false);
    }
  });

  it("an_info_key_this_product_does_not_write_renders_nothing", () => {
    // `?info=` arrives in a URL anybody can craft. The screen looks the key up in a closed set
    // and prints the sentence it finds; it never prints the query string.
    const invented = renderToDocument(
      <StudioShell
        active={{ kind: "workspace" }}
        crumb="Studio"
        frame={frame()}
        info="<b>twoje konto wygasło</b>"
      >
        <p>treść</p>
      </StudioShell>,
    );
    expect(invented.querySelector(".st-info")).toBeNull();
    expect(invented.body.textContent).not.toContain("wygasło");

    const known = renderToDocument(
      <StudioShell
        active={{ kind: "workspace" }}
        crumb="Studio"
        frame={frame()}
        info="project_created"
      >
        <p>treść</p>
      </StudioShell>,
    );
    expect(known.querySelector(".st-info")?.textContent).toContain("Projekt założony");
  });

  it("returnPathFor_builds_the_path_of_the_screen_it_is_called_from", () => {
    expect(returnPathFor(WORKSPACE_ID, { kind: "workspace" })).toBe(`/w/${WORKSPACE_ID}`);
    expect(returnPathFor(WORKSPACE_ID, { kind: "people" })).toBe(`/w/${WORKSPACE_ID}/people`);
    expect(returnPathFor(WORKSPACE_ID, { kind: "codex" })).toBe(`/w/${WORKSPACE_ID}/codex`);
    expect(returnPathFor(WORKSPACE_ID, { kind: "project", projectId: PROJECT_ID })).toBe(
      `/w/${WORKSPACE_ID}/p/${PROJECT_ID}`,
    );
  });

  it("a_workspace_with_no_project_says_so_instead_of_showing_an_empty_list", () => {
    const rendered = renderToDocument(
      <StudioShell active={{ kind: "workspace" }} crumb="Studio" frame={frame({ projects: [] })}>
        <p>treść</p>
      </StudioShell>,
    );
    expect(rendered.querySelectorAll(".st__list .st__item").length).toBe(0);
    expect(rendered.body.textContent).toContain("Nie ma jeszcze żadnego projektu");
  });
});

describe("kontekst projektu w pasku", () => {
  it("every_project_screen_keeps_the_project_section_and_its_return_path", () => {
    // ODBIÓR 1, RÓŻNICA NR 5: na Publikacjach i Społecznościach znikała sekcja „W projekcie" z
    // pięcioma pozycjami ORAZ lista rozmów, a żaden projekt nie był podświetlony, bo `projectOf`
    // nie znało tych dwóch rodzajów. Brak pozycji w tym `switch` nie psuje niczego, co widzi test
    // treści: zabiera pasek, czyli kontekst, dokładnie wtedy, gdy człowiek wchodzi głębiej.
    //
    // Test chodzi po WSZYSTKICH rodzajach, które niosą `projectId`, więc nowy ekran projektu
    // zepsuje go w dniu, w którym powstanie, a nie przy odbiorze za trzy dni.
    const screens: StudioActive[] = [
      { kind: "project", projectId: PROJECT_ID },
      { kind: "conversation", projectId: PROJECT_ID, sessionId: SESSION_ID },
      { kind: "publications", projectId: PROJECT_ID },
      { kind: "communities", projectId: PROJECT_ID },
      { kind: "knowledge", projectId: PROJECT_ID },
      { kind: "presence", projectId: PROJECT_ID },
      // D-055: „Pokój". Brakująca pozycja w `projectOf` zabrałaby pasek dokładnie na ekranie,
      // z którego człowiek wchodzi z Codexa.
      { kind: "room", projectId: PROJECT_ID },
    ];
    for (const active of screens) {
      expect(projectOf(active), active.kind).toBe(PROJECT_ID);
      expect(returnPathFor(WORKSPACE_ID, active), active.kind).toContain(`/p/${PROJECT_ID}`);
      const rendered = renderToDocument(
        <StudioShell active={active} crumb="Studio" frame={frame()}>
          <p>treść</p>
        </StudioShell>,
      );
      expect(
        rendered.querySelectorAll(".st__subnav a").length,
        `${active.kind}: sekcja „W projekcie"`,
      ).toBe(6);
    }
    // I odwrotnie: ekrany PRZESTRZENI nie mają projektu i nie udają, że mają.
    for (const active of [{ kind: "workspace" }, { kind: "people" }, { kind: "codex" }] as const) {
      expect(projectOf(active), active.kind).toBeNull();
    }
  });
});

describe("pasek rozmów projektu (D-045)", () => {
  // Po D-045 projekt jest ZBIOREM ROZMÓW, więc pasek przestaje być listą projektów. Każdy
  // przypadek niżej to jeden sposób, w jaki ta lista mogłaby skłamać albo zniknąć.
  const conversations = [
    session({ session_id: SESSION_ID, tytul: "Odzew w r/devops" }),
    session({
      session_id: "11111111-0000-4000-8000-000000000002",
      tytul: "Plan na tydzień",
      wynik_zdanie: "Zmieniliśmy porę publikacji.",
    }),
  ];

  function shell(props: Record<string, unknown> = {}) {
    return renderToDocument(
      <StudioShell
        active={{ kind: "project", projectId: PROJECT_ID }}
        crumb="Studio"
        frame={frame()}
        {...props}
      >
        <p>treść</p>
      </StudioShell>,
    );
  }

  it("the_conversations_of_the_open_project_hang_under_it", () => {
    const rendered = shell({ conversations });
    const rows = Array.from(rendered.querySelectorAll(".st__convs .st-conv[data-conversation]"));
    expect(rows.length).toBe(2);
    expect(rows[0]?.getAttribute("href")).toBe(
      `/w/${WORKSPACE_ID}/p/${PROJECT_ID}/c/${SESSION_ID}`,
    );
    expect(rendered.body.textContent).toContain("Odzew w r/devops");
    // Wynik jednym zdaniem jedzie w dymku, nie w wierszu: pasek ma się czytać jednym spojrzeniem.
    expect(rows[1]?.getAttribute("title")).toContain("Zmieniliśmy porę publikacji.");
  });

  it("a_project_whose_conversations_were_not_read_shows_no_list_at_all", () => {
    // `undefined` znaczy „ten ekran ich nie czytał", a nie „nie ma rozmów". Pusta lista pod
    // projektem byłaby zdaniem o produkcie, którego nikt nie sprawdził.
    const rendered = shell();
    expect(rendered.querySelector(".st__convs")).toBeNull();
  });

  it("a_project_with_no_conversations_still_offers_to_start_one", () => {
    const rendered = shell({ conversations: [] });
    const start = rendered.querySelector("[data-new-conversation]");
    expect(start?.getAttribute("href")).toBe(`/w/${WORKSPACE_ID}/p/${PROJECT_ID}`);
    // Link, nie formularz: rozmowa bez pierwszego zdania nie ma po co powstawać.
    expect(start?.tagName.toLowerCase()).toBe("a");
  });

  it("the_open_conversation_is_the_only_one_marked_as_current", () => {
    const rendered = renderToDocument(
      <StudioShell
        active={{ kind: "conversation", projectId: PROJECT_ID, sessionId: SESSION_ID }}
        conversations={conversations}
        crumb="Studio"
        frame={frame()}
      >
        <p>treść</p>
      </StudioShell>,
    );
    const current = Array.from(rendered.querySelectorAll('.st-conv[aria-current="page"]'));
    expect(current.length).toBe(1);
    expect(current[0]?.getAttribute("data-conversation")).toBe(SESSION_ID);
    // Projekt też zostaje zaznaczony: rozmowa należy do niego i pasek ma to pokazywać.
    expect(
      rendered.querySelector('.st__list .st__item[aria-current="page"]')?.getAttribute("href"),
    ).toBe(`/w/${WORKSPACE_ID}/p/${PROJECT_ID}`);
  });

  it("the_in_project_section_appears_only_with_a_project_and_leads_only_to_real_screens", () => {
    const withProject = shell({ conversations });
    const subnav = Array.from(withProject.querySelectorAll(".st__subnav a")).map((node) => ({
      sub: node.getAttribute("data-sub"),
      href: node.getAttribute("href"),
    }));
    // KOLEJNOŚĆ JEST Z REFERENCJI (`workspace-studio.html`, blok „W projekcie"): rozmowa,
    // publikacje, społeczności, wiedza, aktywność. Sprawdzamy ją co do miejsca, bo pasek jest
    // pierwszą rzeczą, którą człowiek porównuje z makietą, a przestawiona pozycja jest różnicą,
    // której nie widać w żadnym teście treści.
    //
    // „Pokój" (D-055) stoi zaraz pod rozmowami: to jego człowiek otwiera z Codexa i to on ma być
    // pierwszą pozycją po rozmowie, nie ostatnią w ogonie.
    expect(subnav.map((entry) => entry.sub)).toEqual([
      "conversations",
      "room",
      "publications",
      "communities",
      "knowledge",
      "presence",
    ]);
    // Każda pozycja prowadzi do TRASY, nie do kotwicy. „Dokumenty" prowadziły do `#dokumenty`
    // na stronie projektu i wypadły z paska w E2 razem z sekcją, na którą wskazywały: pozycja
    // celująca w kotwicę, której nie ma, jest gorsza od braku pozycji.
    expect(subnav.map((entry) => entry.href)).toEqual([
      `/w/${WORKSPACE_ID}/p/${PROJECT_ID}`,
      `/w/${WORKSPACE_ID}/p/${PROJECT_ID}/room`,
      `/w/${WORKSPACE_ID}/p/${PROJECT_ID}/publications`,
      `/w/${WORKSPACE_ID}/p/${PROJECT_ID}/communities`,
      `/w/${WORKSPACE_ID}/p/${PROJECT_ID}/knowledge`,
      `/w/${WORKSPACE_ID}/p/${PROJECT_ID}/presence`,
    ]);

    const withoutProject = renderToDocument(
      <StudioShell active={{ kind: "people" }} crumb="Studio" frame={frame()}>
        <p>treść</p>
      </StudioShell>,
    );
    expect(withoutProject.querySelector(".st__subnav")).toBeNull();
  });

  it("the_recent_section_stays_away_when_the_control_plane_has_no_such_route", () => {
    // `null` NIE znaczy „w tej przestrzeni nie ma rozmów". Sekcja, która tak by to pokazała,
    // mówiłaby zdanie o produkcie na podstawie braku wdrożenia.
    expect(shell({ recent: null }).body.textContent).not.toContain("Ostatnie");
    expect(shell({ recent: [] }).body.textContent).not.toContain("Ostatnie");

    // I NIE POKAZUJE SIĘ W OTWARTYM PROJEKCIE (odbiór 2, R1). Rozmowy tego projektu są wyżej,
    // pod jego nazwą, więc sekcja powtarzałaby je i zabierała 150 px kolumnie, która od 21.09
    // się nie przewija; skutkiem było wypchnięcie nawigacji przestrzeni pod dok z awatarem.
    const recent = [session({ session_id: "99999999-0000-4000-8000-000000000009", tytul: "Z innego" })];
    expect(shell({ recent }).body.textContent).not.toContain("Ostatnie");

    // Poza projektem sekcja jest, bo wtedy odpowiada na pytanie „gdzie ostatnio pracowałem".
    const rendered = renderToDocument(
      <StudioShell active={{ kind: "workspace" }} crumb="Studio" frame={frame()} recent={recent}>
        <p>treść</p>
      </StudioShell>,
    );
    expect(rendered.body.textContent).toContain("Ostatnie");
    expect(rendered.querySelector('[data-recent="1"]')).not.toBeNull();
  });
});
