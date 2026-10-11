/**
 * Jak czytać ślad tury, żeby wyszła z niego ROZMOWA (uwaga założyciela z 19.09).
 *
 * PROBLEM, KTÓRY TO ROZWIĄZUJE. Ślad jest zapisem dla audytu: wywołanie narzędzia, jego wynik,
 * żądanie silnika, decyzja. Wszystko to jest potrzebne i nic z tego nie znika. Ale człowiek,
 * który otwiera ekran, zadaje inne pytanie niż audytor: „co mi odpowiedziałeś". Ekran pokazujący
 * ślad wiersz po wierszu odpowiada na pytanie audytora i chowa odpowiedź modelu na końcu listy
 * technicznych linijek. Ten moduł rozdziela oba widoki, nie kasując żadnego.
 *
 * TRZY RODZAJE WIERSZA, po tym, komu służą:
 *
 *   * TREŚĆ: wiadomość człowieka, odpowiedź agenta, błąd, przerwanie. To jest rozmowa.
 *   * KROK: wywołanie narzędzia i jego wynik. To jest praca, zwijana do jednego bloku „Co agent
 *     robił", rozwijanego kliknięciem. Zwinięta, nie ukryta: twierdzenie agenta bez tego, na
 *     czym stoi, to twierdzenie, którego nie da się sprawdzić.
 *   * MECHANIKA (`payload.level === "engine"`): ramki silnika, liczniki, znacznik „pisze".
 *     Schowane o jedno kliknięcie głębiej, bo nie mówią nic o tym, co agent ZROBIŁ.
 *
 * KOLEJNOŚĆ JEST ZACHOWANA CO DO WIERSZA. Kroki zbierają się w blok tylko wtedy, gdy stoją obok
 * siebie; krok po odpowiedzi agenta trafia do NOWEGO bloku, żeby nie powstało wrażenie, że
 * narzędzie wywołane po odpowiedzi było wywołane przed nią.
 */
import type { SessionEvent } from "./session-schema";

/** Wiersze, które są rozmową, a nie mechaniką. */
const CONTENT_KINDS = new Set(["USER_MESSAGE", "ASSISTANT_MESSAGE", "ERROR", "INTERRUPT"]);

/**
 * `STATUS` niosący `payload.status` to ZMIANA STANU SESJI, czyli zdanie produktu do człowieka:
 * „nie ma połączenia Codex, podłącz je", „budżet wyczerpany". Takie zdanie w zwiniętym bloku
 * „Co agent robił" byłoby odpowiedzią schowaną przed tym, kto na nią czeka. Pozostałe `STATUS`
 * (postęp narzędzia, żądanie silnika) to przebieg pracy i tam jest ich miejsce.
 */
function isSessionState(event: SessionEvent): boolean {
  const status = event.payload["status"];
  // Tool results also have a status ("completed"/"failed"). They belong inside
  // the work disclosure, not between every pair of tool calls in the conversation.
  return event.kind === "STATUS" && typeof status === "string" && status.trim() !== "";
}

/** Wiersze, które są krokiem pracy. */
const STEP_KINDS = new Set(["TOOL_CALL", "TOOL_RESULT"]);

export const ENGINE_LEVEL = "engine";

export interface MessageSegment {
  readonly kind: "message";
  readonly event: SessionEvent;
}

export interface StepsSegment {
  readonly kind: "steps";
  /** Kroki pracy, w kolejności. */
  readonly steps: readonly SessionEvent[];
  /** Mechanika silnika z tego samego odcinka. Schowana głębiej, nigdy wyrzucona. */
  readonly engine: readonly SessionEvent[];
  /** Nazwy narzędzi, bez powtórzeń, w kolejności pierwszego użycia. */
  readonly tools: readonly string[];
}

export type Segment = MessageSegment | StepsSegment;

function toolOf(event: SessionEvent): string {
  const value = event.payload["tool"];
  return typeof value === "string" && value.trim() !== "" ? value : "";
}

function isEngine(event: SessionEvent): boolean {
  return event.payload["level"] === ENGINE_LEVEL;
}

/**
 * Ślad w odcinki: wiadomości osobno, sąsiadujące kroki razem.
 *
 * `STATUS` bez poziomu `engine` jest zdaniem dla człowieka (postęp narzędzia, „czekam na Ciebie"),
 * więc ląduje między krokami, a nie w treści: nie jest odpowiedzią agenta i nie ma udawać, że
 * nią jest.
 */
export function groupConversation(events: readonly SessionEvent[]): readonly Segment[] {
  const segments: Segment[] = [];
  let steps: SessionEvent[] = [];
  let engine: SessionEvent[] = [];

  const flush = () => {
    if (steps.length === 0 && engine.length === 0) return;
    const tools: string[] = [];
    for (const step of steps) {
      const tool = toolOf(step);
      if (tool !== "" && !tools.includes(tool)) tools.push(tool);
    }
    segments.push({ kind: "steps", steps, engine, tools });
    steps = [];
    engine = [];
  };

  for (const event of events) {
    if (isEngine(event)) {
      engine.push(event);
      continue;
    }
    if (CONTENT_KINDS.has(event.kind) || isSessionState(event)) {
      flush();
      segments.push({ kind: "message", event });
      continue;
    }
    if (STEP_KINDS.has(event.kind) || event.kind === "STATUS") {
      steps.push(event);
    }
  }
  flush();
  return segments;
}

/** Ile kroków pracy niesie ten odcinek. Mechanika się nie liczy: nie jest pracą agenta. */
export function stepCount(segment: StepsSegment): number {
  return segment.steps.length;
}

/**
 * Długie pauzy na znaki, które da się przeczytać na głos.
 *
 * REGUŁA PRODUKTU, nie preferencja: w tekstach Wenextu nie ma ani `—`, ani `–`. Model czasem je
 * napisze; zamiana przy RENDERZE, a nie przy zapisie, bo ślad ma nieść to, co model naprawdę
 * powiedział. Pauza między spacjami staje się przecinkiem, pauza przyklejona do słowa dwukropkiem
 * albo łącznikiem, zależnie od tego, co po niej stoi.
 */
export function softenDashes(text: string): string {
  return text
    .replace(/\s+[—–]\s+/g, ", ")
    .replace(/([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9])[—–]\s+/g, "$1: ")
    .replace(/[—–]/g, "-");
}

export interface InlinePiece {
  readonly kind: "text" | "bold" | "italic" | "code" | "link";
  readonly text: string;
  readonly href?: string;
  readonly children?: readonly InlinePiece[];
}

/** Routes come from successful tool results, not a hostname invented in the reply. */
export function savedMaterialLinks(events: readonly SessionEvent[], workspaceId: string, projectId: string): ReadonlySet<string> {
  const paths = new Set<string>();
  const prefix = `/w/${workspaceId}/p/${projectId}/knowledge?source=`;
  for (const event of events) {
    if (event.kind !== "TOOL_RESULT" || event.payload["tool"] !== "materials.save" || event.payload["error"]) continue;
    try {
      let result = event.payload["result"] as Record<string, unknown> | undefined;
      if (Array.isArray(result?.["content"])) {
        const block = result["content"].find((entry: Record<string, unknown>) => entry["type"] === "text");
        result = JSON.parse(String(block?.["text"]));
      }
      const address = result?.["adres"];
      if (result?.["stan"] === "WYKONANE" && typeof address === "string" && address.startsWith(prefix) && /^[a-f0-9-]{36}$/i.test(address.slice(prefix.length))) paths.add(address);
    } catch { /* A malformed result cannot create a trusted navigation target. */ }
  }
  return paths;
}

export function materialLinkTarget(href: string, saved: ReadonlySet<string> | undefined): string {
  if (!saved?.size || !/^https?:\/\//i.test(href)) return href;
  try {
    const url = new URL(href);
    const path = `${url.pathname}${url.search}`;
    return !url.hash && saved.has(path) ? path : href;
  } catch { return href; }
}

export interface Paragraph {
  readonly kind: "paragraph";
  readonly pieces: readonly InlinePiece[];
}

export interface ListBlock {
  readonly kind: "list";
  readonly items: readonly (readonly InlinePiece[])[];
  /** Lista numerowana („1.", „2.") czy wypunktowana. Numer rysuje przeglądarka, nie tekst. */
  readonly ordered: boolean;
  readonly start?: number;
}

/**
 * Śródtytuł. JEDEN POZIOM, niezależnie od liczby kratek.
 *
 * Model pisze raz `##`, raz `###`, zależnie od nastroju, a odpowiedź w rozmowie nie jest
 * dokumentem i nie ma spisu treści: dwa rozmiary śródtytułu w jednej odpowiedzi wyglądają jak
 * hierarchia, której nikt nie zaplanował. `h1` nie powstaje w ogóle, bo tytułem strony jest
 * nazwa projektu, a nie zdanie modelu.
 */
export interface HeadingBlock {
  readonly kind: "heading";
  readonly pieces: readonly InlinePiece[];
}

/** Cytat: linie zaczynające się od „>". Renderowany kreską z boku, nie ramką. */
export interface QuoteBlock {
  readonly kind: "quote";
  readonly pieces: readonly InlinePiece[];
}

export interface CodeBlock {
  readonly kind: "codeblock";
  readonly text: string;
}

export type Block = Paragraph | ListBlock | HeadingBlock | QuoteBlock | CodeBlock;

const INLINE = /(\[[^\]\n]+\]\([^\s)]+\)|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;

/**
 * Minimalny, BEZPIECZNY markdown: pogrubienie, kursywa, kod w linii, listy, akapity.
 *
 * ZWRACA DANE, NIE HTML, i to jest cała ochrona: React wstawia tekst jako tekst, więc nic, co
 * model napisze, nie stanie się znacznikiem. Nie ma tu `dangerouslySetInnerHTML` i nie ma parsera
 * HTML, bo odpowiedź modelu jest treścią z zewnątrz, a treść z zewnątrz nie buduje ekranu.
 *
 * CO ROZUMIE (20.09, po uwadze założyciela o „brzydkiej" odpowiedzi): akapity, śródtytuły
 * (`#`, `##`, `###`, wszystkie jako JEDEN poziom), listy wypunktowane i numerowane, cytat (`>`),
 * pogrubienie, kursywę i kod w linii. To jest dokładnie to, czego model używa, odpowiadając na
 * pytanie, i nic więcej.
 *
 * Links accept only HTTP(S), never executable schemes or raw HTML. A clickable citation lets
 * the person inspect the original; it does not certify that the model's claim is correct.
 */
const BULLET = /^\s*[-*]\s+/;
const NUMBER = /^\s*\d+[.)]\s+/;
const HEADING = /^\s{0,3}#{1,6}\s+/;
const QUOTE = /^\s{0,3}>\s?/;

export function renderMarkdown(raw: string): readonly Block[] {
  const text = raw.replace(/\r\n/g, "\n");
  const blocks: Block[] = [];

  // LINIA PO LINII, NIE AKAPIT PO AKAPICIE. Model pisze śródtytuł, listę i zdanie jednym ciągiem
  // bez pustych linii między nimi; dzielenie po pustych liniach zlepiało to w jedną ścianę
  // tekstu z gwiazdkami, i to jest dokładnie ta rzecz, na którą patrzył założyciel o 01:15.
  let paragraph: string[] = [];
  let items: InlinePiece[][] = [];
  let ordered = false;
  let listStart = 1;
  let quote: string[] = [];
  let code: string[] | null = null;
  let fence = "";

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    blocks.push({ kind: "paragraph", pieces: inline(paragraph.join(" ")) });
    paragraph = [];
  };
  const flushList = () => {
    if (items.length === 0) return;
    blocks.push({ kind: "list", items, ordered, ...(ordered ? { start: listStart } : {}) });
    items = [];
    ordered = false;
  };
  const flushQuote = () => {
    if (quote.length === 0) return;
    blocks.push({ kind: "quote", pieces: inline(quote.join(" ")) });
    quote = [];
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
    flushQuote();
  };

  for (const rawLine of text.split("\n")) {
    const marker = /^\s{0,3}(`{3,}|~{3,})(.*)$/.exec(rawLine);
    if (code !== null) {
      if (marker && marker[1]![0] === fence[0] && marker[1]!.length >= fence.length && marker[2]!.trim() === "") {
        blocks.push({ kind: "codeblock", text: code.join("\n") });
        code = null;
      } else code.push(rawLine);
      continue;
    }
    if (marker) {
      flushAll();
      fence = marker[1]!;
      code = [];
      continue;
    }
    const line = softenDashes(rawLine);
    if (line.trim() === "") {
      flushAll();
      continue;
    }
    if (HEADING.test(line)) {
      flushAll();
      blocks.push({ kind: "heading", pieces: inline(line.replace(HEADING, "")) });
      continue;
    }
    if (QUOTE.test(line)) {
      flushParagraph();
      flushList();
      quote.push(line.replace(QUOTE, ""));
      continue;
    }
    const numbered = NUMBER.test(line);
    const bulleted = BULLET.test(line);
    if (numbered || bulleted) {
      flushParagraph();
      flushQuote();
      // Zmiana rodzaju listy zaczyna NOWĄ listę: „1. 2." i „- -" to dwie różne rzeczy i
      // sklejone w jedną wyglądałyby jak pomyłka w numeracji.
      if (items.length > 0 && ordered !== numbered) flushList();
      if (numbered && items.length === 0) listStart = Number.parseInt(line.trim(), 10);
      ordered = numbered;
      items.push(inline(line.replace(numbered ? NUMBER : BULLET, "")));
      continue;
    }
    flushList();
    flushQuote();
    paragraph.push(line.trim());
  }
  flushAll();
  if (code !== null) blocks.push({ kind: "codeblock", text: code.join("\n") });
  return blocks;
}

function inline(text: string, depth = 0): InlinePiece[] {
  if (depth >= 4) return [{ kind: "text", text }];
  const pieces: InlinePiece[] = [];
  for (const part of text.split(INLINE)) {
    // Pusty kawałek i kawałek niezłapany przez grupę (`undefined` w czasie działania, choć typ
    // mówi `string`) są tym samym: niczym do pokazania.
    if (!part) continue;
    const link = /^\[([^\]\n]+)\]\(([^\s)]+)\)$/.exec(part);
    const projectMaterial = /^\/w\/[a-f0-9-]+\/p\/[a-f0-9-]+\/knowledge\?source=[a-f0-9-]+(?:&offset=\d+)?$/i;
    if (link && (/^https?:\/\//i.test(link[2] ?? "") || projectMaterial.test(link[2] ?? ""))) {
      pieces.push({ kind: "link", text: link[1]!.replace(/`/g, ""), href: link[2]! });
    } else if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      pieces.push({ kind: "bold", text: part.slice(2, -2), children: inline(part.slice(2, -2), depth + 1) });
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      pieces.push({ kind: "code", text: part.slice(1, -1) });
    } else if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      pieces.push({ kind: "italic", text: part.slice(1, -1), children: inline(part.slice(1, -1), depth + 1) });
    } else {
      pieces.push({ kind: "text", text: part });
    }
  }
  return pieces;
}
