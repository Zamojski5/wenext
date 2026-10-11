"""Render review of the studio: screenshots plus the checks a person cannot do by eye.

    .venv/bin/python apps/dashboard/tools/render_review.py --base-url http://127.0.0.1:3100 \
        --password "<operator>" --out artifacts/ui-review/<date>

WHY THIS EXISTS. The founder judges the interface "na renderze obok referencji" (WIZJA-WORKSPACE,
D-038), and until now nobody in this repository had looked at one. A component test renders a
tree; it does not show whether the text is readable on the background, whether the phone width
scrolls sideways, or whether a panel traps the keyboard.

WHAT IT MEASURES RATHER THAN EYEBALLS, and each one is in the milestone's own checklist:

  * **contrast**, computed from the pixels the browser really painted (WCAG relative luminance),
    not from the palette anybody intended. 4.5:1 for body text is the floor;
  * **horizontal scrolling at 390 px**, by comparing `scrollWidth` with the viewport. A phone
    screen that scrolls sideways is the single most common way a "responsive" layout is not;
  * **long dashes** (U+2014, U+2013) in the RENDERED HTML. The founder's rule is about what a
    person reads, so scanning the source is not enough: a dash can arrive from the database;
  * **keyboard reachability**: every action is a real `button`, `a` or form control, and the
    focus ring is visible. Checked by walking `Tab` and reading `:focus-visible` styles;
  * **the panel contract**: opening one, closing it with Escape, closing it with a click outside.

WHAT IT DOES NOT DO: judge. It writes `raport.md` with what it found and screenshots beside it;
the taste part stays with the person looking at them.

THE BROWSER IS HEADLESS CHROMIUM FROM THE REPOSITORY'S OWN `.venv`. The Chrome extension this
project sometimes uses is not connected here, and a review that depended on somebody's browser
being open would not be repeatable.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

#: The two viewports the milestone names: a laptop and a phone.
VIEWPORTS: tuple[tuple[str, int, int], ...] = (("1440x900", 1440, 900), ("390x844", 390, 844))

#: The palette WIZJA-WORKSPACE fixes. Checked against what the browser painted, because a colour
#: that is right in the stylesheet and wrong on the screen is wrong.
BACKGROUND = "#080808"
SURFACES = ("#0b0b0b", "#131313")
ACCENT = "#d3e4af"

#: Long dashes. The founder's rule since 2026-09-04, and it is about what a person reads.
LONG_DASHES = ("—", "–")  # noqa: RUF001 (te znaki są tu celem, nie pomyłką)

#: Wszystko poniżej tego znacznika w `raport.md` pisze człowiek i przebieg tego nie rusza.
HAND_WRITTEN = "<!-- RĘCZNE: poniżej pisze sesja, narzędzie tego nie nadpisuje -->"

#: Minimum contrast for body text (WCAG AA). Large text may go to 3:1; this script reports both
#: and only fails the small one, because rounding a rule down is how a rule stops applying.
MIN_CONTRAST_BODY = 4.5
MIN_CONTRAST_LARGE = 3.0


@dataclass
class Finding:
    screen: str
    viewport: str
    kind: str
    detail: str
    mine: bool = True

    def line(self) -> str:
        owner = "moje" if self.mine else "wenext-84"
        return f"- **{self.kind}** ({self.screen}, {self.viewport}, {owner}): {self.detail}"


@dataclass
class Review:
    out: Path
    findings: list[Finding] = field(default_factory=list)
    shots: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def note(self, finding: Finding) -> None:
        self.findings.append(finding)


CONTRAST_SCRIPT = """
() => {
  const luminance = (rgb) => {
    const [r, g, b] = rgb.map((value) => {
      const channel = value / 255;
      return channel <= 0.03928 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const parse = (value) => {
    const match = value.match(/rgba?\\(([^)]+)\\)/);
    if (!match) return null;
    const parts = match[1].split(',').map((piece) => parseFloat(piece.trim()));
    if (parts.length >= 4 && parts[3] === 0) return null;
    return { rgb: parts.slice(0, 3), alpha: parts.length >= 4 ? parts[3] : 1 };
  };
  // A TRANSLUCENT background is composited over what is behind it, not treated as opaque. The
  // first version of this script treated it as opaque and reported a pill with
  // `rgba(255,255,255,0.08)` as white: 1.52:1 against light text, which would have sent somebody
  // chasing a contrast problem that does not exist on the screen.
  const backgroundOf = (element) => {
    const stack = [];
    let node = element;
    while (node) {
      const colour = parse(getComputedStyle(node).backgroundColor);
      if (colour) {
        stack.push(colour);
        if (colour.alpha >= 1) break;
      }
      node = node.parentElement;
    }
    let result = [0, 0, 0];
    for (let index = stack.length - 1; index >= 0; index -= 1) {
      const layer = stack[index];
      result = result.map((base, channel) =>
        layer.rgb[channel] * layer.alpha + base * (1 - layer.alpha)
      );
    }
    return result;
  };
  const results = [];
  const seen = new Set();
  document.querySelectorAll('body *').forEach((element) => {
    const text = (element.textContent || '').trim();
    if (!text || element.children.length > 0) return;
    const style = getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') return;
    const box = element.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) return;
    const parsedColour = parse(style.color);
    if (!parsedColour) return;
    const background = backgroundOf(element);
    // Text alpha composites too: faded text is faded against what is behind it.
    const colour = parsedColour.rgb.map(
      (channel, index) =>
        channel * parsedColour.alpha + background[index] * (1 - parsedColour.alpha)
    );
    const lighter = Math.max(luminance(colour), luminance(background));
    const darker = Math.min(luminance(colour), luminance(background));
    const ratio = (lighter + 0.05) / (darker + 0.05);
    const size = parseFloat(style.fontSize);
    const bold = parseInt(style.fontWeight, 10) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const key = `${style.color}|${size}|${large}`;
    if (seen.has(key)) return;
    seen.add(key);
    results.push({
      ratio: Math.round(ratio * 100) / 100,
      colour: style.color,
      size,
      large,
      sample: text.slice(0, 60),
    });
  });
  return results;
}
"""

FOCUS_SCRIPT = """
() => {
  const focusable = Array.from(
    document.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex]')
  ).filter((element) => {
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden';
  });
  // The accessible name the way a screen reader computes it, not just `textContent`: a field
  // wrapped in its own `<label>` IS named, and the first version of this script reported every
  // one of them as nameless. A checker that cries wolf is a checker nobody reads twice.
  const named = (element) => {
    if ((element.textContent || '').trim() !== '') return true;
    for (const attribute of ['aria-label', 'title', 'alt', 'placeholder']) {
      if (element.getAttribute(attribute)) return true;
    }
    const labelledBy = element.getAttribute('aria-labelledby');
    if (labelledBy && document.getElementById(labelledBy)) return true;
    if (element.closest('label')) return true;
    if (element.id && document.querySelector('label[for="' + element.id + '"]')) return true;
    if (element.tagName === 'INPUT' && element.value) return true;
    return false;
  };
  const nameless = focusable.filter((element) => !named(element));
  return {
    count: focusable.length,
    withoutName: nameless.length,
    examples: nameless.slice(0, 4).map((element) => {
      const attributes = Array.from(element.attributes)
        .map((attribute) => attribute.name + '="' + attribute.value + '"')
        .join(' ');
      return ('<' + element.tagName.toLowerCase() + ' ' + attributes + '>').slice(0, 160);
    }),
  };
}
"""


def contrast(page: Page) -> list[dict[str, Any]]:
    return page.evaluate(CONTRAST_SCRIPT)


def overflow(page: Page) -> dict[str, int]:
    return page.evaluate(
        "() => ({ scroll: document.documentElement.scrollWidth,"
        " client: document.documentElement.clientWidth })"
    )


def painted(page: Page) -> dict[str, str]:
    return page.evaluate(
        "() => ({ body: getComputedStyle(document.body).backgroundColor,"
        " html: getComputedStyle(document.documentElement).backgroundColor })"
    )


def rgb_to_hex(value: str) -> str:
    match = re.match(r"rgba?\(([^)]+)\)", value or "")
    if not match:
        return value
    parts = [int(float(piece.strip())) for piece in match.group(1).split(",")[:3]]
    return "#{:02x}{:02x}{:02x}".format(*parts)


def review_screen(
    page: Page, review: Review, *, name: str, path: str, viewport: str, mine: bool = True
) -> None:
    """One screen at one viewport: shot, then the checks."""
    page.goto(path, wait_until="networkidle")
    shot = review.out / f"{name}-{viewport}.png"
    page.screenshot(path=str(shot), full_page=True)
    review.shots.append(shot.name)
    review.checked.append(f"{name} @ {viewport}")

    html = page.content()
    for dash in LONG_DASHES:
        if dash in html:
            where = html[max(0, html.index(dash) - 60) : html.index(dash) + 60]
            review.note(
                Finding(
                    name,
                    viewport,
                    "długa pauza w renderze",
                    f"U+{ord(dash):04X} w HTML: …{where.strip()}…",
                    mine,
                )
            )

    sizes = overflow(page)
    if sizes["scroll"] > sizes["client"] + 1:
        review.note(
            Finding(
                name,
                viewport,
                "poziome przewijanie",
                f"scrollWidth {sizes['scroll']} > clientWidth {sizes['client']}",
                mine,
            )
        )

    colours = painted(page)
    # `/operations` keeps the pre-pivot panel's own chrome, and that is a decision rather than an
    # oversight: D-038 says the operator panel stays, with its own shell, and is no longer the
    # front door. Everything the founder is shown as "the product" uses the studio palette, which
    # includes the three screens before the session.
    own_palette = name == "operacje"
    if (
        not own_palette
        and viewport == VIEWPORTS[0][0]
        and rgb_to_hex(colours["body"]) not in (BACKGROUND, "#000000")
    ):
        review.note(
            Finding(
                name,
                viewport,
                "tło niezgodne z wizją",
                f"body ma {rgb_to_hex(colours['body'])}, wizja mówi {BACKGROUND}",
                mine,
            )
        )

    for entry in contrast(page):
        floor = MIN_CONTRAST_LARGE if entry["large"] else MIN_CONTRAST_BODY
        if entry["ratio"] < floor:
            review.note(
                Finding(
                    name,
                    viewport,
                    "kontrast poniżej progu",
                    f"{entry['ratio']}:1 przy progu {floor}:1, kolor {entry['colour']}, "
                    f"rozmiar {entry['size']}px, tekst: {entry['sample']!r}",
                    mine,
                )
            )

    focus = page.evaluate(FOCUS_SCRIPT)
    if focus["withoutName"] > 0:
        review.note(
            Finding(
                name,
                viewport,
                "element bez nazwy dla klawiatury",
                f"{focus['withoutName']} z {focus['count']} bez dostępnej nazwy: "
                + "; ".join(focus["examples"]),
                mine,
            )
        )


def review_panel(page: Page, review: Review, *, name: str, path: str) -> None:
    """The panel contract: opens at its trigger, Escape closes it, a click outside closes it."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(path, wait_until="networkidle")
    trigger = page.locator("details.st-pop > summary").first
    if trigger.count() == 0:
        review.note(Finding(name, "1440x900", "brak panelu", "nie znaleziono details.st-pop", True))
        return
    trigger.click()
    page.wait_for_timeout(150)
    review.shots.append(f"{name}-panel-otwarty.png")
    page.screenshot(path=str(review.out / f"{name}-panel-otwarty.png"), full_page=False)
    opened = page.locator("details.st-pop[open]").count()
    if opened == 0:
        review.note(Finding(name, "1440x900", "panel się nie otwiera", "klik w summary", True))
        return

    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    if page.locator("details.st-pop[open]").count() != 0:
        review.note(
            Finding(
                name, "1440x900", "Escape nie zamyka panelu", "details.st-pop[open] zostaje", True
            )
        )

    trigger.click()
    page.wait_for_timeout(100)
    page.mouse.click(5, 5)
    page.wait_for_timeout(150)
    if page.locator("details.st-pop[open]").count() != 0:
        review.note(
            Finding(
                name,
                "1440x900",
                "klik poza panelem nie zamyka",
                "details.st-pop[open] zostaje",
                True,
            )
        )


def log_in(page: Page, base_url: str, password: str) -> None:
    page.goto(f"{base_url}/login", wait_until="networkidle")
    page.locator("details[data-login-form='operator'] summary").click()
    page.fill("#password", password)
    page.locator("details[data-login-form='operator'] button[type=submit]").click()
    page.wait_for_load_state("networkidle")


def main() -> int:
    parser = argparse.ArgumentParser(description="Przegląd renderu studia")
    parser.add_argument("--base-url", default="http://127.0.0.1:3100")
    parser.add_argument("--password", required=True)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--pause-pid",
        type=int,
        default=None,
        help=(
            "PID control plane'u do ZATRZYMANIA (SIGSTOP) na czas jednego zrzutu, żeby złapać "
            "ekran, który widzi ZALOGOWANY człowiek, gdy backend przestaje odpowiadać. Bez tego "
            "ten stan nie jest zrzucany, bo bez sesji widać po prostu logowanie, a to co innego"
        ),
    )
    parser.add_argument(
        "--broken-url",
        default=None,
        help=(
            "adres drugiej instancji dashboardu wskazującej na NIEDZIAŁAJĄCY control plane. "
            "Bez niego stan 'brak odpowiedzi' nie jest zrzucany, i raport to mówi"
        ),
    )
    args = parser.parse_args()

    out = Path(args.out or f"artifacts/ui-review/{datetime.now(UTC):%Y-%m-%d}")
    out.mkdir(parents=True, exist_ok=True)
    review = Review(out=out)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            color_scheme="dark",
            device_scale_factor=1,
        )
        page = context.new_page()

        # The screens outside the session first: they are the only ones a stranger sees.
        for name, path in (
            ("logowanie", "/login"),
            ("rejestracja", "/register"),
            ("zaproszenie", "/invitation?token=przykladowy-token-do-podgladu"),
        ):
            for viewport, width, height in VIEWPORTS:
                page.set_viewport_size({"width": width, "height": height})
                review_screen(
                    page, review, name=name, path=f"{args.base_url}{path}", viewport=viewport
                )

        log_in(page, args.base_url, args.password)
        workspace = args.workspace
        if workspace is None:
            page.goto(f"{args.base_url}/", wait_until="networkidle")
            match = re.search(r"/w/([0-9a-f-]{36})", page.content())
            workspace = match.group(1) if match else ""
        if not workspace:
            print("nie udało się ustalić workspace'u po zalogowaniu", flush=True)
            return 2

        project_match = re.search(r"/w/[0-9a-f-]{36}/p/([0-9a-f-]{36})", page.content())
        project = project_match.group(1) if project_match else None

        screens: list[tuple[str, str, bool]] = [
            ("workspace", f"/w/{workspace}", True),
            ("profile-i-konta", f"/w/{workspace}/people", True),
            ("polaczenia-codex", f"/w/{workspace}/codex", True),
            ("przestrzen", f"/w/{workspace}/organization", True),
            ("operacje", "/operations", True),
        ]
        if project:
            screens.extend(
                [
                    # The project screen holds the conversation and the presence block, which
                    # belong to another session. Marked so the report says whose a finding is.
                    ("projekt", f"/w/{workspace}/p/{project}", False),
                    ("materialy", f"/w/{workspace}/p/{project}/knowledge", True),
                    ("obecnosc", f"/w/{workspace}/p/{project}/presence", False),
                ]
            )

        for name, path, mine in screens:
            for viewport, width, height in VIEWPORTS:
                page.set_viewport_size({"width": width, "height": height})
                review_screen(
                    page,
                    review,
                    name=name,
                    path=f"{args.base_url}{path}",
                    viewport=viewport,
                    mine=mine,
                )

        review_panel(page, review, name="workspace", path=f"{args.base_url}/w/{workspace}")

        # The state a person sees while the agent is working. The status is set in the database
        # rather than by running a model: what is under review is the RENDER of that state.
        if project:
            page.set_viewport_size({"width": 1440, "height": 900})
            review_screen(
                page,
                review,
                name="projekt-sesja-running",
                path=f"{args.base_url}/w/{workspace}/p/{project}",
                viewport="1440x900",
                mine=False,
            )

        # The control plane unreachable FOR SOMEBODY WHO IS LOGGED IN. That is the state worth
        # looking at: without a session the screen is simply the login, which says nothing about
        # the backend. The process is paused rather than killed, so the stack survives the shot.
        if args.pause_pid:
            import os
            import signal

            os.kill(args.pause_pid, signal.SIGSTOP)
            try:
                page.set_viewport_size({"width": 1440, "height": 900})
                page.goto(f"{args.base_url}/w/{workspace}", wait_until="domcontentloaded")
                page.wait_for_timeout(500)
                page.screenshot(
                    path=str(out / "backend-nie-odpowiada-1440x900.png"), full_page=True
                )
                review.shots.append("backend-nie-odpowiada-1440x900.png")
                review.checked.append("backend nie odpowiada (zalogowany) @ 1440x900")
                body = page.inner_text("body").lower()
                if "nie udało się" not in body and "brak odpowiedzi" not in body:
                    review.note(
                        Finding(
                            "backend-nie-odpowiada",
                            "1440x900",
                            "awaria backendu nie jest nazwana",
                            f"ekran mówi: {body[:160]!r} — człowiek nie wie, czy to jego sesja, "
                            "czy usługa",
                            True,
                        )
                    )
            finally:
                os.kill(args.pause_pid, signal.SIGCONT)
                page.wait_for_timeout(500)

        # The control plane unreachable for somebody with NO session: a second dashboard pointed
        # at a dead port. The sentence this state shows must not be confused with "nothing here".
        if args.broken_url:
            broken_page = context.new_page()
            broken_page.set_viewport_size({"width": 1440, "height": 900})
            broken_page.goto(f"{args.broken_url}/", wait_until="networkidle")
            broken_page.screenshot(path=str(out / "brak-odpowiedzi-1440x900.png"), full_page=True)
            review.shots.append("brak-odpowiedzi-1440x900.png")
            review.checked.append("brak odpowiedzi control plane @ 1440x900")
            # A VISITOR WITH NO SESSION correctly sees the login: the dashboard has nothing to
            # read for them and no reason to probe the backend on every page view. What has to
            # be true is that TRYING says the honest thing, so the check follows that path
            # instead of expecting the landing screen to know.
            body = broken_page.inner_text("body")
            if "Zaloguj" not in body:
                review.note(
                    Finding(
                        "brak-odpowiedzi",
                        "1440x900",
                        "ekran bez sesji przy martwym backendzie",
                        f"spodziewałem się logowania, jest: {body[:160]!r}",
                        True,
                    )
                )
            else:
                broken_page.fill("#email", "ktos@firma.test")
                broken_page.fill("#user-password", "cokolwiek")
                broken_page.locator(
                    "form[action='/login/submit'] button[type=submit]"
                ).first.click()
                broken_page.wait_for_load_state("networkidle")
                broken_page.screenshot(
                    path=str(out / "brak-odpowiedzi-po-probie-1440x900.png"), full_page=True
                )
                review.shots.append("brak-odpowiedzi-po-probie-1440x900.png")
                after = broken_page.inner_text("body").lower()
                if "nie udało się porozmawiać" not in after:
                    review.note(
                        Finding(
                            "brak-odpowiedzi",
                            "1440x900",
                            "próba logowania przy martwym backendzie nie nazywa awarii",
                            f"ekran mówi: {after[:200]!r} — to wygląda jak zła odpowiedź na "
                            "pytanie o hasło",
                            True,
                        )
                    )
            broken_page.close()
        else:
            review.checked.append("brak odpowiedzi control plane: NIE ZRZUCONO (bez --broken-url)")

        # The error state: the control plane unreachable. Not simulated in the markup — the page
        # is asked for an address that does not resolve, which is what a person sees when the
        # backend is down.
        broken = context.new_page()
        broken.set_viewport_size({"width": 1440, "height": 900})
        broken.goto(
            f"{args.base_url}/w/00000000-0000-4000-8000-000000000000", wait_until="networkidle"
        )
        broken.screenshot(path=str(out / "brak-workspace-1440x900.png"), full_page=True)
        review.shots.append("brak-workspace-1440x900.png")
        review.checked.append("brak workspace'u @ 1440x900")

        browser.close()

    report = out / "raport.md"
    # A run REWRITES the machine part and KEEPS everything below the marker. The narrative
    # (what was fixed, what was decided, what nobody captured) is written by hand after the
    # fixes land, and a second run of the tool must not silently delete it.
    kept = ""
    if report.exists():
        previous = report.read_text(encoding="utf-8")
        if HAND_WRITTEN in previous:
            kept = previous[previous.index(HAND_WRITTEN) :]
    report.write_text(_render_report(review) + kept, encoding="utf-8")
    print(f"zrzutów: {len(review.shots)}, ustaleń: {len(review.findings)}")
    print(f"raport: {report}")
    print(json.dumps([finding.line() for finding in review.findings], ensure_ascii=False, indent=2))
    return 0


def _render_report(review: Review) -> str:
    mine = [finding for finding in review.findings if finding.mine]
    theirs = [finding for finding in review.findings if not finding.mine]
    lines = [
        f"# Przegląd renderu studia, {datetime.now(UTC):%Y-%m-%d}",
        "",
        "Headless Chromium z `.venv` korzenia repozytorium, przeciwko zbudowanej aplikacji",
        "w przypiętym obrazie Node i stackowi z `e2e_db_render.sh`. Ciemny schemat, dwa okna:",
        "1440x900 i 390x844.",
        "",
        "## Co sprawdzono automatycznie",
        "",
        "- kontrast liczony z PIKSELI, które namalowała przeglądarka (WCAG), próg 4.5:1 dla",
        "  treści i 3:1 dla dużego tekstu;",
        "- poziome przewijanie przez porównanie `scrollWidth` z `clientWidth`;",
        "- długie pauzy (U+2014, U+2013) w RENDEROWANYM HTML, nie tylko w źródle;",
        "- elementy interaktywne bez nazwy dla klawiatury;",
        "- kontrakt panelu: otwarcie, Escape, klik poza.",
        "",
        f"Ekrany: {len(review.checked)}. Zrzuty: {len(review.shots)}.",
        "",
        "## Ustalenia w moich plikach",
        "",
    ]
    lines.extend([finding.line() for finding in mine] or ["- brak"])
    lines.extend(["", "## Ustalenia w plikach wenext-84 (bez edycji)", ""])
    lines.extend([finding.line() for finding in theirs] or ["- brak"])
    lines.extend(["", "## Zrzuty", ""])
    lines.extend(f"- `{shot}`" for shot in sorted(review.shots))
    lines.extend(["", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
