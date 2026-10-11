/**
 * Ikony z makiet D-046, WPISANE W KOD, nie ładowane z sieci.
 *
 * Makiety wołają `lucide` z unpkg. W produkcie nie ma CDN-u (D-016 i KONWENCJE: obraz ma
 * działać bez wychodzenia na zewnątrz), więc każdy potrzebny kształt stoi tutaj jako `path`
 * w układzie 24 na 24, dokładnie tak, jak rysuje go lucide 1.17 (licencja ISC).
 *
 * ROZMIAR I GRUBOŚĆ NIE SĄ USTAWIANE TUTAJ. `chat.css` mówi `svg{width:15px;height:15px;
 * stroke-width:1.5}` dla rozmowy i `14px/1.6` dla postępu, a pojedyncze reguły zmniejszają
 * wybrane ikony (chevron 12px, check przy zrobionym zadaniu 13px). Gdyby komponent wpisywał
 * rozmiar w atrybut, te reguły przestałyby obowiązywać i makieta rozjechałaby się w miejscach,
 * w których celowo używa mniejszego znaku.
 *
 * `aria-hidden`, bo wszystkie są dekoracją przy tekście albo przy przycisku z własną etykietą.
 * Ikona niosąca znaczenie bez tekstu obok potrzebowałaby `<title>`, a takiej tu nie ma.
 */
import type { ReactElement } from "react";

const PATHS: Readonly<Record<string, readonly string[]>> = {
  "arrow-up": ["m5 12 7-7 7 7", "M12 19V5"],
  "arrow-right": ["M5 12h14", "m12 5 7 7-7 7"],
  "arrow-up-right": ["M7 7h10v10", "M7 17 17 7"],
  check: ["M20 6 9 17l-5-5"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  "chevron-right": ["m9 18 6-6-6-6"],
  "chevron-down": ["m6 9 6 6 6-6"],
  "chevron-up": ["m18 15-6-6-6 6"],
  "corner-down-right": ["m15 10 5 5-5 5", "M4 4v7a4 4 0 0 0 4 4h12"],
  "file-text": [
    "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z",
    "M14 2v4a2 2 0 0 0 2 2h4",
    "M10 9H8",
    "M16 13H8",
    "M16 17H8",
  ],
  globe: [
    "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20",
    "M2 12h20",
  ],
  "message-circle": ["M7.9 20A9 9 0 1 0 4 16.1L2 22Z"],
  "messages-square": [
    "M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2Z",
    "M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1",
  ],
  "scan-text": [
    "M3 7V5a2 2 0 0 1 2-2h2",
    "M17 3h2a2 2 0 0 1 2 2v2",
    "M21 17v2a2 2 0 0 1-2 2h-2",
    "M7 21H5a2 2 0 0 1-2-2v-2",
    "M7 8h8",
    "M7 12h10",
    "M7 16h6",
  ],
  plus: ["M5 12h14", "M12 5v14"],
  "book-open": [
    "M12 7v14",
    "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0" +
      " 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z",
  ],
  history: [
    "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8",
    "M3 3v5h5",
    "M12 7v5l4 2",
  ],
  users: [
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2",
    "M22 21v-2a4 4 0 0 0-3-3.87",
    "M16 3.13a4 4 0 0 1 0 7.75",
  ],
  blocks: [
    "M10 22V7a1 1 0 0 0-1-1H3a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1z",
    "M14 2a2 2 0 0 0-2 2v18h8a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z",
  ],
  // Pokój (D-055) i podglądy wpisów: drzwi na pasku, strzałki głosów, odpowiedź, udostępnienie i
  // dwa znaki znane z X. Kształty lucide 1.17, ten sam układ 24 na 24 co wyżej.
  "door-open": [
    "M13 4h3a2 2 0 0 1 2 2v14",
    "M2 20h3",
    "M13 20h9",
    "M10 12v.01",
    "M13 4.562v16.157a1 1 0 0 1-1.242.97L5 20V5.562a2 2 0 0 1 1.515-1.94l4-1A2 2 0 0 1 13 4.561Z",
  ],
  "arrow-down": ["M12 5v14", "m19 12-7 7-7-7"],
  share: ["M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8", "m16 6-4-4-4 4", "M12 2v13"],
  repeat: ["m17 2 4 4-4 4", "M3 11v-1a4 4 0 0 1 4-4h14", "m7 22-4-4 4-4", "M21 13v1a4 4 0 0 1-4 4H3"],
  heart: [
    "M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z",
  ],
  "chart-no-axes-column": ["M5 21v-6", "M12 21V3", "M19 21V9"],
  sparkles: [
    "M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937" +
      " 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1" +
      " 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z",
    "M20 3v4",
    "M22 5h-4",
    "M4 17v2",
    "M5 18H3",
  ],
};

/** Kółka lucide, po nazwie ikony: [cx, cy, r]. */
const CIRCLES: Readonly<Record<string, readonly (readonly [number, number, number])[]>> = {
  globe: [[12, 12, 10]],
  users: [[9, 7, 4]],
};

export type IconName = keyof typeof PATHS;

/** Czy ta nazwa ma kształt. Używane przez test, żeby lista ikon nie rozjechała się z użyciem. */
export function iconNames(): readonly string[] {
  return Object.keys(PATHS);
}

export function Icon({ name }: { readonly name: IconName }): ReactElement {
  const paths = PATHS[name];
  if (paths === undefined) {
    // Nieznana nazwa to pomyłka w kodzie, nie stan produktu. Pusty `svg` byłby dziurą w układzie,
    // której nikt nie zauważy; rzucony wyjątek zatrzymuje render tego ekranu w testach.
    throw new Error(`nie ma ikony ${String(name)}`);
  }
  return (
    <svg
      aria-hidden="true"
      className={`ic ic-${String(name)}`}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      viewBox="0 0 24 24"
    >
      {/* Kółka, których nie da się zapisać jako `path` bez utraty kształtu. Osobna mapa, a nie
          `path` z łukami, bo lucide rysuje je jako `circle` i przybliżenie łukami widać przy
          czternastu pikselach. */}
      {(CIRCLES[String(name)] ?? []).map((one) => (
        <circle cx={one[0]} cy={one[1]} key={`${one[0]}-${one[1]}`} r={one[2]} />
      ))}
      {paths.map((d) => (
        <path d={d} key={d} />
      ))}
    </svg>
  );
}
