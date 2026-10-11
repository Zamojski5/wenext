import { StudioShell } from "../../../../../../../components/studio/shell";
import { StudioMissing, StudioUnreachable } from "../../../../../../../components/studio/states";
import { communityCards } from "../../../../../../../lib/community-cards";
import { getProject, getWorkspaceResults } from "../../../../../../../lib/control-plane";
import { projectConversations } from "../../../../../../../lib/conversations";
import { requireOperator } from "../../../../../../../lib/guard";
import { communitiesHref } from "../../../../../../../lib/routes";
import { loadStudioFrame } from "../../../../../../../lib/studio-frame";
import { moment } from "../../../../../../../lib/studio-labels";

/**
 * `GET /w/<workspace>/p/<projekt>/communities` — „Społeczności": gdzie jesteśmy i co tam robimy.
 *
 * TEN EKRAN MÓWI TYM SAMYM JĘZYKIEM, CO ROZMOWA. Karta SPOŁECZNOŚĆ, przycisk „Obserwuj", zdanie
 * o tym, co obserwacja znaczy — wszystko to samo, co widać w rozmowie, bo to jest ta sama rzecz.
 * Ekran z własnym słownikiem („Dołączono" tu, „Obserwuję" tam) był jednym z ustaleń audytu UX.
 *
 * ZAKRES DANYCH, POWIEDZIANY WPROST. `communities` nie ma kolumny projektu (migracja 0004:
 * organizacja i przestrzeń), więc lista jest listą PRZESTRZENI i nagłówek mówi to słowami.
 * Podpisanie jej „społeczności tego projektu" byłoby dokładnie ustaleniem §6 audytu: cudza praca
 * pokazana jak własna. Ekran stoi pod projektem, bo stamtąd się tu wchodzi i tam się wraca; gdy
 * odczyt nauczy się wiązać społeczność z projektem, zmieni się zdanie w nagłówku, nie trasa.
 *
 * `force-dynamic` jak reszta studia: te wiersze zmienia kolejka odczytów, a strona z pamięci
 * podręcznej byłaby drugą opinią o stanie.
 */
export const dynamic = "force-dynamic";

export default async function CommunitiesPage({
  params,
  searchParams,
}: {
  readonly params: Promise<{ readonly workspaceId: string; readonly projectId: string }>;
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { workspaceId, projectId } = await params;
  const parameters = await searchParams;
  const rawInfo = parameters["info"];
  const info = typeof rawInfo === "string" ? rawInfo : undefined;

  const gate = await requireOperator(communitiesHref(workspaceId, projectId));
  if (gate.kind === "unreachable") return <StudioUnreachable reason={gate.reason} />;

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) return <StudioUnreachable reason={frame.reason} />;
  if (frame.value === null) return <StudioMissing what="workspace" />;

  const detail = await getProject(gate.token, workspaceId, projectId);
  if (!detail.ok) return <StudioUnreachable reason={detail.reason} />;
  if (detail.value === null) return <StudioMissing what="projekt" />;
  const project = detail.value.project;
  // Rozmowy TYLKO dla paska: ten ekran ich nie pokazuje, ale pasek ma trzymać
  // kontekst projektu, w którym stoimy (odbiór 1, różnica nr 5).
  const conversations = await projectConversations(gate.token, workspaceId, projectId);

  const results = await getWorkspaceResults(gate.token, workspaceId);
  // Jedno miejsce, w którym lista przestaje być „może null": dalej jest już tablica, więc
  // `map` i `find` nie muszą tego powtarzać przy każdym wierszu.
  const rows = results.ok && results.value !== null ? results.value.communities : [];
  const here = communitiesHref(workspaceId, projectId);

  return (
    <StudioShell
      active={{ kind: "communities", projectId }}
      conversations={conversations}
      crumb={`${frame.value.workspace.name} / ${project.name} / Społeczności`}
      frame={frame.value}
      info={info}
    >
      <h1 className="st__title">Społeczności</h1>
      {/* Podtytuł z nazwą projektu, jak w referencji (odbiór 1, różnica nr 22): tytuł mówi, CO
          to za ekran, podtytuł, CZYJ jest. Bez niego dwa projekty wyglądają na tym ekranie
          identycznie. */}
      <p className="st__subtitle">{project.name}</p>

      {!results.ok ? (
        // Nieudany odczyt to nie pusta lista i tak ma być napisane.
        <p className="st-tag" data-tone="absent">
          Nie udało się odczytać społeczności: {results.reason}
        </p>
      ) : results.value === null ? (
        <StudioMissing what="workspace" />
      ) : rows.length === 0 ? (
        <p className="st-empty">
          Ta przestrzeń nie obserwuje jeszcze żadnej społeczności. Odczyt się udał, po prostu nic
          tu nie ma. Powiedz w rozmowie, gdzie są ludzie, których szukasz, a agent zaproponuje
          konkretne miejsca.
        </p>
      ) : (
        /* JEDNA POWIERZCHNIA, NIE DWA BLOKI (odbiór 1, różnica nr 6). Do 21.09 stał tu panel
           kart z rozmowy, a pod nim druga lista z liczbami, więc ta sama społeczność pojawiała
           się dwa razy w dwóch językach. Referencja ma jeden wiersz na społeczność: plakietka
           platformy, nazwa, podtytuł, stan po prawej.

           JĘZYK STANU ZOSTAJE Z KART i liczy go ta sama funkcja (`communityCards`), więc ekran i
           rozmowa nie mogą powiedzieć o jednej społeczności dwóch różnych rzeczy. Zmienia się
           kształt wiersza, nie źródło prawdy.

           `st-cards-host` zostaje jako korzeń arkusza, bo przycisk „Obserwuj" i jego formularz są
           nadal elementami karty i biorą stamtąd wygląd. */
        <div className="st-cards-host st-communities-list">
          {communityCards(rows).map((card) => {
            const source = rows.find((one) => one.community_id === card.karta_id);
            const observe = card.akcje.find((one) => one.nazwa === "obserwuj");
            return (
              <div className="st-community-row" data-state={card.stan} key={card.karta_id}>
                <span aria-hidden="true" className="st-platform-badge">
                  {String(card.dane["platforma"] ?? "").slice(0, 2).toLowerCase()}
                </span>
                <span className="st-community-row__body">
                  <strong>{String(card.dane["klucz"] ?? "")}</strong>
                  {/* Podtytuł mówi, ILE stamtąd przeczytaliśmy i KIEDY, bo to jest jedyna rzecz,
                      którą ten ekran wie ponad stan obserwacji. Brak odczytu mówi się słowami. */}
                  <small>
                    {source === undefined
                      ? String(card.dane["nazwa"] ?? "")
                      : `${source.thread_count} przeczytanych wątków · ${moment(source.last_polled_at)}`}
                  </small>
                </span>
                {observe === undefined ? (
                  <span className="st-community-row__state">
                    {card.stan === "obserwowana" ? "Obserwuję" : "Włączam obserwację…"}
                  </span>
                ) : (
                  <form action="/studio/card" className="st-card-form" method="post">
                    <input name="workspace_id" type="hidden" value={workspaceId} />
                    <input name="project_id" type="hidden" value={projectId} />
                    <input name="karta_id" type="hidden" value={card.karta_id} />
                    <input name="akcja" type="hidden" value={observe.nazwa} />
                    <input name="metoda" type="hidden" value={observe.trasa.metoda} />
                    <input name="sciezka" type="hidden" value={observe.trasa.sciezka} />
                    <input name="payload" type="hidden" value={JSON.stringify(observe.payload)} />
                    <input name="return_to" type="hidden" value={here} />
                    <button className="st-button st-soft st-join" type="submit">
                      Obserwuj
                    </button>
                  </form>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Zakres POD listą, nie nad nią: najpierw to, co jest, potem zastrzeżenie o tym, czyje
          to jest. Referencja ma w tym miejscu przypis tej samej wagi. */}
      <p className="st-note st-communities-note">
        Społeczności tej przestrzeni. Produkt nie wiąże jeszcze społeczności z pojedynczym
        projektem, więc ta sama lista jest widoczna z każdego projektu w przestrzeni.
      </p>
    </StudioShell>
  );
}
