/**
 * Okno „Konta projektu": co robi znacznik przy koncie i czego to okno nie udaje.
 *
 * SKĄD TEN KSZTAŁT. Odbiór 1, różnica nr 4: mieliśmy tu formularz z natywnym `select` i osobną
 * listą przypisanych kont, a referencja ma OKNO z wierszami kont w sekcjach po osobie, gdzie
 * przypisanie jest ZNACZNIKIEM przy koncie (✓ albo +). Różnica nie jest kosmetyczna: znacznik
 * pokazuje od razu, czego projekt NIE używa, a mógłby, czego lista przypisanych nie pokaże nigdy.
 *
 * CZEGO PILNUJĄ TE TESTY:
 *
 *   1. każde konto przestrzeni ma DOKŁADNIE JEDEN wiersz, niezależnie od tego, czy jest
 *      przypisane. Dwa wiersze dla jednego konta to dwa konta dla czytającego;
 *   2. znacznik jest PRZYCISKIEM prawdziwego formularza, a nie obrazkiem: ✓ zdejmuje, + przypisuje,
 *      oba przez `/studio/assignment`, czyli tę samą trasę, co przedtem;
 *   3. konto bez integracji mówi o tym TUTAJ, bo to jest ekran, na którym projekt twierdzi, że
 *      tym kontem pracuje;
 *   4. okno nie gubi kont przypisanych, których nie ma wśród kandydatów (kandydaci z definicji
 *      ich nie zawierają, i na tym poległa pierwsza wersja bloku kont w 2026-09).
 */
import { describe, expect, it } from "vitest";

import { AssignmentLink } from "../components/studio/assignment";
import { renderToDocument } from "./render";

const WORKSPACE = "11111111-1111-4111-8111-111111111111";
const PROJECT = "22222222-2222-4222-8222-222222222222";

const ANNA = {
  person_profile_id: "p1",
  workspace_id: WORKSPACE,
  name: "Anna",
  kind: "PERSON",
  created_at: "2026-09-01T10:00:00+00:00",
  archived: false,
};

function account(id: string, platform: string, handle: string, person: string | null) {
  return {
    social_account_id: id,
    platform,
    external_account_id: handle,
    state: "READY",
    created_at: "2026-09-01T10:00:00+00:00",
    person_profile_id: person,
    support: null,
  };
}

function assignment(id: string, platform: string, handle: string, integrated: boolean) {
  return {
    support: { platform, integrated, note: integrated ? "" : "integracja niedostępna" },
    assignment_id: `a-${id}`,
    project_id: PROJECT,
    social_account_id: id,
    platform,
    external_account_id: handle,
    assigned_at: "2026-09-02T10:00:00+00:00",
    unassigned_at: null,
    active: true,
  };
}

function render(props: Record<string, unknown> = {}) {
  return renderToDocument(
    <AssignmentLink
      active={[]}
      candidates={[]}
      people={[ANNA]}
      projectId={PROJECT}
      projectName="Obecność devops"
      returnTo={`/w/${WORKSPACE}/p/${PROJECT}`}
      workspaceId={WORKSPACE}
      {...props}
    />,
  );
}

describe("okno kont projektu", () => {
  it("one_row_per_account_with_a_mark_that_assigns_or_takes_it_off", () => {
    const screen = render({
      candidates: [account("s1", "REDDIT", "u/anna", "p1"), account("s2", "LINKEDIN", "in/m", "p1")],
      active: [assignment("s1", "REDDIT", "u/anna", true)],
    });
    const rows = Array.from(screen.querySelectorAll(".st-account-row"));
    expect(rows.length).toBe(2);

    const marks = rows.map((row) => ({
      assigned: row.querySelector(".st-account-mark")?.getAttribute("data-assigned"),
      action: row.querySelector('input[name="action"]')?.getAttribute("value"),
      target: row.getAttribute("action"),
      account: row.querySelector('input[name="social_account_id"]')?.getAttribute("value"),
    }));
    // Konto przypisane: znacznik „tak" i formularz, który je ZDEJMUJE. Odwrotnie dla wolnego.
    expect(marks).toEqual([
      { assigned: "tak", action: "unassign", target: "/studio/assignment", account: "s1" },
      { assigned: "nie", action: "assign", target: "/studio/assignment", account: "s2" },
    ]);
  });

  it("an_assigned_account_missing_from_the_candidates_still_has_its_row", () => {
    // Kandydaci NIE ZAWIERAJĄ kont już przypisanych. Okno liczące tylko z nich pokazałoby
    // projekt bez kont, którymi właśnie pracuje.
    const screen = render({ candidates: [], active: [assignment("s9", "REDDIT", "u/silent", true)] });
    expect(screen.querySelectorAll(".st-account-row").length).toBe(1);
    expect(screen.body.textContent).toContain("u/silent");
  });

  it("says_here_when_a_platform_the_project_uses_has_no_integration", () => {
    const screen = render({ candidates: [], active: [assignment("s3", "LINKEDIN", "in/m", false)] });
    expect(screen.body.textContent).toContain("integracja niedostępna");
  });

  it("names_the_project_and_offers_a_way_out_without_the_keyboard", () => {
    const screen = render({ candidates: [account("s1", "REDDIT", "u/m", "p1")] });
    // Nazwa projektu pod tytułem: okno odpowiada na pytanie „czyje to konta".
    expect(screen.querySelector(".st-pop__subtitle")?.textContent).toBe("Obecność devops");
    // X zamyka, ale NIE jest jedyną drogą: zapadka `details` zamyka się też własnym `summary`.
    expect(screen.querySelector("[data-pop-close]")).not.toBeNull();
    expect(screen.querySelector("details.st-pop > summary")).not.toBeNull();
    // I wyjście do miejsca, w którym konta się dodaje.
    expect(screen.querySelector(".st-accounts-manage")?.getAttribute("href")).toBe(
      `/w/${WORKSPACE}/people`,
    );
  });

  it("groups_accounts_by_person_and_says_when_nobody_owns_one", () => {
    const screen = render({
      candidates: [account("s1", "REDDIT", "u/m", "p1"), account("s2", "REDDIT", "u/x", null)],
    });
    const people = Array.from(screen.querySelectorAll(".st-accounts-person")).map(
      (node) => node.textContent,
    );
    expect(people).toEqual(["Anna", "Konta bez osoby"]);
  });
});
