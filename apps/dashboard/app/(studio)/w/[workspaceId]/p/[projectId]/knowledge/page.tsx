import { DocumentsBlock } from "../../../../../../../components/studio/documents";
import { MaterialReader } from "../../../../../../../components/studio/material-reader";
import { Pop } from "../../../../../../../components/studio/pop";
import { StudioShell } from "../../../../../../../components/studio/shell";
import { StudioMissing, StudioUnreachable } from "../../../../../../../components/studio/states";
import {
  getKnowledge,
  getKnowledgeSource,
  getProject,
  getProjectDocument,
  getProjectDocuments,
} from "../../../../../../../lib/control-plane";
import { projectConversations } from "../../../../../../../lib/conversations";
import { requireOperator } from "../../../../../../../lib/guard";
import {
  ageNote,
  experienceOf,
  itemKindLabel,
  outcomeLabel,
  sourceKindLabel,
  statedByLabel,
  statusNote,
  verdictOf,
  type KnowledgeSource,
} from "../../../../../../../lib/knowledge-schema";
import { knowledgeHref, projectHref } from "../../../../../../../lib/routes";
import { loadStudioFrame } from "../../../../../../../lib/studio-frame";
import { moment } from "../../../../../../../lib/studio-labels";

/**
 * `GET /w/<workspace>/p/<project>/knowledge` — "Materiały": what was read, and what follows from it.
 *
 * TWO BLOCKS, AND THE ORDER IS THE ARGUMENT. Materials first, findings second, because a finding
 * is only worth as much as the material under it. The screen never shows a statement without
 * saying where it came from: a finding with no material says so in those words, and a material
 * that could not be read shows the reason instead of a date.
 *
 * WHAT THIS SCREEN REFUSES TO IMPLY:
 *
 *   * a dead address is NOT an empty material. `UNAVAILABLE` is printed with its reason, and the
 *     row carries no hash and no content, because there is none;
 *   * a correction does not erase. The withdrawn and superseded findings are behind "Historia",
 *     with the reason, so a week later the person can see that the conversation changed something;
 *   * a search that nothing answered is not "nic nie znaleziono". The material recorded for it
 *     says which backend failed and how;
 *   * a finding standing on old, unreadable or already corrected material is marked
 *     "nieaktualne" WITH the reason, the same flag the agent's own tools receive. One rule, one
 *     place (`knowledge/store.py::staleness`), so the screen and the agent cannot disagree;
 *   * two findings that hold at once and disagree are shown TOGETHER, at the top, with both
 *     dates. The screen picks neither: choosing invisibly on the person's behalf is the failure
 *     this block exists to prevent.
 *
 * MATERIAL FROM OUTSIDE IS DATA. Titles and statements are rendered as text. Nothing on this
 * screen follows an instruction found inside a fetched page (P3).
 *
 * `force-dynamic`, like the rest of the studio: these rows change when a job runs, and a cached
 * page would be a second opinion about the state.
 */
export const dynamic = "force-dynamic";

/** Its own row, so a material renders the same way in the list and in the finding above it. */
function SourceLine({ source }: { readonly source: KnowledgeSource }) {
  const unavailable = source.status === "UNAVAILABLE";
  return (
    <span className="st-row__body">
      <strong>{source.has_raw ? <a href={`?source=${source.source_id}`}>{source.title || "Materiał projektu"}</a> : (source.title || source.locator)}</strong>
      <small>
        {sourceKindLabel(source.kind)}
        {unavailable ? ", " : `, ${source.kind === "DOCUMENT" || source.kind === "USER_NOTE" ? "zapisane" : "pobrane"} ${moment(source.fetched_at)}, `}
        {source.kind === "DOCUMENT" && source.status === "FETCHED" ? "treść zachowana" : statusNote(source)}
      </small>
      {source.kind === "DOCUMENT" ? <small>{source.locator.startsWith("agent-document:") ? "Materiał roboczy agenta" : "Materiał projektu"}</small> : <small>{source.locator}</small>}
    </span>
  );
}

export default async function KnowledgePage({
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

  const returnQuery = new URLSearchParams();
  for (const key of ["source", "offset", "dokument"]) {
    const value = parameters[key];
    if (typeof value === "string") returnQuery.set(key, value);
  }
  const gate = await requireOperator(`${knowledgeHref(workspaceId, projectId)}${returnQuery.size ? `?${returnQuery}` : ""}`);
  if (gate.kind === "unreachable") {
    return <StudioUnreachable reason={gate.reason} />;
  }

  const frame = await loadStudioFrame(gate.token, workspaceId);
  if (!frame.ok) return <StudioUnreachable reason={frame.reason} />;
  if (frame.value === null) return <StudioMissing what="workspace" />;

  const detail = await getProject(gate.token, workspaceId, projectId);
  if (!detail.ok) return <StudioUnreachable reason={detail.reason} />;
  if (detail.value === null) return <StudioMissing what="projekt" />;

  const knowledge = await getKnowledge(gate.token, workspaceId, projectId, {
    includeRetired: true,
  });

  // P8 etap A: dokumenty projektu, tylko do odczytu, i od E2 STOJĄ TUTAJ, a nie na wejściu do
  // projektu. Powód jest ten sam, dla którego wejście zostało ścięte do pytania i rozmów:
  // dokument jest tym, co z rozmów zostało, czyli materiałem, a nie pierwszą rzeczą, którą
  // człowiek widzi po wejściu. Pasek kieruje „Wiedzę" tutaj i tu je znajduje.
  //
  // Indeks zawsze, treść JEDNEGO dokumentu wtedy, gdy człowiek go rozwinął (`?dokument=`):
  // doczytywanie wszystkich naraz kosztowałoby tyle, ile ich jest, przy każdym wejściu.
  const rawDocument = parameters["dokument"];
  const wantedDocument = typeof rawDocument === "string" ? rawDocument : undefined;
  const documents = await getProjectDocuments(gate.token, workspaceId, projectId);
  const openDocument =
    wantedDocument === undefined
      ? null
      : await getProjectDocument(gate.token, workspaceId, projectId, wantedDocument);
  const here = knowledgeHref(workspaceId, projectId);
  const selectedSource = typeof parameters["source"] === "string" ? parameters["source"] : undefined;
  const parsedOffset = Number(parameters["offset"] ?? 0);
  const offset = Number.isSafeInteger(parsedOffset) && parsedOffset >= 0 ? parsedOffset : 0;
  const sourceContent = selectedSource === undefined ? null : await getKnowledgeSource(
    gate.token, workspaceId, projectId, selectedSource, offset,
  );
  const project = detail.value.project;
  // Rozmowy TYLKO dla paska: ten ekran ich nie pokazuje, ale pasek ma trzymać
  // kontekst projektu, w którym stoimy (odbiór 1, różnica nr 5).
  const conversations = await projectConversations(gate.token, workspaceId, projectId);

  if (!knowledge.ok) {
    return (
      <StudioShell
        active={{ kind: "knowledge", projectId }}
        conversations={conversations}
        crumb={`${frame.value.workspace.name} / ${project.name} / Materiały`}
        frame={frame.value}
        info={info}
      >
        <h1 className="st__title">Materiały</h1>
        {/* A broken read is NOT an empty library. The screen says which one this is. */}
        <p className="st-tag" data-tone="absent">
          Nie udało się odczytać materiałów: {knowledge.reason}
        </p>
      </StudioShell>
    );
  }

  const { sources, items } = knowledge.value;
  const inForce = items.filter((item) => item.in_force);
  const stale = inForce.filter((item) => item.stale);
  // P5: what came of our own actions, and what the person said about it. Kept apart from the
  // findings above because they answer a different question: not "what do we know" but "what
  // happened when we tried".
  const experience = inForce.filter((item) => item.kind === "EXPERIENCE");
  const corrections = inForce.filter((item) => verdictOf(item) !== null);
  const history = items.filter((item) => !item.in_force);
  const feeds = sources.filter((source) => source.kind === "RSS");

  return (
    <StudioShell
      active={{ kind: "knowledge", projectId }}
      conversations={conversations}
      crumb={`${frame.value.workspace.name} / ${project.name} / Materiały`}
      frame={frame.value}
      info={info}
    >
      <h1 className="st__title">Materiały</h1>
      {sourceContent !== null ? <MaterialReader
        back={here}
        title={sources.find((source) => source.source_id === selectedSource)?.title ?? "Materiał projektu"}
        generated={sources.find((source) => source.source_id === selectedSource)?.locator.startsWith("agent-document:")}
        slice={sourceContent.ok ? sourceContent.value : undefined}
        error={sourceContent.ok ? undefined : sourceContent.reason}
      /> : null}
      <p className="st__lede">
        Materiały, ustalenia i wnioski z pracy nad {project.name}.{" "}
        <a href={`${projectHref(workspaceId, projectId)}?prompt=${encodeURIComponent("Na podstawie pamięci i materiałów projektu powiedz, co już wiemy o odbiorcy i jego problemie, co pozostaje hipotezą i jaki następny krok ma największy sens. Uwzględnij wcześniejsze korekty.")}`}>
          Omów w rozmowie
        </a>
      </p>

      {/* DOKUMENTY NAD USTALENIAMI, i ta kolejność jest argumentem: dokument odpowiada na
          pytanie „co my właściwie o tym projekcie wiemy", a lista ustaleń na pytanie „skąd to
          wiemy". Pierwsze jest tym, po co człowiek tu wchodzi; drugie tym, czym to sprawdza. */}
      <DocumentsBlock
        documents={documents.ok && documents.value !== null ? documents.value : undefined}
        open={
          openDocument !== null && openDocument.ok && openDocument.value !== null
            ? openDocument.value
            : undefined
        }
        reason={documents.ok ? undefined : documents.reason}
      />

      {/* One string per counter rather than `text {value}`: React puts a comment separator
          between a text node and an expression, so `materiały: 3` is not in the served HTML at
          all when written that way. Measured by the end-to-end suite, which looked for exactly
          that phrase and did not find it. */}
      <div className="st__meta">
        <span className="st-tag" data-count="sources" data-tone="state">
          {`materiały: ${sources.length}`}
        </span>
        <span
          className="st-tag"
          data-count="unavailable"
          data-tone={knowledge.value.unavailable > 0 ? "absent" : "state"}
        >
          {`nieprzeczytane: ${knowledge.value.unavailable}`}
        </span>
        <span className="st-tag" data-count="items" data-tone="state">
          {`ustalenia: ${inForce.length}`}
        </span>
        <span
          className="st-tag"
          data-count="stale"
          data-tone={stale.length > 0 ? "absent" : "state"}
        >
          {`nieaktualne: ${stale.length}`}
        </span>
        <span
          className="st-tag"
          data-count="conflicts"
          data-tone={knowledge.value.conflicts.length > 0 ? "absent" : "state"}
        >
          {`sprzeczne: ${knowledge.value.conflicts.length}`}
        </span>
      </div>

      {knowledge.value.conflicts.length > 0 ? (
        <section className="st__section" data-block="conflicts">
          <div className="st__section-head">Sprzeczne ustalenia</div>
          <div className="st-surface">
            {knowledge.value.conflicts.map((conflict) => (
              <div className="st-row" data-conflict={conflict.about} key={conflict.about}>
                <span aria-hidden="true" className="st-badge">
                  !
                </span>
                <span className="st-row__body">
                  <strong>{conflict.about}</strong>
                  {conflict.items.map((entry) => (
                    <small key={entry.item_id}>
                      {entry.statement} ({statedByLabel(entry.stated_by)}, {moment(entry.created_at)})
                    </small>
                  ))}
                  <small>{conflict.advice}</small>
                </span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="st__section">
        <div className="st__section-head">
          Materiały
          <Pop label="Dodaj materiał" name="add-source" side="right" title="Dodaj materiał">
            <form action="/studio/knowledge" className="st-form" method="post">
              <input name="action" type="hidden" value="add_source" />
              <input name="workspace_id" type="hidden" value={workspaceId} />
              <input name="project_id" type="hidden" value={projectId} />
              <input name="return_to" type="hidden" value={here} />
              <label className="st-field">
                <span>Rodzaj</span>
                <select className="st-select" defaultValue="WEB_PAGE" name="kind">
                  <option value="WEB_PAGE">Strona</option>
                  <option value="RSS">Kanał RSS</option>
                  <option value="NEWSLETTER">Newsletter</option>
                  <option value="USER_NOTE">Notatka</option>
                </select>
              </label>
              <label className="st-field">
                <span>Adres (dla strony, kanału i newslettera)</span>
                <input
                  className="st-input"
                  maxLength={2000}
                  name="locator"
                  placeholder="https://"
                  type="text"
                />
              </label>
              <label className="st-field">
                <span>Tytuł notatki</span>
                <input className="st-input" maxLength={500} name="title" type="text" />
              </label>
              <label className="st-field">
                <span>Treść notatki</span>
                <textarea className="st-textarea" maxLength={20000} name="text" />
              </label>
              {/* Said here rather than only in the backend's refusal: pobieramy po https, a
                  adres, którego nie wolno odwiedzić, zostanie zapisany z powodem. */}
              <p className="st-note">
                Pobieramy wyłącznie po https. Jeśli adresu nie da się przeczytać, materiał i tak
                zostanie na liście, z powodem zamiast treści.
              </p>
              <button className="st-btn" data-kind="primary" type="submit">
                Dodaj
              </button>
            </form>
          </Pop>
        </div>

        {sources.length === 0 ? (
          <p className="st-empty">
            Nic tu jeszcze nie ma. Dodaj stronę, kanał RSS albo notatkę, a agent będzie miał na
            czym się oprzeć.
          </p>
        ) : (
          <div className="st-surface">
            {sources.map((source) => (
              <div className="st-row" data-source={source.source_id} key={source.source_id}>
                <span aria-hidden="true" className="st-badge">
                  {sourceKindLabel(source.kind).slice(0, 2)}
                </span>
                <SourceLine source={source} />
                <span
                  className="st-tag"
                  data-tone={source.status === "UNAVAILABLE" ? "absent" : "state"}
                >
                  {source.status === "UNAVAILABLE" ? "niedostępne" : source.kind === "DOCUMENT" || source.kind === "USER_NOTE" ? "zapisane" : "przeczytane"}
                </span>
                {/* Every material with an address can be read again. A feed goes through the
                    queue, a page is fetched on the spot, and the answer says which happened. A
                    note has no address, so it has no button rather than a button that does
                    nothing. */}
                {source.kind === "USER_NOTE" || source.kind === "DOCUMENT" ? null : (
                  <form action="/studio/knowledge" method="post">
                    <input name="action" type="hidden" value="refresh" />
                    <input name="workspace_id" type="hidden" value={workspaceId} />
                    <input name="project_id" type="hidden" value={projectId} />
                    <input name="source_id" type="hidden" value={source.source_id} />
                    <input name="return_to" type="hidden" value={here} />
                    <button className="st-btn" data-kind="quiet" type="submit">
                      Odśwież
                    </button>
                  </form>
                )}
              </div>
            ))}
          </div>
        )}

        {feeds.length > 0 ? (
          <p className="st-note">
            Kanały odświeżają się same w kolejce, najwyżej raz na sześć godzin. Przycisk zleca
            odczyt od razu.
          </p>
        ) : null}
      </section>

      <section className="st__section">
        <div className="st__section-head">
          Szukanie w internecie
          <Pop label="Szukaj" name="search-web" side="right" title="Szukaj w internecie">
            <form action="/studio/knowledge" className="st-form" method="post">
              <input name="action" type="hidden" value="search" />
              <input name="workspace_id" type="hidden" value={workspaceId} />
              <input name="project_id" type="hidden" value={projectId} />
              <input name="return_to" type="hidden" value={here} />
              <label className="st-field">
                <span>Czego szukamy</span>
                <input className="st-input" maxLength={300} name="query" required type="text" />
              </label>
              {/* Named outright: these are keyless endpoints used by courtesy, and one of them
                  is scraped HTML with no contract. */}
              <p className="st-note">
                Pytamy Hacker News, Wikipedię i DuckDuckGo, bez klucza API. Wynik zapisuje się jako
                materiał z datą. Jeśli żadna z nich nie odpowie, zobaczysz powód, a nie pustą
                listę.
              </p>
              <button className="st-btn" data-kind="primary" type="submit">
                Szukaj
              </button>
            </form>
          </Pop>
        </div>
        <p className="st-note">
          Wyniki trafiają na listę materiałów wyżej, jako rodzaj &quot;wyszukiwanie&quot;, razem z
          nazwą wyszukiwarki, która odpowiedziała.
        </p>
      </section>

      <section className="st__section" data-block="experience">
        <div className="st__section-head">
          Doświadczenie
          <form action="/studio/knowledge" method="post">
            <input name="action" type="hidden" value="measure" />
            <input name="workspace_id" type="hidden" value={workspaceId} />
            <input name="project_id" type="hidden" value={projectId} />
            <input name="return_to" type="hidden" value={here} />
            <button className="st-btn" data-kind="quiet" type="submit">
              Zmierz wyniki
            </button>
          </form>
        </div>
        <p className="st-note">
          Czego uczymy się z rozmów, prób i publikacji. Wynik ma swój kontekst i datę;
          pojedyncza odpowiedź albo cisza nie rozstrzyga jeszcze o popycie.
        </p>

        {experience.length === 0 && corrections.length === 0 ? (
          <p className="st-empty">
            Nie ma jeszcze zapisanych wyników. Opowiedz w rozmowie, co wydarzyło się po
            ostatniej próbie. Agent porówna to z planem i zachowa wnioski.
          </p>
        ) : (
          <div className="st-surface">
            {experience.map((item) => {
              const measured = experienceOf(item);
              return (
                <div className="st-row" data-experience={item.item_id} key={item.item_id}>
                  <span aria-hidden="true" className="st-badge">
                    {(measured?.community ?? "??").slice(2, 4)}
                  </span>
                  <span className="st-row__body">
                    <strong>{item.statement}</strong>
                    {measured === null ? (
                      // A row whose payload does not hold an outcome. Said in words rather than
                      // rendered as an experience with blanks where the result should be.
                      <small>zapis doświadczenia bez odczytanego wyniku</small>
                    ) : (
                      <>
                        <small>
                          {measured.community ?? "nieznana społeczność"}
                          {measured.move === null ? "" : `, ruch ${measured.move}`}
                          {measured.account === null ? "" : `, konto ${measured.account}`}
                          {measured.lengthChars === null
                            ? ""
                            : `, ${measured.lengthChars} znaków`}
                        </small>
                        <small>
                          zmierzone {moment(measured.measuredAt)}
                          {measured.hoursAfter === null
                            ? ""
                            : `, ${Math.round(measured.hoursAfter)} h po publikacji`}
                        </small>
                      </>
                    )}
                  </span>
                  <span
                    className="st-tag"
                    data-tone={measured?.outcome === "REMOVED" ? "danger" : "state"}
                  >
                    {measured === null ? "bez wyniku" : outcomeLabel(measured.outcome)}
                  </span>
                </div>
              );
            })}

            {corrections.map((item) => (
              <div className="st-row" data-correction={item.item_id} key={item.item_id}>
                <span aria-hidden="true" className="st-badge">
                  ok
                </span>
                <span className="st-row__body">
                  <strong>{item.statement}</strong>
                  <small>
                    korekta człowieka, {moment(item.valid_from)}
                    {verdictOf(item) === null ? "" : `, przycisk: ${verdictOf(item)}`}
                  </small>
                </span>
                <span className="st-tag" data-tone="state">
                  od człowieka
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="st__section">
        <div className="st__section-head">
          Ustalenia
          <Pop label="Dopisz ustalenie" name="add-item" side="right" title="Dopisz ustalenie">
            <form action="/studio/knowledge" className="st-form" method="post">
              <input name="action" type="hidden" value="write_item" />
              <input name="workspace_id" type="hidden" value={workspaceId} />
              <input name="project_id" type="hidden" value={projectId} />
              <input name="return_to" type="hidden" value={here} />
              <label className="st-field">
                <span>Rodzaj</span>
                <select className="st-select" defaultValue="INTERPRETATION" name="kind">
                  <option value="FACT">Fakt (wymaga materiału)</option>
                  <option value="INTERPRETATION">Interpretacja</option>
                  <option value="PREFERENCE">Preferencja</option>
                </select>
              </label>
              <label className="st-field">
                <span>Treść</span>
                <textarea className="st-textarea" maxLength={2000} name="statement" required />
              </label>
              <label className="st-field">
                <span>Czego dotyczy (jedno hasło)</span>
                <input
                  className="st-input"
                  maxLength={200}
                  name="about"
                  placeholder="np. pora publikacji"
                  type="text"
                />
              </label>
              <label className="st-field">
                <span>Materiał, z którego to wynika</span>
                <select className="st-select" defaultValue="" name="source_id">
                  <option value="">bez materiału (tylko interpretacja albo preferencja)</option>
                  {sources
                    .filter((source) => source.status === "FETCHED")
                    .map((source) => (
                      <option key={source.source_id} value={source.source_id}>
                        {source.title === "" ? source.locator : source.title}
                      </option>
                    ))}
                </select>
              </label>
              <p className="st-note">
                Fakt bez materiału zostanie odrzucony. To nie jest formalność: fakt bez źródła
                zostaje w pamięci na zawsze i nikt go potem nie sprawdzi. Hasło „czego dotyczy"
                służy do wykrywania sprzeczności: dwa ustalenia z tym samym hasłem pokażą się
                obok siebie zamiast wygrać po cichu jedno z drugim.
              </p>
              <button className="st-btn" data-kind="primary" type="submit">
                Dopisz
              </button>
            </form>
          </Pop>
        </div>

        {inForce.length === 0 ? (
          <p className="st-empty">
            Nie ma jeszcze ustaleń. Pojawią się, gdy agent wyciągnie coś z materiałów albo gdy
            dopiszesz je sam.
          </p>
        ) : (
          <div className="st-surface">
            {inForce.map((item) => (
              <div className="st-row" data-item={item.item_id} key={item.item_id}>
                <span aria-hidden="true" className="st-badge">
                  {itemKindLabel(item.kind).slice(0, 2)}
                </span>
                <span className="st-row__body">
                  <strong>{item.statement}</strong>
                  <small>
                    {itemKindLabel(item.kind)}, {statedByLabel(item.stated_by)}, od{" "}
                    {moment(item.valid_from)}
                  </small>
                  {/* Provenance, on the row, always. An item with no material says that in
                      words rather than showing nothing, because "brak" and "nie pokazano" are
                      the two things a reader must not have to guess between. */}
                  {item.source === null ? (
                    <small>bez materiału, ustalenie z rozmowy</small>
                  ) : (
                    <small>
                      z materiału: {item.source.title === "" ? item.source.locator : item.source.title}
                      {item.source.fetched_at === null
                        ? ""
                        : `, przeczytanego ${moment(item.source.fetched_at)} (${ageNote(item.source_age_days)})`}
                      {item.source.content_hash === null ? "" : `, odcisk ${item.source.content_hash.slice(7, 19)}`}
                    </small>
                  )}
                  {/* The same flag the agent's tools receive, with the same reason. A screen
                      that showed a shaky finding as plainly true would be the human half of the
                      failure this milestone is judged on. */}
                  {item.stale ? (
                    <small data-stale={item.item_id}>
                      nieaktualne: {item.stale_reason ?? "bez podanego powodu"}
                    </small>
                  ) : null}
                </span>
                {item.stale ? (
                  <span className="st-tag" data-tone="absent">
                    nieaktualne
                  </span>
                ) : null}
                <Pop
                  label="Popraw"
                  name={`correct-${item.item_id}`}
                  side="right"
                  title="Popraw ustalenie"
                >
                  <form action="/studio/knowledge" className="st-form" method="post">
                    <input name="action" type="hidden" value="correct" />
                    <input name="workspace_id" type="hidden" value={workspaceId} />
                    <input name="project_id" type="hidden" value={projectId} />
                    <input name="item_id" type="hidden" value={item.item_id} />
                    <input name="kind" type="hidden" value={item.kind} />
                    <input
                      name="source_id"
                      type="hidden"
                      value={item.source === null ? "" : item.source.source_id}
                    />
                    <input name="return_to" type="hidden" value={here} />
                    <label className="st-field">
                      <span>Jak jest naprawdę</span>
                      <textarea
                        className="st-textarea"
                        defaultValue={item.statement}
                        maxLength={2000}
                        name="statement"
                        required
                      />
                    </label>
                    <p className="st-note">
                      Stare ustalenie nie znika. Zostaje w historii ze wskazaniem na nowe.
                    </p>
                    <button className="st-btn" data-kind="primary" type="submit">
                      Popraw
                    </button>
                  </form>
                </Pop>
                <Pop
                  label="Wycofaj"
                  name={`retract-${item.item_id}`}
                  side="right"
                  title="Wycofaj ustalenie"
                >
                  <form action="/studio/knowledge" className="st-form" method="post">
                    <input name="action" type="hidden" value="retract" />
                    <input name="workspace_id" type="hidden" value={workspaceId} />
                    <input name="project_id" type="hidden" value={projectId} />
                    <input name="item_id" type="hidden" value={item.item_id} />
                    <input name="return_to" type="hidden" value={here} />
                    <label className="st-field">
                      <span>Dlaczego wycofujesz</span>
                      <input className="st-input" maxLength={1000} name="reason" required type="text" />
                    </label>
                    <p className="st-note">
                      Powód mówi następnej sesji, czy ustalenie było błędne, czy się
                      zdezaktualizowało.
                    </p>
                    <button className="st-btn" data-kind="primary" type="submit">
                      Wycofaj
                    </button>
                  </form>
                </Pop>
              </div>
            ))}
          </div>
        )}

        {history.length > 0 ? (
          <details className="st-note">
            <summary>Historia ustaleń ({history.length})</summary>
            <ul>
              {history.map((item) => (
                <li key={item.item_id}>
                  {item.statement}
                  {item.retracted_at === null
                    ? ", poprawione"
                    : `, wycofane ${moment(item.retracted_at)}: ${item.retracted_reason ?? "bez powodu"}`}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </section>
    </StudioShell>
  );
}
