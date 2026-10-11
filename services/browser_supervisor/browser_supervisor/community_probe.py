"""Co naprawdę stoi na stronie społeczności, zmierzone raz, zapisane jako artefakt (E3b).

PO CO TO ISTNIEJE. Aktuator dołączenia kontem ma kliknąć przycisk, którego nikt w tym projekcie
jeszcze nie widział. Selektory napisane z pamięci dałyby atrapę zgadzającą się samą ze sobą,
kontrolę, która przechodzi, i pierwsze prawdziwe dołączenie kończące się odmową albo, gorzej,
skutkiem bez dowodu. To jest ten sam kształt usterki, co „Dołączono" o włączonej obserwacji
sprzed 20.09: zielone wszystko i nieprawda na końcu.

DLACZEGO NIE CZYTNIKIEM KAPSUŁY. Czytnik ma politykę adresów i ta polityka odmówiła: origin
tylko `https://www.reddit.com`, a ścieżka `/r/<nazwa>/` jest poza allowlistą, bo czytnik widzi
wątki, komentarze i `me.json`, nigdy strony społeczności. Polityki nie rozluźniamy pod sondę;
sonda idzie drogą AKTUATORA, czyli przez CDP do tej samej przeglądarki, co komentarze.

CZEGO TA SONDA NIE ROBI, i to jest część jej definicji:

  * NIE KLIKA. Ani razu. Wchodzi na stronę, zapisuje, co widzi, i wychodzi. Dołączenie jest
    działaniem na zewnątrz i wymaga powierzenia; pomiar nie jest działaniem.
  * NIE ZMIENIA NICZEGO w profilu ani na platformie. Jedyne żądanie to GET strony, którą ta
    sama przeglądarka i tak otwiera przy czytaniu wątków.
  * NIE ZGADUJE, co jest przyciskiem. Zwraca KANDYDATÓW z ich klasami, tekstem i adresami, a
    wybór należy do człowieka czytającego artefakt. Sonda, która sama orzeka „to jest ten
    przycisk", byłaby tym samym zgadywaniem, tylko w innym pliku.

CO ZOSTAJE W ARTEFAKTACH: `<slug>.html` (całość strony), `<slug>.png` (zrzut ekranu, bo HTML nie
mówi, co widać) i `community-probe.json` z wyciągiem kandydatów na każdy cel.

SONDA DOŁĄCZA SIĘ DO DZIAŁAJĄCEJ PRZEGLĄDARKI, NIE URUCHAMIA WŁASNEJ, i to nie jest detal
wykonania. Znacznik członkostwa i nazwa konta w nagłówku istnieją wyłącznie dla ZALOGOWANEGO, a
zalogowana sesja żyje w profilu działającej kapsuły. Drugi Chrome na tym samym katalogu profilu
jest zakazany (jedna kapsuła, jeden profil, jeden proces), a Chrome na świeżym profilu odpowie
na pytania sondy wersją dla anonima, czyli nie na te pytania. Dlatego endpoint bierze się z
`devtools_port.verify`, tą samą drogą, co przy komentarzu: plik `DevToolsActivePort` z profilu,
sprawdzony w trzech krokach (port odpowiada, build się zgadza, identyfikator przeglądarki ten
sam). Brak działającej przeglądarki to ODMOWA ze zdaniem, nigdy własny start.

URUCHOMIENIE, w DZIAŁAJĄCYM kontenerze kapsuły:

    docker exec wenext-capsule-1 \\
        python3 -m browser_supervisor.main --community-probe AI_Agents --artifacts /tmp/artifacts
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .community_markup import (
    COMMUNITY_HOSTS,
    Candidates,
    authorize_page_url,
    candidates,
    community_url,
)
from .supervisor import BrowserSupervisor

ARTIFACT_NAME = "community-probe.json"

#: Ile celów najwyżej na przebieg. Sonda zdolności zadaje kilka pytań; więcej znaczy zbieranie
#: danych, czyli inną czynność o innych regułach (ta sama zasada, co w `read_probe`).
MAX_TARGETS = 4

TIMEOUT_MS = 25_000


#: Selektory, po których pytamy ŻYWY DOM, a nie zapisany HTML. Nowy Reddit renderuje przycisk
#: dołączenia komponentem (`faceplate-partial` dogrywany po załadowaniu, shadow DOM), więc
#: `page.content()` może go nie zawierać, choć człowiek go widzi. Playwright pyta o to, co jest
#: NA STRONIE. Lista jest zamknięta i krótka: sonda dalej nie orzeka, tylko liczy i przepisuje
#: etykiety.
LIVE_SELECTORS = (
    "shreddit-join-button",
    "faceplate-partial",
    "button:has-text('Join')",
    "button:has-text('Joined')",
    "button:has-text('Leave')",
    "[data-testid*='join']",
)

#: Ile czekamy PO załadowaniu, zanim spiszemy stronę. Komponent dogrywa się sam i bez tej chwili
#: sonda opisywałaby stronę wcześniejszą niż ta, którą widzi człowiek. To jest czekanie na
#: renderowanie, nie ponawianie żądania.
SETTLE_MS = 2500


@dataclass
class LiveHit:
    """Ile elementów pasuje do selektora w ŻYWYM DOM i co jest na nich napisane."""

    selector: str
    count: int = 0
    texts: list[str] = field(default_factory=list)


@dataclass
class Observation:
    slug: str
    url: str
    status: int | None = None
    final_url: str = ""
    title: str = ""
    logged_in_as: str = ""
    html_file: str = ""
    screenshot_file: str = ""
    candidates: Candidates = field(default_factory=Candidates)
    live: list[LiveHit] = field(default_factory=list)
    error: str = ""


@dataclass
class ProbeResult:
    started_at: str
    finished_at: str | None = None
    complete: bool = False
    note: str = ""
    observations: list[Observation] = field(default_factory=list)


def running_endpoint(supervisor: BrowserSupervisor) -> str:
    """Adres CDP DZIAŁAJĄCEJ przeglądarki kapsuły, sprawdzony, albo wyjątek ze zdaniem.

    Nie uruchamia niczego i nie zatrzymuje niczego. `devtools_port.verify` sprawdza trzy rzeczy
    naraz i wszystkie trzy są konieczne: że port odpowiada, że to nasz build i że identyfikator
    przeglądarki zgadza się z plikiem. Sam istniejący plik `DevToolsActivePort` nie wystarcza,
    bo po `kill -9` zostaje w profilu z martwym portem, który mógł już przejąć ktoś inny.
    """
    from . import devtools_port

    endpoint = devtools_port.verify(
        supervisor.user_data_dir,
        expected_browser=supervisor.expected_cdp_browser,
        host=supervisor.cdp_host,
    )
    return endpoint.http_base


def run_community_probe(
    supervisor: BrowserSupervisor,
    slugs: list[str],
    *,
    artifacts_dir: Path,
    urls: list[str] | None = None,
) -> ProbeResult:
    """Otwórz każdą społeczność w DZIAŁAJĄCEJ przeglądarce kapsuły i zapisz, co widać.

    NIC NIE KLIKA i NICZEGO NIE URUCHAMIA. Dokłada się do przeglądarki, która już stoi, tak jak
    aktuator komentarza; własny start na tym samym profilu byłby drugim Chrome na jednym
    katalogu, a na świeżym profilu sonda odpowiadałaby na pytania o członkostwo widokiem
    anonima, czyli nie na te pytania.

    Artefakt powstaje na KAŻDEJ drodze wyjścia. Sonda, która się wywróciła i nie zostawiła nic,
    jest nieodróżnialna od strony, która nic nie pokazała, a to są przeciwne fakty.
    """
    if len(slugs) + len(urls or ()) > MAX_TARGETS:
        raise ValueError(f"{len(slugs) + len(urls or ())} celów przekracza limit {MAX_TARGETS}")
    result = ProbeResult(started_at=datetime.now(UTC).isoformat())
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        base = running_endpoint(supervisor)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(base)
            if not browser.contexts:
                raise RuntimeError("Chrome kapsuły nie ma kontekstu profilu, brak sesji")
            context = browser.contexts[0]
            # KAŻDA SPOŁECZNOŚĆ NA OBU HOSTACH. Który odpowiada, a który blokuje, jest dziś
            # osobnym pytaniem od tego, co stoi w znaczniku, i sonda ma odpowiedzieć na oba.
            for slug in slugs:
                for host in COMMUNITY_HOSTS:
                    result.observations.append(
                        _look(
                            context,
                            slug,
                            community_url(slug, host=host),
                            artifacts_dir,
                            f"{slug}-{host}",
                        )
                    )
            for address in urls or ():
                safe = authorize_page_url(address)
                result.observations.append(
                    _look(context, safe, safe, artifacts_dir, safe.split("//", 1)[-1])
                )
        result.complete = True
    except Exception as exc:
        result.note = f"sonda nie dokończyła: {type(exc).__name__}: {exc}"
    finally:
        result.finished_at = datetime.now(UTC).isoformat()
        (artifacts_dir / ARTIFACT_NAME).write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return result


def _live_hits(page) -> list[LiveHit]:
    """Co widzi PRZEGLĄDARKA, a nie zapisany HTML. Same liczby i etykiety, żadnego werdyktu."""
    hits: list[LiveHit] = []
    for selector in LIVE_SELECTORS:
        hit = LiveHit(selector=selector)
        with contextlib.suppress(Exception):
            locator = page.locator(selector)
            hit.count = locator.count()
            for index in range(min(hit.count, 3)):
                with contextlib.suppress(Exception):
                    text = locator.nth(index).inner_text(timeout=1500)
                    hit.texts.append(" ".join((text or "").split())[:80])
        hits.append(hit)
    return hits


def _look(context, slug: str, url: str, artifacts_dir: Path, name: str) -> Observation:
    observation = Observation(slug=slug, url=url)
    page = None
    try:
        page = context.new_page()
        # `load`, a nie `domcontentloaded`, plus chwila na dogranie komponentu: pierwszy przebieg
        # 21.09 spisał stronę, na której przycisku dołączenia jeszcze nie było, choć na zrzucie
        # ekranu był. To jest czekanie na RENDEROWANIE, nie ponawianie żądania.
        response = page.goto(url, timeout=TIMEOUT_MS, wait_until="load")
        observation.status = response.status if response is not None else None
        with contextlib.suppress(Exception):
            page.wait_for_timeout(SETTLE_MS)
        observation.final_url = page.url
        observation.title = (page.title() or "")[:200]
        html = page.content() or ""
        observation.candidates = candidates(html)
        observation.live = _live_hits(page)

        safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
        html_file = artifacts_dir / f"{safe}.html"
        html_file.write_text(html, encoding="utf-8")
        observation.html_file = html_file.name
        # ZRZUT EKRANU, BO HTML NIE MÓWI, CO WIDAĆ. Przycisk ukryty stylem stoi w znaczniku tak
        # samo jak widoczny, a różnica decyduje o tym, czy aktuator ma w co kliknąć.
        shot = artifacts_dir / f"{safe}.png"
        page.screenshot(path=str(shot), full_page=False)
        observation.screenshot_file = shot.name
        observation.logged_in_as = "; ".join(
            one.text for one in observation.candidates.account_header if one.text
        )[:120]
    except Exception as exc:
        observation.error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        if page is not None:
            with contextlib.suppress(Exception):
                page.close()
    return observation


def summary_lines(result: ProbeResult) -> list[str]:
    """Wiersze raportu, po polsku: czyta je człowiek (D-010)."""
    lines: list[str] = []
    for one in result.observations:
        if one.error:
            lines.append(f"  [BŁĄD] {one.url}: {one.error}")
            continue
        found = one.candidates
        lines.append(
            f"  [OK  ] {one.url}: status {one.status}, kandydatów na przycisk "
            f"{len(found.join_like)}, formularzy zapisu {len(found.subscribe_forms)}, "
            f"liczników {len(found.member_counts)}, konto w nagłówku "
            f"{one.logged_in_as or 'NIE ZNALEZIONO'}"
        )
        for element in found.join_like[:6]:
            lines.append(
                f"          <{element.tag} class={element.classes!r} id={element.element_id!r} "
                f"href={element.href!r}> {element.text!r} ({element.matched})"
            )
    if result.note:
        lines.append(f"  UWAGA: {result.note}")
    return lines
