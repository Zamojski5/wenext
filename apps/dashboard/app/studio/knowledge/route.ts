/**
 * `POST /studio/knowledge` — the six writes of the "Materiały" screen.
 *
 * `add_source`, `refresh`, `search`, `write_item`, `correct`, `retract`. One closed set and a
 * refusing default, like every other studio handler: a hidden field must not be able to name an
 * operation nobody declared.
 *
 * `add_source` DOES NOT FAIL WHEN THE ADDRESS COULD NOT BE READ, and that is not laxity. The
 * control plane answers 201 with a material in state `UNAVAILABLE` and a reason, because the
 * person asked for the address to be added and is owed the record of what happened to it. So the
 * message here is deliberately neutral: it says the material is on the list, not that it was read.
 */
import {
  addKnowledgeSource,
  correctKnowledgeItem,
  recordPublicationFeedback,
  refreshKnowledgeSource,
  requestExperienceSweep,
  retractKnowledgeItem,
  searchTheWeb,
  writeKnowledgeItem,
} from "../../../lib/control-plane";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

const ACTIONS = [
  "add_source",
  "refresh",
  "search",
  "write_item",
  "correct",
  "retract",
  // P5: the person's short correction under one publication, and the request to measure what
  // came of our publications. Both are the person asking for something cheap; neither publishes.
  "feedback",
  "measure",
] as const;

const VERDICTS = ["TOO_LONG", "NOT_LIKE_THIS", "GOOD", "DONT_REPLY_HERE"] as const;

const SOURCE_KINDS = ["WEB_PAGE", "RSS", "NEWSLETTER", "USER_NOTE"] as const;
const ITEM_KINDS = ["FACT", "INTERPRETATION", "PREFERENCE", "EXPERIENCE"] as const;

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const action = field(form, "action");
  if (!ACTIONS.some((known) => known === action)) return backTo(back, "refused");

  const workspaceId = field(form, "workspace_id");
  const projectId = field(form, "project_id");
  if (workspaceId === "") return backTo(back, "refused");

  if (action === "add_source") {
    const kind = field(form, "kind");
    if (!SOURCE_KINDS.some((known) => known === kind)) return backTo(back, "refused");
    const locator = field(form, "locator");
    const text = field(form, "text");
    // A note needs its text; everything else needs an address. Refused here rather than sent on,
    // so an empty form does not become a round trip that comes back a 400.
    if (kind === "USER_NOTE" ? text === "" : locator === "") return backTo(back, "refused");
    return verdictOf(
      await addKnowledgeSource(auth.token, workspaceId, {
        kind,
        ...(locator === "" ? {} : { locator }),
        title: field(form, "title"),
        ...(text === "" ? {} : { text }),
        ...(projectId === "" ? {} : { project_id: projectId }),
      }),
      "knowledge_added",
      back,
    );
  }

  if (action === "refresh") {
    const sourceId = field(form, "source_id");
    if (sourceId === "") return backTo(back, "refused");
    return verdictOf(
      await refreshKnowledgeSource(auth.token, workspaceId, sourceId),
      "knowledge_refresh_queued",
      back,
    );
  }

  if (action === "search") {
    const query = field(form, "query");
    if (query === "") return backTo(back, "refused");
    return verdictOf(
      await searchTheWeb(auth.token, workspaceId, query, projectId === "" ? undefined : projectId),
      "knowledge_searched",
      back,
    );
  }

  if (action === "feedback") {
    const requestId = field(form, "request_id");
    const verdict = field(form, "verdict");
    if (requestId === "" || !VERDICTS.some((known) => known === verdict)) {
      return backTo(back, "refused");
    }
    return verdictOf(
      await recordPublicationFeedback(auth.token, workspaceId, {
        request_id: requestId,
        verdict,
        note: field(form, "note"),
        ...(projectId === "" ? {} : { project_id: projectId }),
      }),
      "knowledge_feedback",
      back,
    );
  }

  if (action === "measure") {
    return verdictOf(
      await requestExperienceSweep(auth.token, workspaceId),
      "knowledge_measure_queued",
      back,
    );
  }

  if (action === "retract") {
    const itemId = field(form, "item_id");
    const reason = field(form, "reason");
    if (itemId === "" || reason === "") return backTo(back, "refused");
    return verdictOf(
      await retractKnowledgeItem(
        auth.token,
        workspaceId,
        itemId,
        reason,
        projectId === "" ? undefined : projectId,
      ),
      "knowledge_retracted",
      back,
    );
  }

  const kind = field(form, "kind");
  if (!ITEM_KINDS.some((known) => known === kind)) return backTo(back, "refused");
  const statement = field(form, "statement");
  if (statement === "") return backTo(back, "refused");
  const sourceId = field(form, "source_id");
  const about = field(form, "about");
  const item = {
    kind,
    statement,
    ...(about === "" ? {} : { about }),
    // The person typing on this screen is the person, never the agent. `stated_by` is not a form
    // field for that reason: a screen that let the user file a finding as the agent's would make
    // "kto to ustalił" unanswerable.
    stated_by: "USER",
    ...(projectId === "" ? {} : { project_id: projectId }),
    ...(sourceId === "" ? {} : { source_id: sourceId }),
  };

  if (action === "write_item") {
    return verdictOf(
      await writeKnowledgeItem(auth.token, workspaceId, item),
      "knowledge_item_written",
      back,
    );
  }

  const itemId = field(form, "item_id");
  if (itemId === "") return backTo(back, "refused");
  return verdictOf(
    await correctKnowledgeItem(auth.token, workspaceId, itemId, item),
    "knowledge_item_corrected",
    back,
  );
}
