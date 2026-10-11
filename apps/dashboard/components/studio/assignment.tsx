/**
 * „Przypisz konta": odsyłacz pod tytułem projektu i popover z kontami przestrzeni (E2).
 *
 * SKĄD TEN KSZTAŁT. Referencja (`workspace-studio.html`) ma pod tytułem projektu jedną cichą
 * linię z ikoną osób, nazwą i liczbą kont, która otwiera wybór. Nie ma tam ani sekcji „Konta
 * przypisane do projektu", ani daty przypisania, ani modala: to jest szczegół projektu, a nie
 * jego treść, więc mieszka przy tytule i otwiera się na żądanie.
 *
 * DLACZEGO ODSYŁACZ, A NIE OSOBNY EKRAN. Przypisanie odpowiada na pytanie „w czyim imieniu
 * agent się odezwie", które zadaje się PATRZĄC NA PROJEKT, a nie idąc do ustawień. Ekran
 * osobny znaczyłby, że trzeba wyjść z rozmowy, żeby się dowiedzieć, i wrócić, żeby pisać dalej.
 *
 * TA SAMA TRASA, CO PRZEDTEM: `/studio/assignment` z akcjami `assign` i `unassign` oraz polem
 * `social_account_id`, czyli dokładnie to, co posyłał blok administracyjny zdjęty w E2. Pierwsza
 * wersja tego komponentu posyłała `/studio/project` z `assignment_id` i była MARTWYM
 * przyciskiem: tamten handler zna tylko `create` i `update`, a domyślna gałąź odmawia, więc oba
 * przyciski wracały z „refused" i wyglądały na chwilową awarię. Reguła jest ta sama, co przy
 * kartach rozmowy: pole, które przechodzi przez granicę, nazywa się po obu stronach tak samo,
 * a trasa jest tą, którą naprawdę obsługuje handler.
 *
 * CO POPOVER MUSI POWIEDZIEĆ, CHOĆ JEST CICHY. Dwie rzeczy, które stały w zdjętym bloku i bez
 * których wybór konta jest wyborem w ciemno:
 *
 *   * BRAK INTEGRACJI przy koncie, które projekt już ma. Zdolności platformy jadą razem z
 *     przypisaniem (`support`), bo szukanie ich wśród kandydatów dawało pustkę: kandydaci z
 *     definicji nie zawierają kont już przypisanych, więc ostrzeżenie znikało dokładnie tam,
 *     gdzie było potrzebne (złapane kiedyś przez suitę e2e, nie przez czytanie kodu);
 *   * HISTORIA PRZYPISAŃ. Zdjęcie konta niczego nie kasuje, control plane zapisuje
 *     `unassigned_at`. Historia jest zwinięta, bo to nie jest wybór, tylko ślad.
 */
import { peopleHref } from "../../lib/routes";
import { moment, platformLabel } from "../../lib/studio-labels";

import type {
  AccountSummary,
  AssignmentSummary,
  PersonProfileSummary,
} from "../../lib/workspace-schema";
import { FirstRun } from "./first-run";
import { Icon } from "./icons";
import { Pop } from "./pop";

/** „2 konta", „1 konto", „5 kont". Odmiana, bo „2 kont" zdradza, że pisała maszyna. */
function konta(count: number): string {
  if (count === 1) return "1 konto";
  const last = count % 10;
  const teen = count % 100 >= 10 && count % 100 < 20;
  return !teen && last >= 2 && last <= 4 ? `${count} konta` : `${count} kont`;
}

export function AssignmentLink({
  workspaceId,
  projectId,
  projectName,
  returnTo,
  active,
  past = [],
  candidates,
  people = [],
}: {
  readonly workspaceId: string;
  readonly projectId: string;
  /** Nazwa projektu pod tytułem okna: odpowiedź na pytanie „czyje to konta". */
  readonly projectName: string;
  readonly returnTo: string;
  /** Przypisania ŻYWE, czyli sam wybór. */
  readonly active: readonly AssignmentSummary[];
  /** Zdjęte przypisania, zwinięte pod wyborem. Puste, gdy nikt niczego nie zdejmował. */
  readonly past?: readonly AssignmentSummary[];
  /** Konta przestrzeni, które da się przypisać. */
  readonly candidates: readonly AccountSummary[];
  /** Osoby przestrzeni, żeby konta stały w sekcjach po osobie, jak w referencji. */
  readonly people?: readonly PersonProfileSummary[];
}) {
  // Podpis mówi, KTO, a nie ile wierszy: nazwa pierwszego konta plus liczba, dokładnie jak w
  // referencji („Anna · 2 konta"). Bez żadnego konta to jest zaproszenie, nie stan błędu.
  const first = active[0];
  const summary =
    first === undefined
      ? "Przypisz konta"
      : active.length === 1
        ? first.external_account_id
        : `${first.external_account_id} · ${konta(active.length)}`;

  // Jedno konto ma jeden wiersz, niezależnie od tego, czy jest przypisane: przypisanie to
  // ZNACZNIK przy koncie (✓ albo +), a nie osobna lista. W referencji wygląda to tak samo i
  // dzięki temu widać od razu, czego projekt NIE używa, a mógłby.
  const assignedBy = new Map(active.map((row) => [row.social_account_id, row]));
  const rows = [
    ...candidates.map((one) => ({ account: one, assignment: assignedBy.get(one.social_account_id) })),
    // Konta przypisane, których nie ma wśród kandydatów (kandydaci z definicji ich nie zawierają).
    ...active
      .filter((row) => !candidates.some((one) => one.social_account_id === row.social_account_id))
      .map((row) => ({
        account: {
          social_account_id: row.social_account_id,
          platform: row.platform,
          external_account_id: row.external_account_id,
          state: "",
          created_at: row.assigned_at,
          person_profile_id: null,
          support: row.support,
        } as AccountSummary,
        assignment: row,
      })),
  ];

  const byPerson = new Map<string | null, typeof rows>();
  for (const row of rows) {
    const key = row.account.person_profile_id;
    byPerson.set(key, [...(byPerson.get(key) ?? []), row]);
  }
  const nameOf = new Map(people.map((one) => [one.person_profile_id, one.name]));

  return (
    <div className="wm-project-meta st-assignment">
      <Pop
        dialog={projectName}
        label="Konta przypisane do projektu"
        name="assign-account"
        text={summary}
        title="Konta projektu"
        variant="link"
      >
        {active.length === 0 ? <FirstRun step={3} /> : null}
        {rows.length === 0 ? (
          <p className="st-note">
            Ta przestrzeń nie ma jeszcze żadnego konta. Konta dodaje się w widoku{" "}
            <a href={peopleHref(workspaceId)}>Profile i konta</a>.
          </p>
        ) : (
          [...byPerson.entries()].map(([person, group]) => (
            <div className="st-accounts-group" key={person ?? "bez-osoby"}>
              {/* Sekcja po osobie. „Konta bez osoby" nie jest błędem: konto może istnieć, zanim
                  ktoś przypnie je do profilu, i wtedy tak ma być napisane. */}
              <p className="st-accounts-person">
                {person === null ? "Konta bez osoby" : (nameOf.get(person) ?? "Osoba")}
              </p>
              <ul className="st-accounts-list">
                {group.map(({ account, assignment }) => (
                  <li key={account.social_account_id}>
                    <form action="/studio/assignment" className="st-account-row" method="post">
                      <input
                        name="action"
                        type="hidden"
                        value={assignment === undefined ? "assign" : "unassign"}
                      />
                      <input name="workspace_id" type="hidden" value={workspaceId} />
                      <input name="project_id" type="hidden" value={projectId} />
                      <input
                        name="social_account_id"
                        type="hidden"
                        value={account.social_account_id}
                      />
                      <input name="return_to" type="hidden" value={returnTo} />
                      <span aria-hidden="true" className="st-platform-badge">
                        {account.platform.slice(0, 2).toLowerCase()}
                      </span>
                      <span className="st-account-name">
                        {platformLabel(account.platform)}
                        <small>{account.external_account_id}</small>
                      </span>
                      {/* Ostrzeżenie o braku integracji jedzie Z PRZYPISANIEM, bo kandydaci go
                          nie niosą, a to jest dokładnie ten ekran, na którym projekt twierdzi,
                          że tym kontem pracuje. */}
                      {assignment?.support != null && !assignment.support.integrated ? (
                        <span className="st-tag" data-tone="absent">
                          {assignment.support.note}
                        </span>
                      ) : null}
                      <button
                        aria-label={
                          assignment === undefined
                            ? `Przypisz ${account.external_account_id}`
                            : `Zdejmij ${account.external_account_id}`
                        }
                        className="st-account-mark"
                        data-assigned={assignment === undefined ? "nie" : "tak"}
                        type="submit"
                      >
                        <Icon name={assignment === undefined ? "plus" : "check"} />
                      </button>
                    </form>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}

        <a className="st-text st-accounts-manage" href={peopleHref(workspaceId)}>
          Zarządzaj profilami <Icon name="arrow-up-right" />
        </a>

        {past.length === 0 ? null : (
          <details className="st-note">
            <summary>{`Historia przypisań (${past.length})`}</summary>
            <ul>
              {past.map((row) => (
                <li key={row.assignment_id}>
                  {`${row.platform}: ${row.external_account_id}, zdjęte ${moment(row.unassigned_at)}`}
                </li>
              ))}
            </ul>
          </details>
        )}
      </Pop>
    </div>
  );
}
