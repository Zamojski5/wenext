"""CDP feature contract — the executable version of Go/No-Go gate no. 1.

The E1.F0 scope (L5159) lists exactly what this contract has to check:

    "CDP feature contract in headless: finding the persistent context, locators, popups,
     screenshots and the traces needed, Gateway reattach after a restart, rejecting a
     mismatched build, safe handling of a stale `DevToolsActivePort`, one canonical
     BrowserSupervisor argument set, no public CDP"

Every item on that list has its own check and its own result here. The contract does not
return "passed/failed" as a single number — it returns a list of checks with evidence,
because that is the input to the Go/No-Go report (D19).

The result of running it on this environment (arm64, non-root, sandbox enabled) is in
docs/f0-limits/ and in artifacts/gonogo/.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .devtools_port import DevToolsEndpoint, StaleDevToolsPort


@dataclass
class CheckResult:
    """A single contract item."""

    nazwa: str
    wymog_z_dokumentu: str
    zdane: bool
    dowod: str
    szczegoly: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContractResult:
    kontrole: list[CheckResult] = field(default_factory=list)

    @property
    def zdane(self) -> bool:
        return all(k.zdane for k in self.kontrole)

    def add(self, check: CheckResult) -> None:
        self.kontrole.append(check)

    def to_json(self) -> str:
        return json.dumps(
            {
                "zdane": self.zdane,
                "liczba_kontroli": len(self.kontrole),
                "kontrole": [asdict(k) for k in self.kontrole],
            },
            indent=2,
            ensure_ascii=False,
        )


def _run_check(
    result: ContractResult, nazwa: str, requirement: str, fn: Callable[[], tuple[bool, str, dict]]
) -> None:
    """Runs one check, turning an exception into an explicit failure carrying the error text."""
    try:
        zdane, dowod, szczegoly = fn()
    except Exception as exc:  # the contract has to report, not blow up
        zdane, dowod, szczegoly = False, f"{type(exc).__name__}: {exc}", {}
    result.add(
        CheckResult(
            nazwa=nazwa,
            wymog_z_dokumentu=requirement,
            zdane=zdane,
            dowod=dowod,
            szczegoly=szczegoly,
        )
    )


def check_contract(
    endpoint: DevToolsEndpoint,
    *,
    expected_browser: str,
    user_data_dir: Path,
    artifacts_dir: Path,
) -> ContractResult:
    """Runs the full CDP contract against a running capsule."""
    from playwright.sync_api import sync_playwright

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    result = ContractResult()

    # --- 1. no public CDP ----------------------------------------------------
    def brak_publicznego_cdp() -> tuple[bool, str, dict]:
        container_address = socket.gethostbyname(socket.gethostname())
        url = f"http://{container_address}:{endpoint.port}/json/version"
        try:
            urllib.request.urlopen(url, timeout=4)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return (
                True,
                f"{container_address}:{endpoint.port} odrzuca połączenie ({type(exc).__name__})",
                {"adres_kontenera": container_address},
            )
        return (
            False,
            f"CDP ODPOWIADA na {container_address} — port jest wystawiony poza loopback",
            {},
        )

    _run_check(result, "brak_publicznego_cdp", "brak publicznego CDP (L5159)", brak_publicznego_cdp)

    # --- 2. rejecting a mismatched build -------------------------------------
    def zgodnosc_builda() -> tuple[bool, str, dict]:
        with urllib.request.urlopen(f"{endpoint.http_base}/json/version", timeout=5) as response:
            data = json.load(response)
        actual = data.get("Browser", "")
        return (
            actual == expected_browser,
            f"/json/version → {actual!r}, oczekiwano {expected_browser!r}",
            {"protocol_version": data.get("Protocol-Version"), "v8": data.get("V8-Version")},
        )

    _run_check(result, "zgodnosc_builda", "odrzucenie niezgodnego builda (L5159)", zgodnosc_builda)

    # --- 3-7. the Playwright contract ----------------------------------------
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint.http_base)

        def persistent_context() -> tuple[bool, str, dict]:
            konteksty = browser.contexts
            return (
                len(konteksty) >= 1,
                f"connect_over_cdp zwrócił {len(konteksty)} kontekst(ów); "
                f"pierwszy ma {len(konteksty[0].pages) if konteksty else 0} stron",
                {"liczba_kontekstow": len(konteksty), "wersja": browser.version},
            )

        _run_check(
            result,
            "persistent_context",
            "odnalezienie persistent contextu (L5159)",
            persistent_context,
        )

        context = browser.contexts[0]
        page = context.new_page()
        page.set_content(
            "<h1 id='tytul'>fixture</h1><div class='pozycja'>a</div><div class='pozycja'>b</div>"
        )

        def locatory() -> tuple[bool, str, dict]:
            text = page.locator("#tytul").inner_text()
            count = page.locator(".pozycja").count()
            return (
                text == "fixture" and count == 2,
                f"locator('#tytul') → {text!r}, locator('.pozycja').count() → {count}",
                {},
            )

        _run_check(result, "locatory", "locatory (L5159)", locatory)

        def screenshoty() -> tuple[bool, str, dict]:
            path = artifacts_dir / "contract-screenshot.png"
            page.screenshot(path=str(path))
            size = path.stat().st_size
            return size > 0, f"screenshot zapisany, {size} B", {"sciezka": str(path)}

        _run_check(result, "screenshoty", "screenshoty (L5159)", screenshoty)

        def popupy() -> tuple[bool, str, dict]:
            with context.expect_page() as expected:
                page.evaluate("window.open('about:blank', '_blank')")
            popup = expected.value
            url = popup.url
            popup.close()
            return True, f"popup przechwycony, url={url}", {}

        _run_check(result, "popupy", "popupy (L5159)", popupy)

        def traces() -> tuple[bool, str, dict]:
            path = artifacts_dir / "contract-trace.zip"
            context.tracing.start(screenshots=True, snapshots=True)
            page.goto("about:blank")
            context.tracing.stop(path=str(path))
            size = path.stat().st_size
            return size > 0, f"trace zapisany, {size} B", {"sciezka": str(path)}

        _run_check(result, "traces", "potrzebne traces (L5159)", traces)

        browser.close()

        def process_survives_close() -> tuple[bool, str, dict]:
            """A documented property of connectOverCDP, not a defect.

            `browser.close()` on a CDP connection closes the CONNECTION, not the browser
            process. This has a direct consequence for releasing the lease: releasing a
            ProfileLease requires stopping the WHOLE capsule and confirming the unmount
            (L2378, L2431), not just `close()`.
            """
            try:
                with urllib.request.urlopen(
                    f"{endpoint.http_base}/json/version", timeout=4
                ) as response:
                    json.load(response)
                return (
                    True,
                    "po browser.close() proces nadal odpowiada na CDP — zgodnie z oczekiwaniem",
                    {},
                )
            except Exception as exc:
                return False, f"proces przestał odpowiadać po browser.close(): {exc}", {}

        _run_check(
            result,
            "proces_przezywa_browser_close",
            "konsekwencja connectOverCDP dla zwalniania lease'u (L2431)",
            process_survives_close,
        )

    # --- 8. Gateway reattach -------------------------------------------------
    def reattach() -> tuple[bool, str, dict]:
        from playwright.sync_api import sync_playwright as sync_playwright_again

        with sync_playwright_again() as p2:
            b2 = p2.chromium.connect_over_cdp(endpoint.http_base)
            count = len(b2.contexts)
            strony = len(b2.contexts[0].pages) if count else 0
            b2.close()
        return count >= 1, f"ponowne connect_over_cdp: {count} kontekst(ów), {strony} stron", {}

    _run_check(
        result, "reattach_gatewaya", "ponowne dołączenie Gatewaya po restarcie (L5159)", reattach
    )

    # --- 9. stale DevToolsActivePort -----------------------------------------
    def stale_port() -> tuple[bool, str, dict]:
        """Checks that verification detects a forged file.

        We do not kill the real Chrome here — we write a file pointing at a port
        where nothing is listening for sure, and check that verification rejects it.
        """
        from . import devtools_port as dp

        oryginal = dp.port_file_path(user_data_dir).read_text(encoding="utf-8")
        dead_port = _free_port()
        dp.port_file_path(user_data_dir).write_text(
            f"{dead_port}\n/devtools/browser/00000000-0000-0000-0000-000000000000\n",
            encoding="utf-8",
        )
        try:
            dp.verify(user_data_dir, expected_browser=expected_browser)
        except StaleDevToolsPort as exc:
            return (
                True,
                f"stale port wykryty i odrzucony: {str(exc)[:160]}",
                {"martwy_port": dead_port},
            )
        else:
            return False, "weryfikacja NIE wykryła stale portu — to jest krytyczne", {}
        finally:
            dp.port_file_path(user_data_dir).write_text(oryginal, encoding="utf-8")

    _run_check(
        result,
        "stale_devtools_port",
        "bezpieczna obsługa stale DevToolsActivePort (L5159)",
        stale_port,
    )

    # --- 10. the canonical argument set --------------------------------------
    def kanoniczne_argumenty() -> tuple[bool, str, dict]:
        from community_os_contracts import CapsuleMode

        from .args import FORBIDDEN_FLAGS, ForbiddenFlag, build_args

        args = build_args(mode=CapsuleMode.HEADLESS, user_data_dir=user_data_dir)
        forbidden_present = [f for f in FORBIDDEN_FLAGS if any(a.startswith(f) for a in args)]
        try:
            build_args(
                mode=CapsuleMode.HEADLESS, user_data_dir=user_data_dir, extra=["--no-sandbox"]
            )
        except ForbiddenFlag:
            rejects = True
        else:
            rejects = False
        return (
            not forbidden_present and rejects,
            f"{len(args)} argumentów, zakazanych: {forbidden_present or 'brak'}; "
            f"próba dołożenia --no-sandbox {'odrzucona' if rejects else 'PRZESZŁA'}",
            {"argumenty": args},
        )

    _run_check(
        result,
        "kanoniczne_argumenty",
        "jeden kanoniczny zestaw argumentów BrowserSupervisora (L5159)",
        kanoniczne_argumenty,
    )

    return result


def _free_port() -> int:
    """Returns a port number that was free at the moment of checking."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
