import type { Read } from "../../lib/control-plane";
import type { CapsuleStatus } from "../../lib/capsule-schema";
import { describeAge } from "../../lib/format";
import { EmptyState } from "../empty-state";

/**
 * „PRZEGLĄDARKA AGENTA” — the one card through which the human signs the agent into a platform.
 * Contract: `lib/capsule-schema.ts` (agreed with the backend session 2026-09-03).
 *
 * WHAT THE OPERATOR DOES: presses one button, a NEW TAB opens with the capsule's own browser,
 * signs in there like a person, comes back and presses "Gotowe". Nothing else. The dashboard
 * never collects a platform password (asserted in `tests/capsule_card.test.tsx`) and never embeds
 * the capsule's screen — the interactive view is out of this package's scope
 * (`tests/source_scan.test.ts`), a decision confirmed by the orchestrator rather than assumed.
 *
 * HOW A NEW TAB HAPPENS WITHOUT JAVASCRIPT: `<form method="post" target="_blank">`. The form posts
 * to a route handler in the NEW tab, the handler asks the control plane for a ticket and answers
 * `303` to the ticket URL, so the new tab lands on the capsule viewer while the dashboard stays
 * where it was. The ticket lives about two minutes, which is why the open must be one click and not
 * a copied link.
 *
 * FOUR THINGS THE CARD KEEPS APART, because they are four different facts:
 *
 *   | what the read says                       | what the card shows                          |
 *   | ---------------------------------------- | -------------------------------------------- |
 *   | read failed                              | "brak odpowiedzi" + reason (EmptyState)      |
 *   | `configured: false`                      | "nie skonfigurowana" — nothing to open       |
 *   | `error` set                              | the stated problem, in mono, and no promises |
 *   | `status` = SIGNED_OUT/AWAITING_HUMAN/SIGNED_IN | the pill and the matching buttons      |
 *
 * `status` IS THE API'S, never derived here from `mode` or `viewer_ready`: "the capsule is up" is
 * not "you are logged in". `viewer_ready` may change the SENTENCE (open it now / still starting)
 * and never the pill. EXACTLY ONE button is emphasised at a time — open when nobody is signed in, confirm
 * when the capsule is waiting, neither once the session works — because two emphasised buttons are
 * two answers to "what do I press now".
 */
export interface CapsuleCardProps {
  readonly capsule: Read<CapsuleStatus>;
  readonly now: Date;
  /** Where the two forms post. Defaults to the handlers under `app/community/`. */
  readonly openAction?: string;
  readonly confirmAction?: string;
}

const PILLS: Readonly<Record<string, { readonly label: string; readonly tone: string }>> = {
  SIGNED_OUT: { label: "niezalogowany", tone: "warn" },
  AWAITING_HUMAN: { label: "czeka na Ciebie", tone: "warn" },
  SIGNED_IN: { label: "zalogowany", tone: "accent" },
};

export function CapsuleCard({
  capsule,
  now,
  openAction = "/community/capsule/open",
  confirmAction = "/community/capsule/confirm",
}: CapsuleCardProps) {
  if (!capsule.ok) {
    return <EmptyState what="Przeglądarka agenta" measured={false} reason={capsule.reason} />;
  }
  const state = capsule.value;
  const pill = PILLS[state.status];

  return (
    <section className="card capsule" data-capsule={state.status}>
      <div className="capsule__head">
        <h2 className="block__title">Przeglądarka agenta</h2>
        {state.configured ? (
          pill === undefined ? null : (
            <span className="pill" data-tone={pill.tone}>
              {pill.label}
            </span>
          )
        ) : (
          <span className="pill" data-tone="warn">
            nie skonfigurowana
          </span>
        )}
      </div>

      {!state.configured ? (
        <p className="capsule__text">
          Kapsuła nie jest skonfigurowana na tej maszynie, więc nie ma czego otwierać. To ustawienie
          środowiska, nie coś, co da się kliknąć tutaj.
        </p>
      ) : state.status === "SIGNED_IN" ? (
        <p className="capsule__text">
          Agent czyta z Twojej zalogowanej sesji{state.account === null ? "" : ` na koncie ${state.account}`}.
          Nic nie publikuje.
        </p>
      ) : state.status === "AWAITING_HUMAN" ? (
        <p className="capsule__text">
          {state.viewer_ready
            ? "Przeglądarka agenta jest otwarta i czeka. Zaloguj się w niej jak zwykle, potem wróć i potwierdź."
            : "Przeglądarka agenta się przygotowuje. Otwórz ją za chwilę. Jeszcze nie przyjmuje połączeń."}
        </p>
      ) : (
        <p className="capsule__text">
          Platforma wymaga Twojego logowania. Otwórz przeglądarkę agenta i zaloguj się w niej sam.
          Panel nigdy nie prosi o hasło do platformy i nie zna go.
        </p>
      )}

      {state.since === null ? null : (
        <p className="capsule__meta">stan z {describeAge(state.since, now)}</p>
      )}

      {state.error === null ? null : (
        <p className="notice" data-capsule-error="tak" data-tone="warn">
          Kapsuła zgłasza problem: <code>{state.error}</code>
        </p>
      )}

      {state.configured ? (
        <div className="capsule__actions">
          {/* `target="_blank"` — plain HTML, no JavaScript: the handler answers 303 to the ticket
              URL inside the NEW tab, so this screen stays where it is. */}
          <form action={openAction} method="post" rel="noopener noreferrer" target="_blank">
            <button className={state.status === "SIGNED_OUT" ? "btn btn--primary" : "btn"} type="submit">
              {state.status === "SIGNED_IN" ? "Otwórz ponownie" : "Otwórz przeglądarkę agenta"}
            </button>
          </form>
          <form action={confirmAction} method="post">
            <button className={state.status === "AWAITING_HUMAN" ? "btn btn--primary" : "btn"} type="submit">
              {state.status === "SIGNED_IN" ? "Sprawdź jeszcze raz" : "Gotowe, jestem zalogowany"}
            </button>
          </form>
        </div>
      ) : null}
    </section>
  );
}
