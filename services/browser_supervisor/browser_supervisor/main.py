"""The capsule entry point.

Two launch modes:

    python -m browser_supervisor.main            # start the capsule and keep it alive
    python -m browser_supervisor.main --contract # a one-off CDP feature contract, then exit

The `--contract` mode is what `make gonogo` calls for gate no. 1.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from community_os_contracts import CapsuleMode

from . import pinned
from .contract import check_contract
from .control import SupervisorModeController
from .reader import (
    CdpPageFetcher,
    ReaderConfigError,
    build_reader_from_env,
    reader_environment,
)
from .supervisor import BrowserSupervisor


def _read_expected_build(lock_path: Path) -> str:
    return pinned.expected_browser_field(lock_path, pinned.build_name())


def build_supervisor() -> BrowserSupervisor:
    lock_path = Path(os.environ.get("WENEXT_VERSIONS_LOCK", "/opt/wenext/versions.lock.toml"))
    vnc = os.environ.get("WENEXT_VNC_SOCKET")
    return BrowserSupervisor(
        binary=Path(os.environ.get("WENEXT_BROWSER_BIN", "/opt/browser/chrome/chrome")),
        profiles_dir=Path(os.environ.get("WENEXT_PROFILES_DIR", "/var/wenext/profiles")),
        capsule_id=os.environ.get("WENEXT_CAPSULE_ID", "capsule-01"),
        expected_cdp_browser=_read_expected_build(lock_path),
        cdp_host=os.environ.get("WENEXT_CDP_HOST", "127.0.0.1"),
        vnc_socket=Path(vnc) if vnc else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BrowserSupervisor kapsuły wenext")
    parser.add_argument(
        "--contract",
        action="store_true",
        help="uruchom CDP feature contract (bramka Go/No-Go nr 1) i zakończ",
    )
    parser.add_argument(
        "--gate2",
        action="store_true",
        help="uruchom test przekazania profilu headful↔headless (bramka nr 2) i zakończ",
    )
    parser.add_argument(
        "--actuator-check",
        action="store_true",
        help=(
            "uruchom sprawdzenie aktuatora komentarzy PRAWDZIWYM Playwrightem przeciw stronie "
            "stub na loopbacku (D-028) i zakończ"
        ),
    )
    parser.add_argument(
        "--read-probe",
        action="store_true",
        help=(
            "zmierz, co da się przeczytać na X i LinkedIn BEZ LOGOWANIA (P6), zapisz artefakt "
            "i zakończ. Nie loguje się, nic nie publikuje i nie dotyka kapsuły live"
        ),
    )
    parser.add_argument(
        "--community-probe",
        action="append",
        metavar="SLUG",
        default=None,
        help=(
            "zmierz, co stoi na stronie społeczności old.reddit.com (E3b): zapisz HTML, zrzut "
            "ekranu i kandydatów na przycisk dołączenia. TYLKO ODCZYT, bez kliknięć. Można "
            "podać kilka razy, żeby zmierzyć też społeczność zamkniętą"
        ),
    )
    parser.add_argument(
        "--probe-url",
        action="append",
        metavar="ADRES",
        default=None,
        help=(
            "dodatkowy adres do zmierzenia tą samą sondą: strona wątku albo społeczności na "
            "www.reddit.com lub old.reddit.com. TYLKO ODCZYT. Służy pytaniu, czy dany host w "
            "ogóle odpowiada tej przeglądarce"
        ),
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path(os.environ.get("WENEXT_ARTIFACTS_DIR", "/var/wenext/artifacts")),
    )
    args = parser.parse_args(argv)

    if args.read_probe:
        # Like the actuator check: manages its own capsule, writes an artifact on every exit
        # path, and never touches a live profile.
        from .read_probe import ARTIFACT_NAME as PROBE_ARTIFACT
        from .read_probe import DEFAULT_TARGETS, run_read_probe, summary_lines

        probe = run_read_probe(
            build_supervisor(), list(DEFAULT_TARGETS), artifacts_dir=args.artifacts / "read-probe"
        )
        for line in summary_lines(probe):
            print(line)
        print(f"\nArtefakt: {args.artifacts / 'read-probe' / PROBE_ARTIFACT}")
        readable = [one for one in probe.observations if one.readable]
        print(f"Czytelne bez konta: {len(readable)} z {len(probe.observations)}")
        # Exit 0 even when nothing is readable: „nic nie da się przeczytać" jest WYNIKIEM sondy,
        # a nie jej porażką. Niepełny przebieg to co innego i wychodzi kodem 1.
        return 0 if probe.complete else 1

    if args.community_probe or args.probe_url:
        # Jak sonda odczytu: własna kapsuła, artefakt na każdej drodze wyjścia. Różnica jest
        # jedna i istotna: ta sonda idzie PROFILEM KAPSUŁY, bo znacznik członkostwa i nazwa
        # konta w nagłówku istnieją wyłącznie dla zalogowanego. Nic nie klika.
        from .community_probe import ARTIFACT_NAME as COMMUNITY_ARTIFACT
        from .community_probe import run_community_probe, summary_lines

        probe = run_community_probe(
            build_supervisor(),
            # `or ()`, bo obie flagi są opcjonalne i wolno podać samą drugą. Bez tego `--probe-url`
            # bez `--community-probe` kończyło się `TypeError` na `None` (zgłoszenie koordynatora
            # 21.09, sonda wątku padła przed startem).
            list(args.community_probe or ()),
            artifacts_dir=args.artifacts / "community-probe",
            urls=list(args.probe_url or ()),
        )
        for line in summary_lines(probe):
            print(line)
        print(f"\nArtefakt: {args.artifacts / 'community-probe' / COMMUNITY_ARTIFACT}")
        # Zero kandydatów jest WYNIKIEM sondy, nie jej porażką: znaczy „tu nie ma czego kliknąć".
        # Niepełny przebieg to co innego i wychodzi kodem 1.
        return 0 if probe.complete else 1

    if args.actuator_check:
        # Like gate 2: manages its own capsule, writes an artifact on every exit path.
        from .actuator_check import ARTIFACT_NAME, run_actuator_check

        result = run_actuator_check(build_supervisor(), artifacts_dir=args.artifacts / "actuator")
        for check in result.kontrole:
            marker = "OK  " if check.zdane else "BŁĄD"
            print(f"  [{marker}] {check.nazwa}: {check.dowod}")
        print(f"\nRaport: {args.artifacts / 'actuator' / ARTIFACT_NAME}")
        print(f"Aktuator komentarzy: {'ZDANY' if result.zdane else 'NIEZDANY'}")
        return 0 if result.zdane else 1

    env_mode = os.environ.get("WENEXT_BROWSER_MODE", "HEADLESS").upper()
    if env_mode != "HEADLESS":
        # INTERACTIVE mode does not start from an environment variable — it requires an
        # explicit reason from the closed list, passed by the execution layer (L5518).
        print(
            "WENEXT_BROWSER_MODE=INTERACTIVE nie jest dozwolony jako tryb startowy. "
            "Tryb interaktywny uruchamia się wyłącznie na żądanie, z powodem z zamkniętej listy.",
            file=sys.stderr,
        )
        return 2

    supervisor = build_supervisor()

    if args.gate2:
        # Gate no. 2 manages the modes itself — we do not start anything here beforehand.
        from .gate2 import run_gate2

        result = run_gate2(supervisor, artifacts_dir=args.artifacts / "gate2")
        for check in result.kontrole:
            marker = "OK  " if check.zdane else "BŁĄD"
            print(f"  [{marker}] {check.nazwa}: {check.dowod}")
        print(f"\nRaport: {args.artifacts / 'gate2' / 'headful-handoff.json'}")
        print(f"Ścieżka headful: {'ZDANA' if result.zdane else 'NIEZDANA'}")
        return 0 if result.zdane else 1

    capsule = supervisor.start(CapsuleMode.HEADLESS)
    print(
        f"kapsuła {supervisor.capsule_id}: tryb={capsule.mode.value} "
        f"pid={capsule.process.pid} instancja={capsule.process_instance_id} "
        f"cdp={capsule.endpoint.http_base}",
        flush=True,
    )

    if args.contract:
        result = check_contract(
            capsule.endpoint,
            expected_browser=supervisor.expected_cdp_browser,
            user_data_dir=supervisor.user_data_dir,
            artifacts_dir=args.artifacts / "gate1",
        )
        args.artifacts.mkdir(parents=True, exist_ok=True)
        report = args.artifacts / "gate1" / "cdp-contract.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(result.to_json(), encoding="utf-8")

        for check in result.kontrole:
            marker = "OK  " if check.zdane else "BŁĄD"
            print(f"  [{marker}] {check.nazwa}: {check.dowod}")
        print(f"\nRaport: {report}")
        print(f"Kontrakt CDP: {'ZDANY' if result.zdane else 'NIEZDANY'}")

        supervisor.stop()
        return 0 if result.zdane else 1

    # Service mode: keep the capsule alive until a signal arrives.
    #
    # The page reader (D-025) starts here and only here: it needs the verified CDP endpoint of
    # THIS launch, and it must not exist in `--contract`/`--gate2` runs, which measure the capsule
    # without anyone steering it. A reader configured without a token is a refusal to run the
    # capsule at all, not a capsule without a reader.
    # The mode controller (plan B, level 2) lets the control plane switch this capsule to
    # INTERACTIVE for a human login and back. The fetcher asks it for the CURRENT endpoint, so
    # reads keep working after a switch instead of pointing at the previous Chrome.
    controller = SupervisorModeController(supervisor)
    try:
        # The comment actuator (R4 minimal, D-028) is offered to the reader; it is served only
        # when the environment also carries WENEXT_WRITER_TOKEN, see `build_reader_from_env`.
        # The X actuator stands next to it under the same rule and the same token.
        from .writer import CdpCommentActuator
        from .x_writer import XActuator

        # ONE Playwright driver for the reader thread: the fetcher starts it, both actuators
        # borrow it. Two drivers on one thread was the first smoke's failure (writer.py).
        fetcher = CdpPageFetcher(controller.endpoint_http)
        reader = build_reader_from_env(
            reader_environment(),
            fetcher=fetcher,
            controller=controller,
            actuator=CdpCommentActuator(controller.endpoint_http, driver=fetcher.driver),
            x_actuator=XActuator(controller.endpoint_http, driver=fetcher.driver),
        )
    except ReaderConfigError as exc:
        print(f"czytnik kapsuły: {exc}", file=sys.stderr, flush=True)
        supervisor.stop()
        return 2
    if reader is not None:
        reader.start()
        print(
            f"czytnik kapsuły: nasłuch {reader.bind_host}:{reader.port}, tylko allowlista URL-i "
            "Reddita i X, za tokenem; /state i /mode dla control plane'u",
            flush=True,
        )

    stop_requested = False

    def handle_signal(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    exit_code = 0
    while not stop_requested:
        # `supervisor.current`, not the `capsule` from before the loop: a mode switch replaces
        # the process, and the old handle would report a healthy capsule as dead (or vice versa).
        running = supervisor.current
        if running is None:
            # No process: either a switch is in progress (stop() done, start() still bringing up
            # Xtigervnc + Chrome — several seconds) or a switch failed and left nothing behind.
            # The controller's flag tells the two apart; only the second one ends the service,
            # and then compose's restart policy brings HEADLESS back.
            if controller.switching:
                time.sleep(0.5)
                continue
            print(
                "Kapsuła nie ma procesu Chrome po przełączeniu trybu",
                file=sys.stderr,
                flush=True,
            )
            exit_code = 1
            break
        if not running.is_alive():
            if controller.switching:
                # `stop()` has terminated Chrome but not yet cleared the handle — part of a switch.
                time.sleep(0.5)
                continue
            kod = running.process.returncode
            print(f"Proces Chrome zakończył się z kodem {kod}", file=sys.stderr, flush=True)
            exit_code = 1
            break
        time.sleep(1.0)

    if reader is not None:
        reader.stop()
    supervisor.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
