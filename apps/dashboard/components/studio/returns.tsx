import { softenDashes } from "../../lib/conversation-render";
import type { ReturnView } from "../../lib/returns-schema";
import { moment } from "../../lib/studio-labels";
import { publicationKindLabel, publicationStatusLabel } from "../../lib/workspace-schema";

/**
 * What a person calls each part of the product that can stop. The keys are the control plane's
 * vocabulary (`ops/health.py::COMPONENTS`); an unknown one is shown as it came, never dropped.
 * 27.09: the screen said "DISPATCHER: brak pulsu od 2026-09-26T22:35:00+00:00 ..." about a gap
 * that had already closed, and read like an outage in progress.
 */
const COMPONENT_NAMES: Readonly<Record<string, string>> = {
  DISPATCHER: "Kolejka pracy agenta",
  COLLECTOR: "Czytanie społeczności",
  PUBLISHER: "Publikowanie",
  CAPSULE: "Przeglądarka agenta",
  CODEX_CONNECTION: "Połączenie z silnikiem",
};

/**
 * „Od Twojej ostatniej wizyty": co się wydarzyło, gdy człowieka nie było (P4 i P5).
 *
 * TO NIE JEST PODSUMOWANIE OD MODELU. Każda liczba tutaj pochodzi z wiersza w bazie, a każdy
 * wiersz ma odnośnik do swojego źródła: sesji, publikacji, materiału. Ładne zdanie napisane
 * przez model byłoby DRUGĄ wersją tego, co się stało, i to tej ładniejszej człowiek by uwierzył.
 *
 * KOLEJNOŚĆ BLOKÓW JEST KOLEJNOŚCIĄ PILNOŚCI, nie kolejnością tabel: najpierw to, co CZEKA na
 * decyzję człowieka, potem to, co agent zrobił, potem skutki w świecie, na końcu praca maszyny
 * i awarie. Człowiek, który ma trzydzieści sekund, przeczyta pierwszy blok i to wystarczy.
 *
 * CISZA MA DWIE POSTACIE I OBIE SĄ TU NAZWANE. „Kolektor pracował N razy i nie było o czym
 * pisać" i „Nie uruchomiono agenta" to różne obserwacje. Brak obudzeń nie dowodzi awarii. Zero bez tego
 * rozróżnienia uspokaja, i dlatego zdania o ciszy przychodzą z backendu jako `uwagi`, policzone,
 * a nie dopisane tutaj do pustej listy.
 */
/**
 * What the counter is called where the publication lives: X counts likes, Reddit counts points.
 * The row carries only the address, and the address names the platform (27.09: an X post read
 * "punkty: 0" on the presence screen).
 */
function scoreWord(address: string): string {
  try {
    const host = new URL(address).hostname.replace(/^www\./, "");
    return host === "x.com" || host === "twitter.com" ? "polubienia" : "punkty";
  } catch {
    return "punkty";
  }
}

export function ReturnsBlock({
  autonomous = false,
  projectId,
  reason,
  returnTo,
  view,
  workspaceId,
}: {
  readonly autonomous?: boolean;
  readonly projectId: string;
  readonly reason?: string;
  readonly returnTo: string;
  readonly view?: ReturnView;
  readonly workspaceId: string;
}) {
  if (reason !== undefined) {
    return (
      <section className="st__section" data-block="powrot">
        <div className="st__section-head">Od Twojej ostatniej wizyty</div>
        <p className="st-tag" data-tone="absent">
          Nie udało się policzyć, co się wydarzyło: {reason}. To nie znaczy, że nic się nie
          wydarzyło.
        </p>
      </section>
    );
  }
  if (view === undefined) return null;

  const { okno, obudzenia, sesje, decyzje, publikacje, korekty, incydenty, uwagi } = view;
  const presenceHref = `/w/${workspaceId}/p/${projectId}/presence`;

  return (
    <section className="st__section" data-block="powrot" data-window-first={okno.pierwsza_wizyta}>
      <div className="st__section-head">Od Twojej ostatniej wizyty</div>
      <p className="st-note" data-window-sentence="1">
        {okno.zdanie}
      </p>

      {uwagi.map((note) => (
        <p className="st-tag" data-tone={obudzenia.razem === 0 ? "absent" : "state"} key={note}>
          {note}
        </p>
      ))}

      {decyzje.length > 0 ? (
        <div className="st-surface" data-part="decyzje">
          {decyzje.map((decision) => (
            <div className="st-row" data-decision={decision.revision_id} key={decision.revision_id}>
              <span className="st-row__body">
                <strong>{`${autonomous ? "Przygotowany szkic" : "Czeka na Twoją decyzję"} od ${moment(decision.czeka_od)}`}</strong>
                <small>
                  {`${decision.konto}, zgoda wygasa ${moment(decision.wygasa)}. `}
                  {decision.tekst === null ? "Treść propozycji jest w Obecności." : decision.tekst}
                  {decision.obraz ? " Z obrazem: zrzut ekranu źródła." : null}
                </small>
              </span>
              {/* Decyzja zapada w Obecności i tylko tam: dwa przyciski do jednej zgody to dwa
                  miejsca, w których można ją wydać przez pomyłkę. */}
              <a className="st-btn" data-kind="primary" href={autonomous ? `/w/${workspaceId}/p/${projectId}/publications#draft-${decision.revision_id}` : presenceHref}>
                {autonomous ? "Zobacz szkic" : "Rozstrzygnij"}
              </a>
            </div>
          ))}
        </div>
      ) : null}

      {sesje.length > 0 ? (
        <div className="st-surface" data-part="sesje">
          {sesje.map((session) => (
            <div className="st-row" data-session={session.session_id} key={session.session_id}>
              <span className="st-row__body">
                <strong>{session.wynik_zdanie ? softenDashes(session.wynik_zdanie) : session.tytul ?? "Rozmowa z agentem"}</strong>
                <small>{moment(session.zaczeto)}</small>
              </span>
              <a className="st-btn" data-kind="quiet" href={`/w/${workspaceId}/p/${projectId}/c/${session.session_id}`}>
                Otwórz rozmowę
              </a>
            </div>
          ))}
        </div>
      ) : null}

      {publikacje.length > 0 ? (
        <div className="st-surface" data-part="publikacje">
          {publikacje.map((publication) => (
            <div
              className="st-row"
              data-publication={publication.request_id}
              key={publication.request_id}
            >
              <span className="st-row__body">
                {/* Najpierw CO to było, potem co się z tym stało: „nasz wątek, PUBLISHED" czyta
                    się jak zdanie, a sam status nie mówi, czy agent założył coś swojego. */}
                <strong>{`${publicationKindLabel(publication.rodzaj)}: ${publicationStatusLabel(publication.status)}`}</strong>
                <small>
                  {publication.widocznosc === null
                    ? publication.opublikowano === null
                      ? publication.status === "BLOCKED" && publication.powod?.includes("submit_started=false")
                        ? "Zatrzymano przed wysłaniem. Nic nie opublikowano."
                        : "Brak potwierdzonej publikacji."
                      : "Widoczności nikt jeszcze nie sprawdzał. To nie znaczy, że wpis zniknął."
                    : `Sprawdzone ${moment(publication.widocznosc.sprawdzono)}: ${
                        publication.widocznosc.jest ? "jest na miejscu" : "nie ma go już"
                      }, odpowiedzi: ${publication.widocznosc.odpowiedzi}${
                        publication.widocznosc.punkty === null
                          ? ""
                          : `, ${scoreWord(publication.adres)}: ${publication.widocznosc.punkty}`
                      }${
                        publication.widocznosc.wyswietlenia === null
                          ? ""
                          : `, wyświetlenia: ${publication.widocznosc.wyswietlenia}`
                      }`}
                  {publication.powod === null ? "" : ` (${publication.powod})`}
                </small>
              </span>
              <a className="st-btn" data-kind="quiet" href={publication.adres} rel="noreferrer">
                Otwórz miejsce publikacji
              </a>
            </div>
          ))}
        </div>
      ) : null}

      {korekty.length > 0 ? (
        <div className="st-surface" data-part="korekty">
          {korekty.map((correction) => (
            <div className="st-row" data-correction={correction.item_id} key={correction.item_id}>
              <span className="st-row__body">
                <strong>{correction.tresc}</strong>
                <small>
                  {`Twoja korekta z ${moment(correction.zapisano)}. `}
                  {/* Dokładnie to, co widać w bazie, i ani słowa więcej: że po zapisaniu ruszyła
                      sesja, która pytała o pamięć. Że agent POSŁUCHAŁ, tego stąd nie widać. */}
                  {correction.w_zasiegu_sesji
                    ? "Sesja po niej pytała o pamięć, więc miała ją przed sobą."
                    : "Nie odnotowano późniejszego odczytu pamięci. To nie rozstrzyga, czy agent wykorzystał korektę z rozmowy."}
                </small>
              </span>
            </div>
          ))}
        </div>
      ) : null}

      <p className="st-note" data-part="obudzenia">
        {obudzenia.razem === 0
          ? "Obudzeń: 0."
          : `Obudzeń: ${obudzenia.razem}, z tego z sesją: ${obudzenia.z_sesja}, pominiętych: ${obudzenia.pominiete}.`}
        {Object.keys(obudzenia.powody).length > 0
          ? ` Powody: ${Object.entries(obudzenia.powody)
              .map(([reasonName, count]) => `${reasonName} ${count}`)
              .join(", ")}.`
          : ""}
        {Object.keys(obudzenia.powody_pominiecia).length > 0
          ? ` Pominięte, bo: ${Object.entries(obudzenia.powody_pominiecia)
              .map(([reasonName, count]) => `${reasonName} ${count}`)
              .join(", ")}.`
          : ""}
      </p>

      {incydenty.length > 0 ? (
        <div className="st-surface" data-part="incydenty">
          {incydenty.map((incident) => (
            <div className="st-row" data-incident={incident.incident_id} key={incident.incident_id}>
              <span className="st-row__body">
                <strong>
                  {`${COMPONENT_NAMES[incident.komponent] ?? incident.komponent}: ${
                    incident.trwa ? "przerwa trwa" : "przerwa zakończona"
                  }`}
                </strong>
                <small>
                  {incident.trwa
                    ? `Wykryta ${moment(incident.od)}. Agent mógł w tym czasie nie móc nic zrobić.`
                    : `Wykryta ${moment(incident.od)}, praca wróciła ${moment(incident.do ?? incident.od)}.`}
                  {` Szczegół: ${incident.powod}`}
                </small>
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {/* Korekta kierunku jednym zdaniem. Pod podsumowaniem, bo to jest odpowiedź na to, co
          człowiek właśnie przeczytał, a nie kolejne polecenie wpisywane od zera. */}
      <form action="/studio/return" className="st-form" method="post">
        <input name="action" type="hidden" value="correct" />
        <input name="workspace_id" type="hidden" value={workspaceId} />
        <input name="project_id" type="hidden" value={projectId} />
        <input name="return_to" type="hidden" value={returnTo} />
        <label className="st-field">
          <span>Skoryguj kierunek jednym zdaniem</span>
          <input
            className="st-input"
            maxLength={400}
            name="text"
            placeholder="np. nie odpowiadaj w wątkach starszych niż dzień"
            required
            type="text"
          />
        </label>
        {/* ZDANIE O SZEŚCIU SŁOWACH ZNIKŁO 20.09 (E5, audyt UX punkt 5) i nie wróci.
            Mówiło prawdę o mechanizmie: trasa zapisywała korektę jako regułę, gdy ZACZYNAŁA SIĘ
            od „nie", „za", „zamiast", „mniej", „więcej" albo „bez". Reguła po pierwszym słowie
            nie rozumie „skróć to o połowę" ani „trzymaj się r/SaaS", a człowiek dostawał do
            nauczenia listę słów sterujących pamięcią produktu. Mechanizmu już nie ma, więc
            instrukcja byłaby dziś nie tylko zbędna, ale nieprawdziwa: pierwsze słowo nie
            decyduje o niczym. */}
        <p className="st-note">
          Zdanie trafia do bieżącej rozmowy jako Twoja wiadomość. Agent je przeczyta i sam
          zdecyduje, czy zapisać je jako ustalenie projektu; zobaczysz to w rozmowie.
        </p>
        <button className="st-btn" data-kind="primary" type="submit">
          Wyślij korektę
        </button>
      </form>
    </section>
  );
}
