/**
 * Pusty projekt pyta, co robimy, i daje pole, które zaczyna rozmowę (D-045).
 *
 * SKĄD TEN KSZTAŁT. Założyciel 20.09 wskazał prototyp `docs/produkt/prototyp/` jako referencję
 * i aplikację Codex jako układ pracy: projekt bez otwartej rozmowy pokazuje pytanie z nazwą
 * projektu, jedną linię stanu i pole do pisania z podpisem silnika. Stąd nagłówek „Co robimy
 * w …?", stąd „Nadaj kierunek albo zapytaj o postępy" i stąd okrągły przycisk wysyłki. Tokeny
 * są NASZE (`studio.css`, D-040), bo prototyp jest jasny, a Studio ciemne; kopiowanie jego CSS
 * jeden do jednego wniosłoby drugą paletę.
 *
 * P1 shipped this disabled, with a sentence saying P2 would make it run real work. This is that
 * change, and the honesty it replaced is kept rather than dropped: the field is enabled when a
 * session can actually start and DISABLED WITH THE REASON when it cannot. The failure this
 * avoids is specific: a box that accepts a goal and answers nothing looks exactly like a product
 * that is thinking about it.
 *
 * WHEN IT IS DISABLED, AND WHY EACH CASE IS SEPARATE:
 *
 *   * the project is paused, so the person stopped the agent here, and a field that started work
 *     anyway would make that switch meaningless;
 *   * there is no Codex connection, so the session would park on its first turn with nothing to
 *     run on. Better to say it before the goal is typed than after.
 *
 * A connection is NOT chosen for the person when several exist. The picker lists them and the
 * first one is preselected, because a session silently bound to whichever connection happened to
 * be first is a session whose quota and identity nobody chose.
 */
import type { ReactNode } from "react";

import { communitiesHref, knowledgeHref } from "../../lib/routes";
import { modeLabel } from "../../lib/studio-labels";
import { Icon } from "./icons";
import { Pop } from "./pop";

export interface ConnectionChoice {
  readonly id: string;
  readonly label: string;
}

/** „3 rozmowy", „1 rozmowa", „5 rozmów". Odmiana, bo „3 rozmów" zdradza, że pisała maszyna. */
function rozmowy(count: number): string {
  if (count === 1) return "1 rozmowa";
  const last = count % 10;
  const teen = count % 100 >= 10 && count % 100 < 20;
  return !teen && last >= 2 && last <= 4 ? `${count} rozmowy` : `${count} rozmów`;
}

export function Composer({
  projectName,
  workspaceId,
  projectId,
  returnTo,
  connections,
  paused,
  mode,
  conversationCount = 0,
  initialGoal = "",
  meta,
  children,
  activity,
}: {
  readonly projectName: string;
  readonly workspaceId: string;
  readonly projectId: string;
  readonly returnTo: string;
  readonly connections: readonly ConnectionChoice[];
  readonly paused: boolean;
  /** Tryb delegacji projektu. Jedna linia stanu nad polem mówi go wprost, jak w prototypie. */
  readonly mode?: string;
  /** Ile rozmów ma ten projekt. Zero to normalny pierwszy dzień, nie usterka. */
  readonly conversationCount?: number;
  readonly initialGoal?: string;
  /**
   * Cicha linia pod tytułem, z referencji: konta projektu i ich wybór (E2).
   *
   * Slot, a nie własna wiedza tego komponentu: pytanie „w czyim imieniu agent się odezwie" ma
   * tu SWOJE MIEJSCE, a kto na nie odpowiada, wie strona.
   */
  readonly meta?: ReactNode;
  /**
   * Treść kolumny MIĘDZY tytułem a polem: dziś lista rozmów projektu.
   *
   * Slot, bo pole rozmowy stoi na DOLE kolumny (`wm-bottom` z `margin-top: auto`, jak w
   * referencji), więc wszystko, co ma być nad nim, musi być dzieckiem tej samej kolumny. Do
   * 21.09 lista stała jako osobna sekcja POD polem i kolumna nie miała dna: pole wisiało tuż
   * pod tytułem, a lista pod nim (odbiór 1, różnica nr 2).
   */
  readonly children?: ReactNode;
  /**
   * Linia NAD polem: co się tu działo, odkąd człowieka nie było (E-H, referencja ramy).
   *
   * Slot, nie własna wiedza tego komponentu, z tego samego powodu, co `meta`: pole rozmowy wie,
   * jak przyjąć zdanie, a nie co się działo w projekcie.
   */
  readonly activity?: ReactNode;
}) {
  /**
   * WSTRZYMANY PROJEKT BLOKUJE POLE. BRAK SILNIKA JUŻ NIE (E1, audyt UX 20.09).
   *
   * Do 20.09 pole było wyłączone także bez połączenia Codex, ze zdaniem odsyłającym na inny
   * ekran. Audyt nazwał to blokadą startu: człowiek, który przyszedł napisać, po co ten projekt
   * jest, dostawał najpierw zadanie administracyjne, a jego zdanie nie miało gdzie trafić.
   *
   * Dziś cel zapisuje się BEZ silnika. Rozmowa idzie w oczekiwanie z kartą SILNIK, a tura rusza
   * sama, gdy połączenie przejdzie w gotowe (`sessions/handlers.py::_waits_for_engine`) — ten sam
   * mechanizm, co przy limicie konta. Dlatego to, co zostało tutaj, to jedno zdanie o tym, że
   * praca zacznie się później, a nie odmowa przyjęcia zdania.
   *
   * Wstrzymanie zostaje blokadą, bo jest decyzją człowieka o tym projekcie, a nie brakiem
   * zasobu, który sam się pojawi.
   */
  const blocked = paused
    ? "Projekt jest wstrzymany. Wznów go w ustawieniach, żeby powierzyć agentowi cel."
    : null;
  const withoutEngine = connections.length === 0;

  // Jedna linia stanu: co projekt robi i ile już rozmów. „Wstrzymany" wygrywa nad trybem, bo
  // tryb wstrzymanego projektu opisuje pracę, której nikt nie wykonuje.
  const state = [
    paused ? "Wstrzymany" : mode === undefined ? null : modeLabel(mode),
    conversationCount === 0 ? "jeszcze bez rozmów" : rozmowy(conversationCount),
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");

  return (
    /*
     * OBIE NAZWY KLAS, tak jak w pasku (E2, pozycja 8 listy różnic). `st-start` zostaje, bo po
     * niej rozpoznają ten blok testy i arkusz Studia; `wm-chat` włącza miarę kolumny, odstęp od
     * góry i stopień pisma nagłówka Z REFERENCJI, czyli stąd, skąd bierze je cała rama.
     *
     * RÓŻNICA ZNACZNIKA, ŚWIADOMA: w referencji `wm-composer` jest FORMULARZEM, a u nas
     * formularz niesie jeszcze pola ukryte i cel wysyłki, więc pudełkiem jest `div` wokół niego
     * i to on dostaje tę nazwę. Reguła opisuje pudełko (tło, promień, wypełnienie, cień), więc
     * ma trafić w element, który tym pudełkiem NAPRAWDĘ jest.
     */
    <section className="st-start wm-chat" data-block="start">
      {/* Pytanie z nazwą projektu, nie „Nowa rozmowa": po D-045 kontekst należy do projektu, a
          to zdanie jest jedynym miejscem, w którym widać, w czyim imieniu się pisze. */}
      <h1 className="st-start__ask">{`Co robimy w ${projectName}?`}</h1>
      <p className="st-start__state" data-conversations={conversationCount}>
        {state}
      </p>
      {meta}

      {/* Co ma stać między tytułem a polem. Pusto jest normalnym pierwszym dniem. */}
      {children}

      {/* DÓŁ KOLUMNY, NIE MIEJSCE POD TYTUŁEM. `wm-bottom` ma `margin-top: auto`, więc pole
          zjeżdża na dno kolumny dokładnie tak, jak w referencji, a to, co nad nim, dostaje całą
          resztę wysokości. */}
      <div className="st-start__bottom wm-bottom">
        {activity}
      {blocked !== null ? (
        <div className="st-composer wm-composer" data-kind="blocked">
          <textarea
            aria-describedby="composer-state"
            aria-label={`Nowa rozmowa w projekcie ${projectName}`}
            disabled
            name="goal"
            placeholder="Nadaj kierunek albo zapytaj o postępy…"
            readOnly
            rows={2}
          />
          {/* No spinner, no "agent myśli". The sentence names what is missing, so a person can
              tell a plan from a bug without reading the code. */}
          <div className="st-composer__bar wm-composerbar" id="composer-state">
            <span className="st-note">{blocked}</span>
          </div>
        </div>
      ) : (
        <div className="st-composer wm-composer">
          <form action="/studio/session" className="st-form" method="post">
            <input name="action" type="hidden" value="start" />
            <input name="workspace_id" type="hidden" value={workspaceId} />
            <input name="project_id" type="hidden" value={projectId} />
            <input name="return_to" type="hidden" value={returnTo} />
            <textarea
              aria-label={`Nowa rozmowa w projekcie ${projectName}`}
              maxLength={4000}
              name="goal"
              defaultValue={initialGoal}
              placeholder="Nadaj kierunek albo zapytaj o postępy…"
              required
              rows={2}
            />
            {/* PASEK POLA MA TO, CO MA REFERENCJA, I NIC WIĘCEJ (odbiór 1, różnica nr 2).
                Zniknęły dwa chipy: projekt (jego nazwa stoi w pytaniu nad polem i w okruszkach)
                i tryb (stoi w prawym górnym rogu ramy). Chip powtarzający to, co widać dwa razy
                wyżej, nie mówi nic nowego, a zabiera miejsce zdaniu, które mówi. */}
            <div className="st-composer__bar wm-composerbar">
              {/* Kółko „+" z referencji, i JEST PRAWDZIWE: otwiera to, co projekt naprawdę ma,
                  czyli wejścia do Wiedzy i Społeczności. Gdyby nie robiło nic, byłoby kłamstwem
                  ekranu, więc w takiej postaci by tu nie stanęło. */}
              <Pop
                frame="wm-circle"
                label="Dodaj kontekst do rozmowy"
                name="composer-context"
                side="left"
                title="Kontekst projektu"
              >
                <ul>
                  <li>
                    <a className="st__item" href={knowledgeHref(workspaceId, projectId)}>
                      <span>Wiedza projektu</span>
                    </a>
                  </li>
                  <li>
                    <a className="st__item" href={communitiesHref(workspaceId, projectId)}>
                      <span>Społeczności</span>
                    </a>
                  </li>
                </ul>
              </Pop>
              {withoutEngine ? (
                // Bez połączenia nie ma czego wybierać, a chip nadal ma powiedzieć prawdę o tym,
                // na czym ta rozmowa ruszy. Pole `codex_connection_id` nie jedzie wcale: handler
                // czyta puste jako „brak", a wysłanie pustego stringa byłoby zdaniem o wyborze,
                // którego nikt nie dokonał.
                <span className="st-engine-pick" data-chip="account" data-engine="brak">
                  Codex: brak połączenia
                  <Icon name="chevron-down" />
                </span>
              ) : connections.length === 1 ? (
                <>
                  <input name="codex_connection_id" type="hidden" value={connections[0]!.id} />
                  {/* Chevron przy nazwie silnika, jak w referencji (odbiór 2, nr 2): mówi, że
                      to się otwiera, nawet gdy dziś jest jedno połączenie i wybór jest pusty. */}
                  <span className="st-engine-pick" data-chip="account">
                    {`Codex · ${connections[0]!.label}`}
                    <Icon name="chevron-down" />
                  </span>
                </>
              ) : (
                // Kilka połączeń: wybór zostaje wyborem, ale wygląda jak tekst z referencji, a
                // nie jak pole formularza. `select` niesie tu prawdziwą decyzję (na czyim limicie
                // pójdzie tura), więc nie zamieniamy go na napis.
                <label className="st-engine-pick" data-chip="account">
                  <span className="st-chip__label">Codex</span>
                  <select className="st-chip__select" name="codex_connection_id">
                    {connections.map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.label}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {/* STRZAŁKA JAKO IKONA, nie znak z klawiatury (odbiór 2, nr 3): „↑" ma inną
                  grubość, inną wysokość i inny środek niż `arrow-up` 16 px z referencji.
                  Przycisk pola PROJEKTU jest jasny ZAWSZE, bo tak ma makieta ramy; gaszenie przy
                  pustym polu jest zasadą makiety ROZMOWY i zostaje tam. */}
              <button aria-label="Zacznij rozmowę" className="st-send wm-send" type="submit">
                <Icon name="arrow-up" />
              </button>
            </div>
            {/* Obietnica składana w chwili pisania, a nie po wysłaniu: człowiek ma wiedzieć
                ZANIM naciśnie, że praca zacznie się później, i że nie będzie musiał pisać tego
                drugi raz. Karta SILNIK w rozmowie prowadzi go dalej. */}
            {withoutEngine ? (
              <p className="st-note" data-engine="brak">
                Zapiszę cel i zacznę, gdy podłączysz Codexa. Połączenie znajdziesz na karcie w tej
                rozmowie.
              </p>
            ) : null}
          </form>
        </div>
      )}
      </div>
    </section>
  );
}
