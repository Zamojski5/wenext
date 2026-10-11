import { JobsView, type JobsRow, type LeaseRead } from "../../../components/views/jobs-view";
import { CONTROL_PLANE_CONCURRENCY, mapWithConcurrency } from "../../../lib/concurrency";
import { getJob, getJobs } from "../../../lib/control-plane";
import { requireOperator } from "../../../lib/guard";
import type { JobSummary } from "../../../lib/schema";

/**
 * `GET /jobs` — the queue, read from PostgreSQL through the control plane on every request.
 *
 * `force-dynamic` because a prerendered queue is a picture of the build, not of the queue. This is
 * the same reason `app/healthz/route.ts` carries it.
 *
 * WHY THERE IS A SECOND READ PER ROW. `GET /api/jobs` does not carry `lease_owner`,
 * `lease_expires_at` or `heartbeat_at` — measured in `api/jobs.py::_job_payload`, which sends
 * `attempt_count` and `holds_live_lease` and no lease columns. Those live on `job_attempts` and
 * come back from `GET /api/jobs/{job_id}`. The alternative was to print "nie ma w odpowiedzi API"
 * in three columns of every row, which is honest and useless. So the page reads the detail of each
 * listed job, at most `CONTROL_PLANE_CONCURRENCY` at a time, and a row whose detail read fails says
 * "nie odczytano" WITH THE REASON in exactly those three cells while the rest of the queue renders.
 *
 * NOTHING ON THIS SCREEN MUTATES ANYTHING. E1.F1's scope names no mutating action on jobs, and
 * reaper plus attempt limit are required to work without a human (L5213-5214).
 */
export const dynamic = "force-dynamic";

function leaseOf(job: JobSummary, detail: Awaited<ReturnType<typeof getJob>>): LeaseRead {
  if (!detail.ok) {
    return { kind: "unread", reason: detail.reason };
  }
  if (detail.value === null) {
    // The job was in the list a moment ago and its detail is a 404. That is a real fact about a
    // queue being worked on, not a missing lease, and it must not read as one.
    return {
      kind: "unread",
      reason: `job ${job.job_id} zniknął między listą a odczytem szczegółów (404)`,
    };
  }
  const attempts = detail.value.attempts;
  if (attempts.length === 0) {
    return { kind: "read", latestAttempt: null };
  }
  // The control plane returns attempts `ORDER BY attempt_no`, so the last element is the newest.
  const latest = attempts[attempts.length - 1] as (typeof attempts)[number];
  return {
    kind: "read",
    latestAttempt: {
      leaseOwner: latest.lease_owner,
      expiresAt: latest.expires_at,
      heartbeatAt: latest.heartbeat_at,
      leaseGeneration: latest.lease_generation,
    },
  };
}

export default async function JobsPage() {
  const gate = await requireOperator();
  const now = new Date();

  if (gate.kind === "unreachable") {
    return <JobsView now={now} reason={gate.reason} rows={undefined} />;
  }

  const page = await getJobs(gate.token);
  if (!page.ok) {
    // `undefined`, never `[]`. A failed read rendered as an empty queue is the whole failure this
    // package exists to prevent (L5241 point 5).
    return <JobsView now={now} reason={page.reason} rows={undefined} />;
  }

  const rows: JobsRow[] = await mapWithConcurrency(
    page.value.rows,
    CONTROL_PLANE_CONCURRENCY,
    async (job) => ({ job, lease: leaseOf(job, await getJob(gate.token, job.job_id)) }),
  );

  return <JobsView now={now} rows={rows} truncated={page.value.truncated} />;
}
