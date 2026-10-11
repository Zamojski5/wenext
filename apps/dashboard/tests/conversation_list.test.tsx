/**
 * Lista rozmów na stronie projektu (D-045).
 *
 * TRZY STANY, NIE DWA, i to jest cały ten plik. „Nie udało się odczytać", „sprawdzone, nie ma"
 * i „oto lista" to trzy różne zdania; produkt, który dwa pierwsze pokazuje tak samo, mówi
 * człowiekowi, że projekt jest pusty, za każdym razem, gdy control plane nie odpowie.
 *
 * Czwarty przypadek jest o tym, PO CO ta lista istnieje obok paska: pasek daje tytuł i datę,
 * a tutaj stoi jedno zdanie wyniku, czyli jedyna rzecz, po której da się wybrać rozmowę bez
 * otwierania ośmiu.
 */
import { describe, expect, it } from "vitest";

import { ConversationList } from "../components/studio/conversation-list";
import { PROJECT_ID, SESSION_ID, WORKSPACE_ID, session } from "./studio-fixtures";
import { renderToDocument } from "./render";

function render(props: Parameters<typeof ConversationList>[0]) {
  return renderToDocument(<ConversationList {...props} />);
}

const BASE = { workspaceId: WORKSPACE_ID, projectId: PROJECT_ID } as const;

describe("rozmowy projektu", () => {
  it("a_failed_read_is_not_shown_as_no_conversations", () => {
    const rendered = render({ ...BASE, reason: "control plane odpowiedział 503" });
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("Nie udało się odczytać rozmów");
    expect(text).toContain("To nie znaczy, że ich nie ma");
    expect(text).not.toContain("nie ma jeszcze żadnej rozmowy");
  });

  it("a_project_with_no_conversations_points_at_the_field_above", () => {
    const rendered = render({ ...BASE, conversations: [] });
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("nie ma jeszcze żadnej rozmowy");
    // Kolejna rozmowa zaczyna od kontekstu projektu i to jest cała różnica po D-045.
    expect(text).toContain("od tego, co projekt już wie");
    expect(rendered.querySelector("[data-tone='absent']")).toBeNull();
  });

  it("every_row_links_to_its_own_conversation", () => {
    const rendered = render({
      ...BASE,
      conversations: [
        session({ tytul: "Odzew w r/devops" }),
        session({ session_id: "11111111-0000-4000-8000-000000000002", tytul: "Plan" }),
      ],
    });
    const rows = Array.from(rendered.querySelectorAll("a.st-row"));
    expect(rows.length).toBe(2);
    expect(rows[0]?.getAttribute("href")).toBe(
      `/w/${WORKSPACE_ID}/p/${PROJECT_ID}/c/${SESSION_ID}`,
    );
  });

  it("the_result_sentence_is_what_this_list_adds_over_the_sidebar", () => {
    const rendered = render({
      ...BASE,
      conversations: [
        session({ tytul: "Odzew", wynik_zdanie: "Zmieniliśmy porę publikacji." }),
        session({ session_id: "11111111-0000-4000-8000-000000000002", tytul: "W toku" }),
      ],
    });
    const rows = Array.from(rendered.querySelectorAll("a.st-row"));
    expect(rows[0]?.querySelector("small")?.textContent).toBe("Zmieniliśmy porę publikacji.");
    // Rozmowa, która jeszcze nic nie powiedziała, MILCZY. „Brak wyniku" przy rozmowie w toku
    // czyta się jak usterka, a jest zwykłym stanem pierwszych minut.
    expect(rows[1]?.querySelector("small")).toBeNull();
  });

  it("a_closed_conversation_is_marked_as_closed_without_being_hidden", () => {
    // Po D-045 do zamkniętej rozmowy się nie wraca po to, żeby ją ciągnąć, ale nadal się ją
    // czyta: tam jest wynik. Ukrycie jej byłoby skasowaniem historii projektu z ekranu.
    const rendered = render({
      ...BASE,
      conversations: [session({ status: "DONE" }), session({ session_id: "x", status: "RUNNING" })],
    });
    const rows = Array.from(rendered.querySelectorAll("a.st-row"));
    expect(rows.map((row) => row.getAttribute("data-over"))).toEqual(["1", "0"]);
  });

  it("nothing_read_and_nothing_refused_renders_nothing", () => {
    // Ekran, który nie pytał o rozmowy, nie ma o nich nic do powiedzenia.
    const rendered = render({ ...BASE });
    expect(rendered.querySelector("a.st-row")).toBeNull();
    expect(rendered.body.textContent).not.toContain("nie ma jeszcze żadnej rozmowy");
  });
});
