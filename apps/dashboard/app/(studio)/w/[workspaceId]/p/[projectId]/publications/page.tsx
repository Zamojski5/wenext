import { Pop } from "../../../../../../../components/studio/pop";
import { StudioShell } from "../../../../../../../components/studio/shell";
import { StudioMissing, StudioUnreachable } from "../../../../../../../components/studio/states";
import { getProject, getProjectResults } from "../../../../../../../lib/control-plane";
import { getDecisions } from "../../../../../../../lib/presence-client";
import { projectConversations } from "../../../../../../../lib/conversations";
import { requireOperator } from "../../../../../../../lib/guard";
import { publicationsHref, workspaceHref } from "../../../../../../../lib/routes";
import { loadStudioFrame } from "../../../../../../../lib/studio-frame";
import { moment } from "../../../../../../../lib/studio-labels";
import {
  permalinkLabel,
  publicationKindLabel,
  publicationReason,
  publicationStatusLabel,
  targetIsOwnPost,
  visibilityLabel,
} from "../../../../../../../lib/workspace-schema";
import type { ResultPublication } from "../../../../../../../lib/workspace-schema";

/**
 * `GET /w/<workspace>/p/<projekt>/publications` — „Publikacje": co wysłał w świat TEN projekt.
 *
 * SKĄD SIĘ WZIĄŁ TEN EKRAN. Ustalenie §6 audytu UX: projekt bez rozmów i bez powierzenia
 * pokazywał 38 wysłanych publikacji, bo strona wołała odczyt PRZESTRZENI. Nie był to wyciek
 * między organizacjami; było gorzej dla czytającego, bo cudza praca wyglądała jak własna i w
 * dodatku jak praca agenta, który jeszcze nie ruszył.
 *
 * TRZY LICZBY, NIE JEDNA, i to jest cała treść tego ekranu:
 *
 *   * `wysłane` — wnioski, które wyszły;
 *   * `potwierdzone jako widoczne` — te, które ktoś PO WYSŁANIU zobaczył na platformie. Nigdy
 *     sumowane z poprzednią: dwa prawdziwe komentarze zniknęły w godzinę, a nasz własny odczyt
 *     nadal mówił, że są (ZAKRES-FUNKCJONALNY §6 reguła 3);
 *   * `czekają` — zgłoszone i jeszcze niewysłane. Osobno, bo „zgłoszone" to nie „wysłane".
 *
 * `still_present === null` TO TRZECIA WARTOŚĆ: nikt nie patrzył. Renderuje się jako „skutek
 * niepotwierdzony", nigdy jako „usunięta" i nigdy jako „widoczna".
 *
 * CZEGO TEN EKRAN NIE MA I TO JEST ŚWIADOME: liczby społeczności. Społeczność należy do
 * przestrzeni (`communities` nie ma kolumny projektu, migracja 0004), więc trasa projektu tych
 * pól nie zwraca, a ekran ich nie wymyśla. Są na osobnym ekranie „Społeczności".
 */
export const dynamic = "force-dynamic";

function visibility(publication: ResultPublication): { text: string; tone: string } {
  // One sentence for the screen and the conversation (`visibilityLabel`), so "not visible" says
  // WHAT is gone when the platform told us: our reply, or the whole thread under it (0032).
  const text = visibilityLabel(publication);
  if (publication.still_present === null) return { text, tone: "absent" };
  return { text, tone: publication.still_present ? "state" : "danger" };
}

/** Jeden wiersz publikacji. Ten sam kształt, co w historii przestrzeni, bo to ten sam wiersz. */
function PublicationRow({
  publication,
  workspaceId,
  projectId,
  returnTo,
}: {
  readonly publication: ResultPublication;
  readonly workspaceId: string;
  readonly projectId: string;
  readonly returnTo: string;
}) {
  const state = publication.published_at === null
    ? { text: publication.status === "BLOCKED" ? "publikacja zatrzymana" : "brak potwierdzonej publikacji", tone: "absent" }
    : visibility(publication);
  return (
    <div className="st-row" key={publication.request_id}>
      {/* 0023: nasz własny wątek czy odpowiedź pod cudzym. Dwa różne działania wobec
          społeczności, więc dwie różne etykiety. */}
      <span className="st-tag" data-publication-kind={publication.kind}>
        {publication.platform === "X" ? "X · " : publication.platform === "REDDIT" ? "Reddit · " : ""}
        {publicationKindLabel(publication.kind)}
      </span>
      <span className="st-row__body">
        <strong><a href={publication.permalink ?? publication.target_url} rel="noreferrer">
          {publication.permalink ? permalinkLabel(publication.permalink) : "Otwórz wątek"}
        </a></strong>
        <small>
          {publicationStatusLabel(publication.status)}
          {publication.published_at === null ? "" : `: ${moment(publication.published_at)}`}
          {publication.observed_at === null ? "" : `, ostatnie sprawdzenie: ${moment(publication.observed_at)}`}
        </small>
        {publication.published_at === null && publicationReason(publication.detail) !== null ? (
          <small data-publication-reason="1">{`Powód: ${publicationReason(publication.detail)}`}</small>
        ) : null}
      </span>
      <span className="st-tag" data-tone={state.tone}>
        {state.text}
      </span>
      <Pop
        label="Popraw"
        name={`feedback-${publication.request_id}`}
        side="right"
        title="Krótka korekta"
      >
        <form action="/studio/knowledge" className="st-form" method="post">
          <input name="action" type="hidden" value="feedback" />
          <input name="workspace_id" type="hidden" value={workspaceId} />
          <input name="project_id" type="hidden" value={projectId} />
          <input name="request_id" type="hidden" value={publication.request_id} />
          <input name="return_to" type="hidden" value={returnTo} />
          <label className="st-field">
            <span>Co jest nie tak</span>
            <select className="st-select" defaultValue="TOO_LONG" name="verdict">
              <option value="TOO_LONG">Za długie</option>
              <option value="NOT_LIKE_THIS">Nie w ten sposób</option>
              <option value="GOOD">Dobrze</option>
              <option value="DONT_REPLY_HERE">Nie odpowiadaj tu</option>
            </select>
          </label>
          <label className="st-field">
            <span>Jednym zdaniem (nieobowiązkowe)</span>
            <input className="st-input" maxLength={2000} name="note" type="text" />
          </label>
          {/* Powiedziane wprost, bo korekta, która znika, uczy człowieka, że mówienie do tego
              produktu niczego nie zmienia. */}
          <p className="st-note">
            Korekta zostaje przy tej publikacji i trafia do pamięci projektu. Następna sesja
            przeczyta ją, zanim cokolwiek napisze.
          </p>
          <button className="st-btn" data-kind="primary" type="submit">
            Zapisz korektę
          </button>
        </form>
      </Pop>
    </div>
  );
}

export default async function PublicationsPage({
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

  const gate = await requireOperator(publicationsHref(workspaceId, projectId));
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

  const results = await getProjectResults(gate.token, workspaceId, projectId);
  const drafts = await getDecisions(gate.token, workspaceId, projectId);
  const here = publicationsHref(workspaceId, projectId);

  return (
    <StudioShell
      active={{ kind: "publications", projectId }}
      conversations={conversations}
      crumb={`${frame.value.workspace.name} / ${project.name} / Publikacje`}
      frame={frame.value}
      info={info}
    >
      <h1 className="st__title">Publikacje</h1>
      {/* Podtytuł z nazwą projektu, jak w referencji (odbiór 1, różnica nr 22). */}
      <p className="st__subtitle">{project.name}</p>

      {!drafts.ok ? <p className="st-tag" data-tone="absent">Nie udało się odczytać szkiców: {drafts.reason}</p> : drafts.value.length > 0 ? (
        <section className="st__section" data-prepared-drafts>
          <div className="st__section-head">Przygotowane szkice</div>
          {drafts.value.map((draft) => (
            <details className="st__section" id={`draft-${draft.revision_id}`} key={draft.revision_id}>
              <summary>{draft.account} · {targetIsOwnPost(draft.target_external_id) ? "własny post" : "odpowiedź"}</summary>
              <p className="st-prose" style={{ whiteSpace: "pre-wrap" }}>{draft.text}</p>
              <p className="st-note">Przygotowano {moment(draft.created_at)}. Jeszcze niewydany do publikacji.</p>
              <a className="st-btn" data-kind="quiet" href={draft.target_url} rel="noreferrer">Otwórz miejsce publikacji</a>
            </details>
          ))}
        </section>
      ) : null}

      {!results.ok ? (
        // Nieudany odczyt to NIE pusty projekt i tak ma być napisane.
        <p className="st-tag" data-tone="absent">
          Nie udało się odczytać publikacji tego projektu: {results.reason}
        </p>
      ) : results.value === null ? (
        <StudioMissing what="projekt" />
      ) : results.value.publications.length === 0 && results.value.totals.waiting === 0 ? (
        <p className="st-empty">
          Ten projekt jeszcze nic nie wysłał. Historia całej przestrzeni jest w{" "}
          <a href={workspaceHref(workspaceId)}>przeglądzie workspace&apos;u</a> i jest tam tak
          nazwana.
        </p>
      ) : (
        <>
          <div className="st__meta">
            <span className="st-tag" data-count="published">
              {`wysłane: ${results.value.totals.published}`}
            </span>
            {/* Nigdy nie sumowane z powyższą. Rozdział JEST liczbą. */}
            <span
              className="st-tag"
              data-count="confirmed"
              data-tone={results.value.totals.confirmed_visible > 0 ? "state" : "absent"}
            >
              {`potwierdzone jako widoczne: ${results.value.totals.confirmed_visible}`}
            </span>
            <span
              className="st-tag"
              data-count="waiting"
              data-tone={results.value.totals.waiting > 0 ? "warn" : "state"}
            >
              {`czekają na wysłanie: ${results.value.totals.waiting}`}
            </span>
          </div>

          {results.value.publications.length === 0 ? (
            // Zgłoszone są, wysłanych nie ma. Bez tego zdania ekran z samymi licznikami
            // wyglądałby na zepsuty.
            <p className="st-empty">
              Nic jeszcze nie wyszło. Wnioski czekają w kolejce publikatora; pojawią się tutaj,
              gdy zostaną wysłane.
            </p>
          ) : (
            <div className="st-surface">
              {results.value.publications.map((publication) => (
                <PublicationRow
                  key={publication.request_id}
                  projectId={projectId}
                  publication={publication}
                  returnTo={here}
                  workspaceId={workspaceId}
                />
              ))}
            </div>
          )}
        </>
      )}
    </StudioShell>
  );
}
