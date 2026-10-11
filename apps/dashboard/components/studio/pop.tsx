import type { ReactNode } from "react";
import { Icon } from "./icons";

/**
 * A panel that opens AT the control that opened it — the one disclosure pattern of the studio.
 *
 * `<details>`, not a client component. This package contains no browser code at all: D-016 keeps
 * the operator session out of it and `tests/source_scan.test.ts` fails on a single `"use client"`.
 * `<details>` gives the open state, the keyboard semantics and the toggle for free, and the panel
 * still works with JavaScript switched off — the trigger closes it again.
 *
 * Escape and a click outside are added by one small inline script in `StudioShell`, and they are
 * an ENHANCEMENT: without it the panel is closed by clicking its trigger, which is the behaviour
 * `<details>` has anyway.
 *
 * `side="right"` anchors the panel to the right edge of the trigger. WIZJA-WORKSPACE asks the
 * panel to pick the side with room; near the right-hand edge of the workspace that side is the
 * left, and this is how a caller says so.
 */
export interface PopProps {
  /** Accessible name of the trigger. Polish: the founder reads it (D-010). */
  readonly label: string;
  /** Heading inside the panel. */
  readonly title: string;
  readonly children: ReactNode;
  readonly side?: "left" | "right";
  /**
   * `circle` is the small "+"; `button` is a labelled pill; `link` is the quiet line with an
   * icon from the reference („Anna · 2 konta" pod tytułem projektu, E2). Trzeci wariant, a
   * nie własny komponent, bo zamykanie Escapem i kliknięciem obok jest w jednym miejscu.
   */
  readonly variant?: "circle" | "button" | "link";
  /** Visible text of a `button` trigger. Ignored by `circle`, which shows a "+". */
  readonly text?: string;
  /** Lands on the root as `data-pop`, so a test can find one panel among several. */
  readonly name: string;
  /**
   * Nazwa klasy z REFERENCJI RAMY, dopisywana do klasy przycisku (E2).
   *
   * Po co osobne pole, skoro można by dopisać ją wszystkim: `Pop` stoi dziś w ośmiu miejscach
   * studia, a referencja ma swój przycisk tylko w niektórych. Dopisanie `wm-circle` wszystkim
   * kółkom przemalowałoby też te, których w makiecie nie ma, czyli zmieniłoby wygląd ekranów
   * spoza tego zadania. Włącza się to więc tam, gdzie referencja naprawdę ma odpowiednik, i
   * widać to w wywołaniu.
   */
  readonly frame?: string;
  /**
   * Podtytuł panelu. Obecność tego pola przełącza panel w OKNO z referencji: szersze, z tytułem
   * 19 px, podtytułem i przyciskiem zamknięcia (odbiór 1, różnica nr 4).
   *
   * Pole niesie treść, a nie flagę `dialog: true`, z rozmysłu: okno bez podtytułu wyglądałoby
   * jak okno z pustym wierszem, a referencja ma tam nazwę projektu, czyli odpowiedź na pytanie
   * „czyje to konta".
   */
  readonly dialog?: string;
  /**
   * Czy przycisk ma chevron. Domyślnie tak dla wariantów z tekstem, bo bez niego nic nie mówi,
   * że to się otwiera; awatar w doku dostaje `false`, bo referencja ma tam samo kółko z literą,
   * a strzałka przy awatarze wygląda jak druga kontrolka.
   */
  readonly chevron?: boolean;
}

export function Pop({
  label,
  title,
  children,
  side = "left",
  variant = "circle",
  text,
  name,
  frame,
  dialog,
  chevron = variant !== "circle",
}: PopProps) {
  return (
    <details className="st-pop" data-pop={name} data-side={side}>
      <summary
        aria-label={label}
        className={[
          variant === "circle" ? "st-circle" : variant === "link" ? "wm-assignment-link" : "st-btn",
          frame,
        ]
          .filter((one): one is string => one !== undefined && one !== "")
          .join(" ")}
        data-kind={variant === "button" ? "quiet" : undefined}
      >
        {variant === "link" ? <Icon name="users" /> : null}
        {/* Kółko rysuje IKONĘ, nie znak plus z klawiatury (odbiór 1, różnica nr 14): referencja
            ma tam `plus` 14 px, a tekstowy „+" ma inną grubość, inną wysokość i inny środek. */}
        {variant === "circle" ? <Icon name="plus" /> : (text ?? label)}
        {/* Chevron przy przełączniku, bo referencja go ma i bo bez niego nic nie mówi, że to
            się otwiera. Wariant `button` rysował dotąd sam tekst, więc reguła
            `.st .wm-workspace-switch svg` nie miała w co trafić (odbiór 1, różnica nr 1). */}
        {chevron ? <Icon name="chevron-down" /> : null}
      </summary>
      <div
        aria-label={title}
        className={`st-pop__panel${dialog === undefined ? "" : " st-pop__panel--dialog"}`}
        role="group"
      >
        <p className="st-pop__title">{title}</p>
        {/* Podtytuł mówi, CZEGO dotyczy panel (referencja ma pod tytułem nazwę projektu), a X
            zamyka go bez szukania miejsca obok. Bez skryptu X nie działa i dlatego NIE JEST
            jedyną drogą wyjścia: zostaje `summary`, Escape i kliknięcie obok. */}
        {dialog === undefined ? null : (
          <>
            <p className="st-pop__subtitle">{dialog}</p>
            <button aria-label="Zamknij" className="st-pop__close" data-pop-close="1" type="button">
              <Icon name="x" />
            </button>
          </>
        )}
        {children}
      </div>
    </details>
  );
}
