"""A multi-hour unattended work test — step 4 of the founder's runbook (D20).

This is the actual measurement of the GATE-3 gate. The document asks the question outright
(L2129):

    "A separate E1.F0 gate runs a multi-hour scheduler test on exactly the ChatGPT plan,
     login method and environment that are to work in the E1.F6 validation window:
     runtime restart, auth refresh, quota exhaustion, resume and work without an open
     dashboard."

The measurement rules this module guards:

1. **A `NO_GO` result is correct.** If the runtime does not withstand unattended work,
   that is the answer, and not a failure of the measurement. We record it as carefully as `GO`.
2. **An interruption gives no result.** Ctrl+C writes a partial run and leaves the
   check unresolved — never as passed nor as failed.
3. **We do not guess causes.** Every event has a timestamp and the raw message.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from community_os_contracts import ThreadRole
from community_os_contracts.codex import ResumeRefused

from .account_adapter import CodexAccountAdapter, ConnectionState, find_codex_bin
from .hardening import prepare_codex_home
from .protocol import AppServerClient
from .threads import ThreadRegistry

#: How often, in seconds, we run a cycle. Five minutes is a compromise: dense enough to catch
#: auth expiry, and rare enough not to spend the whole quota on the measurement itself.
CYCLE_INTERVAL_S = 300

#: Every how many cycles we restart the runtime. The restart is an explicit requirement of L2129.
RESTART_EVERY_N_CYCLES = 6

#: Every how many cycles we check that resume is fail-closed when the binding changes (L2266).
RESUME_CHECK_EVERY_N_CYCLES = 4

#: Margin when comparing the measured window against the planned one. The loop only exits
#: after passing its deadline, so measured time is normally not shorter than planned; this
#: margin covers wall-clock skew between `rozpoczety` and `zakonczony` only — never a
#: genuinely shortened window.
WINDOW_TOLERANCE_S = 60

#: Fixed identifiers of the resume probe. They do not refer to a real workspace —
#: we are checking the rule, not a concrete thread.
_UNATTENDED_CONNECTION_ID = UUID("00000000-0000-0000-0000-00000000c0de")
_UNATTENDED_WORKSPACE_ID = UUID(
    "00000000-0000-0000-0000-0000000w0rk5".replace("w", "0").replace("r", "0").replace("k", "0")
)


@dataclass
class RunEvent:
    czas: str
    rodzaj: str
    szczegol: str


@dataclass
class UnattendedRun:
    rozpoczety: str
    planned_hours: float
    cycles_run: int = 0
    successful_cycles: int = 0
    restartow: int = 0
    reauth_wymagane: int = 0
    rate_limited: int = 0
    events: list[RunEvent] = field(default_factory=list)
    zakonczony: str | None = None
    przerwany_przez_czlowieka: bool = False

    def record(self, rodzaj: str, szczegol: str) -> None:
        self.events.append(
            RunEvent(czas=datetime.now(UTC).isoformat(), rodzaj=rodzaj, szczegol=szczegol[:500])
        )
        print(f"  [{rodzaj}] {szczegol[:160]}", flush=True)


def _connect(codex_home: Path) -> tuple[AppServerClient, CodexAccountAdapter]:
    """Brings up the app-server and drives the adapter to a state where the catalog may be read.

    The state machine from L2134-2141 does not allow going DISCONNECTED → CAPABILITIES_SYNCING.
    An account logged in earlier still starts in DISCONNECTED, because the connection state is
    the state of the PROCESS, not of the account — so one has to pass explicitly through
    AUTH_PENDING to AUTHENTICATED. Without this `zsynchronizuj_katalog()` blows up in every
    cycle, and the run ends with a false NO_GO blaming the runtime for the caller's error.
    """
    client = AppServerClient(codex_bin=find_codex_bin(), codex_home=codex_home)
    client.start()
    client.initialize()
    adapter = CodexAccountAdapter(client=client)
    if adapter.state is ConnectionState.DISCONNECTED:
        adapter.transition_to(ConnectionState.AUTH_PENDING)
        adapter.transition_to(ConnectionState.AUTHENTICATED)
    return client, adapter


def _check_resume(adapter: CodexAccountAdapter) -> str:
    """Checks that resuming a thread with a CHANGED binding is refused.

    The document requires fail-closed on any difference of the stable binding (L2266,
    L5645). We check it negatively — we try to resume with a different role and expect a refusal.

    The identifiers are synthetic and fixed within the run: the point is not to resume a
    real thread, but whether the RULE does not loosen over a long run.
    """
    registry = ThreadRegistry(
        client=adapter.client,
        codex_connection_id=_UNATTENDED_CONNECTION_ID,
        workspace_id=_UNATTENDED_WORKSPACE_ID,
        tool_manifest_version="unattended-probe",
        security_policy_version="unattended-probe",
    )
    stored = registry.binding_for(ThreadRole.WORKER)
    requested = registry.binding_for(ThreadRole.VERIFIER)

    try:
        registry.resume(stored, requested, active_principal_generation=None)
    except ResumeRefused as exc:
        return f"fail-closed potwierdzone: {str(exc)[:120]}"
    return "UWAGA: resume z inną rolą PRZESZEDŁ — reguła fail-closed nie działa"


def _measured_hours(przebieg: UnattendedRun) -> float | None:
    """Hours the run ACTUALLY lasted. `None` while there is no end marker.

    `planned_hours` is a declaration made at startup and must never be quoted as evidence
    of window length — the evidence is `zakonczony - rozpoczety`. Gate evidence built from
    `planned_hours` claimed "10 cykli przez 6.0 h" 48 minutes into the measurement.
    """
    if przebieg.zakonczony is None:
        return None
    start = datetime.fromisoformat(przebieg.rozpoczety)
    end = datetime.fromisoformat(przebieg.zakonczony)
    return (end - start).total_seconds() / 3600


def _verdict(przebieg: UnattendedRun) -> tuple[bool | None, str]:
    """Returns (zdane, dowod). `None` means: unresolved.

    The distinction matters here, because `tools/gonogo/report.py` turns a failed check
    with the `wymaga_czlowieka` flag into `NIE_URUCHOMIONO`, and without the flag — into `NO_GO`.
    """
    # A run with no end marker HAS NO verdict.
    #
    # The artifact is written after every cycle so that an interruption six hours in does
    # not erase six hours of observation — which means this function is also called WHILE
    # the measurement runs. Without this branch it returned `True` after the first
    # successful cycle, and the artifact announced 6/6 minutes into a six-hour window.
    #
    # The same branch covers death without a signal (SIGKILL, OOM, Docker dying): the
    # signal handler never runs, `przerwany_przez_czlowieka` stays `False`, but
    # `zakonczony` stays `None` — so the last write cannot pose as a result.
    #
    # Fourth instance of this error class in this module, and again a false pass rather
    # than a visible failure (D-007).
    if przebieg.zakonczony is None:
        return None, (
            f"Przebieg nie został domknięty: {przebieg.cycles_run} cykli z okna "
            f"{przebieg.planned_hours} h, brak znacznika zakończenia. Pomiar trwa albo "
            "runtime zginął bez sygnału — w obu przypadkach to nie jest wynik bramki."
        )
    if przebieg.przerwany_przez_czlowieka:
        return None, (
            f"Przebieg przerwany przez człowieka po {przebieg.cycles_run} cyklach. "
            "Częściowy pomiar nie jest wynikiem bramki."
        )
    if przebieg.cycles_run == 0:
        return None, "Nie wykonano ani jednego cyklu."

    success_ratio = przebieg.successful_cycles / przebieg.cycles_run
    if przebieg.reauth_wymagane > 0:
        return False, (
            # Znak mnożenia w zdaniu dla człowieka jest celowy, to nie litera alfabetu.
            f"Runtime {przebieg.reauth_wymagane}× zażądał ponownego logowania w trakcie "  # noqa: RUF001
            f"pracy bez nadzoru ({przebieg.cycles_run} cykli). To jest NO_GO dla pracy "
            "bez nadzoru — nie dla projektu (L2127, L2129)."
        )
    if success_ratio < 1.0:
        return False, (
            f"Udanych cykli: {przebieg.successful_cycles}/{przebieg.cycles_run}. "
            "Praca bez nadzoru nie jest ciągła."
        )
    # `zakonczony` is checked on entry, so this is never `None`. The `0.0` is here only so
    # that reordering the branches later would refuse a verdict rather than raise inside
    # the artifact write — fail-closed by construction.
    measured = _measured_hours(przebieg) or 0.0
    if measured + WINDOW_TOLERANCE_S / 3600 < przebieg.planned_hours:
        return None, (
            f"Okno domknięte po {measured:.2f} h z zaplanowanych {przebieg.planned_hours} h "
            f"({przebieg.cycles_run} cykli, wszystkie udane). Skrócone okno nie dowodzi "
            "wielogodzinnej pracy bez nadzoru (L2129): to brak wyniku, nie wynik negatywny."
        )
    return True, (
        f"{przebieg.cycles_run} cykli przez {measured:.2f} h ZMIERZONYCH "
        f"(okno planowane {przebieg.planned_hours} h), {przebieg.restartow} restartów "
        "runtime'u, zero żądań ponownego logowania."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test pracy Codeksa bez nadzoru (GATE-3)")
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path(os.environ.get("WENEXT_ARTIFACTS_DIR", "/var/wenext/artifacts")) / "gate3",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", "/var/wenext/codex-home")),
    )
    args = parser.parse_args(argv)
    args.artifacts.mkdir(parents=True, exist_ok=True)

    prepare_codex_home(args.codex_home)
    client, adapter = _connect(args.codex_home)

    state = adapter.read_account()
    if not state.logged_in:
        print(
            "Konto nie jest zalogowane. Uruchom najpierw `python -m codex_runtime.login`.",
            file=sys.stderr,
        )
        return 2

    przebieg = UnattendedRun(rozpoczety=datetime.now(UTC).isoformat(), planned_hours=args.hours)
    koniec = time.monotonic() + args.hours * 3600

    interrupted = False

    def handle_signal(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    przebieg.record("START", f"okno {args.hours} h, cykl co {CYCLE_INTERVAL_S} s")

    while time.monotonic() < koniec and not interrupted:
        przebieg.cycles_run += 1
        numer = przebieg.cycles_run
        # A cycle counts as successful ONLY after it has passed in its entirety.
        # Earlier the counter grew right after reading the catalog, so a cycle that blew up
        # on the resume test still entered the statistics as successful — the verdict came out
        # GO despite errors in the log. That was the same class of mistake as the false 6/6
        # in gate3.
        cycle_ok = False

        try:
            state = adapter.read_account()
            if not state.logged_in:
                przebieg.reauth_wymagane += 1
                przebieg.record("REAUTH", f"cykl {numer}: konto wymaga ponownego logowania")
            else:
                snapshot = adapter.sync_model_catalog()
                limits = adapter.read_rate_limits()
                if limits.get("rate_limited"):
                    przebieg.rate_limited += 1
                    przebieg.record("QUOTA", f"cykl {numer}: rate limit, {limits}")
                przebieg.record(
                    "OK",
                    f"cykl {numer}: katalog {len(snapshot.models)} modeli, "
                    f"snapshot kompletny={snapshot.complete}",
                )
                cycle_ok = True

            # Fail-closed resume — an explicit requirement of the step 4 description and L2266.
            # The first one-hour run did NOT check this: the loop said it was checking,
            # and it was not. The hole is closed here, so that the night run covers
            # the whole announced scope, and not a part of it.
            if numer % RESUME_CHECK_EVERY_N_CYCLES == 0:
                przebieg.record("RESUME", _check_resume(adapter))

            if numer % RESTART_EVERY_N_CYCLES == 0:
                # The runtime restart is an explicit requirement of L2129: the question is
                # whether auth survives a restart of the app-server process. We bring up a new
                # process on THE SAME CODEX_HOME; the next cycle will check whether the account
                # is still logged in.
                client.close()
                client, adapter = _connect(args.codex_home)
                przebieg.restartow += 1
                przebieg.record("RESTART", f"cykl {numer}: app-server podniesiony od nowa")

        except Exception as exc:  # każdy błąd jest DANĄ pomiarową
            cycle_ok = False
            przebieg.record("BLAD", f"cykl {numer}: {type(exc).__name__}: {exc}")

        if cycle_ok:
            przebieg.successful_cycles += 1

        # We write after EVERY cycle — an interruption six hours in must not
        # erase six hours of observation.
        _write_artifact(args.artifacts, przebieg)

        remaining = koniec - time.monotonic()
        if remaining <= 0 or interrupted:
            break
        time.sleep(min(CYCLE_INTERVAL_S, remaining))

    przebieg.przerwany_przez_czlowieka = interrupted
    przebieg.zakonczony = datetime.now(UTC).isoformat()
    _write_artifact(args.artifacts, przebieg)

    zdane, dowod = _verdict(przebieg)
    print()
    print(f"Wynik: {'ZDANE' if zdane else ('NIEROZSTRZYGNIĘTE' if zdane is None else 'NIEZDANE')}")
    print(f"  {dowod}")
    return 0 if zdane else 1


def _write_artifact(directory: Path, przebieg: UnattendedRun) -> None:
    """Appends the result of the `unattended_rozstrzygniete` check to the gate 3 artifact.

    The format is the same as in `gate3.py` and `contract.py`, so that `tools/gonogo/report.py`
    reads one shape of data for all three gates.
    """
    file_path = directory / "codex-runtime.json"
    if file_path.exists():
        data = json.loads(file_path.read_text(encoding="utf-8"))
    else:
        data = {"zdane": False, "liczba_kontroli": 0, "kontrole": []}

    zdane, dowod = _verdict(przebieg)
    szczegoly: dict = {"przebieg": asdict(przebieg)}
    if zdane is None:
        # Unresolved: report.py will turn this into NIE_URUCHOMIONO, not into NO_GO.
        szczegoly["wymaga_czlowieka"] = True

    check = {
        "nazwa": "unattended_rozstrzygniete",
        "wymog_z_dokumentu": (
            "potwierdzono albo odrzucono wspierany tryb wielogodzinnej pracy bez otwartego "
            "dashboardu (L5182, L2129)"
        ),
        "zdane": bool(zdane),
        "dowod": dowod,
        "szczegoly": szczegoly,
    }

    data["kontrole"] = [k for k in data["kontrole"] if k["nazwa"] != "unattended_rozstrzygniete"]
    data["kontrole"].append(check)
    data["liczba_kontroli"] = len(data["kontrole"])
    data["zdane"] = all(k["zdane"] for k in data["kontrole"])

    file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
