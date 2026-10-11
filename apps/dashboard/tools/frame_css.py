#!/usr/bin/env python3
"""Generuje `app/frame.css` z referencji ramy, zamiast przepisywać ją ręcznie.

    python3 apps/dashboard/tools/frame_css.py            # zapisuje app/frame.css
    python3 apps/dashboard/tools/frame_css.py --links    # to samo + adaptacja dla linków
    python3 apps/dashboard/tools/frame_css.py --out /tmp/podglad.css --stdout-diff

PO CO SKRYPT, A NIE RĘCZNE PRZEPISANIE. Referencja ma 360 reguł. Sto sześćdziesiąt okazji do
drobnej różnicy, której nikt nie zobaczy na przeglądzie, a widać ją przy porównaniu zrzutów, to
nie jest oszczędność. Dlatego arkusz powstaje podmianą korzenia (`#wenext-minimal` na `.st`)
i niczym poza tym.

DLACZEGO TEN PLIK W OGÓLE POWSTAŁ. Pierwsze przeniesienie robiłem jednorazowym skryptem w
terminalu i zgubiło ono opakowania `@media`: reguły odsłony telefonowej obowiązywały zawsze,
więc `.wm-sidebar{display:none}` schowało CAŁY pasek boczny na 1533 px. Nagłówek arkusza mówił
„nie edytować ręcznie", ale nie było czym go odtworzyć, więc następna osoba i tak musiałaby go
przepisać. Skrypt jest tu po to, żeby regeneracja była powtarzalna i żeby adaptacje (niżej) były
widoczne jako KOD, a nie jako różnica, którą ktoś kiedyś wprowadził do wygenerowanego pliku.

DWIE RZECZY, KTÓRE PARSER MUSI ROBIĆ DOBRZE, bo obie już raz kosztowały:
  1. `@media` zostaje opakowaniem, a nie znika;
  2. `@keyframes` i inne reguły blokowe przepisują się w CAŁOŚCI, po liczniku nawiasów.
     Szukanie pierwszego `}` rozjeżdża parser na pierwszej klatce animacji.

ADAPTACJA `--links`, świadoma i opisana. Referencja stylizuje `.wm-navigation button` i
`.wm-projects button`, bo makieta przełącza widoki skryptem i nie ma adresów. Studio renderuje
`<a href>`, bo to są prawdziwe miejsca (D-045: rozmowa ma adres). Bez adaptacji cały wygląd
nawigacji bierze się z arkusza Studia, a reguły referencji nie mają w co trafić. Zamiana linków
na przyciski byłaby regresją produktu w imię zgodności z makietą, więc zamiast tego selektor
przyjmuje jedno i drugie.
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = ROOT / "docs/produkt/prototyp/workspace-studio.html"
DEFAULT_OUT = ROOT / "apps/dashboard/app/frame.css"
#: Blok `<style>` z ciemną odsłoną. Pierwsze dwa to reset strony i fragment w `srcdoc`.
STYLE_BLOCK = 2
LINK = ":is(button, a.st__item)"


def block_end(text: str, open_brace: int) -> int:
    depth, j = 1, open_brace + 1
    while j < len(text) and depth:
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
        j += 1
    return j


def parse(text: str) -> list[tuple[str, str, str]]:
    """(media, selektor, ciało); media `"@"` znaczy „reguła blokowa przepisana w całości”."""
    out: list[tuple[str, str, str]] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \n\r\t;":
            i += 1
        if i >= n:
            break
        brace = text.find("{", i)
        if brace == -1:
            break
        head = text[i:brace].strip()
        end = block_end(text, brace)
        if head.startswith("@media"):
            for media, selector, body in parse(text[brace + 1 : end - 1]):
                out.append(("@", selector, body) if media == "@" else (head, selector, body))
            i = end
            continue
        if head.startswith("@"):
            out.append(("@", text[i:end].strip(), ""))
            i = end
            continue
        body = text[brace + 1 : end - 1].strip()
        if head:
            out.append(("", head, body))
        i = end
    return out


def for_links(selector: str) -> str:
    """`.st .wm-navigation button` → `.st .wm-navigation :is(button, a.st__item)`.

    Tylko w nawigacji i liście projektów, i tylko dla gołego `button`: `button:hover` i
    `button[aria-current="page"]` też, ale `.wm-circle` czy `.wm-send` nie, bo to są klasy,
    które Studio nadaje wprost tam, gdzie chce.
    """
    if ".wm-navigation" not in selector and ".wm-projects" not in selector:
        return selector
    selector = re.sub(r"(?<![\w.-])button(?![\w-])", LINK, selector)
    # ZAZNACZENIE DOSTAJE TĘ SAMĄ PODMIANĘ. Po przepisaniu reguły zwykłej pozycji na
    # `:is(button, a.st__item)` jej swoistość rośnie o element (0,3,1), a reguła zaznaczenia
    # `.wm-selected` zostaje na (0,3,0) i PRZEGRYWA: pozycja bieżąca dostawała tło zaznaczenia i
    # kolor pozycji zwykłej (zmierzone w odbiorze 2: #989894 zamiast #f5f5f2).
    return selector.replace(".wm-selected", f"{LINK}.wm-selected")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--links", action="store_true", help="adaptacja selektorów dla <a>")
    parser.add_argument("--check", action="store_true", help="nie zapisuj, porównaj z --out")
    args = parser.parse_args()

    source = html.unescape(REFERENCE.read_text(encoding="utf-8"))
    css = re.findall(r"<style[^>]*>(.*?)</style>", source, re.S)[STYLE_BLOCK]
    rules = [
        (media, selector.replace("#wenext-minimal", ".st"), body)
        for media, selector, body in parse(css)
        if media == "@" or "#wenext-minimal" in selector
    ]
    if args.links:
        rules = [(m, s if m == "@" else for_links(s), b) for m, s, b in rules]

    out = Path(args.out)
    header = out.read_text(encoding="utf-8")[: out.read_text(encoding="utf-8").index("*/") + 2]
    lines = [header, "", "/* --- reguły z referencji, w kolejności źródła. NIE EDYTOWAĆ RĘCZNIE --- */", ""]
    current: str | None = None
    for media, selector, body in rules:
        if media == "@":
            if current:
                lines.append("}")
                current = None
            lines.append(selector)
            continue
        if media != current:
            if current:
                lines.append("}")
            current = media
            if media:
                lines.append(f"{media}{{")
        lines.append(("  " if current else "") + f"{selector}{{{body}}}")
    if current:
        lines.append("}")
    lines += [
        "",
        "/*",
        " * JEDYNA REGUŁA SPOZA REFERENCJI, i jest adapterem, nie decyzją wyglądu.",
        " *",
        " * `.st` było siatką dwukolumnową starego studia i samo rysowało pasek. Rama z referencji ma",
        " * własny pasek wewnątrz `.wm-scene`, więc zewnętrzna siatka musi zejść z drogi; bez tego",
        " * kolumny są dwie, jedna w drugiej, i szerokość treści wychodzi z niewłaściwej.",
        " */",
        ".st.st--framed{display:block;grid-template-columns:none;background:transparent}",
        "",
    ]
    text = "\n".join(lines)

    if args.check:
        same = out.read_text(encoding="utf-8") == text
        print("zgodny z referencją" if same else "RÓŻNI SIĘ od referencji")
        return 0 if same else 1
    out.write_text(text, encoding="utf-8")
    print(f"zapisane: {out} ({len(rules)} reguł{', adaptacja dla linków' if args.links else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
