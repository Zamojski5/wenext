/** Contextual help for a resource, not a mandatory onboarding sequence. */
export type FirstRunStep = 1 | 2 | 3 | 4 | 5;
export const FIRST_RUN_TOTAL = 5;
export const FIRST_RUN_SENTENCES: Readonly<Record<FirstRunStep, string>> = {
  1: "Załóż projekt i opowiedz agentowi, co chcesz osiągnąć. Research możesz zacząć bez kont społecznościowych.",
  2: "Profil grupuje konta jednej osoby lub marki. Dodaj je, kiedy agent ma działać w jej imieniu, i przypisz wybrane konta do projektu.",
  3: "Wybierz konta, pod którymi agent ma działać. Research i przygotowanie materiałów są dostępne także bez nich.",
  4: "Połącz Codex, żeby uruchomić agenta. Potem wróć do rozmowy w projekcie i nadaj kierunek.",
  5: "Powierz agentowi stałą pracę w wybranym zakresie. Będzie działał również po zamknięciu dashboardu; tryb z przeglądem możesz wybrać opcjonalnie.",
};
export function FirstRun({ step }: { readonly step: FirstRunStep }) {
  return <p className="st-note" data-first-run={step}>{FIRST_RUN_SENTENCES[step]}</p>;
}
