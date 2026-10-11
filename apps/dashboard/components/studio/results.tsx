import { Pop } from "./pop";
import { moment } from "../../lib/studio-labels";
import { publicationKindLabel, publicationStatusLabel, visibilityLabel } from "../../lib/workspace-schema";
import type { ResultPublication, WorkspaceResults } from "../../lib/workspace-schema";

/**
 * What really happened in this workspace, read from the tables the rest of the product writes.
 *
 * FOUR RULES FROM ZAKRES-FUNKCJONALNY §6, AND THIS COMPONENT IS WHERE THEY ARE EITHER KEPT OR
 * BROKEN:
 *
 *   1. every number has a source, a period and a date of the last reading — hence `last_polled_at`
 *      next to a community and `observed_at` next to a publication, and hence no number at all
 *      when there is no timestamp to put next to it;
 *   2. missing data is shown as missing, never as a zero and never by hiding the field;
 *   3. a publication is a success only when its effect was CONFIRMED. "Wysłane" and
 *      "potwierdzone jako widoczne" are two numbers, never one, because two real comments were
 *      removed within an hour while the author's own read still said they were there;
 *   4. `still_present === null` is a third value: nobody looked. It renders as "skutek
 *      niepotwierdzony", not as "usunięta" and not as "widoczna".
 *
 * A FAILED READ IS NOT AN EMPTY WORKSPACE. `reason` and `results` are separate props and exactly
 * one of them is used: with a reason the block says the read did not happen, with results it says
 * what they are — including that they are empty, which for a new workspace is the correct answer.
 *
 * SINCE P5 EACH PUBLICATION CARRIES FOUR BUTTONS AND A FIELD. They are the short correction the
 * person gives without writing an essay, and they are HERE rather than on a settings screen,
 * because this is where the person is when they see what went out. Each one is recorded as the
 * person's own preference, tied to that publication and to what came of it, and the next session
 * reads it before writing anything. Without `workspaceId` the block renders exactly as before:
 * the operator panel shows the same results and has no form handler behind it.
 */
export interface ResultsBlockProps {
  readonly results?: WorkspaceResults;
  readonly reason?: string;
  /** Present on the studio screens; absent in the operator panel, which only reads. */
  readonly workspaceId?: string;
  readonly projectId?: string;
  readonly returnTo?: string;
}

/** The four verdicts, in the order a person reaches for them. The fifth option is the field. */
const VERDICTS: readonly { readonly value: string; readonly label: string }[] = [
  { value: "TOO_LONG", label: "Za długie" },
  { value: "NOT_LIKE_THIS", label: "Nie w ten sposób" },
  { value: "GOOD", label: "Dobrze" },
  { value: "DONT_REPLY_HERE", label: "Nie odpowiadaj tu" },
];

function visibility(publication: ResultPublication): { text: string; tone: string } {
  // One sentence for the screen and the conversation (`visibilityLabel`), so "not visible" says
  // WHAT is gone when the platform told us: our reply, or the whole thread under it (0032).
  const text = visibilityLabel(publication);
  if (publication.still_present === null) return { text, tone: "absent" };
  return { text, tone: publication.still_present ? "state" : "danger" };
}

export function ResultsBlock({
  results,
  reason,
  workspaceId,
  projectId,
  returnTo,
}: ResultsBlockProps) {
  if (results === undefined) {
    return (
      <section className="st__section">
        <div className="st__section-head">Ostatnie rezultaty</div>
        <p className="st-note" data-tone="absent">
          Nie odczytano rezultatów tego workspace&apos;u.{reason === undefined ? "" : ` ${reason}`}
        </p>
      </section>
    );
  }

  const { totals, communities, publications } = results;
  const nothing = communities.length === 0 && publications.length === 0;

  return (
    <section className="st__section">
      <div className="st__section-head">Ostatnie rezultaty</div>

      {nothing ? (
        <p className="st-empty">
          Ten workspace nie ma jeszcze obserwowanych społeczności ani publikacji. Odczyt się udał,
          po prostu nic tu jeszcze nie zaszło.
        </p>
      ) : (
        <>
          <div className="st__meta">
            <span className="st-tag">społeczności: {totals.communities}</span>
            <span className="st-tag">przeczytane wątki: {totals.threads}</span>
            <span className="st-tag">wysłane publikacje: {totals.published}</span>
            {/* Never summed with the line above. The split IS the number. */}
            <span className="st-tag" data-tone={totals.confirmed_visible > 0 ? "state" : "absent"}>
              potwierdzone jako widoczne: {totals.confirmed_visible}
            </span>
          </div>

          {communities.length > 0 ? (
            <div className="st-surface">
              {communities.map((community) => (
                <div className="st-row" key={community.community_id}>
                  <span aria-hidden="true" className="st-badge">
                    {community.platform.slice(0, 2)}
                  </span>
                  <span className="st-row__body">
                    <strong>{community.display_name}</strong>
                    <small>
                      {community.thread_count} wątków, ostatni odczyt: {moment(community.last_polled_at)}
                    </small>
                  </span>
                  <span className="st-tag" data-tone={community.observe_enabled ? "state" : "absent"}>
                    {community.observe_enabled ? "obserwowana" : "wyłączona z zakresu"}
                  </span>
                </div>
              ))}
            </div>
          ) : null}

          {publications.length > 0 ? (
            <div className="st-surface">
              {publications.map((publication) => {
                const state = visibility(publication);
                return (
                  <div className="st-row" key={publication.request_id}>
                    {/* 0023: nasz własny wątek czy odpowiedź pod cudzym. Dwa różne działania
                        wobec społeczności, więc dwie różne etykiety; bez tego oba wiersze
                        wyglądały identycznie. */}
                    <span className="st-tag" data-publication-kind={publication.kind}>
                      {publication.platform === "X" ? "X · " : publication.platform === "REDDIT" ? "Reddit · " : ""}
                      {publicationKindLabel(publication.kind)}
                    </span>
                    <span className="st-row__body">
                      <strong><a href={publication.permalink ?? publication.target_url} rel="noreferrer">
                        {publication.permalink ?? publication.target_url}
                      </a></strong>
                      <small>
                        {publicationStatusLabel(publication.status)}: {moment(publication.published_at)}, ostatnie
                        sprawdzenie: {moment(publication.observed_at)}
                      </small>
                    </span>
                    <span className="st-tag" data-tone={state.tone}>
                      {state.text}
                    </span>
                    {workspaceId === undefined ? null : (
                      <Pop
                        label="Popraw"
                        name={`feedback-${publication.request_id}`}
                        side="right"
                        title="Krótka korekta"
                      >
                        <form action="/studio/knowledge" className="st-form" method="post">
                          <input name="action" type="hidden" value="feedback" />
                          <input name="workspace_id" type="hidden" value={workspaceId} />
                          {projectId === undefined ? null : (
                            <input name="project_id" type="hidden" value={projectId} />
                          )}
                          <input
                            name="request_id"
                            type="hidden"
                            value={publication.request_id}
                          />
                          <input name="return_to" type="hidden" value={returnTo ?? "/"} />
                          <label className="st-field">
                            <span>Co jest nie tak</span>
                            <select className="st-select" defaultValue="TOO_LONG" name="verdict">
                              {VERDICTS.map((verdict) => (
                                <option key={verdict.value} value={verdict.value}>
                                  {verdict.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="st-field">
                            <span>Jednym zdaniem (nieobowiązkowe)</span>
                            <input className="st-input" maxLength={2000} name="note" type="text" />
                          </label>
                          {/* Said out loud, because a correction that vanishes teaches the
                              person that talking to this product changes nothing. */}
                          <p className="st-note">
                            Korekta zostaje przy tej publikacji i trafia do pamięci projektu.
                            Następna sesja przeczyta ją, zanim cokolwiek napisze.
                          </p>
                          <button className="st-btn" data-kind="primary" type="submit">
                            Zapisz korektę
                          </button>
                        </form>
                      </Pop>
                    )}
                  </div>
                );
              })}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
