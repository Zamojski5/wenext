import { JobDetailView } from "../../../../components/views/job-detail-view";
import { getJob } from "../../../../lib/control-plane";
import { requireOperator } from "../../../../lib/guard";

/**
 * `GET /jobs/<job_id>` — one job, every attempt, and the retry verdict. READ-ONLY.
 *
 * FOUR OUTCOMES, FOUR SCREENS, and none of them is allowed to impersonate another:
 *
 *   | outcome                              | screen                                          |
 *   | ------------------------------------ | ----------------------------------------------- |
 *   | no session                           | redirect to `/login` (the guard)                |
 *   | control plane unreachable            | "brak odpowiedzi" with the reason               |
 *   | control plane says 404               | "nie ma takiego joba" — a MEASURED fact         |
 *   | the job exists                       | header, retry verdict, attempts table           |
 *
 * The third row is the one worth naming: a 404 is a successful read whose answer is "there is no
 * such job in this organization". Rendering it as an empty attempt list would claim a job with no
 * attempts, and rendering it as "brak odpowiedzi" would claim a failed read. Both are lies about
 * a measurement that did happen.
 *
 * There is no retry action here for any state — see `components/views/job-detail-view.tsx` for why
 * that is the point rather than an omission.
 */
export const dynamic = "force-dynamic";

export default async function JobDetailPage({
  params,
}: {
  // Next 15+ hands route params as a promise; awaiting it is not optional.
  readonly params: Promise<{ readonly jobId: string }>;
}) {
  const { jobId } = await params;
  const gate = await requireOperator();
  const now = new Date();

  if (gate.kind === "unreachable") {
    return <JobDetailView detail={undefined} jobId={jobId} now={now} reason={gate.reason} />;
  }

  const detail = await getJob(gate.token, jobId);
  if (!detail.ok) {
    return <JobDetailView detail={undefined} jobId={jobId} now={now} reason={detail.reason} />;
  }
  return <JobDetailView detail={detail.value} jobId={jobId} now={now} />;
}
