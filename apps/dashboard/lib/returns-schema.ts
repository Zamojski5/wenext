/**
 * JSON z `api/returns.py`, sprawdzony polami, zanim trafi na ekran powrotu.
 *
 * TA SAMA ZASADA, CO W `workspace-schema.ts`: brakujące pole albo zły typ PSUJĄ CAŁY ODCZYT.
 * Na tym ekranie to nie jest formalność. „Nic się nie wydarzyło" i „nie udało się policzyć, co
 * się wydarzyło" wyglądają identycznie, jeśli brak danych zamieni się w zero, a to jest dokładnie
 * ten ekran, na którym człowiek podejmuje decyzję o tym, czy agent w ogóle działa.
 *
 * LICZBY SĄ LICZBAMI, NIE `null`. Widoczność publikacji jest wyjątkiem: `null` znaczy „nikt tam
 * jeszcze nie zajrzał" i musi przetrwać do ekranu, bo pusta widoczność pokazana jako „zniknęła"
 * byłaby zarzutem wobec agenta za coś, czego nikt nie sprawdził.
 */
import type { Read } from "./control-plane";
import { PUBLICATION_KINDS, type PublicationKind } from "./workspace-schema";

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function record(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function str(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" ? value : null;
}

function num(row: Record<string, unknown>, field: string): number | null {
  const value = row[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function flag(row: Record<string, unknown>, field: string): boolean | null {
  const value = row[field];
  return typeof value === "boolean" ? value : null;
}

function nullableStr(row: Record<string, unknown>, field: string): string | null | undefined {
  if (!(field in row)) return undefined;
  const value = row[field];
  if (value === null) return null;
  return typeof value === "string" ? value : undefined;
}

function counts(value: unknown): Readonly<Record<string, number>> | null {
  const raw = record(value);
  if (raw === null) return null;
  const out: Record<string, number> = {};
  for (const [key, count] of Object.entries(raw)) {
    if (typeof count !== "number" || !Number.isFinite(count)) return null;
    out[key] = count;
  }
  return out;
}

function list(value: unknown): readonly Record<string, unknown>[] | null {
  if (!Array.isArray(value)) return null;
  const rows: Record<string, unknown>[] = [];
  for (const item of value) {
    const row = record(item);
    if (row === null) return null;
    rows.push(row);
  }
  return rows;
}

export interface ReturnWindow {
  readonly od: string | null;
  readonly do: string;
  readonly pierwsza_wizyta: boolean;
  readonly zdanie: string;
}

export interface ReturnWakeups {
  readonly razem: number;
  readonly pominiete: number;
  readonly z_sesja: number;
  readonly powody: Readonly<Record<string, number>>;
  readonly powody_pominiecia: Readonly<Record<string, number>>;
}

export interface ReturnSession {
  readonly session_id: string;
  readonly status: string;
  readonly cel: string;
  readonly wynik: string | null;
  readonly wynik_zdanie?: string | null;
  readonly tytul?: string | null;
  readonly narzedzia: readonly string[];
  readonly zaczeto: string;
  readonly skonczono: string | null;
}

export interface ReturnDecision {
  readonly revision_id: string;
  readonly tekst: string | null;
  /** Zrzut dołączony do własnego posta na X (27.09); `null`, gdy go nie ma albo serwer jest starszy. */
  readonly obraz?: string | null;
  readonly adres: string | null;
  readonly konto: string;
  readonly czeka_od: string;
  readonly wygasa: string;
}

export interface ReturnPublication {
  readonly request_id: string;
  readonly status: string;
  /** 0023: „nasz wątek" czy „odpowiedź". Ten sam zbiór, co w `workspace-schema`. */
  readonly rodzaj: PublicationKind;
  readonly adres: string;
  readonly opublikowano: string | null;
  readonly powod: string | null;
  readonly widocznosc: {
    readonly sprawdzono: string;
    readonly jest: boolean;
    readonly punkty: number | null;
    readonly odpowiedzi: number;
    /** 27.09: views when the platform showed them (X, to the author); absent = not measured. */
    readonly wyswietlenia: number | null;
  } | null;
}

export interface ReturnCorrection {
  readonly item_id: string;
  readonly tresc: string;
  readonly zapisano: string;
  readonly w_zasiegu_sesji: boolean;
}

export interface ReturnIncident {
  readonly incident_id: string;
  readonly komponent: string;
  readonly powod: string;
  readonly od: string;
  readonly do: string | null;
  readonly trwa: boolean;
}

export interface ReturnView {
  readonly okno: ReturnWindow;
  readonly obudzenia: ReturnWakeups;
  readonly sesje: readonly ReturnSession[];
  readonly decyzje: readonly ReturnDecision[];
  readonly publikacje: readonly ReturnPublication[];
  readonly korekty: readonly ReturnCorrection[];
  readonly incydenty: readonly ReturnIncident[];
  readonly uwagi: readonly string[];
  readonly cisza: boolean;
}

function parseWindow(raw: unknown): Read<ReturnWindow> {
  const row = record(raw);
  if (row === null) return failure("okno powrotu: pole `okno` nie jest obiektem");
  const until = str(row, "do");
  const sentence = str(row, "zdanie");
  const first = flag(row, "pierwsza_wizyta");
  const since = nullableStr(row, "od");
  if (until === null || sentence === null || first === null || since === undefined) {
    return failure("okno powrotu: brakuje pól albo mają zły typ (od, do, pierwsza_wizyta, zdanie)");
  }
  return { ok: true, value: { od: since, do: until, pierwsza_wizyta: first, zdanie: sentence } };
}

function parseWakeups(raw: unknown): Read<ReturnWakeups> {
  const row = record(raw);
  if (row === null) return failure("obudzenia: pole `obudzenia` nie jest obiektem");
  const total = num(row, "razem");
  const skipped = num(row, "pominiete");
  const withSession = num(row, "z_sesja");
  const reasons = counts(row["powody"]);
  const skipReasons = counts(row["powody_pominiecia"]);
  if (total === null || skipped === null || withSession === null) {
    return failure("obudzenia: brakuje liczb (razem, pominiete, z_sesja)");
  }
  if (reasons === null || skipReasons === null) {
    return failure("obudzenia: powody nie są mapą liczb");
  }
  return {
    ok: true,
    value: {
      razem: total,
      pominiete: skipped,
      z_sesja: withSession,
      powody: reasons,
      powody_pominiecia: skipReasons,
    },
  };
}

function parseSessions(raw: unknown): Read<readonly ReturnSession[]> {
  const rows = list(raw);
  if (rows === null) return failure("sesje: pole `sesje` nie jest listą obiektów");
  const out: ReturnSession[] = [];
  for (const row of rows) {
    const id = str(row, "session_id");
    const status = str(row, "status");
    const goal = str(row, "cel");
    const started = str(row, "zaczeto");
    const result = nullableStr(row, "wynik");
    const finished = nullableStr(row, "skonczono");
    const sentence = nullableStr(row, "wynik_zdanie");
    const title = nullableStr(row, "tytul");
    const tools = Array.isArray(row["narzedzia"])
      ? (row["narzedzia"] as unknown[]).filter((tool): tool is string => typeof tool === "string")
      : null;
    if (
      id === null ||
      status === null ||
      goal === null ||
      started === null ||
      result === undefined ||
      finished === undefined ||
      ("wynik_zdanie" in row && sentence === undefined) ||
      ("tytul" in row && title === undefined) ||
      tools === null
    ) {
      return failure("sesja w widoku powrotu: brakuje pól albo mają zły typ");
    }
    out.push({
      session_id: id,
      status,
      cel: goal,
      wynik: result,
      wynik_zdanie: sentence,
      tytul: title,
      narzedzia: tools,
      zaczeto: started,
      skonczono: finished,
    });
  }
  return { ok: true, value: out };
}

function parseDecisions(raw: unknown): Read<readonly ReturnDecision[]> {
  const rows = list(raw);
  if (rows === null) return failure("decyzje: pole `decyzje` nie jest listą obiektów");
  const out: ReturnDecision[] = [];
  for (const row of rows) {
    const id = str(row, "revision_id");
    const account = str(row, "konto");
    const waiting = str(row, "czeka_od");
    const expires = str(row, "wygasa");
    const text = nullableStr(row, "tekst");
    const url = nullableStr(row, "adres");
    if (
      id === null ||
      account === null ||
      waiting === null ||
      expires === null ||
      text === undefined ||
      url === undefined
    ) {
      return failure("propozycja do decyzji: brakuje pól albo mają zły typ");
    }
    const image = typeof row["obraz"] === "string" ? (row["obraz"] as string) : null;
    out.push({
      revision_id: id,
      tekst: text,
      obraz: image,
      adres: url,
      konto: account,
      czeka_od: waiting,
      wygasa: expires,
    });
  }
  return { ok: true, value: out };
}

function parsePublications(raw: unknown): Read<readonly ReturnPublication[]> {
  const rows = list(raw);
  if (rows === null) return failure("publikacje: pole `publikacje` nie jest listą obiektów");
  const out: ReturnPublication[] = [];
  for (const row of rows) {
    const id = str(row, "request_id");
    const status = str(row, "status");
    const kind = str(row, "rodzaj");
    const url = str(row, "adres");
    const published = nullableStr(row, "opublikowano");
    const detail = nullableStr(row, "powod");
    // Nieznany rodzaj psuje CAŁY odczyt, a nie staje się „odpowiedzią": własny wątek pokazany
    // jako dopisek do cudzej rozmowy jest zdaniem o czymś, czego agent nie zrobił.
    const knownKind = kind !== null && PUBLICATION_KINDS.some((one) => one === kind);
    if (
      id === null ||
      status === null ||
      url === null ||
      published === undefined ||
      detail === undefined ||
      !knownKind
    ) {
      return failure("publikacja w widoku powrotu: brakuje pól albo mają zły typ");
    }
    let visibility: ReturnPublication["widocznosc"] = null;
    const rawVisibility = row["widocznosc"];
    if (rawVisibility !== null && rawVisibility !== undefined) {
      const seen = record(rawVisibility);
      const checked = seen === null ? null : str(seen, "sprawdzono");
      const present = seen === null ? null : flag(seen, "jest");
      const replies = seen === null ? null : num(seen, "odpowiedzi");
      if (seen === null || checked === null || present === null || replies === null) {
        return failure("widoczność publikacji: brakuje pól albo mają zły typ");
      }
      const score = seen["punkty"];
      // Optional on the wire: an older control plane does not send it, and that reads as "not
      // measured", the same as a read in which the platform showed no views.
      const views = seen["wyswietlenia"];
      visibility = {
        sprawdzono: checked,
        jest: present,
        punkty: typeof score === "number" ? score : null,
        odpowiedzi: replies,
        wyswietlenia: typeof views === "number" ? views : null,
      };
    }
    out.push({
      request_id: id,
      status,
      rodzaj: kind as PublicationKind,
      adres: url,
      opublikowano: published,
      powod: detail,
      widocznosc: visibility,
    });
  }
  return { ok: true, value: out };
}

function parseCorrections(raw: unknown): Read<readonly ReturnCorrection[]> {
  const rows = list(raw);
  if (rows === null) return failure("korekty: pole `korekty` nie jest listą obiektów");
  const out: ReturnCorrection[] = [];
  for (const row of rows) {
    const id = str(row, "item_id");
    const text = str(row, "tresc");
    const written = str(row, "zapisano");
    const inReach = flag(row, "w_zasiegu_sesji");
    if (id === null || text === null || written === null || inReach === null) {
      return failure("korekta w widoku powrotu: brakuje pól albo mają zły typ");
    }
    out.push({ item_id: id, tresc: text, zapisano: written, w_zasiegu_sesji: inReach });
  }
  return { ok: true, value: out };
}

function parseIncidents(raw: unknown): Read<readonly ReturnIncident[]> {
  const rows = list(raw);
  if (rows === null) return failure("incydenty: pole `incydenty` nie jest listą obiektów");
  const out: ReturnIncident[] = [];
  for (const row of rows) {
    const id = str(row, "incident_id");
    const component = str(row, "komponent");
    const reason = str(row, "powod");
    const from = str(row, "od");
    const to = nullableStr(row, "do");
    const ongoing = flag(row, "trwa");
    if (id === null || component === null || reason === null || from === null || to === undefined || ongoing === null) {
      return failure("incydent w widoku powrotu: brakuje pól albo mają zły typ");
    }
    out.push({ incident_id: id, komponent: component, powod: reason, od: from, do: to, trwa: ongoing });
  }
  return { ok: true, value: out };
}

export function parseReturnView(envelope: Record<string, unknown>): Read<ReturnView> {
  const window = parseWindow(envelope["okno"]);
  if (!window.ok) return window;
  const wakeups = parseWakeups(envelope["obudzenia"]);
  if (!wakeups.ok) return wakeups;
  const sessions = parseSessions(envelope["sesje"]);
  if (!sessions.ok) return sessions;
  const decisions = parseDecisions(envelope["decyzje"]);
  if (!decisions.ok) return decisions;
  const publications = parsePublications(envelope["publikacje"]);
  if (!publications.ok) return publications;
  const corrections = parseCorrections(envelope["korekty"]);
  if (!corrections.ok) return corrections;
  const incidents = parseIncidents(envelope["incydenty"]);
  if (!incidents.ok) return incidents;
  const notes = Array.isArray(envelope["uwagi"])
    ? (envelope["uwagi"] as unknown[]).filter((note): note is string => typeof note === "string")
    : null;
  const quiet = typeof envelope["cisza"] === "boolean" ? (envelope["cisza"] as boolean) : null;
  if (notes === null || quiet === null) {
    return failure("widok powrotu: brakuje pól `uwagi` albo `cisza`");
  }
  return {
    ok: true,
    value: {
      okno: window.value,
      obudzenia: wakeups.value,
      sesje: sessions.value,
      decyzje: decisions.value,
      publikacje: publications.value,
      korekty: corrections.value,
      incydenty: incidents.value,
      uwagi: notes,
      cisza: quiet,
    },
  };
}
