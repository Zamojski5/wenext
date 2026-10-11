/** `POST /community/resume` — bring parked jobs back (`POST /api/jobs/waiting/resume`). */
import { resumeWaitingJobs } from "../../../../lib/control-plane";
import { tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  const auth = await tokenOrLogin();
  if ("redirect" in auth) return auth.redirect;
  return verdictOf(await resumeWaitingJobs(auth.token), "resumed", "unreachable");
}
