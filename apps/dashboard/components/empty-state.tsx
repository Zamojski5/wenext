/**
 * The two ways a screen can have nothing to show, said out loud and kept apart.
 *
 *   | `measured` | headline           | fact                                              |
 *   | ---------- | ------------------ | ------------------------------------------------- |
 *   | `true`     | "brak danych"      | the read happened and found nothing               |
 *   | `false`    | "brak odpowiedzi"  | there was no read; nothing is known               |
 *
 * That line is the same one the gates draw between `NO_GO` and `NIE_URUCHOMIONO`
 * (docs/KONWENCJE.md), and L5241 point 5 states it for this layer: "Brak danych nigdy nie jest
 * zamieniany na zero; jest UNKNOWN z powodem." A failed read collapsed into an empty list is a
 * broken screen that looks like a quiet Tuesday.
 *
 * TWO THINGS THIS COMPONENT MAY NEVER DO:
 *
 *   1. dress an empty answer as a good one — no tick, no green, no "wszystko w porządku", no
 *      "0 błędów". An empty collection reported as success is `all([]) == True` (D-011) drawn in
 *      HTML, and this project has already paid for that mistake three times;
 *   2. show a placeholder row. Mock data on this screen is forbidden outright (D-013): the first
 *      real rows arrived with the queue, and nobody should have to guess which of the two they
 *      are looking at.
 *
 * The forbidden vocabulary is asserted string by string in `tests/empty_state.test.tsx`, which
 * also feeds the scanner a screen that IS lying, to prove the scanner catches anything at all.
 *
 * VISUALLY (docs/DASHBOARD-DESIGN.md §2 pkt 7): a quiet card with a small uppercase headline.
 * The measured empty is drawn in neutral ink; the failed read gets the amber edge of "absence" —
 * the one colour this dashboard uses for something the operator must not skim past.
 */
interface EmptyStateProps {
  /** What is empty, in the operator's words (e.g. "Kolejka jobów"). */
  readonly what: string;
  /**
   * `true` — the answer was empty. `false` — there was no answer. Never derive this from the
   * length of anything: that is the substitution the whole component exists to prevent.
   */
  readonly measured: boolean;
  /**
   * Why the read failed, when it failed. REQUIRED in effect: `EntityScreen` always passes one,
   * and an absence with no stated reason is the silence this repository treats as a false pass.
   */
  readonly reason?: string;
}

export function EmptyState({ what, measured, reason }: EmptyStateProps) {
  if (!measured) {
    return (
      <section className="state state--unread" data-state="brak odpowiedzi">
        <p className="state__headline">brak odpowiedzi</p>
        <p className="state__detail">
          {what}: nie udało się odczytać stanu. To nie jest lista pusta, tylko brak wyniku, więc nie
          wolno czytać tego ekranu jako potwierdzenia czegokolwiek.
        </p>
        <p className="state__reason">
          {reason === undefined || reason === ""
            ? "powód nie został podany, a to samo w sobie jest defektem tego ekranu"
            : `Powód: ${reason}`}
        </p>
      </section>
    );
  }

  return (
    <section className="state state--empty" data-state="brak danych">
      <p className="state__headline">brak danych</p>
      <p className="state__detail">
        {what}: odczyt się udał i nie zwrócił ani jednego wiersza. Pusta lista niczego nie
        potwierdza. Wiersze pojawiają się tu dopiero wtedy, gdy zapisze je działająca kolejka albo
        ścieżka deweloperska (D-013), nigdy dlatego, że ekran je wymyślił.
      </p>
    </section>
  );
}
