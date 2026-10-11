import type { Read } from "../../lib/control-plane";
import type { Draft, DraftRevision } from "../../lib/draft-schema";
import { counted, describeAge, formatInstant, type Tone } from "../../lib/format";
import { EmptyState } from "../empty-state";

/**
 * „SZKIC KOMENTARZA” — the one screen through which anything this system writes reaches a platform.
 * Contract: `lib/draft-schema.ts` and docs/DASHBOARD-DESIGN.md §4b. BUILT ON FIXTURES, NOT WIRED.
 *
 * THE WHOLE POINT OF THIS CARD IS THE PAUSE BEFORE PUBLISHING. Three rules shape it, and none of
 * them is decided here: the dashboard renders what the backend says and refuses to infer the rest.
 *
 *   1. **Nothing is published without a click** (roadmap 22.2 point 2). There is no state in which
 *      a form submits itself and no timer anywhere in this file. The operator reads the text, then
 *      presses one of three buttons.
 *   2. **The consent names an exact text** (L5218-5219). The form carries the revision's id and its
 *      `payload_sha256`, so the approval the backend receives is for the text that was ON SCREEN.
 *      Editing the textarea and pressing „Publikuj” cannot smuggle a different body past a consent
 *      given for another one: the hash will not match and the backend refuses. This card computes
 *      no hash and holds no copy of that rule.
 *   3. **After the submit starts there is no automatic retry** (L2673). `SUBMITTING` shows no
 *      buttons at all, and a failure gets a retry control ONLY when the backend's `may_retry` says
 *      so, exactly as the job detail screen treats its own flag. An unknown outcome is not
 *      permission.
 *
 * WHAT IS ON THE SURFACE (zasada nr 1): the thread title as a link, the state as a pill, the draft
 * text in an editable field, three buttons with human names. One click away: why the agent wrote it
 * this way, which voice card and profile version it used, the prompt hash, and how much the account
 * may still say today.
 */
export interface DraftCardProps {
  readonly draft: Read<Draft>;
  readonly now: Date;
  /** Where the three buttons post. One form, three submit buttons, no JavaScript. */
  readonly action?: string;
}

const STATE_PILLS: Readonly<Record<string, { readonly label: string; readonly tone: Tone }>> = {
  DRAFT: { label: "szkic", tone: "accent" },
  AWAITING_APPROVAL: { label: "czeka na Twoją zgodę", tone: "accent" },
  APPROVED: { label: "zatwierdzony", tone: "warn" },
  SUBMITTING: { label: "publikacja w toku", tone: "warn" },
  PUBLISHED: { label: "opublikowany", tone: "neutral" },
  REJECTED: { label: "odrzucony", tone: "neutral" },
  FAILED: { label: "publikacja się nie udała", tone: "danger" },
};

/** States in which the operator still decides. Everything else is a report, not a question. */
const DECIDABLE: readonly string[] = ["DRAFT", "AWAITING_APPROVAL"];

function Revisions({ revisions, now }: { readonly revisions: readonly DraftRevision[]; readonly now: Date }) {
  if (revisions.length <= 1) return null;
  return (
    <details className="disclosure disclosure--inline">
      <summary className="disclosure__summary">
        Wcześniejsze wersje ({revisions.length - 1})
      </summary>
      <ul className="revisions">
        {revisions.slice(0, -1).map((revision) => (
          <li className="revision" data-revision={revision.revision_id} key={revision.revision_id}>
            <p className="revision__meta">
              {revision.author === "OPERATOR" ? "Twoja poprawka" : "wersja agenta"},{" "}
              {describeAge(revision.created_at, now)}
            </p>
            <p className="revision__body">{revision.body}</p>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function DraftCard({ draft, now, action = "/community/drafts/decide" }: DraftCardProps) {
  if (!draft.ok) {
    return <EmptyState what="Szkic komentarza" measured={false} reason={draft.reason} />;
  }
  const value = draft.value;
  const pill = STATE_PILLS[value.state];
  const decidable = DECIDABLE.includes(value.state);
  const revision = value.current_revision;

  return (
    <article className="card draft" data-draft={value.draft_id} data-state-name={value.state}>
      <div className="draft__head">
        <a className="draft__thread" href={value.thread_url} rel="noopener noreferrer" target="_blank">
          {value.thread_title}
        </a>
        {pill === undefined ? null : (
          <span className="pill" data-tone={pill.tone}>
            {pill.label}
          </span>
        )}
      </div>
      <p className="draft__meta">
        {value.community_display_name}, konto {value.account}, napisany {describeAge(value.created_at, now)}
      </p>

      {value.state === "PUBLISHED" && value.published !== null ? (
        <p className="notice" data-tone="neutral">
          Opublikowane {describeAge(value.published.observed_at, now)}.{" "}
          <a href={value.published.url} rel="noopener noreferrer" target="_blank">
            Zobacz komentarz na platformie
          </a>
        </p>
      ) : null}

      {value.state === "SUBMITTING" ? (
        <p className="notice" data-tone="warn">
          Publikacja trwa. Nie klikaj drugi raz. Odśwież ten ekran za minutę, żeby zobaczyć wynik.
        </p>
      ) : null}

      {value.failure !== null ? (
        <p className="notice" data-draft-failure="tak" data-tone="danger">
          Publikacja się nie udała {describeAge(value.failure.at, now)}. Powód:{" "}
          <code>{value.failure.reason}</code>
          {value.failure.may_retry
            ? " Control plane zezwala na ponowienie."
            : " Control plane NIE zezwala na ponowienie, bo komentarz mógł już powstać po stronie platformy."}
        </p>
      ) : null}

      {decidable ? (
        <form action={action} className="draft__form" method="post">
          {/* The consent names an EXACT text. Both values travel back with the decision, and the
              backend refuses if the body no longer hashes to this. See the module docstring. */}
          <input name="draft_id" type="hidden" value={value.draft_id} />
          <input name="revision_id" type="hidden" value={revision.revision_id} />
          <input name="payload_sha256" type="hidden" value={revision.payload_sha256} />
          <label className="field__label" htmlFor={`body-${value.draft_id}`}>
            Tekst komentarza
          </label>
          <textarea
            className="field__textarea draft__body"
            defaultValue={revision.body}
            id={`body-${value.draft_id}`}
            name="body"
            rows={6}
          />
          <div className="draft__actions">
            <button className="btn btn--primary" name="decision" type="submit" value="publish">
              Publikuj
            </button>
            <button className="btn" name="decision" type="submit" value="revise">
              Popraw i publikuj
            </button>
            <button className="btn btn--ghost" name="decision" type="submit" value="reject">
              Odrzuć
            </button>
          </div>
          <p className="draft__hint">
            Nic nie wychodzi na platformę bez tego kliknięcia. Po kliknięciu zobaczysz stan
            „publikacja w toku”.
          </p>
        </form>
      ) : (
        <p className="draft__body draft__body--read">{revision.body}</p>
      )}

      {value.failure !== null && value.failure.may_retry ? (
        <form action={action} className="draft__retry" method="post">
          <input name="draft_id" type="hidden" value={value.draft_id} />
          <input name="revision_id" type="hidden" value={revision.revision_id} />
          <input name="payload_sha256" type="hidden" value={revision.payload_sha256} />
          <input name="body" type="hidden" value={revision.body} />
          <button className="btn" name="decision" type="submit" value="publish">
            Spróbuj opublikować jeszcze raz
          </button>
        </form>
      ) : null}

      <Revisions now={now} revisions={value.revisions} />

      <details className="disclosure disclosure--inline">
        <summary className="disclosure__summary">Dlaczego agent tak napisał</summary>
        <div className="disclosure__body">
          <p className="draft__reasoning">{value.reasoning}</p>
          <p className="draft__technical">
            Karta głosu:{" "}
            {value.voice_version === null ? "nie użyto żadnej" : `wersja ${value.voice_version}`}. Profil:
            wersja {value.user_profile_version}. Model: {value.model}. Prompt: {value.prompt_name}@
            {value.prompt_sha256.slice(0, 12)}. Tekst:{" "}
            <code>{revision.payload_sha256.slice(0, 12)}</code>.
          </p>
          <p className="draft__technical">
            {value.rate_limit === null
              ? "Limitu tempa nie odczytano."
              : `Dziś wykorzystano ${value.rate_limit.used_today} z ${value.rate_limit.limit_per_day}. ` +
                `Zostało ${counted(
                  Math.max(0, value.rate_limit.limit_per_day - value.rate_limit.used_today),
                  ["wypowiedź", "wypowiedzi", "wypowiedzi"],
                )}, licznik zeruje się ${formatInstant(value.rate_limit.resets_at)}.`}
          </p>
          {value.approved_at === null ? null : (
            <p className="draft__technical">Zgoda: {formatInstant(value.approved_at)}.</p>
          )}
          {value.submitted_at === null ? null : (
            <p className="draft__technical">Wysłane do platformy: {formatInstant(value.submitted_at)}.</p>
          )}
        </div>
      </details>
    </article>
  );
}
