/** `POST /community/feedback` — the operator's verdict on one judgment (`POST /api/judgments/{id}/feedback`). */
import { recordFeedback } from "../../../../lib/control-plane";
import { VERDICTS } from "../../../../components/views/community-view";
import { backWith, field, formOf, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const auth = await tokenOrLogin();
  if ("redirect" in auth) return auth.redirect;
  const form = await formOf(request);
  if (form === null) return backWith("feedback_refused");
  const judgmentId = field(form, "judgment_id");
  const verdict = field(form, "verdict");
  const note = field(form, "note");
  if (judgmentId === "" || !VERDICTS.some((known) => known.value === verdict)) {
    return backWith("feedback_refused");
  }
  return verdictOf(
    await recordFeedback(auth.token, judgmentId, verdict, note === "" ? null : note),
    "feedback_saved",
    "feedback_refused",
  );
}
