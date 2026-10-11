/**
 * Row shapes for the view tests, and NOTHING that ships.
 *
 * These objects imitate what `api/jobs.py` and `api/audit.py` send. They are here so a view test
 * can render a table without a database — and they are NOT the proof that the screen reads
 * PostgreSQL. That proof is `e2e/dashboard_reads_postgres.e2e.test.ts`, which inserts a row with
 * raw SQL and looks for it in the rendered HTML of a real Next.js server talking to a real control
 * plane. A fixture cannot prove a read; it can only prove what the renderer does with a row.
 *
 * This distinction is the reason `services/control_plane/tests/test_jobs_api.py` inserts with SQL
 * too, and the reason `TicketRegistry` in the stream gateway passes every one of its tests while
 * losing all its data on restart.
 */
import type { AttemptRecord, AuditEvent, JobDetailRecord, JobSummary } from "../lib/schema";

/** Fixed instants, so no assertion depends on when the suite ran. */
export const NOW = new Date("2026-08-31T12:00:00.000Z");
export const ONE_MINUTE_AGO = "2026-08-31T11:59:00.000Z";
export const SOON = "2026-08-31T12:01:00.000Z";

export function jobSummary(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    job_id: "11111111-1111-4111-8111-111111111111",
    job_type: "FIXTURE",
    routing_key: "fixture.key",
    status: "RUNNING",
    priority: 100,
    dispatch_version: 1,
    not_before: ONE_MINUTE_AGO,
    attempt_limit: 5,
    attempt_count: 2,
    lock_version: 3,
    created_at: ONE_MINUTE_AGO,
    holds_live_lease: true,
    workspace_id: "22222222-2222-4222-8222-222222222222",
    payload_ref: null,
    ...overrides,
  };
}

export function attempt(overrides: Partial<AttemptRecord> = {}): AttemptRecord {
  return {
    attempt_id: "33333333-3333-4333-8333-333333333333",
    attempt_no: 1,
    dispatch_version: 1,
    lease_generation: 1,
    lease_owner: "44444444-4444-4444-8444-444444444444",
    status: "RUNNING",
    not_before: null,
    heartbeat_at: ONE_MINUTE_AGO,
    expires_at: SOON,
    started_at: ONE_MINUTE_AGO,
    completed_at: null,
    failure_class: null,
    execution_attempt_state: null,
    may_retry_automatically: null,
    ...overrides,
  };
}

export function jobDetail(overrides: Partial<JobDetailRecord> = {}): JobDetailRecord {
  const attempts = overrides.attempts ?? [attempt()];
  return {
    job: overrides.job ?? jobSummary(),
    attempts,
    attempt_limit: overrides.attempt_limit ?? 5,
    attempts_used: overrides.attempts_used ?? attempts.length,
  };
}

export function auditEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: 1,
    occurred_at: ONE_MINUTE_AGO,
    kind: "OPERATOR_SESSION_OPENED",
    subject_ref: "operator:44444444-4444-4444-8444-444444444444",
    actor_ref: "operator:44444444-4444-4444-8444-444444444444",
    workspace_id: null,
    payload: { expires_at: SOON },
    ...overrides,
  };
}
