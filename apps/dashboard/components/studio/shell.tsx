import type { ReactNode } from "react";

import { LOCAL_MODE_BANNER, localModeWho } from "../../lib/local-access";
import { conversationResult, conversationTitle } from "../../lib/conversations";
import { infoFor } from "../../lib/studio-info";
import type { SessionRow } from "../../lib/session-schema";
import type { StudioFrame } from "../../lib/studio-frame";
import {
  OPERATIONS_PATH,
  codexHref,
  communitiesHref,
  conversationHref,
  knowledgeHref,
  labelOf,
  organizationHref,
  peopleHref,
  presenceHref,
  projectHref,
  publicationsHref,
  roomHref,
  workspaceHref,
} from "../../lib/routes";
import type { ServiceStatus } from "../../lib/presence-schema";
import { moment, modeLabel } from "../../lib/studio-labels";
import { Icon } from "./icons";
import { Pop } from "./pop";

/**
 * The studio shell: workspace and projects on the left, the work in the middle.
 *
 * WIZJA-WORKSPACE decides the shape, and three of its sentences are load-bearing here:
 *
 *   * "Projekty i nawigacja są po lewej, rozmowa w centrum" — the grid, and the fact that the
 *     conversation area sits at the bottom edge of the content column, not in a panel;
 *   * "Wejście do Profili i kont jest dostępne z tego samego poziomu, bo dotyczy wszystkich
 *     projektów" — hence the second nav group, below the projects and separated by a hairline;
 *   * "Stare ekrany operatora zostają dostępne" — the "Operacje" entry, which leaves the studio
 *     for the other shell. It is a link out, so it is the last item and it says so.
 *
 * D-045 ZMIENIA JEDNOSTKĘ: projekt jest ZBIOREM ROZMÓW, więc pasek pokazuje rozmowy, a nie tylko
 * projekty. Trzy rzeczy z tej decyzji są tutaj, i każda z nich jest wyborem, nie przepisaniem
 * prototypu:
 *
 *   1. **rozmowy widać pod OTWARTYM projektem, bez przycisku rozwijania.** Prototyp rozwija każdy
 *      projekt. Zrobienie tego naprawdę wymagałoby albo jednego odczytu na projekt przy KAŻDYM
 *      wejściu na dowolny ekran (pasek jest wszędzie), albo stanu rozwinięcia, który i tak
 *      zerowałby się przy każdym przejściu, bo to są pełne przeładowania strony. Rozmowy innych
 *      projektów są w sekcji „Ostatnie", która kosztuje jeden odczyt na całą przestrzeń.
 *   2. **„Nowa rozmowa" to LINK do projektu, nie formularz.** Rozmowa bez pierwszego zdania nie
 *      ma po co powstawać: pusty projekt pyta „Co robimy w …?" i to pytanie jest miejscem, gdzie
 *      rozmowa się zaczyna. Formularz tutaj tworzyłby sesje, których nikt nie zaczął.
 *   3. **„W projekcie" prowadzi wyłącznie tam, gdzie coś jest.** Prototyp ma w tej sekcji
 *      „Publikacje" i „Społeczności"; w Studiu nie ma takich ekranów, a pozycja prowadząca do
 *      pustej ramki jest drogowskazem, którego ten kod nie stawia (KONWENCJE, D-011).
 *
 * THE ACTIVE ENTRY IS PASSED IN, NOT GUESSED. A layout would not know which project is open and
 * CSS cannot compare two dynamic values, so every screen states what it is (`lib/studio-frame.ts`
 * explains the trade this buys).
 *
 * THE MARK CARRIES NO WORDMARK. D-039: a small standalone symbol, no "Wenext" next to it, no
 * frame and no box, because the product's name is going to change. It does not move at all here.
 * WIZJA-WORKSPACE gives the symbol two motions — assembling on load, and a slow wave WHILE the
 * model is really answering — and both belong to P2, where there is a model answering. Nothing
 * in this shell runs one, so nothing may look like it is thinking (ZAKRES-FUNKCJONALNY §5:
 * "udawana aktywność jest kłamstwem wobec użytkownika").
 */
export type StudioActive =
  | { readonly kind: "workspace" }
  | { readonly kind: "project"; readonly projectId: string }
  | { readonly kind: "conversation"; readonly projectId: string; readonly sessionId: string }
  | { readonly kind: "publications"; readonly projectId: string }
  | { readonly kind: "communities"; readonly projectId: string }
  | { readonly kind: "knowledge"; readonly projectId: string }
  | { readonly kind: "presence"; readonly projectId: string }
  | { readonly kind: "room"; readonly projectId: string }
  | { readonly kind: "people" }
  | { readonly kind: "codex" }
  | { readonly kind: "organization" };

/**
 * Ile rozmów projektu stoi w pasku, zanim zrobi się z tego lista do przewijania.
 *
 * TRZY, NIE OSIEM (odbiór 1, różnica nr 17). Przy sześciu projektach i trzech rozmowach pasek
 * urósł do 1369 px i „Profile i konta" razem ze stopką wypadły poza okno, więc lista rozmów
 * zjadała nawigację całej przestrzeni. Pasek ma prowadzić, a nie archiwizować: trzy ostatnie
 * plus wejście do pełnej listy mówią to samo, a mieszczą się na ekranie.
 */
const CONVERSATIONS_IN_SIDE = 3;

/**
 * Ile rozmów pokazuje „Ostatnie".
 *
 * CZTERY, NIE DZIESIĘĆ. Sekcja jest dodatkiem produktu (referencja jej nie ma) i przy dziesięciu
 * wierszach spychała nawigację przestrzeni poza okno, a od 21.09 pasek NIE przewija się jako
 * całość, więc to, co nie mieści się w kolumnie, po prostu znika z oczu. Cztery wiersze mówią to
 * samo: „tu ostatnio pracowałeś".
 */
const RECENT_IN_SIDE = 4;

/**
 * Escape, a click outside i przycisk X zamykają otwarty panel. TO JEST UDOGODNIENIE, nie
 * mechanizm: panele są `<details>` i zamykają się własnym `summary` bez tego skryptu.
 *
 * X-a dokładamy, bo referencja ma go w oknie kont projektu (odbiór 1, różnica nr 4), a bez
 * skryptu nie da się zamknąć zapadki przyciskiem stojącym w jej środku. Bez JS zostaje
 * `summary`, Escape i kliknięcie obok, więc panel nigdy nie jest pułapką.
 *
 * It touches no session, no URL and no control plane. The rule this package enforces is that the
 * operator session and the private API's address never reach browser code (D-016) — not that the
 * document may never carry a listener.
 *
 * An opened panel is also scrolled into view (27.09). The account card's panel opens under the
 * card, the card is usually the last thing in the conversation, and "Kontynuuj" sat behind the
 * composer at 1440x900 and 1280x720: the person saw a panel header and no way on. `toggle` does
 * not bubble, hence the capture listener; the panel's `scroll-margin-bottom` (chat.css) keeps
 * "into view" above the composer.
 */
export const CLOSE_PANELS_SCRIPT = `(function(){
var opened=function(){return document.querySelectorAll('details.st-pop[open]')};
document.addEventListener('toggle',function(event){
var panel=event.target;
if(!panel||!panel.matches||!panel.matches('details.st-pop[open]')){return}
var box=panel.querySelector('.st-popover,.st-pop__panel');
if(box&&box.scrollIntoView){box.scrollIntoView({block:'nearest'})}},true);
document.addEventListener('keydown',function(event){
if(event.key!=='Escape'){return}
opened().forEach(function(panel){panel.removeAttribute('open')})});
document.addEventListener('pointerdown',function(event){
opened().forEach(function(panel){if(!panel.contains(event.target)){panel.removeAttribute('open')}})});
document.addEventListener('click',function(event){
var close=event.target.closest&&event.target.closest('[data-pop-close]');
if(!close){return}
var panel=close.closest('details.st-pop');
if(panel){event.preventDefault();panel.removeAttribute('open')}})})();`;

/**
 * The white PNG preserves the connected silhouette selected by the founder on 20.09.
 *
 * `aria-hidden`, because it is decoration: the workspace switch already names where you are.
 */
function Mark() {
  return (
    <img
      alt=""
      aria-hidden="true"
      className="st__mark"
      height={658}
      src="/brand/mark-white.png"
      width={791}
    />
  );
}

/**
 * Which project this screen belongs to, or `null` for the workspace-level screens.
 *
 * KAŻDY EKRAN PROJEKTU MA TU BYĆ WYMIENIONY, i to jest cała pułapka tej funkcji: brak pozycji
 * nie psuje niczego, co widać w teście treści, tylko zabiera pasek. Odbiór 1 (różnica nr 5)
 * znalazł to na Publikacjach i Społecznościach: po wejściu na nie znikała sekcja „W projekcie" z
 * pięcioma pozycjami ORAZ lista rozmów pod projektem, a żaden projekt nie był podświetlony,
 * więc człowiek tracił kontekst dokładnie w chwili, w której wszedł głębiej w projekt.
 *
 * Dlatego `default` zwraca `null` tylko dla ekranów PRZESTRZENI, a każdy nowy ekran projektu
 * dopisuje się tutaj razem z trasą. Pilnuje tego test w `studio_shell.test.tsx`: przechodzi po
 * wszystkich rodzajach `StudioActive`, które niosą `projectId`, i wymaga, żeby `projectOf` je
 * rozpoznał.
 */
/**
 * Klasy pozycji nawigacji: `st__item` zawsze, `wm-selected` przy bieżącej.
 *
 * `aria-current` zostaje, bo to ono mówi czytnikowi ekranu, gdzie jesteśmy, ale REGUŁA WYGLĄDU
 * z referencji stoi na KLASIE (`.st .wm-navigation .wm-selected`), więc bez niej zaznaczenie
 * brało kolory ze Studia (odbiór 2, nr 13). Dwa mechanizmy, jeden warunek: nie da się ich
 * rozjechać, bo liczy je ta sama funkcja.
 */
function current(isCurrent: boolean): string {
  return isCurrent ? "st__item wm-selected" : "st__item";
}

export function projectOf(active: StudioActive): string | null {
  switch (active.kind) {
    case "project":
    case "conversation":
    case "publications":
    case "communities":
    case "knowledge":
    case "presence":
    case "room":
      return active.projectId;
    default:
      return null;
  }
}

/** Where a form on this screen sends the browser back to. Derived, so no screen can mistype it. */
export function returnPathFor(workspaceId: string, active: StudioActive): string {
  switch (active.kind) {
    case "project":
      return projectHref(workspaceId, active.projectId);
    case "conversation":
      return conversationHref(workspaceId, active.projectId, active.sessionId);
    // Ta sama pułapka, co w `projectOf` piętro wyżej, tylko cichsza: brak pozycji nie zabiera
    // paska, tylko odsyła człowieka po wysłaniu formularza na przegląd przestrzeni zamiast tam,
    // gdzie stał. Nowy ekran projektu dopisuje się w OBU funkcjach.
    case "publications":
      return publicationsHref(workspaceId, active.projectId);
    case "communities":
      return communitiesHref(workspaceId, active.projectId);
    case "knowledge":
      return knowledgeHref(workspaceId, active.projectId);
    case "presence":
      return presenceHref(workspaceId, active.projectId);
    case "room":
      return roomHref(workspaceId, active.projectId);
    case "people":
      return peopleHref(workspaceId);
    case "codex":
      return codexHref(workspaceId);
    case "organization":
      return organizationHref(workspaceId);
    default:
      return workspaceHref(workspaceId);
  }
}

/**
 * Jeden wiersz rozmowy w pasku.
 *
 * Wynik jednym zdaniem jedzie w `title`, czyli w dymku po najechaniu, a nie w wierszu: pasek ma
 * dawać się przeczytać jednym spojrzeniem, a trzy linie na rozmowę zamieniłyby go w drugi ekran.
 * Gdy wyniku nie ma, `title` niesie sam tytuł, bo pusty dymek wygląda jak zepsuty.
 */
function ConversationRow({
  session,
  workspaceId,
  open,
}: {
  readonly session: SessionRow;
  readonly workspaceId: string;
  readonly open: boolean;
}) {
  const title = conversationTitle(session);
  const result = conversationResult(session);
  return (
    <a
      aria-current={open ? "page" : undefined}
      className="st-conv"
      data-conversation={session.session_id}
      href={conversationHref(workspaceId, session.project_id, session.session_id)}
      title={result === null ? title : `${title}. ${result}`}
    >
      <span className="st__dot" data-state={session.status === "RUNNING" ? "active" : "paused"} />
      <span className="st-conv__title">{title}</span>
      <time className="st-conv__when" dateTime={session.created_at}>
        {moment(session.created_at)}
      </time>
    </a>
  );
}

/**
 * Okruszki nagłówka w typografii makiety D-046 (`st-crumb`).
 *
 * ROZBITE NA CZĘŚCI, A NIE JEDEN NAPIS, bo makieta wyróżnia OSTATNI człon: reszta drogi jest
 * szara, a miejsce, w którym stoisz, jaśniejsze. W jednym napisie nie da się tego zrobić, a
 * to jest jedyne rozróżnienie, po którym widać, gdzie kończy się droga, a zaczyna nazwa.
 *
 * Ukośniki są `aria-hidden`: czytnik ma przeczytać nazwy, a nie „ukośnik" trzy razy.
 */
function Crumb({ crumb }: { readonly crumb: string }) {
  const parts = crumb.split(" / ").filter((part) => part.trim() !== "");
  if (parts.length === 0) return <span className="st__crumb st-crumb" />;
  const last = parts[parts.length - 1] as string;
  return (
    <span className="st__crumb st-crumb">
      {parts.slice(0, -1).map((part) => (
        <span key={part}>
          {part}
          <span aria-hidden="true">/</span>
        </span>
      ))}
      <b>{last}</b>
    </span>
  );
}

export interface StudioShellProps {
  readonly frame: StudioFrame;
  readonly active: StudioActive;
  /** The quiet line in the header. Says where you are, not what the screen is called. */
  readonly crumb: string;
  /** Value of `?info=`, or undefined. Rendered only when it is a key this product writes. */
  readonly info?: string;
  /**
   * Rozmowy OTWARTEGO projektu, najnowsze pierwsze (D-045).
   *
   * `undefined` znaczy „ten ekran ich nie czytał" i wtedy pod projektem nie ma nic; pusta tablica
   * znaczy „projekt nie ma jeszcze rozmów" i wtedy jest sam odsyłacz do nowej. To są dwa różne
   * zdania i pasek nie ma prawa ich pomylić.
   */
  readonly conversations?: readonly SessionRow[];
  /**
   * Ostatnie rozmowy całej przestrzeni, albo `null`, gdy control plane tej trasy jeszcze nie ma.
   *
   * `null` NIE znaczy „nie ma rozmów": sekcja się wtedy nie rysuje, zamiast twierdzić, że w
   * przestrzeni nic się nie dzieje.
   */
  readonly recent?: readonly SessionRow[] | null;
  /**
   * Stan całej usługi (P7 etap B), albo `null`, gdy nie udało się go odczytać.
   *
   * `null` NIE znaczy „usługa działa" i nie może się tak wyświetlić: pasek, którego nie ma, jest
   * uczciwszy niż zapewnienie oparte na nieudanym odczycie. Widać go tylko wtedy, gdy coś jest
   * niedostępne, bo pasek mówiący „wszystko w porządku" na każdym ekranie przestaje być czytany
   * po dwóch dniach i nie zostaje zauważony w trzecim, kiedy zmienia treść.
   */
  readonly service?: ServiceStatus | null;
  /**
   * Tryb delegacji projektu, pokazywany jako chip po PRAWEJ stronie nagłówka (E2, referencja).
   *
   * Stąd, a nie z treści: w referencji to jest stan ramy, widoczny na każdym ekranie projektu,
   * a nie akapit na jednym z nich. Opis trybu zniknął z głównego ekranu razem z resztą
   * konfiguracji; chip mówi, w czym rzecz, jednym słowem.
   */
  readonly mode?: string;
  readonly children: ReactNode;
}

export function StudioShell({
  frame,
  active,
  crumb,
  info,
  conversations,
  recent = null,
  service = null,
  mode,
  children,
}: StudioShellProps) {
  const workspaceId = frame.workspace.workspace_id;
  const returnTo = returnPathFor(workspaceId, active);
  const message = infoFor(info);
  const openProject = projectOf(active);
  const openSession = active.kind === "conversation" ? active.sessionId : null;

  return (
    /* OPAKOWANIA SĄ Z REFERENCJI (E2): `wm-shell` niesie tło i obramowanie okna, `wm-scene`
       jest siatką 220 px plus reszta. `st--framed` wyłącza starą siatkę na korzeniu, żeby nie
       było siatki w siatce; obie klasy powstają tutaj i nie da się mieć jednej bez drugiej. */
    <div className="st st--framed" data-st-route={active.kind}>
    <div className="wm-shell">
    <div className="wm-scene">
      <aside aria-label="Workspace, projekty i nawigacja" className="wm-sidebar st__side">
        <div className="wm-sidebar-top st__top">
          <span className="wm-brand-host">
            <Mark />
          </span>
          {/* Obie nazwy klas: `st-btn` zostaje, bo po niej rozpoznają ten przycisk testy i
              arkusz Studia, a `wm-workspace-switch` włącza regułę referencji, która dotąd nie
              miała w co trafić. Reguła jest po KLASIE, nie po elemencie, więc `summary` ją
              łapie (E2, pozycja 5 listy różnic; zrzut w rundzie 2). */}
          <Pop
            frame="wm-workspace-switch"
            label={`Workspace: ${frame.workspace.name}. Zmień lub dodaj`}
            name="workspace-switch"
            text={frame.workspace.name}
            title="Workspace"
            variant="button"
          >
            <ul>
              {frame.workspaces.map((candidate) => (
                <li key={candidate.workspace_id}>
                  <a
                    aria-current={
                      candidate.workspace_id === workspaceId ? "page" : undefined
                    }
                    className="st__item"
                    href={workspaceHref(candidate.workspace_id)}
                  >
                    <span>{candidate.name}</span>
                  </a>
                </li>
              ))}
            </ul>
            <form action="/studio/workspace" className="st-form" method="post">
              <input name="return_to" type="hidden" value={returnTo} />
              <label className="st-field">
                <span>Nowy workspace</span>
                <input
                  className="st-input"
                  maxLength={120}
                  name="name"
                  placeholder="Nazwa przestrzeni"
                  required
                  type="text"
                />
              </label>
              <button className="st-btn" data-kind="primary" type="submit">
                Utwórz workspace
              </button>
            </form>
          </Pop>
        </div>

        {/* ETYKIETA PROJEKTOW I PRZYCISK STOJA POZA REGIONEM PRZEWIJANIA (odbior 3, R4).
            Panel zapadki stoi w drzewie pod przyciskiem, a region ma `overflow`, wiec panel
            byl obcinany przy krawedzi paska: z 320 px widac bylo 49. Element, ktory OTWIERA
            panel, nie moze mieszkac w pudelku przycinajacym ten panel. */}
          <div className="wm-nav-label st__group">
            Projekty
            {/* Jak wyżej (pozycja 6). Włączone TYLKO tutaj, a nie dla wszystkich ośmiu miejsc,
                w których stoi `Pop`: kółka na ekranach spoza tego zadania nie mają się
                przemalować przy okazji. */}
            <Pop frame="wm-circle" label="Nowy projekt" name="new-project" title="Nowy projekt">
              <form action="/studio/project" className="st-form" method="post">
                <input name="action" type="hidden" value="create" />
                <input name="workspace_id" type="hidden" value={workspaceId} />
                <input name="return_to" type="hidden" value={returnTo} />
                <label className="st-field">
                  <span>Nazwa</span>
                  <input
                    className="st-input"
                    maxLength={120}
                    name="name"
                    placeholder="np. Obecność w społecznościach devops"
                    required
                    type="text"
                  />
                </label>
                <label className="st-field">
                  <span>Cel, własnymi słowami. Można dopowiedzieć później</span>
                  <textarea
                    className="st-textarea"
                    maxLength={4000}
                    name="goal"
                    placeholder="Co ma się wydarzyć i dla kogo"
                  />
                </label>
                <label className="st-field">
                  <span>Tryb pracy</span>
                  <select className="st-select" defaultValue="AUTONOMOUS" name="mode">
                    <option value="AUTONOMOUS">Autonomiczny</option>
                    <option value="REVIEW">Z przeglądem</option>
                    <option value="RESEARCH">Research</option>
                  </select>
                </label>
                <button className="st-btn" data-kind="primary" type="submit">
                  Załóż projekt
                </button>
              </form>
            </Pop>
          </div>
        {/* REGION, KTORY SIE PRZEWIJA: lista projektow razem z rozmowami biezacego projektu
            i wejsciem do nowej rozmowy. Reszta paska stoi. */}
        <div className="st__scroll">

          {frame.projects.length === 0 ? (
            <p className="st-note">Nie ma jeszcze żadnego projektu.</p>
          ) : (
            <ul className="wm-projects st__list">
              {frame.projects.map((project) => {
                const isOpen = project.project_id === openProject;
                return (
                  <li key={project.project_id}>
                    <a
                      aria-current={isOpen ? "page" : undefined}
                      className="st__item"
                      href={projectHref(workspaceId, project.project_id)}
                    >
                      {/* A dot, not a colour on the row: the accent means STATE. Paused is grey,
                          and "grey" here means "nothing is running", which is a fact, not a
                          warning. */}
                      <span
                        className={`wm-project-dot st__dot${project.paused ? "" : " is-active"}`}
                        data-state={project.paused ? "paused" : "active"}
                      />
                      <span>{project.name}</span>
                    </a>
                    {isOpen && conversations !== undefined ? (
                      <div className="st__convs" data-convs={conversations.length}>
                        {conversations.slice(0, CONVERSATIONS_IN_SIDE).map((session) => (
                          <ConversationRow
                            key={session.session_id}
                            open={session.session_id === openSession}
                            session={session}
                            workspaceId={workspaceId}
                          />
                        ))}
                        {/* WEJŚCIE DO PEŁNEJ LISTY, gdy rozmów jest więcej niż w pasku. Bez
                            niego skrócenie paska do trzech ukrywałoby resztę bez słowa, a to
                            jest gorsze niż długi pasek: człowiek nie wie, że coś jeszcze jest. */}
                        {conversations.length > CONVERSATIONS_IN_SIDE ? (
                          <a
                            className="st-conv st-conv--all"
                            data-all-conversations={conversations.length}
                            href={projectHref(workspaceId, project.project_id)}
                          >
                            {`Wszystkie rozmowy (${conversations.length})`}
                          </a>
                        ) : null}
                        {/* Link, nie formularz: rozmowa zaczyna się od pierwszego zdania, a to
                            pole stoi na pustym projekcie. Patrz nagłówek pliku, punkt 2. */}
                        <a
                          className="st-conv st-conv--new"
                          data-new-conversation="1"
                          href={projectHref(workspaceId, project.project_id)}
                        >
                          Nowa rozmowa
                        </a>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {openProject !== null ? (
          <div>
            <div className="wm-nav-label wm-project-label st__group">W projekcie</div>
            {/* Osobna klasa od `st__nav`: tamta jest nawigacją PRZESTRZENI i jej zawartość jest
                sprawdzana co do kolejności. Zlanie obu w jedną listę zrobiłoby z nich jedno
                menu, w którym pozycja projektu i pozycja przestrzeni wyglądają tak samo. */}
            <nav aria-label="W projekcie" className="wm-navigation st__subnav">
              {/* Rozmowa NALEŻY do „Rozmów" (odbiór 2, N2): wchodząc w wątek, człowiek nie
                  opuszcza tej sekcji, więc pozycja zostaje bieżąca. Bez tego na stronie rozmowy
                  żadna pozycja nie była podświetlona i pasek przestawał mówić, gdzie się jest. */}
              <a
                aria-current={
                  active.kind === "project" || active.kind === "conversation" ? "page" : undefined
                }
                className={current(active.kind === "project" || active.kind === "conversation")}
                data-sub="conversations"
                href={projectHref(workspaceId, openProject)}
              >
                <Icon name="message-circle" />
                <span>Rozmowy</span>
              </a>
              {/* POKÓJ STOI TUŻ POD ROZMOWAMI, bo jest tym, po co człowiek wchodzi z Codexa:
                  ludzie, którzy teraz mają problem projektu, gotowe odpowiedzi i wyniki. Nie ma
                  go w referencji z 19.09; doszedł w D-055 jako pierwszy ekran wtyczki. */}
              <a
                aria-current={active.kind === "room" ? "page" : undefined}
                className={current(active.kind === "room")}
                data-sub="room"
                href={roomHref(workspaceId, openProject)}
              >
                <Icon name="door-open" />
                <span>Pokój</span>
              </a>
              {/* Kolejność jest z referencji, nie z wygody: rozmowa, potem to, co z niej
                  wyszło (publikacje, społeczności), potem to, z czego korzysta (wiedza), na
                  końcu ślad (aktywność). */}
              <a
                aria-current={active.kind === "publications" ? "page" : undefined}
                className={current(active.kind === "publications")}
                data-sub="publications"
                href={publicationsHref(workspaceId, openProject)}
              >
                <Icon name="file-text" />
                <span>Publikacje</span>
              </a>
              <a
                aria-current={active.kind === "communities" ? "page" : undefined}
                className={current(active.kind === "communities")}
                data-sub="communities"
                href={communitiesHref(workspaceId, openProject)}
              >
                <Icon name="messages-square" />
                <span>Społeczności</span>
              </a>
              <a
                aria-current={active.kind === "knowledge" ? "page" : undefined}
                className={current(active.kind === "knowledge")}
                data-sub="knowledge"
                href={knowledgeHref(workspaceId, openProject)}
              >
                <Icon name="book-open" />
                <span>Wiedza</span>
              </a>
              <a
                aria-current={active.kind === "presence" ? "page" : undefined}
                className={current(active.kind === "presence")}
                data-sub="presence"
                href={presenceHref(workspaceId, openProject)}
              >
                <Icon name="history" />
                <span>Aktywność</span>
              </a>
            </nav>
          </div>
        ) : null}

        {/* „OSTATNIE" TYLKO WTEDY, GDY ŻADEN PROJEKT NIE JEST OTWARTY (odbiór 2, R1).
            Przy otwartym projekcie jego rozmowy są wyżej, pod jego nazwą, więc ta sekcja
            powtarzała je i zabierała 150 px kolumnie, która od 21.09 się nie przewija: skutkiem
            było wypchnięcie nawigacji przestrzeni pod dok. Sekcja jest dodatkiem produktu (w
            referencji jej nie ma) i ma odpowiadać na pytanie „gdzie ostatnio pracowałem", które
            zadaje się PRZED wejściem w projekt, a nie w nim. */}
        {openProject === null && recent !== null && recent.length > 0 ? (
          <div>
            <div className="st__group">Ostatnie</div>
            <div className="st__convs" data-recent={recent.length}>
              {recent.slice(0, RECENT_IN_SIDE).map((session) => (
                <ConversationRow
                  key={session.session_id}
                  open={session.session_id === openSession}
                  session={session}
                  workspaceId={workspaceId}
                />
              ))}
            </div>
          </div>
        ) : null}

        <nav aria-label="Workspace" className="wm-navigation wm-global-nav st__nav">
          <a
            aria-current={active.kind === "people" ? "page" : undefined}
            className={current(active.kind === "people")}
            data-nav="people"
            href={peopleHref(workspaceId)}
          >
            <Icon name="users" />
            <span>{labelOf("people")}</span>
          </a>
          <a
            aria-current={active.kind === "codex" ? "page" : undefined}
            className={current(active.kind === "codex")}
            data-nav="codex"
            href={codexHref(workspaceId)}
          >
            <Icon name="blocks" />
            <span>{labelOf("codex")}</span>
          </a>
        </nav>

        {/* DÓŁ PASKA JAK W REFERENCJI (odbiór 1, różnica nr 16): cicha etykieta przestrzeni, a
            pod nią DOK z awatarem. Referencja trzyma tam wejście do konta, a nie listę pozycji
            bez ikon i tekstowy „Wyloguj" pod nimi.

            Co się przeniosło do popovera awatara: „Przestrzeń", „Operacje" i „Wyloguj". To są
            prawdziwe wejścia, więc awatar naprawdę coś otwiera; gdyby nie miał czego, nie
            stanąłby tu wcale, tak jak nie stoi kółko „+" z referencyjnego doku. */}
        <div className="wm-workspace-label st__foot">
          <span>{frame.workspace.name}</span>
          <div className="wm-dock st__dock">
            <Pop
              chevron={false}
              frame="wm-avatar"
              label="Konto i przestrzeń"
              name="account-dock"
              side="left"
              text={frame.workspace.name.slice(0, 1).toUpperCase()}
              title="Konto"
              variant="button"
            >
              <ul>
                <li>
                  <a
                    aria-current={active.kind === "organization" ? "page" : undefined}
                    className="st__item"
                    data-nav="organization"
                    href={organizationHref(workspaceId)}
                  >
                    <span>{labelOf("organization")}</span>
                  </a>
                </li>
                <li>
                  {/* Wyjście do panelu operatora, czyli do ekranów sprzed pivotu. Nadal są i
                      nadal się czytają; przestały być wejściem do produktu. */}
                  <a className="st__item" data-nav="operations" href={OPERATIONS_PATH}>
                    <span>Operacje</span>
                  </a>
                </li>
              </ul>
              <form action="/logout" method="post">
                <button className="st-btn" data-kind="quiet" type="submit">
                  Wyloguj
                </button>
              </form>
            </Pop>
          </div>
        </div>
      </aside>

      <main className="wm-main st__main">
        <header className="wm-header st__head">
          <Crumb crumb={crumb} />
          <div className="wm-header-actions">
            {mode === undefined ? null : (
              <span className="wm-mode-button" data-mode={mode}>
                <span aria-hidden="true" className="wm-dot st__dot" />
                {modeLabel(mode)}
              </span>
            )}
          </div>
          {/* Widoczny TYLKO przy awarii. Zdanie pochodzi z backendu, bo dwa miejsca liczące
              „czy usługa działa" rozjechałyby się, a to jest zdanie, przy którym rozjechanie
              się kosztuje najwięcej. */}
          {service !== null && !service.healthy ? (
            <span className="st-tag" data-tone="warn" data-service="down" role="status">
              {service.sentence}
            </span>
          ) : null}
          {localModeWho() !== "" ? <span className="st-local-mode" title={LOCAL_MODE_BANNER} data-local-mode="1">Tryb lokalny</span> : null}
        </header>
        <div className="st__content">
          {message !== null ? (
            <p className="st-info" data-tone={message.tone} role="status">
              {message.text}
            </p>
          ) : null}
          {children}
        </div>
      </main>

    </div>
    </div>
      <script dangerouslySetInnerHTML={{ __html: CLOSE_PANELS_SCRIPT }} />
    </div>
  );
}
