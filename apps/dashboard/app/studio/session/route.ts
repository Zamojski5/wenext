/**
 * `POST /studio/session`: the four things a person does with an agent session.
 *
 * Start it, say something to it, stop it, resume it. Four actions behind one handler, dispatched
 * on a CLOSED set with a refusing default, exactly like the other studio handlers: a permissive
 * default would turn a misspelled hidden field into "do whatever the first branch does".
 *
 * NONE OF THESE WAITS FOR THE AGENT. Each one is one control plane write that enqueues a job,
 * and then a 303 back to the project. The person sees the conversation on the next render and
 * the turn happens in a worker. A handler that waited for the model would hold the browser for
 * minutes and lose the work when the tab was closed.
 *
 * "Send" is allowed while a turn is running, and that is deliberate: the message lands in the
 * trace and the NEXT turn picks it up. Refusing it would make the person wait to be allowed to
 * speak, which is the opposite of a conversation.
 */
import {
  continueSession,
  interruptSession,
  resumeSession,
  sendSessionMessage,
  startSession,
} from "../../../lib/sessions-client";
import { conversationHref } from "../../../lib/routes";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

// 0026: „continue" to nowa rozmowa wyrastająca z tej, która doszła do limitu. Osobna akcja,
// nie wariant „resume": tamta wznawia TĘ SAMĄ sesję, a ta zakłada następną i mówi to wprost.
const ACTIONS = ["start", "message", "interrupt", "resume", "continue"] as const;

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const action = field(form, "action");
  const workspaceId = field(form, "workspace_id");
  const projectId = field(form, "project_id");
  if (!ACTIONS.some((known) => known === action) || workspaceId === "" || projectId === "") {
    return backTo(back, "refused");
  }

  if (action === "start") {
    const goal = field(form, "goal");
    // An empty goal is refused HERE as well as in the control plane. The session would be
    // refused there anyway; stopping it here means the person gets the sentence without a round
    // trip, and the control plane's own check stays as the one that actually guarantees it.
    if (goal === "") return backTo(back, "refused");
    const connection = field(form, "codex_connection_id");
    const started = await startSession(
      auth.token,
      workspaceId,
      projectId,
      goal,
      connection === "" ? null : connection,
    );
    // D-045: po wysłaniu pierwszego zdania człowiek ląduje W ROZMOWIE, którą właśnie zaczął, a
    // nie z powrotem na pustym projekcie, który zaraz pokazałby to samo pytanie. Gdy control
    // plane nie odesłał identyfikatora, wracamy tam, skąd przyszliśmy: rozmowa powstała i widać
    // ją w pasku, więc kosztuje to jedno kliknięcie, a nie adres zbudowany ze zgadywania.
    const where =
      started.ok && started.value.sessionId !== null && started.value.status < 300
        ? conversationHref(workspaceId, projectId, started.value.sessionId)
        : back;
    return verdictOf(started, "session_started", where);
  }

  const sessionId = field(form, "session_id");
  if (sessionId === "") return backTo(back, "refused");

  if (action === "message") {
    const text = field(form, "text");
    if (text === "") return backTo(back, "refused");
    return verdictOf(
      await sendSessionMessage(auth.token, workspaceId, projectId, sessionId, text),
      "session_message",
      back,
    );
  }

  if (action === "continue") {
    return verdictOf(
      await continueSession(auth.token, workspaceId, projectId, sessionId),
      "session_continued",
      back,
    );
  }

  if (action === "interrupt") {
    return verdictOf(
      await interruptSession(auth.token, workspaceId, projectId, sessionId),
      "session_interrupted",
      back,
    );
  }

  return verdictOf(
    await resumeSession(auth.token, workspaceId, projectId, sessionId),
    "session_resumed",
    back,
  );
}
