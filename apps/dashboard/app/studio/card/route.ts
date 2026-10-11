/**
 * `POST /studio/card`: jeden przycisk na karcie rozmowy, jedna droga do usługi (D-046).
 *
 * KARTA NIESIE TRASĘ, EKRAN JEJ NIE WYMYŚLA. Kontrakt (`docs/produkt/KARTY-ROZMOWY.md`) mówi,
 * że każda akcja ma `nazwa`, `trasa` i `payload`, a ekran wysyła je bez własnej wiedzy o
 * produkcie. Dzięki temu „dołącz do społeczności" z przycisku i `communities.join` z narzędzia
 * agenta to jedna operacja, a nie dwie implementacje, które kiedyś się rozejdą.
 *
 * DLATEGO WŁAŚNIE TEN HANDLER MUSI SPRAWDZAĆ ŚCIEŻKĘ. Formularz z trasą w polu ukrytym jest
 * wygodny dla karty i wygodny dla kogoś, kto podmieni to pole: bez sprawdzenia mielibyśmy
 * trasę, którą da się wskazać dowolny adres API i wysłać tam sesję operatora. Reguły są trzy
 * i wszystkie muszą przejść:
 *
 *   1. metoda ze zbioru `POST`, `PATCH`, `DELETE`. Akcja karty ZMIENIA stan; `GET` z tego
 *      formularza znaczyłby, że ktoś pomylił odczyt z działaniem;
 *   2. ścieżka jest na LIŚCIE, poniżej, i nic poza nią nie przejdzie. Pierwsza wersja tej
 *      reguły brzmiała „musi zaczynać się od `/api/workspaces/<workspace_id>/`" i była BŁĘDNA:
 *      dwie z trzech tras, których karty naprawdę używają, są w zakresie organizacji, nie
 *      przestrzeni (`/api/capsule/session`, `/api/communities`). Reguła strukturalna, która nie
 *      pasuje do produktu, odrzucałaby każde kliknięcie na karcie konta i społeczności, więc
 *      zastąpiła ją lista tego, co karta MOŻE wskazać. Trasa spoza listy to albo nowa funkcja
 *      (wtedy dopisujemy ją świadomie), albo podmienione pole (wtedy odmowa jest tu po to);
 *   2b. trasa z rodziny przestrzeni musi dodatkowo należeć do przestrzeni z formularza, żeby
 *      karta z jednej przestrzeni nie sięgnęła do drugiej;
 *   3. ścieżka nie zawiera `..` ani `//`. Pierwsze wychodzi z katalogu, drugie w niektórych
 *      klientach staje się adresem z hostem.
 *
 * `payload` jest brany z karty i UZUPEŁNIANY o pola, które człowiek wpisał na ekranie (dziś
 * tylko `tresc` poprawionego szkicu). Nadpisanie jest jednostronne: to, co wpisał człowiek,
 * wygrywa nad tym, co karta zapamiętała, bo po to było pole do pisania.
 */
import { allowedCardRoute, sessionCheckPlatform } from "../../../lib/card-routes";
import {
  isRoomReturn,
  roomActionOf,
  roomRefusalKey,
  roomSuccessKey,
} from "../../../lib/room-actions";
import { CAPSULE_CONFIRM_TIMEOUT_MS, callCardAction } from "../../../lib/control-plane";
import { sendSessionMessage } from "../../../lib/sessions-client";
import { accountHandle, platformLabel } from "../../../lib/studio-labels";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

const METHODS = ["POST", "PATCH", "DELETE"] as const;

/**
 * Pola, które człowiek może dopisać do ładunku z ekranu. Lista zamknięta i krótka z rozmysłu.
 *
 * `text`, a NIE `tresc`, i ta nazwa jest z API zatwierdzania (`.../approvals/{rewizja}/revise`
 * przyjmuje `{"text": "..."}`). Pole formularza nazywa się tak samo, bo tłumaczenie nazwy w
 * połowie drogi jest dokładnie tym miejscem, w którym poprawiony szkic zapisuje się jako pusty.
 */
const FROM_SCREEN = ["text"] as const;

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const workspaceId = field(form, "workspace_id");
  const method = field(form, "metoda").toUpperCase();
  const path = field(form, "sciezka");
  const rawPayload = field(form, "payload");

  if (workspaceId === "" || path === "") return backTo(back, "refused");
  if (!METHODS.some((known) => known === method)) return backTo(back, "refused");
  if (path.includes("..") || path.includes("//")) return backTo(back, "refused");
  if (!allowedCardRoute(path, workspaceId)) return backTo(back, "refused");

  let payload: Record<string, unknown>;
  try {
    const parsed: unknown = rawPayload === "" ? {} : JSON.parse(rawPayload);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return backTo(back, "refused");
    }
    payload = parsed as Record<string, unknown>;
  } catch {
    return backTo(back, "refused");
  }

  for (const name of FROM_SCREEN) {
    const value = field(form, name);
    if (value !== "") payload[name] = value;
  }

  // A session check reads the platform and the backend measured up to a minute, so it waits as
  // long as the Operations confirm does; every other card route keeps the default few seconds.
  const checkedPlatform = sessionCheckPlatform(path);
  const outcome = await callCardAction(
    auth.token,
    method,
    path,
    payload,
    checkedPlatform === null ? {} : { timeoutMs: CAPSULE_CONFIRM_TIMEOUT_MS },
  );
  const isCheck =
    checkedPlatform !== null && method === "POST" && field(form, "akcja") === "sprawdz";
  // STOPPED WAITING IS NOT "FAILED" (`lib/capsule-flow.ts::confirmDecision`): the check may have
  // finished upstream and recorded the account, so it gets the sentence that says exactly that
  // instead of "unreachable", which would send the person to sign in a second time.
  if (isCheck && !outcome.ok && !outcome.unauthorized && outcome.timedOut === true) {
    return backTo(back, "account_check_pending");
  }
  // `w_rozmowie: false` z trasy poprawki znaczy „zapisane, ale ślad tego nie niesie". Zdanie
  // „zlecone" byłoby wtedy prawdziwe o wysłaniu i mylące o skutku, a to jest ta sama rodzina
  // co „Dołączono" po samej obserwacji. `null` (trasa tego nie mówi) zostaje przy „zlecone".
  const unseen = outcome.ok && outcome.value.inConversation === false;
  // THE ROOM HAS NO CARD (D-055). Its three buttons use these same routes, but "the card in the
  // conversation will show the result" would be a sentence about a place the person is not in,
  // and a 409 on approve has a precise remedy (sign the scope on "Obecność"). Decided in
  // `lib/room-actions.ts`, and only for a person who came from the Room.
  const roomAction = isRoomReturn(back) ? roomActionOf(path) : null;
  if (outcome.ok && roomAction !== null) {
    const status = outcome.value.status;
    if (status >= 400) return backTo(back, roomRefusalKey(roomAction, status, outcome.value.detail));
    if (status >= 200 && status < 300) return backTo(back, roomSuccessKey(roomAction));
  }
  if (outcome.ok && outcome.value.status >= 400) return backTo(back, "card_refused");
  const projectId = field(form, "project_id");
  const sessionId = field(form, "session_id");
  if (outcome.ok && outcome.value.status >= 200 && outcome.value.status < 300 &&
      method === "POST" && field(form, "akcja") === "powierz" && projectId && sessionId &&
      path === `/api/workspaces/${workspaceId}/projects/${projectId}/delegation`) {
    // The successful click is the user's action. Continue its existing task; do not leave
    // the agent waiting for the user to repeat that the scope has just been entrusted.
    const continued = await sendSessionMessage(auth.token, workspaceId, projectId, sessionId,
      "Uruchomiłem powierzenie z karty. Kontynuuj wcześniejsze zadanie w zapisanym zakresie.");
    if (!continued.ok || continued.value.status >= 300) return backTo(back, "card_saved_unseen");
  }
  if (isCheck && outcome.ok && outcome.value.status >= 200 && outcome.value.status < 300) {
    // "CONNECTED" IS THE CONTROL PLANE'S MEASUREMENT, never the click: only `polaczone: true`
    // continues the conversation, `false` is a check that found nobody signed in, and a missing
    // field (an older backend) falls through to the neutral "zlecone" below.
    if (outcome.value.connected === false) return backTo(back, "account_not_connected");
    if (outcome.value.connected === true && projectId && sessionId) {
      // The same move as after `powierz` above: the person did what the agent asked for, so the
      // agent hears it now instead of waiting for the person to repeat it in words. The platform
      // comes from the card's payload when it names one, else from the route that was checked.
      const named = payload["platform"];
      const platform = typeof named === "string" && named.trim() !== "" ? named.trim() : checkedPlatform;
      const who =
        outcome.value.account === null ? "" : ` (${accountHandle(platform, outcome.value.account)})`;
      const continued = await sendSessionMessage(auth.token, workspaceId, projectId, sessionId,
        `Konto ${platformLabel(platform)} jest połączone${who}. Kontynuuj pracę.`);
      return backTo(back,
        !continued.ok || continued.value.status >= 300 ? "account_connected_unsent" : "account_connected");
    }
  }
  return verdictOf(outcome, unseen ? "card_saved_unseen" : "card_done", back);
}
