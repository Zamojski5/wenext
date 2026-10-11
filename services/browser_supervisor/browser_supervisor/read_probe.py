"""Co da się przeczytać na platformie BEZ LOGOWANIA, zmierzone raz, zapisane jako artefakt (P6 A).

PO CO TO ISTNIEJE. Roadmapa P6 mówi „wiele platform”, a adaptery X i LinkedIn wymagają kont i
decyzji założyciela. Zanim ktokolwiek te decyzje podejmie, warto wiedzieć, czego one naprawdę
dotyczą: czy publiczny profil daje się odczytać bez konta, czy pojedynczy post daje się odczytać,
czy wyszukiwanie odsyła do logowania. Trzy różne odpowiedzi znaczą trzy różne adaptery, a bez
pomiaru rozmowa o nich jest zgadywaniem.

CZEGO TA SONDA NIE ROBI, i to jest część jej definicji:

  * NIE LOGUJE SIĘ i nie dotyka żadnego konta. Profil przeglądarki jest pusty i jednorazowy;
    kapsuła live nie jest uruchamiana ani czytana.
  * NIC NIE PUBLIKUJE. Jedyne żądania to GET-y stron, które te platformy same serwują publicznie.
  * NIE OMIJA NICZEGO. Jeśli platforma odpowiada 403 albo przekierowaniem do logowania, to JEST
    wynik i tak go zapisujemy. Sonda, która próbowałaby to obejść, mierzyłaby już co innego:
    czy umiemy się przecisnąć, a nie czy adapter ma się o co oprzeć.
  * NIE CHODZI W PĘTLI. `MAX_TARGETS` jest twardym limitem żądań na przebieg. To jest pomiar
    zdolności, a nie zbieranie danych, i różnica jest widoczna w liczbie żądań.

WYNIK: artefakt JSON z jednym wierszem na cel (status, adres końcowy, czy widać ścianę
logowania, tytuł, rozmiar) plus przycięty fragment HTML jako fixture, tak jak §8.6 zapisało
fixture Reddita. Rekomendacja, czego wymaga adapter, jest w raporcie, nie tutaj: ten plik mierzy.

URUCHOMIENIE, w jednorazowym kontenerze z własnym profilem:

    docker compose run --rm --no-deps -e WENEXT_PROFILES_DIR=/tmp/probe-profiles capsule \\
        python3 -m browser_supervisor.main --read-probe --artifacts /tmp/artifacts
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from community_os_contracts import CapsuleMode

from .supervisor import BrowserSupervisor

ARTIFACT_NAME = "read-probe.json"

#: Hard cap on requests per run. A capability probe asks a handful of questions; anything that
#: needs more than this is collecting data, which is a different activity with different rules.
MAX_TARGETS = 8

#: How long one page may take before the answer is "nie odpowiedziało w czasie". Generous, because
#: a slow answer is still an answer and a timeout that is too tight would report a false refusal.
TIMEOUT_MS = 20_000

#: How much HTML is kept per target. Enough to see the shape a parser would face, small enough
#: that the artifact stays readable and carries no more of somebody's page than it must.
FIXTURE_BYTES = 4000

#: Signs that a page is a login wall rather than content. Matched against the FINAL url and the
#: visible title, never against the body: a word like "login" inside an article is not a wall.
_LOGIN_MARKERS = ("/login", "/signin", "/sign-in", "/uas/login", "/i/flow/login", "authwall")


@dataclass(frozen=True)
class Target:
    """One public address to look at, and what it is an example OF."""

    platform: str
    #: `profile`, `post` albo `search`. To one z trzech pytań, na które odpowiada sonda.
    kind: str
    url: str


#: The addresses P6 etap A asks about: for each platform, a public PROFILE, a public POST and a
#: SEARCH. Three questions and not one, because they have different answers and each implies a
#: different adapter: a profile that reads without a login supports "kim jest ta osoba", a post
#: supports "co tu napisano", and search supports "gdzie warto być".
#:
#: The specific accounts are deliberately institutional ones with obviously public pages, chosen
#: so the probe measures the PLATFORM's policy rather than one person's privacy settings.
DEFAULT_TARGETS: tuple[Target, ...] = (
    Target("X", "profile", "https://x.com/Reuters"),
    Target("X", "post", "https://x.com/Reuters/status/1"),
    Target("X", "search", "https://x.com/search?q=kubernetes"),
    Target("LINKEDIN", "profile", "https://www.linkedin.com/company/microsoft/"),
    Target("LINKEDIN", "post", "https://www.linkedin.com/feed/"),
    Target(
        "LINKEDIN", "search", "https://www.linkedin.com/search/results/content/?keywords=devops"
    ),
)


@dataclass
class Observation:
    """What came back. Every field is measured; nothing here is inferred from the platform name."""

    platform: str
    kind: str
    url: str
    #: HTTP status of the MAIN document, or `None` when nothing arrived at all.
    status: int | None = None
    final_url: str = ""
    redirected: bool = False
    #: True when the final address or the title looks like a login wall.
    login_wall: bool = False
    title: str = ""
    html_bytes: int = 0
    #: Whether the page carried readable text at all. A 200 that renders an empty shell is not a
    #: readable page, and an adapter built on it would report emptiness as absence.
    text_bytes: int = 0
    error: str = ""
    fixture: str = ""

    @property
    def readable(self) -> bool:
        """Czy adapter miałby się tu o co oprzeć BEZ konta."""
        return (
            self.status is not None
            and 200 <= self.status < 300
            and not self.login_wall
            and self.text_bytes > 200
        )


@dataclass
class ProbeResult:
    started_at: str
    finished_at: str = ""
    observations: list[Observation] = field(default_factory=list)
    #: `True` only when every target produced an observation. A run that died halfway is NOT a
    #: result about the platform; it is a result about the run, and the artifact says so.
    complete: bool = False
    note: str = ""


def _looks_like_a_login_wall(final_url: str, title: str) -> bool:
    haystack = f"{final_url} {title}".lower()
    if any(marker in haystack for marker in _LOGIN_MARKERS):
        return True
    return bool(re.search(r"\b(zaloguj|sign in|log in|join now)\b", title, re.I))


def run_read_probe(
    supervisor: BrowserSupervisor,
    targets: list[Target],
    *,
    artifacts_dir: Path,
) -> ProbeResult:
    """Open each target anonymously, write an artifact on EVERY exit path.

    The artifact is written even when the run fails, and then `complete` is false. A probe that
    crashed and left nothing behind would be indistinguishable from a platform that answered
    nothing, and those are opposite facts.
    """
    if len(targets) > MAX_TARGETS:
        raise ValueError(
            f"{len(targets)} celów przekracza limit {MAX_TARGETS}. To sonda zdolności, "
            "nie zbieranie danych"
        )
    result = ProbeResult(started_at=datetime.now(UTC).isoformat())
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    capsule = None
    try:
        capsule = supervisor.start(CapsuleMode.HEADLESS)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(capsule.endpoint.http_base)
            for target in targets:
                result.observations.append(_look(browser, target))
        result.complete = True
    except Exception as exc:  # a probe that could not run says so instead of reporting silence
        result.note = f"sonda nie dokończyła: {type(exc).__name__}: {exc}"
    finally:
        result.finished_at = datetime.now(UTC).isoformat()
        if capsule is not None:
            # Stopping is best effort; the artifact matters more than a tidy shutdown.
            with contextlib.suppress(Exception):
                supervisor.stop()
        (artifacts_dir / ARTIFACT_NAME).write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return result


def _look(browser, target: Target) -> Observation:
    """One address, one anonymous context. A FRESH context per target, so nothing a platform set
    on the first page (a cookie, a consent banner's choice) changes what the second one sees."""
    observation = Observation(platform=target.platform, kind=target.kind, url=target.url)
    context = None
    try:
        context = browser.new_context()
        page = context.new_page()
        response = page.goto(target.url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        observation.status = response.status if response is not None else None
        observation.final_url = page.url
        observation.redirected = page.url.rstrip("/") != target.url.rstrip("/")
        observation.title = (page.title() or "")[:200]
        html = page.content() or ""
        observation.html_bytes = len(html)
        observation.fixture = html[:FIXTURE_BYTES]
        text = page.inner_text("body") if html else ""
        observation.text_bytes = len(text or "")
        observation.login_wall = _looks_like_a_login_wall(page.url, observation.title)
    except Exception as exc:
        observation.error = f"{type(exc).__name__}: {exc}"[:300]
        # A navigation that failed still has a status somewhere, and the status is the finding.
        # Chrome reports `ERR_HTTP_RESPONSE_CODE_FAILURE` for an error response with no body,
        # which tells us the platform refused but not HOW: 403 and 404 mean different adapters.
        # One plain request answers that, and it is the same request the browser just made.
        if context is not None:
            with contextlib.suppress(Exception):
                probe = context.request.get(target.url, timeout=TIMEOUT_MS)
                observation.status = probe.status
                observation.error += f" (status {probe.status})"
    finally:
        if context is not None:
            # A context that will not close is not the finding we came for.
            with contextlib.suppress(Exception):
                context.close()
    return observation


def summary_lines(result: ProbeResult) -> list[str]:
    """The report's lines, in Polish: a person reads them (D-010)."""
    lines: list[str] = []
    for one in result.observations:
        if one.error:
            verdict = f"nie udało się odczytać ({one.error})"
        elif one.login_wall:
            verdict = f"ściana logowania (status {one.status}, adres {one.final_url})"
        elif one.readable:
            verdict = f"czytelne bez konta (status {one.status}, {one.text_bytes} znaków tekstu)"
        else:
            verdict = (
                f"status {one.status}, ale bez czytelnej treści "
                f"({one.text_bytes} znaków): pusta skorupa albo render po stronie klienta"
            )
        lines.append(f"  {one.platform} / {one.kind}: {verdict}")
    if not result.complete:
        lines.append(f"  NIEPEŁNE: {result.note}")
    return lines
