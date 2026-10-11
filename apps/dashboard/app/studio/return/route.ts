/**
 * `POST /studio/return` — jedna akcja: korekta kierunku po powrocie.
 *
 * Osobna trasa, a nie akcja w `/studio/session`, z dwóch powodów. Pierwszy: tamta trasa należy do
 * rozmowy i jej zamknięty zbiór akcji opisuje pracę z sesją, a ta jest odpowiedzią na
 * podsumowanie nieobecności. Drugi: korekta może TRAFIĆ do nowej sesji, a tamta trasa wymaga
 * identyfikatora sesji w formularzu, którego tutaj z założenia nie ma.
 *
 * Werdykt po zapisie mówi, co się stało z jednym zdaniem: czy poszło do trwającej sesji, czy
 * zaczęło nową. Człowiek ma prawo wiedzieć, czy właśnie obudził agenta.
 */
import { sendReturnCorrection } from "../../../lib/control-plane";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const action = field(form, "action");
  const workspaceId = field(form, "workspace_id");
  const projectId = field(form, "project_id");
  const text = field(form, "text");
  if (action !== "correct" || workspaceId === "" || projectId === "" || text === "") {
    return backTo(back, "refused");
  }
  return verdictOf(
    await sendReturnCorrection(auth.token, workspaceId, projectId, text),
    "correction_sent",
    back,
  );
}
