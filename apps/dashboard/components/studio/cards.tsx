/**
 * Karty w rozmowie: konto, społeczność, szkic, powierzenie (D-046).
 *
 * UKŁAD JEST Z MAKIETY 1:1 (`docs/produkt/prototyp/chat-inline-actions.html`), a treść z
 * kontraktu (`docs/produkt/KARTY-ROZMOWY.md`). Klasy `st-*` są tymi samymi napisami, co w
 * makiecie, bo arkusz `app/chat.css` jest z niej przepisany maszynowo i każda inna nazwa
 * znaczyłaby element bez stylu.
 *
 * TRZY ZASADY, KTÓRE OBOWIĄZUJĄ KAŻDĄ KARTĘ, i wszystkie trzy są o tym samym: ekran nie wie
 * nic o produkcie ponad to, co dostał.
 *
 *   1. **Przycisk bierze się z `akcje`, nie z `stan`.** Komponent nie decyduje, czy wolno
 *      opublikować: pyta kartę, czy przyszła akcja `opublikuj`. Dzięki temu „opublikuj bez
 *      delegacji" nie jest przypadkiem do zapamiętania w dwóch miejscach, tylko akcją, której
 *      serwer nie wystawił.
 *   2. **Sukces jest napisem serwera, nie skutkiem kliknięcia.** „Połączono", „Dołączono",
 *      „Opublikowano" rysuje się ze stanu karty, który przychodzi po potwierdzeniu. Kliknięcie
 *      zmienia stan na trwający i tyle.
 *   3. **Odmowa to treść, nie wyjątek.** Stan `blad` pokazuje `powod` przy karcie i zostawia
 *      akcję ponowienia, o ile serwer ją wystawił.
 *
 * ŻADNEGO IDENTYFIKATORA NA EKRANIE. `karta_id` jedzie w polu ukrytym formularza i w `data-`,
 * bo formularz musi wiedzieć, czego dotyczy; w tekście dla człowieka nie ma go nigdzie.
 */
import {
  actionOf,
  cardFlag,
  cardList,
  cardText,
  type CardAction,
  type ConversationCard,
} from "../../lib/cards-schema";
import { plural } from "../../lib/format";
import { peopleHref } from "../../lib/routes";
import { accountHandle, platformLabel, warsawMoment } from "../../lib/studio-labels";
import { Icon } from "./icons";

/** Nazwy sufitów po ludzku. Klucz spoza listy jedzie jak przyszedł, zamiast zniknąć. */
const LIMIT_LABELS: Readonly<Record<string, string>> = {
  publikacje_godzina: "publikacji na godzinę",
  publikacje_doba: "publikacji na dobę",
  wlasne_posty_doba: "własnych postów na dobę",
  tury_modelu_doba: "uruchomień agenta na dobę",
  zaplanowane_powroty: "zaplanowanych powrotów",
};

export interface CardsProps {
  readonly cards: readonly ConversationCard[];
  readonly workspaceId: string;
  readonly projectId: string;
  /**
   * Rozmowa, z której pochodzą karty, albo `null`, gdy nie pochodzą z żadnej.
   *
   * `null` ma dokładnie jedno źródło i jest nim ekran „Społeczności" (E2): tam karty są
   * projekcją ODCZYTU społeczności przestrzeni, a nie śladu tury. Puste `""` znaczyłoby
   * „rozmowa o pustym identyfikatorze", więc go nie wysyłamy wcale; handler `/studio/card` i
   * tak czyta z formularza tylko przestrzeń, trasę i ładunek.
   */
  readonly sessionId: string | null;
  readonly returnTo: string;
}

/** Wspólne pola ukryte każdego formularza akcji. Jedno miejsce, żeby żadne się nie zgubiło. */
function ActionFields({
  action,
  card,
  workspaceId,
  projectId,
  sessionId,
  returnTo,
}: {
  readonly action: CardAction;
  readonly card: ConversationCard;
  readonly workspaceId: string;
  readonly projectId: string;
  readonly sessionId: string | null;
  readonly returnTo: string;
}) {
  return (
    <>
      <input name="workspace_id" type="hidden" value={workspaceId} />
      <input name="project_id" type="hidden" value={projectId} />
      {sessionId === null ? null : <input name="session_id" type="hidden" value={sessionId} />}
      <input name="karta_id" type="hidden" value={card.karta_id} />
      <input name="akcja" type="hidden" value={action.nazwa} />
      <input name="metoda" type="hidden" value={action.trasa.metoda} />
      <input name="sciezka" type="hidden" value={action.trasa.sciezka} />
      <input name="payload" type="hidden" value={JSON.stringify(action.payload)} />
      <input name="return_to" type="hidden" value={returnTo} />
    </>
  );
}

/** Zdanie powodu pod kartą. Rysowane WYŁĄCZNIE przy błędzie, bo pusty wiersz udaje usterkę. */
function Reason({ card }: { readonly card: ConversationCard }) {
  if (card.stan !== "blad" || card.powod === null) return null;
  return (
    <p className="st-card-reason" data-reason="1">
      {card.powod}
    </p>
  );
}

/**
 * The connect button in the platform's own grammar (26.09). The name is the shared label
 * (`lib/studio-labels.ts::platformLabel`); only the button declines, because Polish does:
 * "Połącz Reddita", but "Połącz X" and "Połącz LinkedIn". A card naming no platform keeps the
 * mock-up's words.
 */
const CONNECT_BUTTONS: Readonly<Record<string, string>> = { REDDIT: "Połącz Reddita" };

function connectLabel(platform: string | null): string {
  if (platform === null) return "Połącz konto";
  return CONNECT_BUTTONS[platform] ?? `Połącz ${platformLabel(platform)}`;
}

/**
 * The one card route whose answer is a place to GO rather than a new state: `POST
 * /api/capsule/session` returns the ticket URL of the agent's browser. A `polacz` pointing there
 * becomes the new-tab form of `/studio/capsule/open`; a `polacz` pointing anywhere else keeps the
 * ordinary `/studio/card` road, so the card still carries its route and the screen invents none.
 */
function opensAgentBrowser(action: CardAction): boolean {
  return action.trasa.metoda.toUpperCase() === "POST" && action.trasa.sciezka === "/api/capsule/session";
}

/**
 * Which sign-in the agent's browser opens: the action's payload names it (`{"platform": "X"}`);
 * a card from before 26.09 names nothing there, and then the card's own platform decides, and
 * only a card naming neither falls back to Reddit, which every such card was.
 */
function capsulePlatformOf(action: CardAction, card: ConversationCard): string {
  const named = action.payload["platform"];
  if (typeof named === "string" && named.trim() !== "") return named.trim();
  return cardText(card, "platforma") ?? "REDDIT";
}

/**
 * The form that opens the agent's browser in a NEW TAB. Plain HTML, no script: it posts inside the
 * new tab and `/studio/capsule/open` answers with a 303 to the ticket URL, while the conversation
 * stays where it was. The handler acts on `platform` alone (checked there against a closed set);
 * the other fields name the card and the way back, as on every card form.
 */
function OpenAgentBrowser({
  action,
  card,
  workspaceId,
  projectId,
  sessionId,
  returnTo,
}: CardsProps & { readonly action: CardAction; readonly card: ConversationCard }) {
  return (
    <form action="/studio/capsule/open" method="post" rel="noopener noreferrer" target="_blank">
      <input name="workspace_id" type="hidden" value={workspaceId} />
      <input name="project_id" type="hidden" value={projectId} />
      {sessionId === null ? null : <input name="session_id" type="hidden" value={sessionId} />}
      <input name="karta_id" type="hidden" value={card.karta_id} />
      <input name="platform" type="hidden" value={capsulePlatformOf(action, card)} />
      <input name="return_to" type="hidden" value={returnTo} />
      <button className="st-button" type="submit">
        Kontynuuj <Icon name="arrow-right" />
      </button>
    </form>
  );
}

function AccountCard(props: CardsProps & { readonly card: ConversationCard }) {
  const { card } = props;
  const connected = card.stan === "polaczone";
  const connecting = card.stan === "lacze";
  const unavailable = card.stan === "niedostepne";
  const profile = cardText(card, "profil") ?? "Profil projektu";
  const platformCode = cardText(card, "platforma");
  const platform = platformCode === null ? "Konto" : platformLabel(platformCode);
  const connect = actionOf(card, "polacz");
  const check = actionOf(card, "sprawdz");
  const cancel = actionOf(card, "anuluj");
  const login = cardText(card, "adres_logowania");
  // `konto` is empty on a card whose account does not exist yet: a new X account is created from
  // whoever signs in (26.09). The instruction then asks for the right person, not for a handle.
  const account = cardText(card, "konto");
  const connectedAs = cardText(card, "polaczone_jako") ?? account;
  // WHY IT IS NOT CONNECTED, in the capsule's words (`docs/produkt/KARTY-ROZMOWY.md`, KONTO).
  // Dropped by this screen until 26.09, so the reason was computed and went nowhere a person looks.
  const reading = cardText(card, "odczyt_kapsuly");
  const connectText = connectLabel(platformCode);
  // THE INSTRUCTION STANDS ONLY WHERE THERE IS SOMETHING TO DO. A connected card needs none, a
  // paused one cannot be fixed by signing in, and a card with no action would describe buttons
  // that are not there.
  const guided = !connected && !unavailable && (connect !== null || check !== null);
  const signInAs =
    account !== null
      ? `jako ${accountHandle(platformCode, account)}`
      : platformCode === null
        ? "kontem, którym agent ma działać"
        : `kontem ${platform}, którym agent ma działać`;
  // Step 2 names "Gotowe" only when that button is on the card: an older Reddit card has no
  // `sprawdz`, and an instruction pointing at a missing button is a small lie.
  const instruction =
    check === null
      ? `W oknie przeglądarki agenta zaloguj się ${signInAs}, potem wróć do tej rozmowy.`
      : `1. W oknie przeglądarki agenta zaloguj się ${signInAs}. 2. Wróć tutaj i kliknij Gotowe.`;

  return (
    <div className={`st-account${connected ? " connected" : ""}`} data-card={card.karta_id}>
      <span aria-hidden="true" className="st-avatar">
        {profile.slice(0, 1).toUpperCase()}
      </span>
      <span className="st-account-copy">
        <span>{`${platform} · ${profile}`}</span>
        <small data-sub="1">
          {connected
            ? "Gotowe do działania"
            : connecting
              ? "Wracam do rozmowy…"
              : unavailable
                ? "Konto wstrzymane"
                : card.stan === "blad"
                  ? "Nie udało się połączyć"
                  : "Brakuje połączenia"}
        </small>
      </span>

      {connected ? (
        <span className="st-connected" data-state="polaczone">
          <Icon name="check" />
          {connectedAs === null
            ? " Połączono"
            : ` Połączono jako ${accountHandle(platformCode, connectedAs)}`}
        </span>
      ) : null}

      {/* KONTO WSTRZYMANE NIE DOSTAJE PRZYCISKU, bo logowanie go nie odblokuje, a przycisk,
          który obiecuje naprawę i jej nie robi, kłamie dopiero po kliknięciu. Zamiast niego
          zdanie i droga do miejsca, w którym da się z tym coś zrobić naprawdę. Zdanie jest
          nasze, nie z karty: stan `niedostepne` nie niesie tekstu, bo to jest jeden stan, a
          nie lista powodów. */}
      {unavailable ? (
        <span className="st-account-blocked" data-state="niedostepne">
          Tym kontem teraz nie zadziałam.{" "}
          <a href={peopleHref(props.workspaceId)}>Profile i konta</a>
        </span>
      ) : null}

      {/* Okno logowania kapsuły otwiera się w NOWEJ KARCIE, a rozmowa zostaje otwarta: człowiek
          wraca do niej i widzi stan, który przyszedł z serwera, a nie ten, który ekran założył. */}
      {connecting && login !== null ? (
        <a
          className="st-button"
          data-open-login="1"
          href={login}
          rel="noreferrer"
          target="_blank"
        >
          Dokończ logowanie <Icon name="arrow-up-right" />
        </a>
      ) : null}

      {connecting && cancel !== null ? (
        <form action="/studio/card" className="st-card-form" method="post">
          <ActionFields {...props} action={cancel} card={card} />
          <button className="st-text" type="submit">
            Anuluj
          </button>
        </form>
      ) : null}

      {/* Popover z makiety jest tu `details`, więc otwiera się bez skryptu. Klasa `st-pop`
          dokłada zamykanie Escape'em i kliknięciem obok, które w pasku Studia już działa. */}
      {connect !== null ? (
        <details className="st-pop st-account-pop" data-pop="connect-account">
          <summary className="st-button">
            {connectText} <Icon name="arrow-up-right" />
          </summary>
          <div className="st-popover" role="dialog">
            <div className="st-pophead">
              <span>{connectText}</span>
              {/* X z referencji (odbiór 1, różnica nr 18). Bez skryptu nie działa i dlatego nie
                  jest jedyną drogą wyjścia: zapadkę zamyka też Escape i kliknięcie obok. */}
              <button
                aria-label="Zamknij"
                className="st-icon"
                data-pop-close="1"
                type="button"
              >
                <Icon name="x" />
              </button>
            </div>
            <div className="st-popprofile">
              <span aria-hidden="true" className="st-avatar">
                {profile.slice(0, 1).toUpperCase()}
              </span>
              <span>
                {profile}
                <small>Profil w tym projekcie</small>
              </span>
            </div>
            {/* THE AGENT'S BROWSER OPENS HERE, IN A NEW TAB (26.09). Until then this form posted
                `polacz` to `/studio/card`, the ticket URL in the answer was dropped, and the person
                was never shown a login screen at all. */}
            {opensAgentBrowser(connect) ? (
              <OpenAgentBrowser {...props} action={connect} card={card} />
            ) : (
              <form action="/studio/card" method="post">
                <ActionFields {...props} action={connect} card={card} />
                <button className="st-button" type="submit">
                  Kontynuuj <Icon name="arrow-right" />
                </button>
              </form>
            )}
          </div>
        </details>
      ) : null}

      {/* THE LINES THE MOCK-UP NEVER SHOWS, each on its own row under the card: why the account is
          not connected (the capsule's sentence), what to do (one line, two steps), and the check
          that closes the loop. The check is an ordinary card action; "Połączono" still comes only
          from the card's state after the control plane measured it. */}
      {reading !== null && !connected ? (
        <small className="st-account-note" data-capsule-reading="1">
          {reading}
        </small>
      ) : null}
      {guided ? (
        <small className="st-account-note" data-instruction="1">
          {instruction}
        </small>
      ) : null}
      {check === null ? null : (
        <form action="/studio/card" className="st-card-form" method="post">
          <ActionFields {...props} action={check} card={card} />
          <button className="st-button st-soft" type="submit">
            Gotowe, sprawdź połączenie
          </button>
        </form>
      )}
      <Reason card={card} />
    </div>
  );
}

function EngineCard(props: CardsProps & { readonly card: ConversationCard }) {
  const { card } = props;
  const connected = card.stan === "polaczone";
  const connecting = card.stan === "lacze";
  const connect = actionOf(card, "polacz");
  const cancel = actionOf(card, "anuluj");
  const label = cardText(card, "nazwa") ?? "Codex";
  const address = cardText(card, "adres_logowania");
  const code = cardText(card, "kod");

  return (
    <div className={`st-account${connected ? " connected" : ""}`} data-card={card.karta_id}>
      <span aria-hidden="true" className="st-avatar">
        C
      </span>
      <span className="st-account-copy">
        <span>{label}</span>
        <small data-sub="1">
          {connected
            ? "Gotowe do pracy"
            : connecting
              ? "Czekam na potwierdzenie logowania"
              : card.stan === "blad"
                ? "Nie udało się połączyć"
                : "Brak połączenia z Codexem"}
        </small>
      </span>

      {connected ? (
        <span className="st-connected" data-state="polaczone">
          <Icon name="check" /> Połączono
        </span>
      ) : null}

      {/* LOGOWANIE URZĄDZENIA: człowiek otwiera adres i przepisuje kod. Tyle ma do zrobienia i
          tylko to pokazujemy. Karta świadomie NIE niesie ścieżki katalogu ani `auth.json`:
          audyt UX nazwał to wiedzą operatora wdrożenia, nie klienta (E1). */}
      {connecting && address !== null ? (
        <span className="st-engine-login">
          <a className="st-button" href={address} rel="noreferrer" target="_blank">
            Otwórz logowanie <Icon name="arrow-up-right" />
          </a>
          {code === null ? null : (
            <code className="st-engine-code" data-code="1">
              {code}
            </code>
          )}
        </span>
      ) : null}

      {connecting && cancel !== null ? (
        <form action="/studio/card" className="st-card-form" method="post">
          <ActionFields {...props} action={cancel} card={card} />
          <button className="st-text" type="submit">
            Anuluj
          </button>
        </form>
      ) : null}

      {connect !== null ? (
        <form action="/studio/card" className="st-card-form" method="post">
          <ActionFields {...props} action={connect} card={card} />
          <button className="st-button" type="submit">
            {card.stan === "blad" ? "Spróbuj ponownie" : "Połącz Codex"} <Icon name="arrow-up-right" />
          </button>
        </form>
      ) : null}
      <Reason card={card} />
    </div>
  );
}

function CommunityRow(props: CardsProps & { readonly card: ConversationCard }) {
  const { card } = props;
  const key = cardText(card, "klucz") ?? "społeczność";
  const why = cardText(card, "uzasadnienie");
  // `nazwa` to opis społeczności z rejestru, a `uzasadnienie` to zdanie agenta o tym, po co tam
  // wchodzić. Dwie różne rzeczy i obie z karty; ekran nie dopisuje żadnej z nich.
  const nazwa = cardText(card, "nazwa");
  // DZIŚ JEST DOKŁADNIE JEDNA AKCJA: `obserwuj`. Przycisku „Dołącz kontem" nie ma nigdzie, bo
  // nie ma trasy, która by to wykonała, a kontrakt zabrania akcji bez trasy (E3). Dopóki
  // wykonawca nie powstanie, `dolaczam` i `dolaczono` są stanami nieosiągalnymi; rysujemy je,
  // żeby dzień ich włączenia nie był dniem pisania komponentu.
  const observe = actionOf(card, "obserwuj");
  const observing = card.stan === "obserwuje";
  const observed = card.stan === "obserwowana";
  const joined = card.stan === "dolaczono";
  const joining = card.stan === "dolaczam";
  const watches = card.dane["obserwacja"] === true;
  const meaning = cardText(card, "co_to_znaczy");

  return (
    <div className="st-community" data-card={card.karta_id} data-state={card.stan}>
      <div className="st-community-info">
        {why === null ? (
          <span className="st-community-title">{key}</span>
        ) : (
          <details className="st-community-why">
            <summary className="st-community-title">
              {key}
              <Icon name="chevron-right" />
            </summary>
            <div className="st-community-detail">{why}</div>
          </details>
        )}
        {nazwa === null ? null : <small>{nazwa}</small>}
      </div>

      {joined ? (
        <span className="st-button st-soft st-join st-joined" data-state="dolaczono">
          <Icon name="check" /> Konto zapisane
        </span>
      ) : joining ? (
        <span className="st-button st-soft st-join" data-state="dolaczam">
          Zapisuję konto…
        </span>
      ) : observed ? (
        <span className="st-button st-soft st-join st-joined" data-state="obserwowana">
          <Icon name="check" /> Obserwuję
        </span>
      ) : observing ? (
        <span className="st-button st-soft st-join" data-state="obserwuje">
          Włączam obserwację…
        </span>
      ) : observe !== null ? (
        <form action="/studio/card" className="st-card-form" method="post">
          <ActionFields {...props} action={observe} card={card} />
          <button className="st-button st-soft st-join" type="submit">
            {card.stan === "blad" ? "Spróbuj ponownie" : "Obserwuj"}
          </button>
        </form>
      ) : null}

      {/* CO TO ZNACZY, zdaniem serwera. „Obserwowana bez dołączenia" to dla człowieka nowe
          pojęcie i karta jest jedynym miejscem, w którym je spotyka. */}
      {meaning === null ? null : (
        <small className="st-community-meaning" data-meaning="1">
          {meaning}
        </small>
      )}

      {/* Konto zapisane, a nic stamtąd nie czytamy. Bez tego zdania stan „dołączyłem, a agent
          nie widzi nowych wątków" byłby niewidoczny: `dolaczono` przykrywa na karcie
          informację o obserwacji, więc musi ją oddać słowami. */}
      {joined && !watches ? (
        <small className="st-community-meaning" data-meaning="bez-obserwacji">
          Konto jest tam zapisane, ale nie czytam stamtąd wątków.
        </small>
      ) : null}
      <Reason card={card} />
    </div>
  );
}

function DraftCard(props: CardsProps & { readonly card: ConversationCard }) {
  const { card } = props;
  const title = cardText(card, "tytul") ?? "Szkic odpowiedzi";
  const where = [cardText(card, "spolecznosc"), cardText(card, "profil")]
    .filter((part): part is string => part !== null)
    .join(" · ");
  const body = cardText(card, "tresc") ?? "";
  const fix = actionOf(card, "popraw");
  const publish = actionOf(card, "opublikuj");
  const published = card.stan === "opublikowano";
  const publishing = card.stan === "publikuje";
  const needsAttention = card.stan === "blad";
  // Pola, które dokłada projekcja po publikacji. `adres` to permalink z odczytu zwrotnego,
  // `widocznosc` to jej zdanie o tym, czy publikacja jest widoczna publicznie i kiedy to
  // sprawdzono. Obu brak znaczy „jeszcze nie wiadomo", więc ekran wtedy milczy.
  const address = cardText(card, "adres");
  const visibility = cardText(card, "widocznosc");
  const why = cardText(card, "dlaczego_bez_akcji");
  // Własny post na X może nieść jeden zrzut ekranu (27.09). Serwer podaje go osobno, bo `tresc`
  // to tekst, który trafi na platformę, a zgoda obejmuje tekst razem z tym obrazem.
  const image = cardText(card, "obraz");

  return (
    <div className="st-draft" data-card={card.karta_id} data-state={card.stan}>
      <details className="st-draft-open">
        <summary className="st-draft-head">
          <Icon name="message-circle" />
          <span>
            {published ? "Opublikowano" : publishing ? "Publikuję…"
              : needsAttention ? "Publikacja wymaga uwagi" : title}
            {where === "" ? null : <small>{where}</small>}
          </span>
          {/* DWA NAPISY, PRZEŁĄCZANE PRZEZ `details[open]` W ARKUSZU (odbiór 1, różnica nr 10).
              Referencja zmienia „Zobacz ⌄" na „Zwiń ⌃" po otwarciu; napis, który po otwarciu
              nadal mówi „Zobacz", każe zgadywać, co zrobi kliknięcie. Bez skryptu, bo to jest
              stan `details`, a nie stan aplikacji. */}
          <span className="st-text st-draft-toggle">
            <span data-when="zamkniete">
              Zobacz <Icon name="chevron-down" />
            </span>
            <span data-when="otwarte">
              Zwiń <Icon name="chevron-up" />
            </span>
          </span>
        </summary>

        <div className="st-draft-body">
          <p className="st-draft-text">{body}</p>
          {image === null ? null : (
            <p className="st-draft-image" data-image={image}>
              Z obrazem: zrzut ekranu źródła
            </p>
          )}
          <Reason card={card} />
          <div className="st-draft-actions">
            {published ? (
              /* WIDOCZNOŚĆ JEST ZDANIEM SERWERA, nie naszym. Stało tu wcześniej „Widoczna w
                 rozmowie", które ja wymyśliłem, i było to twierdzenie o skutku na cudzej
                 platformie zrobione przez ekran: publikacja bywa usunięta przez moderację
                 minutę po wyjściu. Projekcja liczy `widocznosc` z odczytu zwrotnego i tylko to
                 pokazujemy; gdy jeszcze nie sprawdzono, nie piszemy nic. */
              <span className="st-draft-done">
                <span className="st-connected">
                  <Icon name="check" /> Opublikowano
                </span>
                {address === null ? null : (
                  <a
                    className="st-draft-address"
                    href={address}
                    rel="noreferrer"
                    target="_blank"
                  >
                    Zobacz publikację <Icon name="arrow-up-right" />
                  </a>
                )}
                {visibility === null ? null : (
                  <small className="st-draft-visibility">{visibility}</small>
                )}
              </span>
            ) : publishing ? (
              <span className="st-draft-done">
                <span className="st-connected">Publikuję…</span>
                {why === null ? null : <small className="st-draft-visibility">{why}</small>}
              </span>
            ) : needsAttention ? null : fix === null && publish === null ? (
              /* Karta bez akcji NIE jest kartą zepsutą. Dziś szkicu nie da się z ekranu ani
                 poprawić, ani opublikować, bo w API nie ma takiej trasy: zgody chodzą podpisaną
                 polityką i kanałem zatwierdzania. Zdanie stąd pochodzi z karty, bo to serwer wie,
                 czego brakuje; przycisk celujący w nieistniejącą trasę kłamałby po kliknięciu. */
              <span className="st-note" data-no-actions="1">
                {why ?? "Z tego ekranu nie da się dziś nic z tym szkicem zrobić."}
              </span>
            ) : (
              <>
                {/* BRAK „OPUBLIKUJ" MÓWI, DLACZEGO GO NIE MA, ZAWSZE (PLAN 4.5, odbiór 1 nr 10).
                    Do 21.09 zdanie pokazywało się tylko wtedy, gdy nie było ŻADNEJ akcji, więc
                    szkic z samym „Popraw" wyglądał, jakby publikacji po prostu zapomniano.
                    Powód jest z karty, bo to serwer wie, czy brakuje powierzenia, czy czegoś
                    innego; gdy nic nie przysłał, mówimy najprostszą prawdę. */}
                {publish === null ? (
                  <span className="st-note" data-no-publish="1">
                    {why ?? "Publikacja wymaga powierzenia pracy w tym projekcie."}
                  </span>
                ) : null}
                {fix === null ? null : (
                  <details className="st-draft-fix">
                    <summary className="st-text">Popraw</summary>
                    <form action="/studio/card" className="st-form" method="post">
                      <ActionFields {...props} action={fix} card={card} />
                      {/* `name="text"` to nazwa z API zatwierdzania, nie z karty: ładunek
                          akcji `popraw` niesie obecną treść, a my odsyłamy tę samą strukturę ze
                          zmienionym `text`. Własna nazwa pola i tłumaczenie jej w handlerze
                          byłoby miejscem, w którym poprawka zapisuje się jako pusta. */}
                      <textarea
                        aria-label="Popraw szkic odpowiedzi"
                        className="st-edit"
                        defaultValue={body}
                        maxLength={8000}
                        name="text"
                      />
                      <button className="st-button st-soft" type="submit">
                        Zapisz zmianę
                      </button>
                    </form>
                  </details>
                )}
                {publish === null ? null : (
                  <form action="/studio/card" className="st-card-form" method="post">
                    <ActionFields {...props} action={publish} card={card} />
                    <button className="st-button st-soft" type="submit">
                      Opublikuj <Icon name="arrow-up-right" />
                    </button>
                  </form>
                )}
              </>
            )}
          </div>
        </div>
      </details>
    </div>
  );
}

/**
 * The accounts a delegation covers, each with its platform: `@anna (X)`, `anna (Reddit)`.
 *
 * OPTIONAL AND TOLERANT (26.09): `konta` is new on the card, so a card without it renders exactly
 * as before, and an entry missing one of the two fields shows the one it has. The platform is on
 * screen because the same person may be signing for two platforms at once, and "anna" alone
 * does not say which of them will be publishing.
 */
function delegationAccounts(card: ConversationCard): readonly string[] {
  const raw = card.dane["konta"];
  if (!Array.isArray(raw)) return [];
  const named: string[] = [];
  for (const entry of raw) {
    if (typeof entry === "string") {
      if (entry.trim() !== "") named.push(entry.trim());
      continue;
    }
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) continue;
    const row = entry as Record<string, unknown>;
    const platform =
      typeof row["platforma"] === "string" && row["platforma"].trim() !== ""
        ? row["platforma"].trim()
        : null;
    const login =
      typeof row["konto"] === "string" && row["konto"].trim() !== "" ? row["konto"].trim() : null;
    if (login !== null && platform !== null) {
      named.push(`${accountHandle(platform, login)} (${platformLabel(platform)})`);
    } else if (login !== null) {
      named.push(login);
    } else if (platform !== null) {
      named.push(platformLabel(platform));
    }
  }
  return named;
}

function DelegationCard(props: CardsProps & { readonly card: ConversationCard }) {
  const { card } = props;
  // Nazwa akcji jest z kontraktu: `powierz`, nie `podpisz`. Ciało podpisu (konta projektu,
  // zakres, sufity) przychodzi gotowe w ładunku karty, bo ekran nie składa go z własnej wiedzy.
  const sign = actionOf(card, "powierz");
  const communities = cardList(card, "spolecznosci");
  const actions = cardList(card, "dzialania");
  const researchOnly = actions.length > 0 && actions.every((action) => action === "RESEARCH");
  const signed = card.stan === "podpisana";
  const uzasadnienie = cardText(card, "uzasadnienie");
  // 26.09: the goal the work serves and the moment the signature stops covering it. Both are part
  // of what is signed, so both stand next to the button, and the deadline is read on the person's
  // wall clock (Europe/Warsaw), not in UTC.
  const goal = cardText(card, "cel");
  const expires = cardText(card, "wygasa");
  const deadline = expires === null ? null : `Ważne do ${warsawMoment(expires)}`;
  const accounts = delegationAccounts(card);
  const limits = card.dane["limity"];
  const limitParts =
    typeof limits === "object" && limits !== null && !Array.isArray(limits)
      ? Object.entries(limits as Record<string, unknown>)
          .filter(([, value]) => typeof value === "number")
          .map(([name, value]) => `${LIMIT_LABELS[name] ?? name}: ${String(value)}`)
      : [];
  const actionLabels: Readonly<Record<string, string>> = {
    RESEARCH: "Research bez publikacji", REPLY: "Odpowiedzi", OWN_POST: "Własne posty",
  };
  const accountPart =
    accounts.length === 0
      ? ""
      : `${plural(accounts.length, ["konto", "konta", "kont"])}: ${accounts.join(", ")}`;
  const scope = [communities.join(", "), actions.map((action) => actionLabels[action] ?? action).join(", "), accountPart, limitParts.join(", ")]
    .filter((one) => one !== "")
    .join(" · ");

  return (
    <div className="st-autonomy" data-card={card.karta_id} data-state={card.stan}>
      {signed ? (
        <>
          <Icon name="check" />
          <span>{researchOnly ? "Research w tle włączony" : "Agent prowadzi dalej sam"}</span>
          {deadline === null ? null : (
            <small className="st-autonomy-scope" data-deadline="1">
              {deadline}
            </small>
          )}
        </>
      ) : card.stan === "odrzucona" ? (
        <span>Powierzenie odrzucone. Możesz prowadzić rozmowę dalej samodzielnie.</span>
      ) : sign === null ? null : (
        <form action="/studio/card" className="st-card-form" method="post">
          <ActionFields {...props} action={sign} card={card} />
          {/* PODPIS SKŁADA CZŁOWIEK Z EKRANU i to jedyne miejsce, gdzie kliknięcie nie ma
              odpowiednika w narzędziu agenta (kontrakt, sekcja POWIERZENIE). Zakres jest przy
              przycisku, a nie za odsyłaczem: podpis bez widocznego zakresu jest podpisem in blanco. */}
          <button className="st-text" type="submit">
            <Icon name="sparkles" /> {researchOnly ? "Uruchom research w tle" : "Możesz też powierzyć mi resztę"}
          </button>
          {/* ZAKRES I SUFITY STOJĄ PRZY PRZYCISKU, nie za odsyłaczem: podpis bez widocznego
              zakresu jest podpisem in blanco, a limity są tą częścią zakresu, o którą człowiek
              pyta najpóźniej i żałuje najbardziej. */}
          {goal === null ? null : (
            <small className="st-autonomy-why" data-goal="1">
              {`Cel: ${goal}`}
            </small>
          )}
          {scope === "" ? null : <small className="st-autonomy-scope">{scope}</small>}
          {deadline === null ? null : (
            <small className="st-autonomy-scope" data-deadline="1">
              {deadline}
            </small>
          )}
          {uzasadnienie === null ? null : (
            <small className="st-autonomy-why">{uzasadnienie}</small>
          )}
        </form>
      )}
      <Reason card={card} />
    </div>
  );
}

/**
 * Wszystkie karty tej rozmowy, w kolejności z serwera.
 *
 * Społeczności są ZGRUPOWANE w jeden panel, tak jak w makiecie: dwie osobne karty pod sobą
 * miałyby dwie ramki i wyglądały jak dwa różne wyniki, a to jest jeden wynik z dwiema pozycjami.
 * Pozostałe rodzaje stoją samodzielnie, w miejscu wyznaczonym przez `od_seq`.
 */
export function ConversationCards(props: CardsProps) {
  const { cards } = props;
  if (cards.length === 0) return null;

  const communities = cards.filter((card) => card.rodzaj === "SPOLECZNOSC");
  const others = cards.filter((card) => card.rodzaj !== "SPOLECZNOSC");
  const firstCommunity = communities[0];

  return (
    <div className="st-cards" data-cards={cards.length}>
      {others
        .filter((card) => firstCommunity === undefined || card.od_seq <= firstCommunity.od_seq)
        .map((card) => (
          <Card key={card.karta_id} {...props} card={card} />
        ))}

      {communities.length === 0 ? null : (
        <div className="st-results">
          <div className="st-communities">
            {communities.map((card) => (
              <CommunityRow key={card.karta_id} {...props} card={card} />
            ))}
          </div>
        </div>
      )}

      {others
        .filter((card) => firstCommunity !== undefined && card.od_seq > firstCommunity.od_seq)
        .map((card) => (
          <Card key={card.karta_id} {...props} card={card} />
        ))}
    </div>
  );
}

function Card(props: CardsProps & { readonly card: ConversationCard }) {
  switch (props.card.rodzaj) {
    case "KONTO":
      return <AccountCard {...props} />;
    case "SZKIC":
      return <DraftCard {...props} />;
    case "POWIERZENIE":
      return <DelegationCard {...props} />;
    case "SILNIK":
      return <EngineCard {...props} />;
    default:
      return <CommunityRow {...props} />;
  }
}

/** Czy karta szkicu wolno opublikować. Wystawiane do testu, żeby reguła miała jedno miejsce. */
export function mayPublish(card: ConversationCard): boolean {
  return actionOf(card, "opublikuj") !== null && cardFlag(card, "delegacja_pozwala");
}

/**
 * Odcisk kart tej rozmowy: identyfikator i stan każdej, po kolei.
 *
 * Po nim strumień poznaje, że karty się zmieniły, i przeładowuje stronę. Liczony z `karta_id`
 * i `stan`, a NIE z całej treści: poprawiony szkic zmienia tekst, ale nie stan, i nie ma powodu
 * przeładowywać strony pod człowiekiem, który właśnie czyta.
 */
export function cardsDigestOf(cards: readonly ConversationCard[]): string {
  return cards.map((card) => `${card.karta_id}:${card.stan}`).join("|");
}
