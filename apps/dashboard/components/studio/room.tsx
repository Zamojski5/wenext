import type { ReactNode } from "react";

import { plural } from "../../lib/format";
import { softenDashes } from "../../lib/conversation-render";
import type {
  Opportunity,
  OpportunityState,
  Room,
  RoomDraft,
  RoomPublication,
  RoomResults,
} from "../../lib/room-schema";
import {
  ageSentence,
  authorLabel,
  communityLabel,
  draftHandle,
  draftStateOf,
  expirySentence,
  figure,
  headlineOf,
  initialsOf,
  openOpportunities,
  summaryOf,
  warsawClock,
} from "../../lib/room-words";
import { platformLabel } from "../../lib/studio-labels";
import { Icon } from "./icons";
import { Pop } from "./pop";

/**
 * The parts of the "Pokój" screen (D-055).
 *
 * WHAT THIS SCREEN HAS TO DO IN THE FIRST TEN SECONDS. The founder opens it from Codex after
 * saying "znajdź moich pierwszych użytkowników". If it does not show real people, replies he
 * would recognise as his own and a button that sends them, the plugin was "meh". So three things
 * are drawn as what they are: a person's thread (`OpportunityCard`), a reply as it will look on
 * the platform (`DraftPost`), and what came of it in numbers big enough to read from the door
 * (`ResultsBlock`).
 *
 * WHAT IT REFUSES TO DO, because every one of these is a way this screen could lie:
 *
 *   * a number nobody measured is "brak pomiaru", never a zero (`figure`);
 *   * the preview shows the draft's text EXACTLY. Not softened, not trimmed: it is what goes out,
 *     and a preview that differs from the publication is the worst kind of wrong. The words the
 *     agent wrote ABOUT a person (`dlaczego`, `kąt`) are different: they are read here, never
 *     published, so they get the dash rule (`softenDashes`) like every other model text on screen;
 *   * "Opublikuj" says it queued the draft, not that it is live; the publisher confirms that;
 *   * the platform's chrome (vote arrows, the heart) is drawn without a single number, since an
 *     invented "1 reply" on a draft would be a measurement about something that does not exist.
 *
 * NOTHING HERE ACTS ON WHAT A STRANGER WROTE. Titles and excerpts are text; the links to them are
 * `http(s)` (checked in the parser) and open in a new tab with `noopener`.
 */

// ---------------------------------------------------------------------------------------------
// small pieces
// ---------------------------------------------------------------------------------------------

/** A letter in a disc. The visible words next to it name the platform, so the mark is decoration. */
function PlatformMark({ platform }: { readonly platform: string }) {
  const letter = platform === "REDDIT" ? "R" : platform === "X" ? "X" : platform.slice(0, 1);
  return (
    <span aria-hidden="true" className="st-room-mark" data-platform={platform}>
      {letter}
    </span>
  );
}

function Avatar({ handle, label }: { readonly handle: string | null; readonly label?: string }) {
  return (
    <span aria-hidden="true" className="st-room-avatar" data-kind={label === undefined ? undefined : "community"}>
      {label ?? initialsOf(handle)}
    </span>
  );
}

const STATE_WORDS: Readonly<Record<OpportunityState, string>> = {
  NOWA: "Nowa",
  SZKIC: "Odpowiedź gotowa",
  ODPOWIEDZIANO: "Odpowiedziano",
  POMINIETA: "Pominięta",
};

/** The reply's quiet chrome. Icons only, no counts: see the file header. */
function PostActions({ platform }: { readonly platform: string }) {
  if (platform === "X") {
    return (
      <div aria-hidden="true" className="st-room-chrome" data-platform="X">
        <span><Icon name="message-circle" /></span>
        <span><Icon name="repeat" /></span>
        <span><Icon name="heart" /></span>
        <span><Icon name="chart-no-axes-column" /></span>
        <span><Icon name="share" /></span>
      </div>
    );
  }
  return (
    <div aria-hidden="true" className="st-room-chrome" data-platform="REDDIT">
      <span className="st-room-chrome__votes">
        <Icon name="arrow-up" />
        <Icon name="arrow-down" />
      </span>
      <span><Icon name="message-circle" /> Odpowiedz</span>
      <span><Icon name="share" /> Udostępnij</span>
    </div>
  );
}

/**
 * The hidden fields of one `/studio/card` form. The route is built here from the ids the page
 * already has, and the handler checks it against the workspace again: a hidden field is something
 * anybody can edit, so the check there is what keeps it from being an address of their choosing.
 */
function ActionForm({
  workspaceId,
  projectId,
  action,
  path,
  returnTo,
  children,
  className,
}: {
  readonly workspaceId: string;
  readonly projectId: string;
  readonly action: string;
  readonly path: string;
  readonly returnTo: string;
  readonly children: ReactNode;
  readonly className?: string;
}) {
  return (
    <form action="/studio/card" className={className} method="post">
      <input name="workspace_id" type="hidden" value={workspaceId} />
      <input name="project_id" type="hidden" value={projectId} />
      <input name="akcja" type="hidden" value={action} />
      <input name="metoda" type="hidden" value="POST" />
      <input name="sciezka" type="hidden" value={path} />
      <input name="payload" type="hidden" value="{}" />
      <input name="return_to" type="hidden" value={returnTo} />
      {children}
    </form>
  );
}

function projectApi(workspaceId: string, projectId: string): string {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}`;
}

// ---------------------------------------------------------------------------------------------
// the top of the screen
// ---------------------------------------------------------------------------------------------

/**
 * The promise and the live count, in one sentence, with the moment of the read beside it.
 *
 * "ODCZYT O 15:32" IS THE MOMENT THIS PAGE WAS BUILT, NOT A TIMESTAMP OF THE DATA. The contract
 * carries none, and deriving "refreshed at" from the newest thread would say the collector ran
 * when only a person posted. The line therefore says what is true: when this screen read the
 * control plane, and how old the newest person is.
 */
export function RoomHero({
  room,
  now,
  refreshHref,
}: {
  readonly room: Room;
  readonly now: Date;
  readonly refreshHref: string;
}) {
  const headline = headlineOf(room, now);
  const facts = summaryOf(room, now);
  return (
    <header className="st-room-hero">
      <p className="st-room-hero__eyebrow">
        <span className="st-room-hero__dot" aria-hidden="true" />
        {`Odczyt o ${warsawClock(now)}`}
      </p>
      <h1 className="st__title st-room-hero__headline">
        {headline === null ? (
          "Teraz nikt nowy nie pisze o Twoim problemie."
        ) : (
          <>
            <strong>{headline.count}</strong> {headline.rest}
          </>
        )}
      </h1>
      <p className="st-room-hero__meta">
        {facts.map((fact) => (
          <span key={fact}>{fact}</span>
        ))}
        <a className="st-room-hero__refresh" href={refreshHref}>
          Odśwież
        </a>
      </p>
    </header>
  );
}

/**
 * What to tell the founder when the room has nobody in it yet. It still makes the promise, and it
 * says the one thing to do: write the sentence in Codex. `user-select: all` on the sentence makes
 * one click select all of it, because this package has no browser script that could copy it.
 */
export function EmptyRoom({ projectName }: { readonly projectName: string }) {
  return (
    <section aria-labelledby="room-empty" className="st-room-empty">
      <h1 className="st__title st-room-hero__headline" id="room-empty">
        <strong>Tu pojawią się ludzie,</strong> którzy potrzebują tego, co zbudowałeś.
      </h1>
      <p className="st-room-empty__lede">
        {`Projekt „${projectName}” nie ma jeszcze żadnej znalezionej osoby. Napisz w Codexie:`}
      </p>
      <p className="st-room-prompt">
        <span className="st-room-prompt__mark" aria-hidden="true">
          <Icon name="message-circle" />
        </span>
        <span className="st-room-prompt__text">znajdź moich pierwszych użytkowników</span>
      </p>
      <ol className="st-room-steps">
        <li>
          <b>Agent szuka</b>
          <span>ludzi, którzy właśnie teraz piszą o problemie, który rozwiązuje Twój kod.</span>
        </li>
        <li>
          <b>Pisze odpowiedzi</b>
          <span>w Twoim głosie, tak jak zobaczysz je tutaj, pod ich wątkami.</span>
        </li>
        <li>
          <b>Ty klikasz Opublikuj</b>
          <span>a wyniki, w liczbach, wracają na tę stronę.</span>
        </li>
      </ol>
    </section>
  );
}

/**
 * "Podpisz zakres": shown BEFORE the click when the project has no live scope, and again after a
 * refused one. The approve route answers 409 without a signature, and a founder who meets that
 * answer for the first time in front of a camera deserves to have been told what to do.
 */
export function ScopeNote({
  presenceHref,
  refused,
}: {
  readonly presenceHref: string;
  readonly refused: boolean;
}) {
  return (
    <aside className="st-room-scope" data-refused={refused ? "1" : undefined} role="note">
      {refused ? (
        <p className="st-room-scope__line">
          <strong>Opublikuj nie zadziałał: brak podpisanego zakresu.</strong> Podpisz zakres w
          trybie „Z przeglądem”, potem wróć i kliknij Opublikuj jeszcze raz. Szkice poczekają.
        </p>
      ) : (
        <p className="st-room-scope__line">
          <strong>Do publikacji potrzebny jest podpisany zakres.</strong> W trybie „Z przeglądem”
          agent przygotowuje, a Ty klikasz Opublikuj.
        </p>
      )}
      <a className="st-btn" data-kind="primary" href={presenceHref}>
        {refused ? "Otwórz Obecność" : "Podpisz na Obecności"}
      </a>
    </aside>
  );
}

// ---------------------------------------------------------------------------------------------
// opportunities
// ---------------------------------------------------------------------------------------------

function Metric({ label, value }: { readonly label: string; readonly value: number | null }) {
  const shown = figure(value);
  return (
    <li data-measured={shown.measured ? "1" : "0"}>
      <span>{label}</span>
      {shown.measured ? <b>{shown.text}</b> : <i>{shown.text}</i>}
    </li>
  );
}

export function OpportunityCard({
  one,
  now,
  workspaceId,
  projectId,
  returnTo,
  draftShown,
}: {
  readonly one: Opportunity;
  /** Whether the draft this card points at is on the page. A link to an anchor that is not there
   *  would scroll nowhere, so without the draft there is no link. */
  readonly draftShown: boolean;
  readonly now: Date;
  readonly workspaceId: string;
  readonly projectId: string;
  readonly returnTo: string;
}) {
  const where = communityLabel(one.platform, one.community);
  const age = ageSentence(one.created_at, now);
  const author = authorLabel(one.platform, one.author);
  const title = one.title !== null && one.title.trim() !== "" ? one.title.trim() : null;
  const excerpt = one.excerpt !== null && one.excerpt.trim() !== "" ? one.excerpt.trim() : null;
  const canSkip = one.state === "NOWA" || one.state === "SZKIC";
  return (
    <article
      aria-labelledby={`okazja-${one.id}-title`}
      className="st-room-card"
      data-opportunity={one.id}
      data-platform={one.platform}
      data-state={one.state}
      id={`okazja-${one.id}`}
    >
      <div className="st-room-card__meta">
        <PlatformMark platform={one.platform} />
        <span className="st-room-card__where">{where ?? platformLabel(one.platform)}</span>
        {age === null ? null : (
          <>
            <span aria-hidden="true">·</span>
            <time dateTime={one.created_at ?? undefined}>{age}</time>
          </>
        )}
        {author === null ? null : (
          <>
            <span aria-hidden="true">·</span>
            <span className="st-room-card__author">{author}</span>
          </>
        )}
        <span className="st-room-state" data-state={one.state}>
          {STATE_WORDS[one.state]}
        </span>
      </div>

      {/* A post with no title (every one on X) is its own text: the excerpt stands where the
          title would, so the card never opens with "Otwórz wątek". */}
      <h3
        className="st-room-card__title"
        data-untitled={title === null ? "1" : undefined}
        id={`okazja-${one.id}-title`}
      >
        <a href={one.url} rel="noopener noreferrer" target="_blank">
          {title ?? excerpt ?? "Otwórz wątek"}
        </a>
      </h3>
      {title === null || excerpt === null ? null : <p className="st-room-card__excerpt">{excerpt}</p>}

      {one.why === null && one.angle === null ? null : (
        <dl className="st-room-why">
          {one.why === null ? null : (
            <div>
              <dt>Dlaczego ta osoba</dt>
              <dd>{softenDashes(one.why)}</dd>
            </div>
          )}
          {one.angle === null ? null : (
            <div>
              <dt>Proponowany kąt</dt>
              <dd>{softenDashes(one.angle)}</dd>
            </div>
          )}
        </dl>
      )}

      <div className="st-room-card__foot">
        <ul aria-label="Zasięg wątku" className="st-room-metrics">
          <Metric label="wynik" value={one.score} />
          <Metric label="komentarze" value={one.comments} />
        </ul>
        <div className="st-room-card__links">
          <a className="st-room-link" href={one.url} rel="noopener noreferrer" target="_blank">
            Otwórz oryginał <Icon name="arrow-up-right" />
          </a>
          {one.state === "SZKIC" && one.draft_revision !== null && draftShown ? (
            <a className="st-room-link" href={`#szkic-${one.draft_revision}`}>
              Zobacz odpowiedź <Icon name="arrow-down" />
            </a>
          ) : null}
          {canSkip ? (
            <ActionForm
              action="pomin"
              className="st-room-skip"
              path={`${projectApi(workspaceId, projectId)}/opportunities/${encodeURIComponent(one.id)}/dismiss`}
              projectId={projectId}
              returnTo={returnTo}
              workspaceId={workspaceId}
            >
              <button className="st-room-link" type="submit">
                Pomiń
              </button>
            </ActionForm>
          ) : null}
        </div>
      </div>
    </article>
  );
}

export function OpportunitiesBlock({
  room,
  now,
  workspaceId,
  projectId,
  returnTo,
}: {
  readonly room: Room;
  readonly now: Date;
  readonly workspaceId: string;
  readonly projectId: string;
  readonly returnTo: string;
}) {
  const open = openOpportunities(room);
  const answered = room.opportunities.filter((one) => one.state === "ODPOWIEDZIANO");
  const skipped = room.opportunities.filter((one) => one.state === "POMINIETA");
  const props = { now, workspaceId, projectId, returnTo };
  const shown = new Set(room.drafts.map((draft) => draft.revision_id));
  const hasDraft = (one: Opportunity) => one.draft_revision !== null && shown.has(one.draft_revision);
  return (
    <section aria-labelledby="room-opportunities" className="st-room-section">
      <div className="st-room-section__head">
        <h2 id="room-opportunities">Kto teraz pisze o tym problemie</h2>
        <span>{open.length}</span>
      </div>
      {open.length === 0 ? (
        <p className="st-empty">
          Wszystkie znalezione osoby mają już odpowiedź albo zostały pominięte. Poproś Codex o
          nowe wyszukiwanie: „znajdź moich pierwszych użytkowników”.
        </p>
      ) : (
        <div className="st-room-list">
          {open.map((one) => (
            <OpportunityCard draftShown={hasDraft(one)} key={one.id} one={one} {...props} />
          ))}
        </div>
      )}
      {answered.length === 0 ? null : (
        <details className="st-room-fold" data-fold="answered">
          <summary>{`Odpowiedziano (${answered.length})`}</summary>
          <div className="st-room-list">
            {answered.map((one) => (
              <OpportunityCard draftShown={hasDraft(one)} key={one.id} one={one} {...props} />
            ))}
          </div>
        </details>
      )}
      {skipped.length === 0 ? null : (
        <details className="st-room-fold" data-fold="skipped">
          <summary>{`Pominięte (${skipped.length})`}</summary>
          <div className="st-room-list">
            {skipped.map((one) => (
              <OpportunityCard draftShown={hasDraft(one)} key={one.id} one={one} {...props} />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------
// drafts, drawn as the post they will become
// ---------------------------------------------------------------------------------------------

function RedditPreview({
  draft,
  handle,
}: {
  readonly draft: RoomDraft;
  readonly handle: string;
}) {
  const where = communityLabel("REDDIT", draft.community);
  const target = draft.in_reply_to;
  if (draft.kind === "OWN_POST") {
    return (
      <div className="st-room-preview" data-platform="REDDIT" data-kind="OWN_POST">
        <p className="st-room-preview__line">
          <Avatar handle={handle} label="r/" />
          <b>{where ?? "Reddit"}</b>
          <span aria-hidden="true">·</span>
          <span>{handle}</span>
          <span aria-hidden="true">·</span>
          <span>teraz</span>
        </p>
        {draft.title === null || draft.title.trim() === "" ? null : (
          <p className="st-room-preview__title">{draft.title.trim()}</p>
        )}
        <p className="st-room-preview__text">{draft.text}</p>
        <PostActions platform="REDDIT" />
      </div>
    );
  }
  const targetAuthor = authorLabel("REDDIT", target?.author ?? null);
  return (
    <div className="st-room-preview" data-platform="REDDIT" data-kind="REPLY">
      <div className="st-room-quote">
        <span className="st-room-quote__label">Wątek</span>
        {target === null ? (
          <span className="st-room-quote__title">Wątek, do którego odpowiadasz</span>
        ) : (
          <a
            className="st-room-quote__title"
            href={target.url}
            rel="noopener noreferrer"
            target="_blank"
          >
            {target.title ?? "Otwórz wątek"}
          </a>
        )}
        <span className="st-room-quote__by">
          {[where, targetAuthor].filter((one): one is string => one !== null).join(" · ")}
        </span>
      </div>
      <div className="st-room-comment">
        <Avatar handle={handle} />
        <div className="st-room-comment__body">
          <p className="st-room-preview__line">
            <b>{handle}</b>
            <span aria-hidden="true">·</span>
            <span>teraz</span>
          </p>
          <p className="st-room-preview__text">{draft.text}</p>
          <PostActions platform="REDDIT" />
        </div>
      </div>
    </div>
  );
}

function XPreview({ draft, handle }: { readonly draft: RoomDraft; readonly handle: string }) {
  const target = draft.in_reply_to;
  const targetAuthor = authorLabel("X", target?.author ?? null);
  return (
    <div className="st-room-preview" data-platform="X" data-kind={draft.kind}>
      {draft.kind === "REPLY" && target !== null ? (
        <div className="st-room-xthread">
          <div className="st-room-xthread__rail">
            <Avatar handle={targetAuthor} />
            <span aria-hidden="true" className="st-room-xthread__line" />
          </div>
          <div className="st-room-xthread__body">
            <p className="st-room-preview__line">
              <b>{targetAuthor ?? "Autor wpisu"}</b>
            </p>
            {target.title === null || target.title.trim() === "" ? null : (
              <p className="st-room-xthread__text">{target.title.trim()}</p>
            )}
            <a className="st-room-link" href={target.url} rel="noopener noreferrer" target="_blank">
              Otwórz wpis <Icon name="arrow-up-right" />
            </a>
          </div>
        </div>
      ) : null}
      <div className="st-room-xpost">
        <Avatar handle={handle} />
        <div className="st-room-comment__body">
          <p className="st-room-preview__line">
            <b>{handle}</b>
            <span aria-hidden="true">·</span>
            <span>teraz</span>
          </p>
          {draft.kind === "REPLY" && targetAuthor !== null ? (
            <p className="st-room-xreplying">
              W odpowiedzi do <span>{targetAuthor}</span>
            </p>
          ) : null}
          <p className="st-room-preview__text">{draft.text}</p>
          <PostActions platform="X" />
        </div>
      </div>
    </div>
  );
}

export function DraftPost({
  draft,
  now,
  assigned,
  workspaceId,
  projectId,
  returnTo,
}: {
  readonly draft: RoomDraft;
  readonly now: Date;
  readonly assigned: readonly { readonly platform: string; readonly external_account_id: string }[];
  readonly workspaceId: string;
  readonly projectId: string;
  readonly returnTo: string;
}) {
  const handle = draftHandle(draft.platform, assigned);
  const state = draftStateOf(draft);
  const expiry = expirySentence(draft.expires_at, now);
  const expired = expiry?.expired === true;
  const canAct = state.actionable && !expired;
  const base = `${projectApi(workspaceId, projectId)}/approvals/${encodeURIComponent(draft.revision_id)}`;
  const what = draft.kind === "OWN_POST" ? "Własny post" : "Odpowiedź";
  const where = communityLabel(draft.platform, draft.community);
  return (
    <article
      aria-label={`${what} na ${platformLabel(draft.platform)}`}
      className="st-room-draft"
      data-draft={draft.revision_id}
      data-kind={draft.kind}
      data-platform={draft.platform}
      id={`szkic-${draft.revision_id}`}
    >
      <div className="st-room-draft__head">
        <PlatformMark platform={draft.platform} />
        <span>
          {what} na {platformLabel(draft.platform)}
          {where === null ? "" : ` · ${where}`}
        </span>
        {state.label === null ? null : <span className="st-room-state">{state.label}</span>}
      </div>

      {draft.platform === "X" ? (
        <XPreview draft={draft} handle={handle.text} />
      ) : (
        <RedditPreview draft={draft} handle={handle.text} />
      )}

      <div className="st-room-draft__foot">
        <p className="st-room-draft__note">
          {handle.real
            ? `Wyjdzie z konta ${handle.text}, po Twoim kliknięciu.`
            : "Wyjdzie z konta przypisanego do projektu, po Twoim kliknięciu."}
          {expiry === null ? null : (
            <span data-expired={expiry.expired ? "1" : undefined}>{` ${expiry.text}.`}</span>
          )}
        </p>
        {canAct ? (
          <div className="st-room-draft__actions">
            <Pop
              chevron={false}
              label="Popraw szkic"
              name={`room-edit-${draft.revision_id}`}
              side="right"
              text="Popraw"
              title="Popraw szkic"
              variant="button"
            >
              <ActionForm
                action="popraw"
                className="st-form"
                path={`${base}/revise`}
                projectId={projectId}
                returnTo={returnTo}
                workspaceId={workspaceId}
              >
                <label className="st-field">
                  <span>Treść, dokładnie tak, jak wyjdzie</span>
                  <textarea
                    className="st-textarea"
                    defaultValue={draft.text}
                    maxLength={4000}
                    name="text"
                    required
                    rows={9}
                  />
                </label>
                <p className="st-note">
                  Poprawka tworzy nową wersję. Poprzednia zostaje w historii, a nowa nadal czeka
                  na Twoją zgodę.
                </p>
                <button className="st-btn" data-kind="primary" type="submit">
                  Zapisz poprawkę
                </button>
              </ActionForm>
            </Pop>
            <ActionForm
              action="opublikuj"
              path={`${base}/approve`}
              projectId={projectId}
              returnTo={returnTo}
              workspaceId={workspaceId}
            >
              <button className="st-btn" data-kind="primary" type="submit">
                Opublikuj
              </button>
            </ActionForm>
          </div>
        ) : (
          <p className="st-note" data-tone="absent">
            {expired
              ? "Tego szkicu nie da się już opublikować. Poproś agenta w Codexie o nową wersję."
              : "Ten szkic jest już poza etapem zgody."}
          </p>
        )}
      </div>
    </article>
  );
}

export function DraftsBlock({
  room,
  now,
  assigned,
  workspaceId,
  projectId,
  returnTo,
  scope,
}: {
  readonly room: Room;
  readonly now: Date;
  readonly assigned: readonly { readonly platform: string; readonly external_account_id: string }[];
  readonly workspaceId: string;
  readonly projectId: string;
  readonly returnTo: string;
  readonly scope: ReactNode;
}) {
  if (room.drafts.length === 0) return null;
  return (
    <section aria-labelledby="room-drafts" className="st-room-section">
      <div className="st-room-section__head">
        <h2 id="room-drafts">Gotowe do wysłania</h2>
        <span>{room.drafts.length}</span>
      </div>
      <p className="st-room-section__lede">
        Tak wyglądają odpowiedzi na platformie. Przeczytaj, popraw, jeśli trzeba, i kliknij
        Opublikuj.
      </p>
      {scope}
      <div className="st-room-list">
        {room.drafts.map((draft) => (
          <DraftPost
            assigned={assigned}
            draft={draft}
            key={draft.revision_id}
            now={now}
            projectId={projectId}
            returnTo={returnTo}
            workspaceId={workspaceId}
          />
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------
// results
// ---------------------------------------------------------------------------------------------

const STATS: readonly {
  readonly key: "published" | "replies" | "votes" | "removed";
  readonly label: string;
}[] = [
  { key: "published", label: "Opublikowane" },
  { key: "replies", label: "Odpowiedzi" },
  { key: "votes", label: "Głosy" },
  { key: "removed", label: "Usunięte" },
];

function PublicationRow({ item, now }: { readonly item: RoomPublication; readonly now: Date }) {
  const votes =
    item.votes === null ? null : `${item.votes} ${plural(item.votes, ["głos", "głosy", "głosów"])}`;
  const replies =
    item.replies === null
      ? null
      : `${item.replies} ${plural(item.replies, ["odpowiedź", "odpowiedzi", "odpowiedzi"])}`;
  // Two gaps are one sentence, not two: "głosy i odpowiedzi: brak pomiaru".
  const counts =
    votes === null && replies === null
      ? ["głosy i odpowiedzi: brak pomiaru"]
      : [votes ?? "głosy: brak pomiaru", replies ?? "odpowiedzi: brak pomiaru"];
  const age = ageSentence(item.published_at, now);
  const visibility =
    item.visibility.kind === "visible"
      ? { text: "widoczny", tone: "state" }
      : item.visibility.kind === "removed"
        ? { text: "usunięty z platformy", tone: "danger" }
        : { text: "skutek niepotwierdzony", tone: "absent" };
  return (
    <li className="st-room-result" data-visibility={item.visibility.kind}>
      <PlatformMark platform={item.platform} />
      <span className="st-room-result__body">
        <a href={item.url} rel="noopener noreferrer" target="_blank">
          {item.text === "" ? "Otwórz wpis" : item.text}
        </a>
        <small>{[age === null ? null : `opublikowano ${age}`, ...counts].filter(Boolean).join(" · ")}</small>
      </span>
      <span className="st-tag" data-tone={visibility.tone}>
        {visibility.text}
      </span>
    </li>
  );
}

/**
 * The numbers of the window, big, and the short list under them.
 *
 * FOUR TILES, ONE RULE: a tile shows a measured number or "brak pomiaru". "Usunięte" is the one a
 * founder would like to be zero and the screen does not paint it either way: there is no colour
 * for "good" in this product (`studio.css`, rule 1), and a removed reply is a fact to read, not a
 * score. Big values use the font's own proportional figures; tabular ones make `121` look loose.
 */
export function ResultsBlock({ results, now }: { readonly results: RoomResults; readonly now: Date }) {
  const nothing =
    results.items.length === 0 &&
    STATS.every((stat) => results[stat.key] === null || results[stat.key] === 0);
  return (
    <section aria-labelledby="room-results" className="st-room-section">
      <div className="st-room-section__head">
        <h2 id="room-results">Wyniki</h2>
        <span>{`ostatnie ${results.window_days} ${plural(results.window_days, ["dzień", "dni", "dni"])}`}</span>
      </div>
      <ul className="st-room-stats">
        {STATS.map((stat) => {
          const shown = figure(results[stat.key]);
          return (
            <li data-measured={shown.measured ? "1" : "0"} data-stat={stat.key} key={stat.key}>
              <span className="st-room-stat__value">{shown.text}</span>
              <span className="st-room-stat__label">{stat.label}</span>
            </li>
          );
        })}
      </ul>
      <p className="st-note">
        Liczymy to, co po publikacji faktycznie odczytano z platformy. „Usunięte” to wpisy, które
        zniknęły po wysłaniu; „brak pomiaru” znaczy, że nikt jeszcze tego nie sprawdził.
      </p>
      {results.items.length === 0 ? (
        <p className="st-empty">
          {nothing
            ? "Wyniki pojawią się tutaj po pierwszej publikacji."
            : "Brak pozycji do pokazania w tym oknie."}
        </p>
      ) : (
        <ul className="st-room-results">
          {results.items.map((item, index) => (
            <PublicationRow item={item} key={`${index}-${item.url}`} now={now} />
          ))}
        </ul>
      )}
    </section>
  );
}
