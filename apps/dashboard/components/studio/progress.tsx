/**
 * Przebieg pracy agenta w bieżącej turze (D-046, makieta `agent-progress-detail.html`).
 *
 * CO TO JEST, A CZYM NIE JEST. To jest projekcja ŚLADU tej tury: co agent zaczął, co skończył,
 * czym się w tej chwili zajmuje. Nie jest planem z góry i nie jest paskiem postępu: licznik
 * liczy etapy ROZPOCZĘTE, więc nigdy nie obiecuje, ile jeszcze zostało. Pasek, który obiecuje
 * „3 z 7", a potem robi dziewięć rzeczy, uczy człowieka, że liczby na tym ekranie nie znaczą nic.
 *
 * RUCH NALEŻY DO STANU, NIE DO ZEGARA. W makiecie animacja jest sterowana czasem, bo makieta
 * nie ma agenta. Tutaj `data-playing` bierze się z tego, czy jakikolwiek etap NAPRAWDĘ trwa, a
 * `data-finished` z tego, czy wszystkie się domknęły. Ekran, który miga, gdy nic nie działa,
 * jest tym, czego zabrania ZAKRES-FUNKCJONALNY §5, i nie ma znaczenia, że wygląda żywo.
 *
 * ROZWIJANIE JEST `details`, BEZ SKRYPTU. Makieta trzyma `data-expanded` w JavaScripcie; tutaj
 * lista etapów jest zwykłym `details`, otwartym, gdy tura trwa. Powód praktyczny: strona jest
 * renderowana na serwerze i przeładowuje się przy każdej akcji, więc stan rozwinięcia trzymany
 * w skrypcie i tak zerowałby się co kliknięcie.
 */
import type { ConversationProgress, ProgressItem } from "../../lib/cards-schema";
import { Icon } from "./icons";

/** Ikona przy podlinii, dobrana do etapu. Z makiety: dokument, kula, skan, rozmowy. */
function sublineIcon(index: number) {
  const names = ["file-text", "globe", "scan-text", "messages-square"] as const;
  return names[index % names.length] as (typeof names)[number];
}

function Item({ item, index }: { readonly item: ProgressItem; readonly index: number }) {
  const state =
    item.stan === "trwa"
      ? "running"
      : item.stan === "zrobione"
        ? "done"
        : item.stan === "blad"
          ? "error"
          : "pending";
  return (
    <li className="ap-item" data-stage={index} data-state={state}>
      <div className="ap-line">
        <span aria-hidden="true" className="ap-status-icon">
          <span className="ap-idle" />
          <span className="ap-spinner" />
          <Icon name="check" />
        </span>
        <span className="ap-label">{item.etykieta}</span>
      </div>
      {item.podlinia === null && item.zrodla.length === 0 ? null : (
        <div className="ap-detail-wrap">
          <div className="ap-detail-clip">
            <div className="ap-details">
              {item.zrodla.length > 0 ? (
                <div className="ap-sources">
                  {item.zrodla.map((source, position) => (
                    <span className="ap-chip" key={source}>
                      {position === 0 ? <Icon name="globe" /> : null}
                      {source}
                    </span>
                  ))}
                </div>
              ) : null}
              {item.podlinia === null ? null : (
                <div className="ap-subline">
                  <Icon name={sublineIcon(index)} />
                  {item.podlinia}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </li>
  );
}

export function AgentProgress({
  progress,
  canWrite = true,
}: {
  readonly progress: ConversationProgress | null;
  /** Czy pole do pisania jest otwarte w czasie pracy. Stopka mówi o tym wprost (makieta). */
  readonly canWrite?: boolean;
}) {
  if (progress === null) return null;

  const running = progress.pozycje.some((item) => item.stan === "trwa");
  const finished = progress.pozycje.length > 0 && !running;
  const { zrobione, wszystkie } = progress.licznik;

  return (
    <section
      aria-label="Przebieg pracy agenta"
      className="ap-progress"
      data-expanded="true"
      data-finished={String(finished)}
      data-playing={String(running)}
    >
      <div className="ap-surface">
        <details className="ap-open" open={running}>
          <summary className="ap-top">
            <span className="ap-trigger">
              <span aria-hidden="true" className="ap-symbol">
                <img alt="" height={658} src="/brand/mark-white.png" width={791} />
              </span>
              <span className="ap-heading" role="status">
                {progress.naglowek}
              </span>
              <Icon name="chevron-right" />
            </span>
            {/* Licznik etapów ROZPOCZĘTYCH. `tabular-nums` z makiety, żeby nie skakał. */}
            <span aria-label="Ukończone etapy" className="ap-count">
              {`${zrobione} / ${wszystkie}`}
            </span>
          </summary>

          <div className="ap-list-wrap">
            <div className="ap-list-clip">
              <ol aria-label="Działania agenta" className="ap-list">
                {progress.pozycje.map((item, index) => (
                  <Item index={index} item={item} key={`${index}-${item.etykieta}`} />
                ))}
              </ol>
            </div>
          </div>
        </details>

        <div className="ap-footer">
          <Icon name="corner-down-right" />
          <span>
            {progress.linia_zdarzenia ??
              (finished
                ? "Praca w tej turze skończona"
                : canWrite
                  ? "Możesz pisać dalej, kiedy pracuję"
                  : "Pracuję")}
          </span>
        </div>
      </div>
    </section>
  );
}
