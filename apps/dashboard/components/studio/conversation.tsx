/**
 * The conversation with the agent: what was said, what it reached for, and what it is doing now.
 *
 * FOUR RULES THIS COMPONENT EXISTS TO KEEP, and each one is a way a screen can lie about work:
 *
 *   1. **Activity is shown only when a turn is really in flight.** `isWaitingOnTheModel` is the
 *      single place that decides, and it says `RUNNING` and nothing else. `QUEUED` means a job
 *      exists and nobody has claimed it, which is not the same thing and must not animate.
 *      ZAKRES-FUNKCJONALNY §5 forbids a screen showing activity with no model running.
 *   2. **Tool calls are collapsed but never hidden.** A conversation that shows only prose lets
 *      the agent's claims stand without what they rest on. Every call is here, with its
 *      arguments and its outcome one click away. Od 19.09 sąsiadujące kroki stoją w JEDNYM
 *      zwiniętym bloku „Co agent robił", bo uwaga założyciela po pierwszym użyciu studia
 *      brzmiała: odpowiedź modelu ginie między dziesiątkami wierszy mechaniki. Blok mówi, ile
 *      było kroków i po jakie narzędzia agent sięgał, a rozwija się kliknięciem.
 *   3. **A tool result says WHICH of four things happened.** `WYKONANE`, `OCZEKUJE`,
 *      `NIEDOSTEPNE`, `BLAD` come from the tool layer and are printed as they are. A draft
 *      waiting for consent is not a success and is not a failure, and the screen must not round
 *      it to either.
 *   4. **Nothing is invented.** No placeholder reply, no "agent myśli" while the status says
 *      otherwise, and when there is no Codex connection the field says so instead of accepting
 *      a message nothing will answer.
 *
 * `prefers-reduced-motion` turns the waiting mark static. The rule is in `studio.css`; the
 * element carries `data-waiting` and nothing here animates by itself.
 */
import type { ReactNode } from "react";
import { counted } from "../../lib/format";
import {
  groupConversation,
  renderMarkdown,
  softenDashes,
  savedMaterialLinks,
  materialLinkTarget,
  type Block,
  type InlinePiece,
  type StepsSegment,
} from "../../lib/conversation-render";
import type { SessionDetail, SessionEvent, SessionStatus } from "../../lib/session-schema";
import { isWaitingOnTheModel } from "../../lib/session-schema";
import { AgentProgress } from "./progress";
import { ConversationCards, cardsDigestOf } from "./cards";
import { Icon } from "./icons";

/** What each status means, in words a person reads rather than a state name. */
const STATUS_TEXT: Readonly<Record<SessionStatus, { readonly label: string; readonly note: string }>> =
  {
    QUEUED: { label: "w kolejce", note: "Rozmowa ruszy, gdy zwolni się silnik." },
    RUNNING: { label: "pracuje", note: "Tura trwa. Możesz dopisać kontekst albo przerwać." },
    WAITING_USER: { label: "odpowiedź gotowa", note: "Możesz kontynuować rozmowę." },
    WAITING_EVENT: {
      label: "czeka na zdarzenie",
      note: "Agent czeka na coś z zewnątrz, nie na Ciebie.",
    },
    PAUSED: { label: "wstrzymana", note: "Przerwana. Wznów, żeby kontynuować na tym samym wątku." },
    DONE: { label: "zakończona", note: "Sesja zamknięta." },
    FAILED: { label: "błąd", note: "Tura się nie udała. Powód jest w rozmowie poniżej." },
    BUDGET_EXHAUSTED: {
      label: "budżet wyczerpany",
      note: "Sesja zatrzymała się na granicy, którą jej postawiono.",
    },
  };

function text(payload: Record<string, unknown>, field: string): string {
  const value = payload[field];
  return typeof value === "string" ? value : "";
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "";
  } catch {
    return String(value);
  }
}

function ToolCall({ event }: { readonly event: SessionEvent }) {
  const tool = text(event.payload, "tool") || "(nieznane narzędzie)";
  const args = event.payload["arguments"];
  return (
    <details className="st-tool" data-kind="call">
      <summary>
        <span className="st-tool__name">{tool}</span>
        <span className="st-note">sięgnął po narzędzie</span>
      </summary>
      <pre className="st-tool__body">{pretty(args)}</pre>
    </details>
  );
}

function ToolResult({ event }: { readonly event: SessionEvent }) {
  const tool = text(event.payload, "tool") || "(nieznane narzędzie)";
  const status = text(event.payload, "status");
  const error = text(event.payload, "error");
  const result = event.payload["result"];
  // The tool layer's own four-way outcome, dug out of the result it returned. Printed as it is,
  // never rounded: "OCZEKUJE" is not a success and not a failure.
  const outcome = text(event.payload, "outcome") || (
    typeof result === "object" && result !== null
      ? (((result as Record<string, unknown>)["stan"] as string | undefined) ?? "")
      : "");
  const tone = error !== "" || status === "failed" ? "absent" : "state";
  return (
    <details className="st-tool" data-kind="result">
      <summary>
        <span className="st-tool__name">{tool}</span>
        {outcome === "" ? null : (
          <span className="st-tag" data-tone={tone}>
            {outcome}
          </span>
        )}
        {error === "" ? null : <span className="st-note">{error}</span>}
      </summary>
      <pre className="st-tool__body">{pretty(result)}</pre>
    </details>
  );
}

function Inline({ pieces, materialLinks }: { readonly pieces: readonly InlinePiece[]; readonly materialLinks?: ReadonlySet<string> }) {
  return (
    <>
      {pieces.map((piece, index) => {
        const key = `${piece.kind}-${index}`;
        if (piece.kind === "bold") return <strong key={key}>{piece.children ? <Inline pieces={piece.children} materialLinks={materialLinks} /> : piece.text}</strong>;
        if (piece.kind === "italic") return <em key={key}>{piece.children ? <Inline pieces={piece.children} materialLinks={materialLinks} /> : piece.text}</em>;
        if (piece.kind === "code") return <code key={key}>{piece.text}</code>;
        if (piece.kind === "link") {
          const href = materialLinkTarget(piece.href ?? "", materialLinks);
          return <a key={key} href={href} target={href.startsWith("/") ? undefined : "_blank"} rel="noopener noreferrer">{piece.text}</a>;
        }
        return <span key={key}>{piece.text}</span>;
      })}
    </>
  );
}

/**
 * Odpowiedź agenta, renderowana z minimalnego markdownu.
 *
 * TEKST JEST DANYMI, NIE KODEM: `renderMarkdown` zwraca kawałki, a React wstawia je jako tekst.
 * Nigdzie tu nie ma `dangerouslySetInnerHTML`, więc cokolwiek model napisze, zostanie napisem.
 */
export function AgentText({ text: raw, materialLinks }: { readonly text: string; readonly materialLinks?: ReadonlySet<string> }) {
  const blocks: readonly Block[] = renderMarkdown(raw);
  if (blocks.length === 0) return <p>{softenDashes(raw)}</p>;
  return (
    <div className="st-answer">
      {blocks.map((block, index) => {
        const key = `b-${index}`;
        if (block.kind === "codeblock") {
          return <pre className="st-answer__code" key={key}><code>{block.text}</code></pre>;
        }
        if (block.kind === "list") {
          const items = block.items.map((item, itemIndex) => (
            <li key={`i-${itemIndex}`}>
              <Inline pieces={item} materialLinks={materialLinks} />
            </li>
          ));
          // Keep numbering when explanations split the model's list into separate blocks.
          return block.ordered ? <ol key={key} start={block.start}>{items}</ol> : <ul key={key}>{items}</ul>;
        }
        if (block.kind === "heading") {
          // Jeden poziom śródtytułu i nigdy `h1`: tytułem tego ekranu jest nazwa projektu,
          // a nie zdanie modelu.
          return (
            <h3 key={key}>
              <Inline pieces={block.pieces} materialLinks={materialLinks} />
            </h3>
          );
        }
        if (block.kind === "quote") {
          return (
            <blockquote key={key}>
              <Inline pieces={block.pieces} materialLinks={materialLinks} />
            </blockquote>
          );
        }
        return (
          <p key={key}>
            <Inline pieces={block.pieces} materialLinks={materialLinks} />
          </p>
        );
      })}
    </div>
  );
}

/** Jedno zdanie o kroku, napisane przez backend (`payload.summary`), albo nazwa narzędzia. */
function stepSentence(event: SessionEvent): string {
  const summary = text(event.payload, "summary");
  if (summary !== "") return summary;
  const detail = text(event.payload, "detail");
  if (detail !== "") return detail;
  return text(event.payload, "tool") || event.kind;
}

/**
 * „Co agent robił": kroki jednego odcinka pracy, zwinięte do jednej linii.
 *
 * DWA POZIOMY ZWINIĘCIA, bo są dwa różne pytania. Pierwsze („co robił") otwiera listę kroków z
 * jednym zdaniem każdy. Drugie („co się działo w silniku") otwiera mechanikę: ramki, liczniki,
 * znacznik pisania. Mechanika jest schowana głębiej, ale JEST: zdarzenie, które znika bez śladu,
 * jest nieodróżnialne od zdarzenia, którego nie było.
 */
function Steps({ segment }: { readonly segment: StepsSegment }) {
  const count = segment.steps.length;
  if (count === 0 && segment.engine.length === 0) return null;
  return (
    /* LINIA PRACY AGENTA ZE ZNAKIEM MARKI (odbiór 1, różnica nr 7). Te same dane, co przedtem
       („ile kroków i po jakie narzędzia sięgał"), inny kształt: znak 22 px, etykieta 12 px i
       chevron, czyli `st-work` z makiety. Etykiety NIE WYMYŚLAMY: makieta ma tam zdanie o
       etapie („Znalazłem dwie społeczności"), którego nasz ślad nie niesie, więc zostaje to, co
       naprawdę wiemy. Kiedy projekcja zacznie przysyłać nazwę etapu, wejdzie w to samo miejsce. */
    <details className={count === 0 ? "st-steps st-note" : "st-steps st-work"} data-steps={count}>
      <summary className={count === 0 ? undefined : "st-work-toggle"}>
        {count === 0 ? null : <span aria-hidden="true" className="st-indicator">
          <img alt="" className="st-still" height={20} src="/brand/mark-white.png" width={22} />
        </span>}
        <span className="st-work-label">
          {count === 0
            ? "Szczegóły połączenia"
            : "Przebieg pracy"}
        </span>
        <Icon name="chevron-right" />
      </summary>
      {count > 0 ? <p className="st-note">{counted(count, ["wpis", "wpisy", "wpisów"])} w śladzie pracy</p> : null}
      <ol className="st-steps__list">
        {segment.steps.map((event) => (
          <li className="st-steps__item" data-kind={event.kind} key={event.seq}>
            <span>{stepSentence(event)}</span>
            {event.kind === "TOOL_CALL" ? <ToolCall event={event} /> : null}
            {event.kind === "TOOL_RESULT" ? <ToolResult event={event} /> : null}
          </li>
        ))}
      </ol>
      {segment.engine.length === 0 ? null : (
        <details className="st-steps__engine">
          <summary>
            <span className="st-note">
              Szczegóły techniczne: {segment.engine.length} wpisów silnika
            </span>
          </summary>
          <ul>
            {segment.engine.map((event) => (
              <li key={event.seq}>
                <span className="st-note">{text(event.payload, "detail") || event.kind}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </details>
  );
}

function Line({ event, materialLinks, background = false }: {
  readonly event: SessionEvent;
  readonly materialLinks?: ReadonlySet<string>;
  readonly background?: boolean;
}) {
  switch (event.kind) {
    case "USER_MESSAGE": {
      const body = text(event.payload, "text");
      const isGoal = event.payload["is_goal"] === true;
      if (isGoal && background) {
        return <details className="st-steps" data-background-context="true">
          <summary><span>Kontekst pracy w tle</span><Icon name="chevron-right" /></summary>
          <p className="st-note">{body}</p>
        </details>;
      }
      return (
        <div className="st-turn" data-who="user">
          {isGoal ? <span className="st-note">powierzony cel</span> : null}
          {/* `st-user` to bańka z makiety D-046: uniesiona, po prawej, ścięta w dolnym rogu.
              `st-turn` zostaje na zewnątrz, bo po nim rozpoznają wypowiedzi testy i strumień. */}
          <p className="st-user">{body}</p>
        </div>
      );
    }
    case "ASSISTANT_MESSAGE":
      return (
        <div className="st-turn" data-who="agent">
          {/* Agent mówi TEKSTEM, bez bańki i bez ramki (makieta D-046). Czytelny renderer
              odpowiedzi zostaje w środku: to jest ta sama praca z 20.09, tylko w innym pudełku. */}
          <div className="st-agent">
            <AgentText text={text(event.payload, "text")} materialLinks={materialLinks} />
          </div>
        </div>
      );
    case "TOOL_CALL":
      return <ToolCall event={event} />;
    case "TOOL_RESULT":
      return <ToolResult event={event} />;
    case "INTERRUPT":
      return (
        <p className="st-note" data-kind="interrupt">
          Przerwane: {text(event.payload, "detail") || "na Twoją prośbę"}
        </p>
      );
    case "ERROR":
      return (
        <details className="st-note st-turn-error">
          <summary>Nie udało się dokończyć tej tury. Szczegóły</summary>
          <pre className="st-answer__code">{(text(event.payload, "detail") || "błąd bez opisu").replace(/\u001b\[[0-9;]*m/g, "")}</pre>
        </details>
      );
    case "STATUS":
    default: {
      // Wiersz stanu bez zdania (np. wyczerpany budżet, gdzie payload niesie same liczby) nie
      // może być pustym akapitem: pusty wiersz to informacja, że nic się nie stało, a stało się.
      const detail = text(event.payload, "detail");
      const status = text(event.payload, "status");
      if (detail === "" && status === "") return null;
      return (
        <p className="st-note" data-status={status === "" ? undefined : status}>
          {detail === "" ? STATUS_TEXT[status as SessionStatus]?.note ?? status : detail}
        </p>
      );
    }
  }
}

export function Conversation({
  detail,
  workspaceId,
  projectId,
  returnTo,
  children,
}: {
  readonly detail: SessionDetail;
  readonly workspaceId: string;
  readonly projectId: string;
  readonly returnTo: string;
  readonly children?: ReactNode;
}) {
  const { session, events, budzet } = detail;
  const materialLinks = savedMaterialLinks(events, workspaceId, projectId);
  const status = STATUS_TEXT[session.status];
  const waiting = isWaitingOnTheModel(session.status);
  // BUDGET_EXHAUSTED nie jest tu wymienione z rozmysłem: rozmowa, która doszła do limitu, ma
  // wyjście (nowa rozmowa z odwołaniem do tej), a nie ekran bez wyjścia. Patrz `exhausted`.
  const finished = ["DONE", "FAILED"].includes(session.status);
  const exhausted = session.status === "BUDGET_EXHAUSTED";

  // Ostatnia wypowiedź agenta w logu. Gdy niesie to samo, co `result_summary`, wynik nie jest
  // pokazywany drugi raz: to samo zdanie dwa razy pod sobą czyta się jak usterka, a nie jak
  // podsumowanie. Porównanie po znakach widocznych, bo backend bywa przycina białe znaki.
  const lastAgentText = [...events]
    .reverse()
    .find((event) => event.kind === "ASSISTANT_MESSAGE" && text(event.payload, "text") !== "");
  const saidAlready =
    lastAgentText !== undefined &&
    text(lastAgentText.payload, "text").trim() === (session.result_summary ?? "").trim();
  const repeatsError = events.some((event) => event.kind === "ERROR" &&
    text(event.payload, "detail").trim() === (session.result_summary ?? "").trim());
  const resultToShow = session.result_summary !== null && !saidAlready && !repeatsError ? session.result_summary : null;

  /**
   * Czy ten wiersz mówi DOKŁADNIE to samo, co wynik pokazany niżej.
   *
   * Zdanie „Sesja nie ma połączenia Codex, na którym mogłaby ruszyć" przychodzi i jako `STATUS`
   * w śladzie, i jako `result_summary` tury, więc na ekranie stało dwa razy pod sobą: raz małym
   * szarym drukiem, raz jako wynik (uwaga koordynatora z 20.09 przy porównaniu 1:1). To samo
   * zdanie dwa razy czyta się jak usterka, a nie jak podkreślenie. Zostaje WYNIK, bo jest
   * pełniejszy i stoi w miejscu, w którym człowiek go szuka.
   */
  function repeatsResult(event: SessionEvent): boolean {
    if (resultToShow === null || event.kind !== "STATUS") return false;
    const line = text(event.payload, "detail").trim();
    return line !== "" && line === resultToShow.trim();
  }

  return (
    /* `st-chat-view` to KORZEŃ z makiety D-046: pod nim obowiązują jej tokeny (`app/chat.css`),
       a nad nim zostają tokeny Studia. Dzięki temu rozmowa wygląda jak makieta, a reszta
       produktu nie zmienia się przy okazji. `st-chat` zostaje, bo po nim rozpoznają ten blok
       testy i arkusz Studia. */
    <section className="st-chat-view st-chat" data-session={session.session_id}>
      <div className="st-thread">
      <div className="st-chat__head" data-session-status={session.status}>
        <span className="st-tag" data-session-label="1" data-tone={waiting ? "state" : "plain"}>
          {status.label}
        </span>
        {/* The mark waves ONLY here, and only while a turn is in flight. `studio.css` makes it
            static under prefers-reduced-motion. */}
        <span aria-hidden="true" className="st-wave" data-waiting={waiting ? "yes" : "no"} />
        <span className="st-note" data-session-note="1">{status.note}</span>
        {/* PRZERWANIE I WZNOWIENIE STOI PRZY STANIE TURY, NIE POD POLEM (odbiór 1, różnica
            nr 20). „Wznów" odpowiada na to samo, co mówi znacznik obok („wstrzymana",
            „przerwana"), więc czyta się je razem; pod polem stało jak druga połowa pisania,
            czyli obok czynności, z którą nie ma nic wspólnego. */}
        {!waiting && session.status !== "PAUSED" ? null : (
          <form action="/studio/session" className="st-turn-aside" method="post">
            <input name="action" type="hidden" value={waiting ? "interrupt" : "resume"} />
            <input name="workspace_id" type="hidden" value={workspaceId} />
            <input name="project_id" type="hidden" value={projectId} />
            <input name="session_id" type="hidden" value={session.session_id} />
            <input name="return_to" type="hidden" value={returnTo} />
            <button className="st-text" type="submit">
              {waiting ? "Przerwij" : "Wznów"}
            </button>
          </form>
        )}
        {/* Ile z limitu tej rozmowy zostało, POKAZANE ZANIM się skończy. Założyciel zobaczył
            limit dopiero wtedy, gdy rozmowa się o niego rozbiła, i wziął go za limit swojego
            konta Codex. Brak danych z control plane znaczy milczenie, nie zera. */}
        {/* Rozmowa wstrzymana czymś z zewnątrz (na przykład limitem konta Codex) mówi CZYM,
            gdy control plane to przysłał. Zdanie jest stamtąd, nie stąd: godzinę odnowienia zna
            serwer, a ekran, który by ją zgadywał, podałby porę z innej strefy czasowej. */}
        {session.status === "WAITING_EVENT" && (session.result_summary ?? "") !== "" ? (
          <span className="st-note" data-waiting-reason="1">
            {session.result_summary}
          </span>
        ) : null}
        {budzet === null ? null : (
          <details className="st-note st-budget"><summary>Limit rozmowy</summary><span data-budget="tak">
            {`${budzet.tury.zuzyte} z ${budzet.tury.limit} wymian, ` +
              `${budzet.narzedzia.zuzyte} z ${budzet.narzedzia.limit} użyć narzędzi` +
              (budzet.wlasny ? "" : " (limit domyślny Wenextu)")}
          </span></details>
        )}
      </div>

      <div className="st-chat__log">
        {events.length === 0 ? (
          <p className="st-empty">Ta rozmowa nie ma jeszcze ani jednego wpisu.</p>
        ) : (
          groupConversation(events)
            .filter((segment) => !(segment.kind === "message" && repeatsResult(segment.event)))
            .map((segment, index) =>
              segment.kind === "message" ? (
                <Line event={segment.event} key={segment.event.seq} materialLinks={materialLinks} background={session.rodzaj === "PRESENCE"} />
              ) : (
                <Steps key={`steps-${index}`} segment={segment} />
              ),
            )
        )}
      </div>

      {/* WYNIK TURY JEST ODPOWIEDZIĄ, NIE NOTKĄ. Wcześniej stał tu jako „Ostatni wynik: …"
          w jednej linii szarego tekstu, razem z gwiazdkami i kratkami markdownu; założyciel
          zobaczył to 20.09 o 01:15 i miał rację. Pokazujemy go tym samym rendererem, co
          odpowiedź agenta, a jeśli ostatnia wypowiedź w logu niesie TO SAMO zdanie, nie
          pokazujemy go drugi raz: dwa razy ta sama odpowiedź to nie jest podsumowanie. */}
      {resultToShow === null ? null : (
        <div className="st-msg" data-kind="summary" data-role="agent">
          <span className="st-msg__who">Wynik</span>
          <AgentText text={resultToShow} materialLinks={materialLinks} />
        </div>
      )}

        {/* KARTY I POSTĘP STOJĄ W WĄTKU, nie w panelu obok (D-046). To są rzeczy, które agent
            właśnie zrobił albo proponuje, więc czyta się je w kolejności rozmowy. Panel z boku
            kazałby przenosić wzrok i po dwóch dniach nikt by tam nie zaglądał. */}
        {children}
        <AgentProgress progress={session.status === "RUNNING" ? detail.postep ?? null : null} />
        <ConversationCards
          cards={detail.karty ?? []}
          projectId={projectId}
          returnTo={returnTo}
          sessionId={session.session_id}
          workspaceId={workspaceId}
        />
      </div>

      {/* Stopka z makiety: pole do pisania siedzi pod wątkiem, nie w nim, i zostaje na miejscu,
          gdy wątek się przewija. */}
      <footer className="st-footer">
      {exhausted || finished ? (
        /* D-045: DO ZAKOŃCZONEJ ROZMOWY SIĘ NIE WRACA, więc jej ekran prowadzi do następnej.
           Wcześniej stan końcowy kończył się zdaniem „zacznij nową" i człowiek zostawał sam z
           pytaniem, gdzie ta nowa jest. Jedno wyjście na wszystkie trzy stany końcowe, przez
           `…/sessions/{id}/continue`, czyli tę samą trasę co przy limicie: nowa rozmowa niesie
           wtedy `parent_session_id` i po tygodniu widać, która z której wyrosła. */
        <div className="st-composer" data-kind={exhausted ? "exhausted" : "finished"}>
          <p className="st-note">
            {exhausted
              ? (session.result_summary ??
                "Ta rozmowa doszła do limitu ustawionego w Wenext. To nie jest limit Twojego " +
                  "konta Codex.")
              : session.status === "FAILED"
                ? "Nie udało się dokończyć odpowiedzi. Możesz ponowić ją w tej rozmowie."
                : "Ta rozmowa jest zamknięta. Kolejna zaczyna od tego, co ten projekt już wie."}
          </p>
          <form action="/studio/session" method="post">
            <input name="action" type="hidden" value="continue" />
            <input name="workspace_id" type="hidden" value={workspaceId} />
            <input name="project_id" type="hidden" value={projectId} />
            <input name="session_id" type="hidden" value={session.session_id} />
            <input name="return_to" type="hidden" value={returnTo} />
            <button className="st-btn" data-kind="primary" type="submit">
              Nowa rozmowa
            </button>
          </form>
          {session.status === "FAILED" ? (
            <form action="/studio/session" method="post">
              <input name="action" type="hidden" value="resume" />
              <input name="workspace_id" type="hidden" value={workspaceId} />
              <input name="project_id" type="hidden" value={projectId} />
              <input name="session_id" type="hidden" value={session.session_id} />
              <input name="return_to" type="hidden" value={returnTo} />
              <button className="st-btn" type="submit">Ponów odpowiedź</button>
            </form>
          ) : null}
          <p className="st-note">
            {exhausted
              ? "Nowa rozmowa zaczyna się od tego samego celu i wskazuje tę jako poprzednią. " +
                "Limit zmienisz w ustawieniach projektu."
              : "Nowa rozmowa wskazuje tę jako poprzednią i widzi kontekst projektu: cel, " +
                "dokumenty i ustalenia."}
          </p>
        </div>
      ) : (
        <>
          {/* POLE JEST OTWARTE TAKŻE W CZASIE PRACY AGENTA (D-046). Wiadomość ląduje w śladzie i
              bierze ją następna tura; czekanie na pozwolenie, żeby się odezwać, jest
              przeciwieństwem rozmowy. Linia stanu pod polem mówi, co się dzieje, zamiast
              blokować pisanie. */}
          <form action="/studio/session" className="st-composer" method="post">
            <input name="action" type="hidden" value="message" />
            <input name="workspace_id" type="hidden" value={workspaceId} />
            <input name="project_id" type="hidden" value={projectId} />
            <input name="session_id" type="hidden" value={session.session_id} />
            <input name="return_to" type="hidden" value={returnTo} />
            <textarea
              aria-label="Wiadomość do agenta"
              className="st-input"
              maxLength={4000}
              name="text"
              placeholder={
                waiting
                  ? "Możesz pisać w trakcie tury. Agent dostanie to w następnej."
                  : "Dopowiedz kierunek…"
              }
              required
              rows={1}
            />
            <div className="st-composer-bottom">
              <span>
                <span aria-hidden="true" className="st-mode-dot" />
                <span data-composer-status="1">
                  {waiting ? "Działa samodzielnie · możesz dopowiedzieć" : "Rozmowa trwa dalej"}
                </span>
              </span>
              <button aria-label="Wyślij wiadomość" className="st-send" type="submit">
                <Icon name="arrow-up" />
              </button>
            </div>
          </form>

        </>
      )}
      </footer>
    </section>
  );
}
