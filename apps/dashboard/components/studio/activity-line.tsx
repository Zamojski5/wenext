import { Icon } from "./icons";
import type { ReturnView } from "../../lib/returns-schema";
import { presenceHref } from "../../lib/routes";
import { moment } from "../../lib/studio-labels";

/**
 * Jedna linia nad polem rozmowy: co się tu działo, odkąd Cię nie było (element E-H).
 *
 * SKĄD SIĘ WZIĘŁA. Referencja ramy ma nad polem linię „• Obserwuje · 3 nowe działania ↗ 23:12",
 * czyli stan i jedną liczbę, a nie raport. My mieliśmy raport: blok „Od Twojej ostatniej wizyty"
 * z licznikami obudzeń stał na WEJŚCIU do projektu i był jednym z powodów, dla których audyt UX
 * nazwał ten ekran administracyjnym. Zdjęliśmy go w E2 i przenieśliśmy na „Aktywność"; ta linia
 * jest tym, co z niego zostaje w miejscu, gdzie człowiek zaczyna pisać: jedno zdanie i odsyłacz.
 *
 * KAŻDY CZŁON JEST Z DANYCH, ŻADEN Z WYOBRAŹNI:
 *
 *   * stan bierze się z tego, czy w oknie było cokolwiek („Obserwuję" kontra „Cicho");
 *   * liczba to obudzenia Z SESJĄ plus publikacje, czyli rzeczy, które NAPRAWDĘ się wydarzyły.
 *     Obudzenia pominięte („nic nowego") świadomie nie wchodzą: to jest praca kolektora, nie
 *     nowe działanie, a licznik, który je liczy, mówi „trzy nowe działania" o nocy, w której nic
 *     się nie stało;
 *   * godzina to ostatnie zdarzenie, jakie widzimy w oknie, a nie „teraz".
 *
 * CISZA MÓWI, ŻE JEST CISZA. Linia nie znika, gdy nic się nie działo, bo zniknięcie czyta się
 * jak „nie wiadomo", a to jest inna odpowiedź niż „nic się nie działo" (ZAKRES-FUNKCJONALNY §6).
 */
export function ActivityLine({
  view,
  workspaceId,
  projectId,
}: {
  /** Widok powrotu albo `undefined`, gdy odczyt się nie udał: wtedy nie zgadujemy. */
  readonly view?: ReturnView;
  readonly workspaceId: string;
  readonly projectId: string;
}) {
  if (view === undefined) return null;

  const dzialania = view.obudzenia.z_sesja + view.publikacje.length;
  const noWakeups = view.obudzenia.razem === 0 && dzialania === 0;
  const ostatnie =
    view.publikacje.map((one) => one.opublikowano).filter((one): one is string => one !== null).at(-1) ??
    view.sesje.map((one) => one.skonczono).filter((one): one is string => one !== null).at(-1) ??
    null;

  return (
    <p className="st-activity wm-activity" data-activity={dzialania}>
      <span aria-hidden="true" className="wm-dot st__dot" data-state={dzialania > 0 ? "active" : "paused"} />
      <span>{dzialania > 0 ? "Praca w tle" : noWakeups ? "W tle" : "Cicho"}</span>
      <span aria-hidden="true">·</span>
      {/* Liczba mnoga odmieniana, bo „3 nowe działania" pisze człowiek, a „3 nowych działań"
          maszyna. Zero mówi się słowem: „nic nowego" czyta się od razu, a „0 działań" każe
          liczyć. */}
      <a href={presenceHref(workspaceId, projectId)}>
        {noWakeups ? "brak uruchomień" : dzialania === 0 ? "nic nowego" : `${dzialania} ${dzialan(dzialania)}`}
        <Icon name="arrow-up-right" />
      </a>
      {ostatnie === null ? null : <time dateTime={ostatnie}>{moment(ostatnie).slice(-5)}</time>}
    </p>
  );
}

/** „1 nowe działanie", „3 nowe działania", „5 nowych działań". */
function dzialan(count: number): string {
  if (count === 1) return "nowe działanie";
  const last = count % 10;
  const teen = count % 100 >= 10 && count % 100 < 20;
  return !teen && last >= 2 && last <= 4 ? "nowe działania" : "nowych działań";
}
