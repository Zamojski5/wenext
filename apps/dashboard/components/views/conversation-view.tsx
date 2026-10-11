import type { Read } from "../../lib/control-plane";
import type { ConversationDetail, ConversationMessage } from "../../lib/conversation-schema";
import { describeAge, formatInstant } from "../../lib/format";
import { EmptyState } from "../empty-state";

/**
 * ONE CONVERSATION — the exchange, as cards: what the user wrote, who answered, what they said.
 * BUILT ON FIXTURES, NOT WIRED (see `components/views/conversations-view.tsx` and
 * docs/DASHBOARD-DESIGN.md §4 for the contract this renders and for why nothing calls it yet).
 *
 * NO WALLS OF TEXT (zasada nr 1). Every message shows its first `EXCERPT_CHARS` characters; the
 * rest is behind „pokaż całość” — a `<details>`, so it works without JavaScript and the whole body
 * stays in the DOM. The user's own messages are drawn as their card; everyone else's are plain.
 * A message newer than `seen_at` carries the unread dot the API computed (`is_new`).
 *
 * ONE ACTION, AND IT DOES NOT PUBLISH ANYTHING. „Odpowiedz” leads to the next stage (composing an
 * answer, Outcome C) and is rendered as a DISABLED-looking link to nowhere only when the backend
 * says a reply is possible; until that route exists it is absent, because a button that does
 * nothing teaches the operator that buttons do nothing. What IS here is „Otwórz na Reddicie” — the
 * evidence anyone can check — and „Widziałem”, which clears the unread dot through a plain form
 * POST (the API computes `is_new` against `seen_at`, so only a write can change it).
 */
export interface ConversationViewProps {
  readonly now: Date;
  /** `undefined` — the read failed. `null` — the API answered 404. A record — the conversation. */
  readonly detail: ConversationDetail | null | undefined;
  readonly reason?: string;
  /** Where the reply flow lives, when it exists. Absent → no reply button at all. */
  readonly replyHref?: string;
}

/** Beyond this a message is cut, and the cut is SAID rather than implied by an ellipsis alone. */
const EXCERPT_CHARS = 220;

function MessageCard({ message, now }: { readonly message: ConversationMessage; readonly now: Date }) {
  const long = message.body.length > EXCERPT_CHARS;
  const excerpt = long ? `${message.body.slice(0, EXCERPT_CHARS)}…` : message.body;
  return (
    <li
      className={message.is_ours ? "message message--ours" : "message"}
      data-message={message.message_id}
      data-new={message.is_new ? "tak" : undefined}
      data-ours={message.is_ours ? "tak" : undefined}
    >
      <div className="message__head">
        {message.is_new ? <span aria-hidden="true" className="message__dot" /> : null}
        <b className="message__author">{message.is_ours ? `${message.author} (Ty)` : message.author}</b>
        {message.is_thread_author ? (
          <span className="pill" data-tone="neutral">
            autor wątku
          </span>
        ) : null}
        <span className="message__age">{describeAge(message.created_at_source, now)}</span>
      </div>
      <p className="message__body">{excerpt}</p>
      {long ? (
        <details className="disclosure disclosure--inline">
          <summary className="disclosure__summary">pokaż całość</summary>
          <p className="message__full">{message.body}</p>
        </details>
      ) : null}
      <details className="disclosure disclosure--inline">
        <summary className="disclosure__summary">Szczegóły</summary>
        <p className="message__meta">
          <code>{message.external_id}</code> · poziom {message.depth} · punkty{" "}
          {message.score.value === null ? (
            <span data-missing="tak">nie odczytano ({message.score.unknownReason})</span>
          ) : (
            message.score.value
          )}{" "}
          · napisane {formatInstant(message.created_at_source)} · odczytane{" "}
          {formatInstant(message.observed_at)}
        </p>
      </details>
    </li>
  );
}

export function ConversationView({ now, detail, reason, replyHref }: ConversationViewProps) {
  if (detail === null) {
    // A 404 is a MEASURED fact and gets its own screen — not "brak danych" (which would claim an
    // empty conversation) and not "brak odpowiedzi" (which would claim a failed read).
    return (
      <section className="screen" data-route="conversations">
        <header className="screen__head">
          <h1 className="screen__title">Nie ma takiej rozmowy</h1>
        </header>
        <section className="state state--absent" data-state="nie ma takiej rozmowy">
          <p className="state__headline">nie ma takiej rozmowy</p>
          <p className="state__detail">
            Odczyt się UDAŁ i nie znalazł tej rozmowy w Twoim workspace. To nie jest brak
            odpowiedzi.
          </p>
        </section>
        <a className="btn" href="/conversations">
          Wróć do rozmów
        </a>
      </section>
    );
  }

  if (detail === undefined) {
    return (
      <section className="screen" data-route="conversations">
        <header className="screen__head">
          <h1 className="screen__title">Rozmowa</h1>
        </header>
        <EmptyState what="Rozmowa" measured={false} reason={reason} />
      </section>
    );
  }

  const unseen = detail.messages.filter((message) => message.is_new).length;

  return (
    <section className="screen" data-conversation={detail.conversation_id} data-route="conversations">
      <header className="screen__head">
        <a className="conversation__back" href="/conversations">
          ← Rozmowy
        </a>
        <h1 className="screen__title">{detail.thread_title}</h1>
        <p className="conversation__meta">
          {detail.community_display_name} · Twoja wypowiedź {describeAge(detail.our_message_at, now)} ·{" "}
          {detail.reply_count} odpowiedzi
        </p>
      </header>

      <div className="conversation__bar">
        <a className="btn" href={detail.thread_url} rel="noopener noreferrer" target="_blank">
          Otwórz na Reddicie
        </a>
        {unseen > 0 ? (
          <form action={`/conversations/${encodeURIComponent(detail.conversation_id)}/seen`} method="post">
            <button className="btn btn--ghost" type="submit">
              Widziałem ({unseen})
            </button>
          </form>
        ) : null}
        {replyHref === undefined ? null : (
          <a className="btn btn--primary" href={replyHref}>
            Odpowiedz
          </a>
        )}
      </div>

      {detail.messages.length === 0 ? (
        <p className="quiet">
          Rozmowa: brak wiadomości. Odczyt się udał i nie zwrócił ani jednej. Wątek mógł zostać
          usunięty po stronie platformy.
        </p>
      ) : (
        <ul className="messages">
          {detail.messages.map((message) => (
            <MessageCard key={message.message_id} message={message} now={now} />
          ))}
        </ul>
      )}

      <details className="disclosure disclosure--inline">
        <summary className="disclosure__summary">Szczegóły techniczne</summary>
        <p className="conversation__technical">
          <code>{detail.thread_ref}</code> · <code>{detail.community_ref}</code> · konto{" "}
          {detail.our_account} · stan <code>{detail.state}</code> · ostatnio widziane{" "}
          {detail.seen_at === null ? "nigdy" : formatInstant(detail.seen_at)}
        </p>
      </details>
    </section>
  );
}
