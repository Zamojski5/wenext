/**
 * Rozmowa ma wyglądać jak rozmowa, a nie jak ślad silnika (uwaga założyciela z 19.09).
 *
 * CZEGO PILNUJĄ TE TESTY. Ekran sesji jest jedynym miejscem, w którym człowiek widzi, co agent
 * mu odpowiedział, więc każdy z tych przypadków to jeden sposób, w jaki ta odpowiedź może się
 * zgubić:
 *
 *   * mechanika silnika wpuszczona do głównego nurtu topi zdanie modelu w dziesiątkach linijek;
 *   * kroki pracy wymieszane z treścią każą czytać listę identyfikatorów zamiast rozmowy;
 *   * markdown pokazany surowo („**bez publikowania**") wygląda jak błąd, a nie jak wyróżnienie;
 *   * mechanika WYRZUCONA zamiast schowanej znika bez śladu, a wtedy po awarii nie da się
 *     odtworzyć, co się działo.
 */
import { describe, expect, it } from "vitest";

import { Conversation } from "../components/studio/conversation";
import {
  groupConversation,
  renderMarkdown,
  softenDashes,
  type StepsSegment,
} from "../lib/conversation-render";
import type { SessionDetail, SessionEvent } from "../lib/session-schema";
import { renderToDocument } from "./render";

const WORKSPACE = "aaaaaaaa-0000-4000-8000-000000000001";
const PROJECT = "bbbbbbbb-0000-4000-8000-000000000001";
const SESSION = "cccccccc-0000-4000-8000-000000000001";

let seq = 0;

function event(kind: string, payload: Record<string, unknown>): SessionEvent {
  seq += 1;
  return {
    seq,
    kind: kind as SessionEvent["kind"],
    payload,
    created_at: "2026-09-19T21:00:00+00:00",
  };
}

function conversation(events: readonly SessionEvent[]): SessionDetail {
  return {
    session: {
      session_id: SESSION,
      project_id: PROJECT,
      goal: "sprawdź, o czym rozmawia społeczność",
      status: "WAITING_USER",
      engine_thread_id: null,
      result_summary: null,
      result_refs: [],
      created_at: "2026-09-19T20:00:00+00:00",
      updated_at: "2026-09-19T21:00:00+00:00",
      finished_at: null,
    },
    events,
    last_seq: events.length,
    budzet: null,
  };
}

describe("podział śladu na rozmowę i pracę", () => {
  it("sasiadujace_kroki_stoja_w_jednym_bloku_a_tresc_osobno", () => {
    const segments = groupConversation([
      event("USER_MESSAGE", { text: "sprawdź to" }),
      event("TOOL_CALL", { tool: "thread.read", summary: "przeczytał wątek: t3_abc" }),
      event("TOOL_RESULT", { tool: "thread.read", summary: "thread.read: gotowe, 1.2 s" }),
      event("STATUS", { detail: "narzędzie pracuje" }),
      event("ASSISTANT_MESSAGE", { text: "Gotowe." }),
    ]);

    expect(segments.map((one) => one.kind)).toEqual(["message", "steps", "message"]);
    const steps = segments[1] as StepsSegment;
    expect(steps.steps.length).toBe(3);
    expect(steps.tools).toEqual(["thread.read"]);
  });

  it("krok_po_odpowiedzi_zaczyna_nowy_blok", () => {
    // Inaczej ekran sugerowałby, że narzędzie wywołane PO odpowiedzi poprzedzało ją.
    const segments = groupConversation([
      event("TOOL_CALL", { tool: "a" }),
      event("ASSISTANT_MESSAGE", { text: "pierwsza" }),
      event("TOOL_CALL", { tool: "b" }),
    ]);
    expect(segments.map((one) => one.kind)).toEqual(["steps", "message", "steps"]);
  });

  it("zdanie_produktu_do_czlowieka_nie_chowa_sie_pod_zapadke", () => {
    // „Nie ma połączenia Codex" to odpowiedź na pytanie „czemu nic się nie dzieje". Schowana
    // w bloku „Co agent robił" byłaby odpowiedzią ukrytą przed tym, kto na nią czeka.
    const segments = groupConversation([
      event("TOOL_CALL", { tool: "thread.read" }),
      event("STATUS", {
        status: "WAITING_USER",
        detail: "Sesja nie ma połączenia Codex, na którym mogłaby ruszyć.",
      }),
      event("STATUS", { detail: "narzędzie pracuje" }),
    ]);

    expect(segments.map((one) => one.kind)).toEqual(["steps", "message", "steps"]);
  });

  it("mechanika_silnika_jest_schowana_ale_policzona_i_zachowana", () => {
    const segments = groupConversation([
      event("STATUS", { detail: "agent pisze odpowiedź", level: "engine", wenext_pisze: true }),
      event("STATUS", { detail: "silnik: 325 ramek pominiętych", level: "engine" }),
      event("TOOL_CALL", { tool: "thread.read" }),
    ]);

    expect(segments.length).toBe(1);
    const steps = segments[0] as StepsSegment;
    expect(steps.steps.length).toBe(1);
    expect(steps.engine.length).toBe(2);
    // Policzona i zachowana: nic z tych dwóch wierszy nie zniknęło.
    expect(steps.engine.map((one) => one.payload["detail"])).toEqual([
      "agent pisze odpowiedź",
      "silnik: 325 ramek pominiętych",
    ]);
  });
});

describe("bezpieczny markdown odpowiedzi", () => {
  it("szablon_zachowuje_wiersze_i_nie_interpretuje_html_wewnatrz_bloku", () => {
    const template = "Rozmówca:\n1. Przypadek\n  [ ] Ważny problem\n<script>test()</script>\nleft — right";
    expect(renderMarkdown("```text\n" + template + "\n```\n\nDalej.")).toEqual([
      { kind: "codeblock", text: template },
      { kind: "paragraph", pieces: [{ kind: "text", text: "Dalej." }] },
    ]);
  });
  it("pogrubienie_kursywa_kod_i_listy", () => {
    const blocks = renderMarkdown("**Trzy rzeczy**, po kolei.\n\n- pierwsza\n- `druga`");
    expect(blocks[0]?.kind).toBe("paragraph");
    expect(blocks[1]?.kind).toBe("list");
  });

  it("html_z_modelu_zostaje_tekstem", () => {
    // Nie ma tu parsera HTML i nie ma `dangerouslySetInnerHTML`; znacznik ma zostać napisem.
    const blocks = renderMarkdown("<script>alert(1)</script>");
    expect(JSON.stringify(blocks)).toContain("<script>alert(1)</script>");
  });

  it("dlugie_pauzy_zamieniane_zgodnie_z_regula_ui", () => {
    expect(softenDashes("tak — nie")).toBe("tak, nie");
    expect(softenDashes("uwaga— to jest to")).toBe("uwaga: to jest to");
    expect(softenDashes("2020–2021")).toBe("2020-2021");
  });
});

describe("ekran rozmowy", () => {
  it("odpowiedz_jest_trescia_a_nie_surowym_markdownem", () => {
    seq = 0;
    const document_ = renderToDocument(
      <Conversation
        detail={conversation([
          event("USER_MESSAGE", { text: "sprawdź to" }),
          event("ASSISTANT_MESSAGE", { text: "**Bez publikowania**, na razie." }),
        ])}
        projectId={PROJECT}
        returnTo="/"
        workspaceId={WORKSPACE}
      />,
    );

    const agent = document_.querySelector("[data-who='agent']");
    expect(agent?.querySelector("strong")?.textContent).toBe("Bez publikowania");
    expect(agent?.textContent).not.toContain("**");
  });

  it("praca_jest_jedna_zwinieta_linia_a_narzedzia_sa_w_srodku", () => {
    seq = 0;
    const document_ = renderToDocument(
      <Conversation
        detail={conversation([
          event("TOOL_CALL", { tool: "catalog.list", summary: "sprawdził, czym dysponuje" }),
          event("TOOL_RESULT", { tool: "catalog.list", summary: "catalog.list: gotowe" }),
          event("TOOL_CALL", { tool: "thread.read", summary: "przeczytał wątek: t3_abc" }),
          event("TOOL_RESULT", { tool: "thread.read", summary: "thread.read: gotowe" }),
          event("ASSISTANT_MESSAGE", { text: "Gotowe." }),
        ])}
        projectId={PROJECT}
        returnTo="/"
        workspaceId={WORKSPACE}
      />,
    );

    const steps = document_.querySelector(".st-steps");
    expect(steps?.getAttribute("data-steps")).toBe("4");
    const summary = steps?.querySelector("summary")?.textContent ?? "";
    expect(summary).toContain("Przebieg pracy");
    expect(summary).not.toContain("catalog.list");
    expect(steps?.textContent).toContain("catalog.list");
    expect(steps?.textContent).toContain("thread.read");
    // Zdania kroków są w środku, nie w podsumowaniu: podsumowanie ma się mieścić w jednej linii.
    expect(steps?.textContent).toContain("przeczytał wątek: t3_abc");

    // LINIA PRACY AGENTA MA KSZTAŁT Z MAKIETY (odbiór 1, różnica nr 7): znak marki, etykieta i
    // chevron, czyli `st-work`. Te same dane, co przedtem; inny kształt, bo w referencji to jest
    // linia pracy, a nie szara notka. Etykiety nie wymyślamy: makieta ma tam zdanie o etapie,
    // którego nasz ślad nie niesie.
    expect(steps?.classList.contains("st-work")).toBe(true);
    const toggle = steps?.querySelector("summary.st-work-toggle");
    expect(toggle?.querySelector(".st-indicator img")).not.toBeNull();
    expect(toggle?.querySelector(".st-work-label")?.textContent).toContain("Przebieg pracy");
    expect(toggle?.querySelector(".ic-chevron-right")).not.toBeNull();
  });

  it("mechanika_jest_pod_druga_zapadka_i_nie_udaje_kroku", () => {
    seq = 0;
    const document_ = renderToDocument(
      <Conversation
        detail={conversation([
          event("TOOL_CALL", { tool: "thread.read", summary: "przeczytał wątek: t3_abc" }),
          event("STATUS", { detail: "silnik: 325 ramek pominiętych", level: "engine" }),
          event("ASSISTANT_MESSAGE", { text: "Gotowe." }),
        ])}
        projectId={PROJECT}
        returnTo="/"
        workspaceId={WORKSPACE}
      />,
    );

    const steps = document_.querySelector(".st-steps");
    expect(steps?.getAttribute("data-steps")).toBe("1");
    const engine = document_.querySelector(".st-steps__engine");
    expect(engine?.querySelector("summary")?.textContent).toContain("Szczegóły techniczne");
    expect(engine?.textContent).toContain("325 ramek");
  });
});

describe("limit rozmowy na ekranie (0026)", () => {
  function withBudget(status: string, budzet: SessionDetail["budzet"]): SessionDetail {
    seq = 0;
    const detail = conversation([event("ASSISTANT_MESSAGE", { text: "Gotowe." })]);
    return {
      ...detail,
      session: { ...detail.session, status: status as SessionDetail["session"]["status"] },
      budzet,
    };
  }

  it("pasek_stanu_mowi_ile_zostalo_zanim_sie_skonczy", () => {
    // Założyciel zobaczył limit dopiero wtedy, gdy rozmowa się o niego rozbiła, i wziął go za
    // limit swojego konta Codex. Liczby mają stać na ekranie od początku.
    const document_ = renderToDocument(
      <Conversation
        detail={withBudget("WAITING_USER", {
          tury: { zuzyte: 12, limit: 30 },
          narzedzia: { zuzyte: 40, limit: 120 },
          minuty_na_ture: 60,
          wlasny: false,
        })}
        projectId={PROJECT}
        returnTo="/"
        workspaceId={WORKSPACE}
      />,
    );

    const bar = document_.querySelector("[data-budget]")?.textContent ?? "";
    expect(bar).toContain("12 z 30 wymian");
    expect(bar).toContain("40 z 120 użyć narzędzi");
    // Limit domyślny nazwany wprost: limit, o którym nie wiadomo, kto go wybrał, wygląda na cudzy.
    expect(bar).toContain("limit domyślny Wenextu");
  });

  it("brak_danych_o_limicie_to_milczenie_a_nie_zera", () => {
    const document_ = renderToDocument(
      <Conversation
        detail={withBudget("WAITING_USER", null)}
        projectId={PROJECT}
        returnTo="/"
        workspaceId={WORKSPACE}
      />,
    );
    expect(document_.querySelector("[data-budget]")).toBe(null);
  });

  it("rozmowa_po_limicie_ma_wyjscie_a_nie_martwy_ekran", () => {
    const document_ = renderToDocument(
      <Conversation
        detail={withBudget("BUDGET_EXHAUSTED", null)}
        projectId={PROJECT}
        returnTo="/"
        workspaceId={WORKSPACE}
      />,
    );

    const composer = document_.querySelector("[data-kind='exhausted']");
    expect(composer).not.toBe(null);
    const form = composer?.querySelector("form");
    expect(form?.getAttribute("action")).toBe("/studio/session");
    expect(form?.querySelector("input[name='action']")?.getAttribute("value")).toBe("continue");
    expect(form?.querySelector("button")?.textContent).toContain("Nowa rozmowa");
    // I żadnego „Sesja jest zakończona", bo to zdanie zostawiało człowieka bez ruchu.
    expect(composer?.textContent).not.toContain("Sesja jest zakończona");
  });
});

it("real tool result statuses stay in one collapsed work block", () => {
  const events = [event("USER_MESSAGE", { text: "Research" }),
    event("TOOL_CALL", { tool: "sources.search" }),
    event("TOOL_RESULT", { tool: "sources.search", status: "completed" }),
    event("TOOL_CALL", { tool: "sources.read" }),
    event("TOOL_RESULT", { tool: "sources.read", status: "failed" }),
    event("ASSISTANT_MESSAGE", { text: "Wynik" })];
  expect(groupConversation(events).map(s => s.kind)).toEqual(["message", "steps", "message"]);
  const doc = renderToDocument(<Conversation detail={conversation(events)} workspaceId={WORKSPACE} projectId={PROJECT} returnTo="/" />);
  expect(doc.querySelectorAll(".st-steps")).toHaveLength(1);
  expect(doc.querySelectorAll(".st-steps .st-tool")).toHaveLength(4);
});

it("renders usable citations while refusing executable link schemes", () => {
  const doc = renderToDocument(<Conversation detail={conversation([event("ASSISTANT_MESSAGE", {
    text: "[Źródło](https://example.com/research) oraz [złe](javascript:alert) <script>x</script>",
  })])} workspaceId={WORKSPACE} projectId={PROJECT} returnTo="/" />);
  expect(doc.querySelector(".st-answer a")?.getAttribute("href")).toBe("https://example.com/research");
  expect(doc.querySelectorAll(".st-answer a")).toHaveLength(1);
  expect(doc.querySelector(".st-answer script")).toBeNull();
});

it("keeps research links clickable inside emphasis and numbering after explanations", () => {
  const doc = renderToDocument(<Conversation detail={conversation([event("ASSISTANT_MESSAGE", {
    text: "1. **Osoba — [problem `update_cart`](https://example.com/issue)**\n\nDlaczego pasuje.\n\n2. **[Druga](https://example.com/repo)**\n\n3. **[złe](javascript:alert)**",
  })])} workspaceId={WORKSPACE} projectId={PROJECT} returnTo="/" />);
  expect(doc.querySelectorAll(".st-answer strong a")).toHaveLength(2);
  expect(doc.querySelector(".st-answer strong a")?.textContent).toBe("problem update_cart");
  expect(Array.from(doc.querySelectorAll(".st-answer ol")).map(list => list.getAttribute("start"))).toEqual(["1", "2", "3"]);
});

it("opens saved artifacts locally using tool provenance, without rewriting unrelated links", () => {
  const path = `/w/${WORKSPACE}/p/${PROJECT}/knowledge?source=9bb5ab34-2dad-44df-8852-5ec7bbd63c09`;
  const doc = renderToDocument(<Conversation detail={conversation([
    event("TOOL_RESULT", { tool: "materials.save", status: "completed", result: { content: [{ type: "text", text: JSON.stringify({ stan: "WYKONANE", adres: path }) }] } }),
    event("ASSISTANT_MESSAGE", { text: `**[Materiał](https://example.invalid${path})** i [źródło](https://example.com/article)` }),
  ])} workspaceId={WORKSPACE} projectId={PROJECT} returnTo="/" />);
  const links = doc.querySelectorAll(".st-answer a");
  expect(links[0]?.getAttribute("href")).toBe(path);
  expect(links[0]?.getAttribute("target")).toBeNull();
  expect(links[1]?.getAttribute("href")).toBe("https://example.com/article");
  const withoutEvidence = renderToDocument(<Conversation detail={conversation([
    event("TOOL_RESULT", { tool: "sources.read", result: { stan: "WYKONANE", adres: path } }),
    event("ASSISTANT_MESSAGE", { text: `[Przykład](https://example.com${path})` }),
  ])} workspaceId={WORKSPACE} projectId={PROJECT} returnTo="/" />);
  expect(withoutEvidence.querySelector(".st-answer a")?.getAttribute("href")).toBe(`https://example.com${path}`);
});

it("background_instructions_are_collapsed_while_the_result_stays_visible", () => {
  const detail = conversation([
    event("USER_MESSAGE", { text: "Internal context: draft.create and scope", is_goal: true }),
    event("ASSISTANT_MESSAGE", { text: "Brak nowego sygnału. Wracam, gdy będzie po co." }),
  ]);
  const doc = renderToDocument(<Conversation
    detail={{ ...detail, session: { ...detail.session, rodzaj: "PRESENCE" } }}
    workspaceId={WORKSPACE} projectId={PROJECT} returnTo="/" />);
  const context = doc.querySelector("details[data-background-context]");
  expect(context).not.toBeNull();
  expect(context?.hasAttribute("open")).toBe(false);
  expect(context?.textContent).toContain("draft.create");
  expect(doc.querySelector("[data-who='agent']")?.textContent).toContain("Brak nowego sygnału");
  expect(doc.querySelector(".st-user")).toBeNull();
});
