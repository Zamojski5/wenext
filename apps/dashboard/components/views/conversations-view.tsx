import type { Read } from "../../lib/control-plane";
import type { CapsuleStatus } from "../../lib/capsule-schema";
import type { ConversationSummary, ObservedScore } from "../../lib/conversation-schema";
import { describeAge } from "../../lib/format";
import { EmptyState } from "../empty-state";
import { CapsuleCard } from "./capsule-card";

/**
 * ROZMOWY — the list of threads the user has spoken in, and the capsule sign-in card above it.
 * BUILT ON FIXTURES, NOT WIRED: the data contract is `lib/conversation-schema.ts` and
 * docs/DASHBOARD-DESIGN.md §4; no route in `app/` renders this yet, because the API that would
 * feed it does not exist. A screen wired to nothing renders "brak danych" over a read that never
 * happened, which is the one thing this package exists to prevent.
 *
 * WHAT IS ON THE SURFACE (zasada nr 1, „klikanie, nie czytanie”): one row per conversation, three
 * lines — who answered last and when, the thread's title, the excerpt of the last message — plus a
 * state pill and an unread dot. Nothing else. The refs, the account, the raw enums and the
 * timestamps live in the row's own „Szczegóły” or on the conversation screen.
 *
 * THE UNREAD DOT AND THE COUNT COME FROM THE API (`unread_count`, computed against `seen_at`). The
 * browser has no clock it may use for this, and a count recomputed locally would change on every
 * refresh — the visual form of a measurement nobody made.
 *
 * THE CAPSULE CARD IS THE ONLY ACTION HERE and it is the shared one (`capsule-card.tsx`), so the
 * sign-in flow looks and behaves the same on `/community` and here.
 */
export type ConversationsRead = Read<readonly ConversationSummary[]>;

export interface ConversationsViewProps {
  readonly now: Date;
  readonly conversations: ConversationsRead;
  readonly capsule: Read<CapsuleStatus>;
  readonly flash?: string | null;
}

const STATE_LABELS: Readonly<Record<string, { readonly label: string; readonly tone: string }>> = {
  AWAITING_REPLY: { label: "czeka na odpowiedź", tone: "warn" },
  REPLIED: { label: "ktoś odpisał", tone: "accent" },
  CLOSED: { label: "zamknięte", tone: "neutral" },
};

/** An `Observed[int]` as one short string: the number, or the stated absence. Never a zero. */
function scoreText(score: ObservedScore): string {
  return score.value === null ? "punkty nieznane" : `${score.value} pkt`;
}

function ConversationRow({
  conversation,
  now,
}: {
  readonly conversation: ConversationSummary;
  readonly now: Date;
}) {
  const state = STATE_LABELS[conversation.state];
  return (
    <li
      className="thread-row"
      data-conversation={conversation.conversation_id}
      data-state-name={conversation.state}
      data-unread={conversation.unread_count > 0 ? "tak" : undefined}
    >
      <a className="thread-row__link" href={`/conversations/${encodeURIComponent(conversation.conversation_id)}`}>
        <span aria-hidden="true" className="thread-row__dot" />
        <span className="thread-row__lines">
          <span className="thread-row__top">
            <b className="thread-row__author">{conversation.last_message_author}</b>
            <span className="thread-row__age">{describeAge(conversation.last_message_at, now)}</span>
          </span>
          <span className="thread-row__title">{conversation.thread_title}</span>
          <span className="thread-row__excerpt">{conversation.last_message_excerpt}</span>
        </span>
        <span className="thread-row__side">
          {state === undefined ? null : (
            <span className="pill" data-tone={state.tone}>
              {state.label}
            </span>
          )}
          <span className="thread-row__counts">
            {conversation.reply_count} odp. · {scoreText(conversation.score)}
          </span>
        </span>
      </a>
    </li>
  );
}

export function ConversationsView({ now, conversations, capsule, flash = null }: ConversationsViewProps) {
  const waiting = conversations.ok
    ? conversations.value.filter((conversation) => conversation.unread_count > 0).length
    : 0;

  return (
    <section className="screen" data-route="conversations" data-screen="conversations">
      <header className="screen__head">
        <h1 className="screen__title">Rozmowy</h1>
        <details className="disclosure disclosure--inline">
          <summary className="disclosure__summary">Co tu widzę</summary>
          <div className="disclosure__body">
            <p>
              Wątki, w których się odezwałeś. Kropka oznacza wiadomości, których jeszcze nie
              widziałeś; pastylka mówi, czy ktoś już odpisał. Panel nic nie publikuje.
            </p>
          </div>
        </details>
      </header>
      {flash !== null ? (
        <p className="notice" data-flash="tak" data-tone="accent">
          {flash}
        </p>
      ) : null}

      <CapsuleCard capsule={capsule} confirmAction="/conversations/capsule/confirm" now={now} openAction="/conversations/capsule/open" />

      <section className="block" data-conversations="tak">
        <h2 className="block__title">
          Twoje rozmowy{" "}
          {waiting > 0 ? (
            <span className="pill" data-tone="accent">
              {waiting} nowe
            </span>
          ) : null}
        </h2>
        {!conversations.ok ? (
          <EmptyState what="Rozmowy" measured={false} reason={conversations.reason} />
        ) : conversations.value.length === 0 ? (
          <div className="quiet quiet--action">
            <p className="quiet__sentence">Jeszcze nigdzie się nie odezwałeś.</p>
            <p className="quiet__fine">
              Rozmowy: brak. Pojawią się tutaj, gdy zaakceptujesz pierwszą odpowiedź agenta.
            </p>
            <a className="btn btn--small" href="/community">
              Przejrzyj osądy
            </a>
          </div>
        ) : (
          <ul className="thread-list">
            {conversations.value.map((conversation) => (
              <ConversationRow conversation={conversation} key={conversation.conversation_id} now={now} />
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
