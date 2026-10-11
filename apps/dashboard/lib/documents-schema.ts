/**
 * JSON z `api/documents.py`, sprawdzony polami, zanim trafi na ekran projektu (P8 etap A).
 *
 * TA SAMA ZASADA, CO W POZOSTAŁYCH PARSERACH STUDIA: brakujące pole albo zły typ PSUJĄ CAŁY
 * ODCZYT. Tu ma to własny ciężar: dokument jest czytelnym widokiem rejestru ustaleń, a zdanie
 * bez podstawy jest twierdzeniem, którego nie da się sprawdzić. Pusta `podstawa` to zepsuty
 * odczyt, nie zdanie „bez źródła": baza tego zabrania (więz z 0028), więc jeśli tu przyszło,
 * rozmawiamy z czymś, co nie jest tym API.
 */
import type { Read } from "./control-plane";

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function record(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function str(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" && value !== "" ? value : null;
}

function num(row: Record<string, unknown>, field: string): number | null {
  const value = row[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function nullableStr(row: Record<string, unknown>, field: string): string | null | undefined {
  if (!(field in row)) return undefined;
  const value = row[field];
  if (value === null) return null;
  return typeof value === "string" ? value : undefined;
}

/** Role dokumentów, w kolejności czytania. Ta sama lista i ta sama kolejność, co w API. */
export const DOCUMENT_ROLES = [
  "PROJECT_CARD",
  "WHAT_WORKS",
  "DECISIONS",
  "COMMUNITIES",
  "PLATFORM_HANDBOOK",
] as const;
export type DocumentRole = (typeof DOCUMENT_ROLES)[number];

/** Skąd wzięła się wersja: z reguł czy ze skrótu modelu. Widać to na ekranie, bo to różnica. */
export const DOCUMENT_SOURCES = ["RULES", "MODEL"] as const;
export type DocumentSource = (typeof DOCUMENT_SOURCES)[number];

/** Nazwa roli po ludzku. Słowa z bazy (`WHAT_WORKS`) na ekran nie trafiają. */
export function documentRoleLabel(role: DocumentRole): string {
  switch (role) {
    case "PROJECT_CARD":
      return "Karta projektu";
    case "WHAT_WORKS":
      return "Co działa";
    case "DECISIONS":
      return "Decyzje";
    case "COMMUNITIES":
      return "Społeczności";
    default:
      return "Podręcznik platformy";
  }
}

/**
 * Zdanie o pochodzeniu wersji.
 *
 * „Z reguł" znaczy, że zdania powstały z zapytań do rejestru i nikt ich nie pisał; „skrócone
 * modelem" znaczy, że model streścił akapit, ale jego zdania też muszą wskazywać istniejące
 * ustalenia. Różnica jest na ekranie, bo czytający ma prawo wiedzieć, co powstało bez modelu.
 */
export function documentSourceLabel(source: DocumentSource): string {
  return source === "MODEL" ? "skrócone modelem" : "z reguł, bez modelu";
}

export interface DocumentEntry {
  readonly document_id: string;
  readonly rola: DocumentRole;
  readonly platforma: string | null;
  readonly tytul: string;
  readonly wersja: number;
  readonly wygenerowano: string;
  readonly zrodlo: DocumentSource;
  readonly liczba_zdan: number;
}

export interface DocumentSentence {
  readonly pozycja: number;
  readonly sekcja: string | null;
  readonly tekst: string;
  readonly podstawa: readonly string[];
  readonly liczba_podstaw: number;
}

export interface DocumentDetail extends DocumentEntry {
  readonly zdania: readonly DocumentSentence[];
}

function parseEntryFields(row: Record<string, unknown>): Read<DocumentEntry> {
  const id = str(row, "document_id");
  const role = str(row, "rola");
  const title = str(row, "tytul");
  const version = num(row, "wersja");
  const generated = str(row, "wygenerowano");
  const source = str(row, "zrodlo");
  const sentences = num(row, "liczba_zdan");
  const platform = nullableStr(row, "platforma");
  const missing: string[] = [];
  if (id === null) missing.push("document_id");
  if (role === null || !DOCUMENT_ROLES.some((known) => known === role)) missing.push("rola");
  if (title === null) missing.push("tytul");
  if (version === null) missing.push("wersja");
  if (generated === null) missing.push("wygenerowano");
  if (source === null || !DOCUMENT_SOURCES.some((known) => known === source)) missing.push("zrodlo");
  if (sentences === null) missing.push("liczba_zdan");
  if (platform === undefined) missing.push("platforma");
  if (missing.length > 0) {
    return failure(
      `dokument projektu: brakuje pól albo mają zły typ (${missing.join(", ")}). ` +
        "To zepsuty odczyt, nie puste dane",
    );
  }
  return {
    ok: true,
    value: {
      document_id: id as string,
      rola: role as DocumentRole,
      platforma: platform as string | null,
      tytul: title as string,
      wersja: version as number,
      wygenerowano: generated as string,
      zrodlo: source as DocumentSource,
      liczba_zdan: sentences as number,
    },
  };
}

export function parseDocumentIndex(envelope: Record<string, unknown>): Read<readonly DocumentEntry[]> {
  const raw = envelope["dokumenty"];
  if (!Array.isArray(raw)) return failure("indeks dokumentów: pole `dokumenty` nie jest listą");
  const entries: DocumentEntry[] = [];
  for (const item of raw) {
    const row = record(item);
    if (row === null) return failure("indeks dokumentów: pozycja nie jest obiektem");
    const parsed = parseEntryFields(row);
    if (!parsed.ok) return parsed;
    entries.push(parsed.value);
  }
  return { ok: true, value: entries };
}

export function parseDocumentDetail(envelope: Record<string, unknown>): Read<DocumentDetail> {
  const head = parseEntryFields(envelope);
  if (!head.ok) return head;
  const raw = envelope["zdania"];
  if (!Array.isArray(raw)) return failure("dokument: pole `zdania` nie jest listą");
  const sentences: DocumentSentence[] = [];
  for (const item of raw) {
    const row = record(item);
    if (row === null) return failure("dokument: zdanie nie jest obiektem");
    const position = num(row, "pozycja");
    const textValue = str(row, "tekst");
    const section = nullableStr(row, "sekcja");
    const basis = Array.isArray(row["podstawa"])
      ? (row["podstawa"] as unknown[]).filter((one): one is string => typeof one === "string")
      : null;
    // ZDANIE BEZ PODSTAWY PSUJE ODCZYT. Baza go nie dopuszcza, więc jeśli tu przyszło, to nie
    // jest „zdanie bez źródła", tylko odpowiedź, której nie rozumiemy.
    if (position === null || textValue === null || section === undefined || basis === null) {
      return failure("dokument: zdanie bez pozycji, tekstu, sekcji albo podstawy");
    }
    if (basis.length === 0) {
      return failure("dokument: zdanie z pustą podstawą, a każde zdanie ma wskazywać ustalenia");
    }
    sentences.push({
      pozycja: position,
      sekcja: section,
      tekst: textValue,
      podstawa: basis,
      liczba_podstaw: num(row, "liczba_podstaw") ?? basis.length,
    });
  }
  return { ok: true, value: { ...head.value, zdania: sentences } };
}
