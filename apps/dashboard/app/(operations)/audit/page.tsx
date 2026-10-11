import { AuditView } from "../../../components/views/audit-view";
import { getAuditEvents } from "../../../lib/control-plane";
import { requireOperator } from "../../../lib/guard";

/**
 * `GET /audit` — the append-only log, newest first, no filter (L5195).
 *
 * The request carries `limit` and nothing else: no `kind`, no `since`. That promise lives in
 * `lib/control-plane.ts::getAuditEvents` and is asserted on the URL by
 * `tests/control_plane_client.test.ts`, because a promise about a request can only be proved by
 * looking at the request.
 *
 * READ-ONLY, and here it is not a design choice: `audit_events` refuses UPDATE, DELETE and
 * TRUNCATE in the schema (migration 0001 plus the statement-level trigger of 0002). A screen with
 * an edit control would fail at the database — after telling every reader that the log can be
 * edited.
 */
export const dynamic = "force-dynamic";

export default async function AuditPage() {
  const gate = await requireOperator();
  if (gate.kind === "unreachable") {
    return <AuditView events={undefined} reason={gate.reason} />;
  }

  const page = await getAuditEvents(gate.token);
  if (!page.ok) {
    return <AuditView events={undefined} reason={page.reason} />;
  }
  return <AuditView events={page.value.rows} truncated={page.value.truncated} />;
}
