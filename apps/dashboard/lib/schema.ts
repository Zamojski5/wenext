/**
 * The control plane's JSON, checked field by field before any of it reaches a screen.
 *
 * WHY A PARSER AND NOT A CAST. `JSON.parse(...) as JobSummary` compiles, runs, and produces
 * `undefined` in the middle of a table the moment a field is renamed on the other side of the
 * wire. `undefined` rendered as a cell is a blank, a blank looks like "no value", and "no value"
 * on this dashboard is a claim (L5241 point 5). The dashboard would then be reporting an absence
 * it never measured — the same class of defect as a gate announcing GO with no measurement
 * (D-007), several layers down.
 *
 * SO: every field is read explicitly, a missing or wrongly typed field is a FAILURE OF THE WHOLE
 * READ, and the failure names the field. Field names here are the control plane's JSON keys, which
 * are English (D-010, and `api/jobs.py` says so at `_job_payload`).
 *
 * THE ONE FIELD WHOSE DIRECTION MATTERS MOST is `may_retry_automatically`. The control plane sends
 * `true`, `false` or `null`, and `null` means UNKNOWN — an attempt whose outcome nobody recorded
 * says nothing about whether the post went out (L2673, L4560, L5335). This parser therefore keeps
 * the three values apart and refuses to invent any of them: a MISSING field is an error, not
 * `null`, and certainly not `false`. `false` would be a claim ("retry is forbidden") and `true`
 * would be permission. Both are measurements, and there is none.
 */
import type { Read } from "./control-plane";

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function requireString(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" ? value : null;
}

function requireNumber(row: Record<string, unknown>, field: string): number | null {
  const value = row[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function requireNullableString(
  row: Record<string, unknown>,
  field: string,
): { readonly present: true; readonly value: string | null } | { readonly present: false } {
  if (!(field in row)) {
    return { present: false };
  }
  const value = row[field];
  if (value === null) {
    return { present: true, value: null };
  }
  if (typeof value === "string") {
    return { present: true, value };
  }
  return { present: false };
}

/** Who the control plane says the cookie belongs to. */
export interface SessionDescription {
  readonly operator_id: string;
  readonly organization_id: string;
  readonly expires_at: string;
}

export function parseSessionDescription(
  row: Record<string, unknown>,
): Read<SessionDescription> {
  const operator = requireString(row, "operator_id");
  const organization = requireString(row, "organization_id");
  const expires = requireString(row, "expires_at");
  if (operator === null || organization === null || expires === null) {
    return failure("sesja: odpowiedź nie ma pól operator_id / organization_id / expires_at");
  }
  return { ok: true, value: { operator_id: operator, organization_id: organization, expires_at: expires } };
}

/** One row of `jobs`, as `api/jobs.py::_job_payload` sends it. */
export interface JobSummary {
  readonly job_id: string;
  readonly job_type: string;
  readonly routing_key: string;
  readonly status: string;
  readonly priority: number;
  readonly dispatch_version: number;
  readonly not_before: string;
  readonly attempt_limit: number;
  readonly attempt_count: number;
  readonly lock_version: number;
  readonly created_at: string;
  readonly holds_live_lease: boolean;
  readonly workspace_id: string | null;
  readonly payload_ref: string | null;
}

export function parseJobSummary(row: Record<string, unknown>): Read<JobSummary> {
  const jobId = requireString(row, "job_id");
  const jobType = requireString(row, "job_type");
  const routingKey = requireString(row, "routing_key");
  const status = requireString(row, "status");
  const notBefore = requireString(row, "not_before");
  const createdAt = requireString(row, "created_at");
  const priority = requireNumber(row, "priority");
  const dispatchVersion = requireNumber(row, "dispatch_version");
  const attemptLimit = requireNumber(row, "attempt_limit");
  const attemptCount = requireNumber(row, "attempt_count");
  const lockVersion = requireNumber(row, "lock_version");
  const holdsLiveLease = row["holds_live_lease"];
  const workspace = requireNullableString(row, "workspace_id");
  const payloadRef = requireNullableString(row, "payload_ref");

  const missing: string[] = [];
  if (jobId === null) missing.push("job_id");
  if (jobType === null) missing.push("job_type");
  if (routingKey === null) missing.push("routing_key");
  if (status === null) missing.push("status");
  if (notBefore === null) missing.push("not_before");
  if (createdAt === null) missing.push("created_at");
  if (priority === null) missing.push("priority");
  if (dispatchVersion === null) missing.push("dispatch_version");
  if (attemptLimit === null) missing.push("attempt_limit");
  if (attemptCount === null) missing.push("attempt_count");
  if (lockVersion === null) missing.push("lock_version");
  if (typeof holdsLiveLease !== "boolean") missing.push("holds_live_lease");
  if (!workspace.present) missing.push("workspace_id");
  if (!payloadRef.present) missing.push("payload_ref");
  if (missing.length > 0) {
    return failure(`job: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }

  return {
    ok: true,
    value: {
      job_id: jobId as string,
      job_type: jobType as string,
      routing_key: routingKey as string,
      status: status as string,
      priority: priority as number,
      dispatch_version: dispatchVersion as number,
      not_before: notBefore as string,
      attempt_limit: attemptLimit as number,
      attempt_count: attemptCount as number,
      lock_version: lockVersion as number,
      created_at: createdAt as string,
      holds_live_lease: holdsLiveLease as boolean,
      workspace_id: (workspace as { value: string | null }).value,
      payload_ref: (payloadRef as { value: string | null }).value,
    },
  };
}

/** One row of `job_attempts`, as `api/jobs.py::_attempt_payload` sends it. */
export interface AttemptRecord {
  readonly attempt_id: string;
  readonly attempt_no: number;
  readonly dispatch_version: number;
  readonly lease_generation: number;
  readonly lease_owner: string;
  readonly status: string;
  readonly not_before: string | null;
  readonly heartbeat_at: string | null;
  readonly expires_at: string | null;
  readonly started_at: string | null;
  readonly completed_at: string | null;
  readonly failure_class: string | null;
  readonly execution_attempt_state: string | null;
  /**
   * `true`, `false` or `null`. NEVER inferred, never defaulted — see the module docstring. The
   * dashboard does not compute this value and holds no copy of the rule that produces it: it is
   * `contracts.execution.may_retry_automatically`, reached through
   * `control_plane.domain.models.JobAttemptView`.
   */
  readonly may_retry_automatically: boolean | null;
}

export function parseAttempt(row: Record<string, unknown>): Read<AttemptRecord> {
  const attemptId = requireString(row, "attempt_id");
  const status = requireString(row, "status");
  const leaseOwner = requireString(row, "lease_owner");
  const attemptNo = requireNumber(row, "attempt_no");
  const dispatchVersion = requireNumber(row, "dispatch_version");
  const leaseGeneration = requireNumber(row, "lease_generation");

  const nullableFields = [
    "not_before",
    "heartbeat_at",
    "expires_at",
    "started_at",
    "completed_at",
    "failure_class",
    "execution_attempt_state",
  ] as const;
  const nullable: Record<string, string | null> = {};
  const missing: string[] = [];
  for (const field of nullableFields) {
    const read = requireNullableString(row, field);
    if (!read.present) {
      missing.push(field);
      continue;
    }
    nullable[field] = read.value;
  }

  if (attemptId === null) missing.push("attempt_id");
  if (status === null) missing.push("status");
  if (leaseOwner === null) missing.push("lease_owner");
  if (attemptNo === null) missing.push("attempt_no");
  if (dispatchVersion === null) missing.push("dispatch_version");
  if (leaseGeneration === null) missing.push("lease_generation");

  // The retry flag, read on its own terms. An ABSENT field is an error: turning it into `null`
  // here would quietly manufacture the "UNKNOWN" verdict, and turning it into a boolean would
  // manufacture a measurement. The screen must be able to say "control plane did not answer this"
  // and it can only do that if this read fails loudly.
  const retry = row["may_retry_automatically"];
  if (!("may_retry_automatically" in row)) {
    missing.push("may_retry_automatically (brak pola)");
  } else if (retry !== null && typeof retry !== "boolean") {
    missing.push(`may_retry_automatically (typ ${typeof retry})`);
  }

  if (missing.length > 0) {
    return failure(`próba: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }

  return {
    ok: true,
    value: {
      attempt_id: attemptId as string,
      attempt_no: attemptNo as number,
      dispatch_version: dispatchVersion as number,
      lease_generation: leaseGeneration as number,
      lease_owner: leaseOwner as string,
      status: status as string,
      not_before: nullable["not_before"] ?? null,
      heartbeat_at: nullable["heartbeat_at"] ?? null,
      expires_at: nullable["expires_at"] ?? null,
      started_at: nullable["started_at"] ?? null,
      completed_at: nullable["completed_at"] ?? null,
      failure_class: nullable["failure_class"] ?? null,
      execution_attempt_state: nullable["execution_attempt_state"] ?? null,
      may_retry_automatically: retry as boolean | null,
    },
  };
}

/** One job plus every attempt it has had, oldest attempt first (the control plane orders it). */
export interface JobDetailRecord {
  readonly job: JobSummary;
  readonly attempts: readonly AttemptRecord[];
  readonly attempt_limit: number;
  readonly attempts_used: number;
}

export function parseJobDetail(envelope: Record<string, unknown>): Read<JobDetailRecord> {
  const rawJob = envelope["job"];
  if (typeof rawJob !== "object" || rawJob === null || Array.isArray(rawJob)) {
    return failure("szczegóły joba: pole 'job' nie jest obiektem");
  }
  const job = parseJobSummary(rawJob as Record<string, unknown>);
  if (!job.ok) {
    return job;
  }

  const rawAttempts = envelope["attempts"];
  if (!Array.isArray(rawAttempts)) {
    // Not "then there are no attempts". A job with no attempts and a response that lost the
    // field are different facts, and only one of them is a record.
    return failure("szczegóły joba: pole 'attempts' nie jest listą, czyli to zepsuty odczyt");
  }
  const attempts: AttemptRecord[] = [];
  for (const [index, raw] of rawAttempts.entries()) {
    if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
      return failure(`szczegóły joba: próba ${index} nie jest obiektem`);
    }
    const parsed = parseAttempt(raw as Record<string, unknown>);
    if (!parsed.ok) {
      return parsed;
    }
    attempts.push(parsed.value);
  }

  const attemptLimit = requireNumber(envelope, "attempt_limit");
  const attemptsUsed = requireNumber(envelope, "attempts_used");
  if (attemptLimit === null || attemptsUsed === null) {
    return failure("szczegóły joba: brak attempt_limit albo attempts_used");
  }

  return {
    ok: true,
    value: { job: job.value, attempts, attempt_limit: attemptLimit, attempts_used: attemptsUsed },
  };
}

/** One row of `audit_events`, as `api/audit.py` sends it. */
export interface AuditEvent {
  readonly id: number;
  readonly occurred_at: string;
  readonly kind: string;
  readonly subject_ref: string | null;
  readonly actor_ref: string | null;
  readonly workspace_id: string | null;
  readonly payload: unknown;
}

export function parseAuditEvent(row: Record<string, unknown>): Read<AuditEvent> {
  const id = requireNumber(row, "id");
  const occurredAt = requireString(row, "occurred_at");
  const kind = requireString(row, "kind");
  const subject = requireNullableString(row, "subject_ref");
  const actor = requireNullableString(row, "actor_ref");
  const workspace = requireNullableString(row, "workspace_id");

  const missing: string[] = [];
  if (id === null) missing.push("id");
  if (occurredAt === null) missing.push("occurred_at");
  if (kind === null) missing.push("kind");
  if (!subject.present) missing.push("subject_ref");
  if (!actor.present) missing.push("actor_ref");
  if (!workspace.present) missing.push("workspace_id");
  if (!("payload" in row)) missing.push("payload");
  if (missing.length > 0) {
    return failure(`zdarzenie audytu: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }

  return {
    ok: true,
    value: {
      id: id as number,
      occurred_at: occurredAt as string,
      kind: kind as string,
      subject_ref: (subject as { value: string | null }).value,
      actor_ref: (actor as { value: string | null }).value,
      workspace_id: (workspace as { value: string | null }).value,
      payload: row["payload"],
    },
  };
}
