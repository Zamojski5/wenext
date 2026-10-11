/**
 * Umówione powroty agenta i jedyna rzecz, jaką człowiek z nimi robi: odwołanie jednego (0023).
 *
 * OSOBNA SEKCJA OD HISTORII OBUDZEŃ, bo odpowiada na inne pytanie. Historia mówi, co się stało;
 * ta lista mówi, co ma się dopiero stać, i to jest jedyne miejsce na tym ekranie, gdzie widać
 * obietnicę agenta wobec przyszłości. Zmieszane w jedną listę dawałyby ekran, na którym
 * przyszłość wygląda jak przeszłość, a „agent wróci o 18" czyta się wtedy jak „agent wrócił".
 *
 * WIERSZ POKAZUJE ZDANIE AGENTA, a nie kod powodu obudzenia: kod dla każdego zaplanowanego
 * powrotu jest ten sam („DUE"), więc lista kodów to lista identycznych wierszy.
 *
 * ODWOŁANIE JEST PRZY WIERSZU, NIE NAD LISTĄ. Jeden formularz na wiersz, z identyfikatorem tego
 * wiersza w ukrytym polu: nieuważne kliknięcie odwołuje to, pod czym stoi, a nie „następny”.
 *
 * CZEGO TA SEKCJA NIE OBIECUJE: że odwołanie zatrzyma agenta. Znika termin, nie sprawa i nie
 * powierzenie; agent pracuje dalej i wróci, gdy coś się wydarzy.
 */
import { moment } from "../../lib/studio-labels";
import type { Wakeup } from "../../lib/presence-schema";

export function PlannedReturns({
  planned,
  workspaceId,
  projectId,
  here,
}: {
  readonly planned: readonly Wakeup[];
  readonly workspaceId: string;
  readonly projectId: string;
  readonly here: string;
}) {
  return (
    <section className="st-block" data-block="planned">
      <h2 className="st-block__title">Umówione powroty</h2>
      {planned.length === 0 ? (
        <p className="st-note">
          Agent nie umówił się jeszcze na żaden powrót w konkretnym terminie.
        </p>
      ) : (
        <>
          <p className="st-note">
            Terminy w UTC. Agent wróci do zapisanej sprawy. Odwołanie usuwa zaplanowany powrót.
          </p>
          <ul className="st__list">
            {planned.map((one) => (
              <li className="st-row" data-tone="state" data-kind="planned" key={one.wakeup_id}>
                <span className="st-row__body">
                  <strong>{`${moment(one.due_at ?? one.created_at)}, umówiony powrót`}</strong>
                  {/*
                    Zdanie agenta, nie kod powodu: `reason` zaplanowanego powrotu to zawsze
                    „DUE" i lista takich kodów niczego nie mówi. Gdy notatki nie ma (stare api
                    albo wiersz bez zdarzenia), mówimy to wprost, zamiast pokazywać kod.
                  */}
                  <small>{one.note ?? "bez notatki: nie wiadomo, po co agent wraca"}</small>
                  <small>{`zaplanowany ${moment(one.created_at)}`}</small>
                </span>
                <form action="/studio/presence" method="post">
                  <input name="action" type="hidden" value="cancel_return" />
                  <input name="workspace_id" type="hidden" value={workspaceId} />
                  <input name="project_id" type="hidden" value={projectId} />
                  <input name="wakeup_id" type="hidden" value={one.wakeup_id} />
                  <input name="return_to" type="hidden" value={here} />
                  <button className="st-btn" data-kind="secondary" type="submit">
                    Odwołaj
                  </button>
                </form>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
