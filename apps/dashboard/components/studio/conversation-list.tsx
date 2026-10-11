/**
 * Rozmowy projektu na stronie projektu (D-045).
 *
 * PO CO DRUGA LISTA, SKORO ROZMOWY SĄ W PASKU. Pasek służy do przeskakiwania i mieści jedną
 * linię na rozmowę: tytuł i datę. Tutaj jest miejsce na to, PO CO się do rozmowy wraca, czyli na
 * jedno zdanie wyniku. Bez niego wybór między ośmioma tytułami jest zgadywaniem, a przeczytanie
 * ośmiu rozmów po to, żeby znaleźć jedną, jest dokładnie tą pracą, którą ten produkt ma zdjąć.
 *
 * TRZY STANY, NIE DWA. `undefined` znaczy „nie udało się odczytać" (i wtedy stoi tu powód),
 * pusta tablica znaczy „sprawdzone, ten projekt nie ma jeszcze rozmów", a lista znaczy listę.
 * Zlanie pierwszych dwóch w „brak rozmów" jest tym samym błędem, co pokazanie zera zamiast
 * „nie zmierzono".
 *
 * WYNIK JEST ZDANIEM SERWERA. `wynik_zdanie` liczy control plane, tak jak tytuł; ekran go nie
 * skraca, bo skracanie w dwóch miejscach daje dwa różne zdania o jednej rozmowie.
 */
import {
  conversationIsOver,
  conversationResult,
  conversationTitle,
} from "../../lib/conversations";
import { conversationHref } from "../../lib/routes";
import type { SessionRow } from "../../lib/session-schema";
import { moment } from "../../lib/studio-labels";

export function ConversationList({
  conversations,
  workspaceId,
  projectId,
  reason,
}: {
  readonly conversations?: readonly SessionRow[];
  readonly workspaceId: string;
  readonly projectId: string;
  readonly reason?: string;
}) {
  return (
    <section className="st__section" data-block="rozmowy">
      <div className="st__section-head">Rozmowy</div>

      {reason !== undefined ? (
        <p className="st-tag" data-tone="absent">
          Nie udało się odczytać rozmów: {reason}. To nie znaczy, że ich nie ma.
        </p>
      ) : conversations === undefined ? null : conversations.length === 0 ? (
        <p className="st-empty" data-conversations="0">
          Ten projekt nie ma jeszcze żadnej rozmowy. Zacznij od pytania albo od kierunku w polu
          wyżej. Kolejne rozmowy zaczynają od tego, co projekt już wie.
        </p>
      ) : (
        <div className="st-surface">
          {conversations.map((session) => {
            const result = conversationResult(session);
            return (
              <a
                className="st-row"
                data-conversation={session.session_id}
                data-over={conversationIsOver(session) ? "1" : "0"}
                href={conversationHref(workspaceId, projectId, session.session_id)}
                key={session.session_id}
              >
                <span className="st-row__body">
                  <strong>{conversationTitle(session)}</strong>
                  {/* Milczenie, gdy rozmowa jeszcze nic nie powiedziała. „Brak wyniku" przy
                      rozmowie w toku czyta się jak usterka, a jest normalnym stanem. */}
                  {result === null ? null : <small>{result}</small>}
                </span>
                <time dateTime={session.created_at}>{moment(session.created_at)}</time>
              </a>
            );
          })}
        </div>
      )}
    </section>
  );
}
