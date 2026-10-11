/**
 * `POST /studio/capsule/open`: "Połącz X" on an account card opens the AGENT'S BROWSER in a new tab.
 *
 * THE SAME MECHANISM AS `/community/capsule/open`, moved to where the person actually is. The form
 * on the card carries `target="_blank"`, so this handler runs in a NEW tab: the 303 below lands
 * that tab on the capsule's viewer, the person signs in there like anywhere else, and the
 * conversation stays open in the tab they came from, with "Gotowe, sprawdź połączenie" waiting.
 *
 * WHY NOT `/studio/card`, which carries every other card action. That handler answers 303 back to
 * the conversation, and this one has to answer 303 to the ticket URL the control plane returns.
 * Until 26.09 the card posted `polacz` there, `callCardAction` kept `detail` and dropped `href`,
 * and the button said "zlecone" while nothing opened: the person was never shown a login screen.
 *
 * THE PLATFORM IS A CLOSED SET (`lib/capsule-flow.ts::capsulePlatform`). The card writes it into a
 * hidden field from its own `polacz` payload, and a hidden field is only a request until checked.
 *
 * THE REDIRECT LEAVES THIS APPLICATION, so it is checked against the configured viewer origins
 * before any browser follows it (`openDecision`, the same function the Operations handler uses).
 * X may land on the X capsule's own screen (D-051), so its list has that origin too.
 *
 * A FAILURE LANDS IN THE NEW TAB AS A SENTENCE: back to `return_to`, the conversation, validated as
 * a same-origin path, with a key from `lib/studio-info.ts`. Not a blank tab and not an error page
 * in a window the person did not expect to have.
 *
 * `tokenOrLogin` is this package's route-handler form of `requireOperator`: the same session check
 * against the control plane, answered with a 303 instead of a thrown redirect.
 */
import { capsulePlatform, capsuleViewerOrigins, openDecision } from "../../../../lib/capsule-flow";
import { openCapsuleSession } from "../../../../lib/control-plane";
import { LOGIN_PATH } from "../../../../lib/routes";
import { backTo, field, formOf, safeReturn, seeOther, tokenOrLogin } from "../../_handler";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "capsule_open_refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const platform = capsulePlatform(field(form, "platform"));
  if (platform === null) return backTo(back, "capsule_open_refused");

  const decision = openDecision(
    await openCapsuleSession(auth.token, platform),
    capsuleViewerOrigins(platform),
  );
  if (decision.kind === "login") return seeOther(LOGIN_PATH);
  if (decision.kind === "info") return backTo(back, decision.info);
  return seeOther(decision.href);
}
