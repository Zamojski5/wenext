/**
 * Panel „Głos” (0022): co widzi człowiek, zanim sesja napisze cokolwiek w czyimś imieniu.
 *
 * CZEGO PILNUJĄ TE TESTY, i każdy odpowiada innemu sposobowi zrobienia komuś krzywdy:
 *
 *   * brak głosu wygląda jak głos pusty, więc nikt go nie uzupełnia, a agent pisze bez niego;
 *   * treść profilu pilota trafia do formularza zapisu i jedno kliknięcie „Zapisz” robi z niej
 *     głos innej osoby, bez decyzji, że to ten sam człowiek;
 *   * zapis wygląda na poprawkę, więc nikt nie wie, że poprzednia wersja została;
 *   * nieudany odczyt głosu wygląda jak profil bez głosu.
 */
import { describe, expect, it } from "vitest";

import { VoicePanel } from "../components/studio/voice-panel";
import { renderToDocument } from "./render";

const PROFILE = "dddddddd-0000-4000-8000-000000000001";

const VERSION = {
  voice_version_id: "eeeeeeee-0000-4000-8000-000000000001",
  person_profile_id: PROFILE,
  version: 2,
  knows_deeply: "utrzymanie starych systemów",
  positions: "przepisanie od zera rzadko się zwraca",
  would_never_claim: "że zna się na rynkach finansowych",
  voice_samples: "Piszę krótko.",
  created_by: null,
  created_at: "2026-09-19T10:00:00+00:00",
};

const PROPOSAL = {
  user_profile_version_id: "ffffffff-0000-4000-8000-000000000001",
  version: 8,
  knows_deeply: "kolejki i systemy zdarzeniowe",
  positions: "ack jest ważniejszy niż przepustowość",
  would_never_claim: "że zna się na prawie",
  voice_samples: "Krótko i z liczbami.",
  created_at: "2026-09-10T10:00:00+00:00",
};

function render(view: Parameters<typeof VoicePanel>[0]["view"], failure?: string) {
  return renderToDocument(
    <VoicePanel
      failure={failure}
      profileId={PROFILE}
      profileName="Anna"
      returnTo="/w/ws/people"
      view={view}
      workspaceId="ws"
    />,
  );
}

function formsOf(document: ReturnType<typeof render>) {
  return Array.from(document.querySelectorAll("form"));
}

describe("panel głosu osoby", () => {
  it("says_a_person_has_no_voice_instead_of_showing_empty_fields_as_a_voice", () => {
    const rendered = render({ voice: null, history: [], proposal: null });
    expect(rendered.body.textContent).toContain("nie ma jeszcze opisanego głosu");
    // I mówi, co z tego wynika dla agenta, bo to jest powód, żeby go uzupełnić.
    expect(rendered.body.textContent).toContain("dostaje odmowę");
  });

  it("fills_the_form_with_the_current_version_so_one_sentence_can_be_corrected", () => {
    const rendered = render({ voice: VERSION, history: [VERSION], proposal: null });
    const textareas = Array.from(rendered.querySelectorAll("textarea"));
    expect(textareas.length).toBe(4);
    const values = textareas.map((node) => node.textContent);
    expect(values).toContain(VERSION.knows_deeply);
    expect(values).toContain(VERSION.would_never_claim);
    // Zapis tworzy nową wersję i przycisk mówi to wprost.
    expect(rendered.body.textContent).toContain("Zapisz jako nową wersję");
    expect(rendered.body.textContent).toContain("Obowiązuje wersja 2");
  });

  it("keeps_the_pilot_proposal_in_a_form_of_its_own", () => {
    const rendered = render({ voice: null, history: [], proposal: PROPOSAL });
    const forms = formsOf(rendered);
    expect(forms.length).toBe(2);
    const proposal = forms.find((form) => form.getAttribute("data-voice-proposal") === "pilot");
    expect(proposal, "propozycja musi być osobnym formularzem").toBeDefined();
    // Treść pilota NIE jest wpisana w pola formularza zapisu: tam zostają puste pola, więc
    // kliknięcie „Zapisz" nie zapisze cudzego głosu bez decyzji człowieka.
    const textareas = Array.from(rendered.querySelectorAll("textarea"));
    expect(textareas.map((node) => node.textContent)).toEqual(["", "", "", ""]);
    expect(proposal?.textContent).toContain("Przejmij treść profilu pilota");
    expect(proposal?.textContent).toContain("wersja 8");
    expect(rendered.body.textContent).toContain("Nic się nie zapisze, dopóki nie klikniesz");
  });

  it("offers_nothing_to_take_over_when_the_person_already_has_a_voice", () => {
    const rendered = render({ voice: VERSION, history: [VERSION], proposal: null });
    expect(formsOf(rendered).length).toBe(1);
    expect(rendered.body.textContent).not.toContain("Przejmij treść profilu pilota");
  });

  it("a_failed_read_says_so_and_is_not_shown_as_a_person_without_a_voice", () => {
    const rendered = render(undefined, "control plane odpowiedział 503");
    expect(rendered.body.textContent).toContain("Nie udało się odczytać głosu");
    expect(rendered.body.textContent).toContain("503");
    expect(rendered.body.textContent).not.toContain("nie ma jeszcze opisanego głosu");
  });

  it("shows_the_history_once_there_is_more_than_one_version", () => {
    const older = { ...VERSION, voice_version_id: "older", version: 1 };
    const rendered = render({ voice: VERSION, history: [VERSION, older], proposal: null });
    expect(rendered.body.textContent).toContain("Historia głosu (2)");
  });
});
