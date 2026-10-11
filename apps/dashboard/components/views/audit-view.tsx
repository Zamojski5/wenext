import { EntityScreen, type TableRow } from "../entity-screen";
import { instantCell, missingCell, tonedCell, valueCell } from "../../lib/format";
import { labelOf } from "../../lib/routes";
import type { AuditEvent } from "../../lib/schema";

/**
 * The append-only log, newest first, WITH NO FILTER (L5195).
 *
 * The rule this screen is built around is `api/audit.py`'s second decision, and it is worth
 * repeating on this side of the wire because this is where it would be broken by convenience: "A
 * default `kind` filter is the most comfortable way to build a log nobody can trust — the page
 * returns rows, looks alive, and a whole category is invisible with no error and no alarm."
 *
 * So there is no filter control here, no default `kind`, no "hide routine events", and the request
 * carries neither parameter (`lib/control-plane.ts::getAuditEvents`, asserted on the URL by
 * `tests/control_plane_client.test.ts`). What this component adds is the OTHER half of the same
 * promise: a visible tally of the kinds present in the page. A screen that silently dropped a kind
 * would have to drop it from the tally too, and `tests/audit_view.test.tsx` renders three kinds and
 * demands all three.
 *
 * ORDERING IS THE CONTROL PLANE'S. `ORDER BY occurred_at DESC, id DESC` happens in SQL, and this
 * component does not re-sort: a second sort here would be a second opinion about which event came
 * first, and the tie-break (`id`) exists precisely because `occurred_at` defaults to the
 * TRANSACTION's start time, so two rows written by one command share a timestamp.
 *
 * PRESENTATION: `kind` is a neutral pill — every kind the same colour, on purpose. Colouring kinds
 * would be a filter by another name: the eye would learn to skip the grey ones.
 */

export const AUDIT_COLUMNS: readonly string[] = [
  "id",
  "occurred_at",
  "kind",
  "subject_ref",
  "actor_ref",
  "workspace_id",
  "payload",
];

/** Beyond this the payload is cut, and the cut is SAID rather than implied by an ellipsis alone. */
const PAYLOAD_BUDGET = 300;

function payloadText(payload: unknown): string {
  let serialised: string;
  try {
    serialised = JSON.stringify(payload);
  } catch (error) {
    return `nieserializowalny payload: ${String(error)}`;
  }
  if (serialised === undefined) {
    // `JSON.stringify(undefined)` is `undefined`, not a string. An event whose payload field held
    // `undefined` is a broken read, not an empty payload.
    return "payload nie jest wartością JSON";
  }
  if (serialised.length <= PAYLOAD_BUDGET) {
    return serialised;
  }
  return `${serialised.slice(0, PAYLOAD_BUDGET)}… (SKRÓCONE, pełne ${serialised.length} znaków)`;
}

function rowOf(event: AuditEvent): TableRow {
  return {
    key: String(event.id),
    cells: [
      valueCell("id", String(event.id)),
      instantCell("occurred_at", event.occurred_at, "brak occurred_at"),
      tonedCell("kind", event.kind, "neutral"),
      event.subject_ref === null
        ? missingCell("subject_ref", "brak (kolumna dopuszcza NULL)")
        : valueCell("subject_ref", event.subject_ref),
      event.actor_ref === null
        ? missingCell("actor_ref", "brak (kolumna dopuszcza NULL)")
        : valueCell("actor_ref", event.actor_ref),
      event.workspace_id === null
        ? missingCell("workspace_id", "zdarzenie organizacyjne, bez workspace'u")
        : valueCell("workspace_id", event.workspace_id),
      valueCell("payload", payloadText(event.payload)),
    ],
  };
}

/**
 * Human names for the event kinds the control plane writes (grepped from `control_plane/`, 8 kinds
 * plus the approval-path four that F1 does not yet produce). An unknown kind is shown AS IT CAME —
 * never dressed as one of the known ones, and never hidden: this screen has no filter, and a kind
 * nobody named is exactly the kind somebody must see.
 */
const KIND_LABELS: Readonly<Record<string, string>> = {
  OPERATOR_SESSION_OPENED: "logowanie operatora",
  OPERATOR_SESSION_CLOSED: "wylogowanie operatora",
  COMMUNITY_OBSERVATION_ENABLED: "włączenie obserwacji",
  USER_PROFILE_SAVED: "zapis profilu",
  JUDGMENT_FEEDBACK_RECORDED: "Twoja odpowiedź na osąd",
  WAITING_JOBS_RESUMED: "wznowienie zadań",
  JOB_STATE_CHANGED: "zmiana stanu zadania",
  JOB_LEASE_EXPIRED: "wygaśnięcie lease'u",
  APPROVAL_DECISION_RECORDED: "decyzja o zgodzie",
  EXECUTION_INTENT_ISSUED: "wydanie intencji",
  EXECUTION_INTENT_CONSUMED: "zużycie intencji",
  JOB_ENQUEUED: "nowe zadanie w kolejce",
  JOB_LEASE_TAKEN: "zadanie pobrane przez workera",
  OBSERVE_POLL_COMPLETED: "odczyt community",
  OBSERVE_SNAPSHOT_RECORDED: "pomiar wątku",
  COMMUNITY_PICTURE_DRAWN: "nowy obraz community",
  THREAD_JUDGED: "osąd wątku",
  CAPSULE_SESSION_OPENED: "otwarcie przeglądarki agenta",
  CAPSULE_SESSION_CONFIRMED: "potwierdzenie logowania",
};

/**
 * What a kind nobody named is called on the surface. The RAW name never disappears — it is in the
 * table, in `data-kind` and in the tally — but a tile showing `OBSERVE_POLL_COMPLETED` is a tile
 * that has to be read rather than seen (docs/DASHBOARD-DESIGN.md §1, zasada nr 1).
 */
const UNKNOWN_KIND_LABEL = "inne zdarzenie";

/** Kinds present in this page, newest first by count, as human tiles. */
function Summary({ events }: { readonly events: readonly AuditEvent[] }) {
  const counts = new Map<string, number>();
  for (const event of events) {
    counts.set(event.kind, (counts.get(event.kind) ?? 0) + 1);
  }
  const ordered = Array.from(counts.entries()).sort(
    (left, right) => right[1] - left[1] || left[0].localeCompare(right[0]),
  );
  return (
    <ul className="stats stats--totals" data-summary="tak">
      {ordered.map(([kind, count]) => (
        <li className="stat" data-kind={kind} key={kind}>
          <b className="stat__value">{count}</b>{" "}
          <span className="stat__label">{KIND_LABELS[kind] ?? UNKNOWN_KIND_LABEL}</span>
        </li>
      ))}
    </ul>
  );
}

/** Kinds present in this page, with counts, in descending count then alphabetical order. */
function tally(events: readonly AuditEvent[]): string {
  const counts = new Map<string, number>();
  for (const event of events) {
    counts.set(event.kind, (counts.get(event.kind) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .map(([kind, count]) => `${kind}: ${count}`)
    .join(", ");
}

interface AuditViewProps {
  /** `undefined` — the read failed. `[]` — the read succeeded and the log is empty. */
  readonly events: readonly AuditEvent[] | undefined;
  readonly reason?: string;
  readonly truncated?: boolean;
}

export function AuditView({ events, reason, truncated }: AuditViewProps) {
  // The tally is the OTHER half of "no kind is hidden", and it is asserted by the e2e suite — so
  // it stays in the document, as fine print behind "Szczegóły strony". The truncation warning is a
  // different thing entirely and stays on the surface: it is the one sentence that changes what
  // the reader may conclude from the rows they see.
  const tallyNote =
    events !== undefined && events.length > 0
      ? `Rodzaje na tej stronie (bez filtra): ${tally(events)}.`
      : undefined;
  const truncationNote =
    truncated === true
      ? "Strona zapełniła się do limitu. Zdarzeń MOŻE BYĆ WIĘCEJ niż widać. Brak dalszych " +
        "wierszy nie znaczy, że nic więcej się nie stało."
      : undefined;

  return (
    <EntityScreen
      columns={AUDIT_COLUMNS}
      heading={labelOf("audit")}
      lede={
        "Co system zrobił, najnowsze pierwsze. Dziennik jest append-only i bez filtra. Żaden " +
        "rodzaj zdarzenia nie jest ukryty."
      }
      pageNote={truncationNote}
      pageNoteQuiet={tallyNote}
      reason={reason}
      routeId="audit"
      rows={events === undefined ? undefined : events.map(rowOf)}
      tableCollapsed
      what={labelOf("audit")}
    >
      {events !== undefined && events.length > 0 ? <Summary events={events} /> : null}
    </EntityScreen>
  );
}
