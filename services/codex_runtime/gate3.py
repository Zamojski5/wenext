"""Go/No-Go gate no. 3 — Codex as the marketing runtime.

    make codex-gonogo   →   artifacts/gate3/codex-runtime.json

The artifact format is the same as in `services/browser_supervisor/browser_supervisor/contract.py`
(the `nazwa`, `wymog_z_dokumentu`, `zdane`, `dowod`, `szczegoly` fields), because the Go/No-Go
report (D19) reads all three gates with one parser.

The checks required by `tools/gonogo/criteria.yaml` (GATE-3):

    sdk_wystawia_model_effort_schema
    model_list_pelna_paginacja
    cztery_role_w_osobnych_watkach
    brak_shell_i_exec
    tylko_dozwolony_mcp
    unattended_rozstrzygniete

The first five are decidable WITHOUT a ChatGPT account and this script really does decide them:
it starts the pinned binary on an isolated `CODEX_HOME`, does the handshake, reads the effective
configuration, paginates `model/list` and opens four threads.

The sixth IS NOT decidable without an account and without a multi-hour window. Without an account
it returns `zdane: false` with the evidence "wymaga człowieka". In line with the threshold from
criteria.yaml: "Multi-hour unattended work confirmed OR explicitly rejected as NO_GO — a lack of
confirmation is NOT a positive result."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))

#: In the repo the script sits in `services/codex_runtime/`, so the root is two levels up.
#: In the container image it sits in `/srv/` and that path does not exist — the default
#: artifact directory then falls back to the working directory. `--artifacts` overrides both.
REPO_ROOT = _HERE.parents[2] if len(_HERE.parents) > 2 else Path.cwd()

from codex_runtime import (  # noqa: E402
    ALLOWED_METHODS,
    ROUTES,
    AppServerClient,
    CodexAccountAdapter,
    MethodNotAllowed,
    ThreadRegistry,
    effective_config_mismatches,
    extra_mcp_servers,
    find_codex_bin,
    load_contract,
    methods_outside_contract,
    prepare_codex_home,
    threads_are_disjoint,
    validate_all_routes,
)
from codex_runtime.routing import WIRE_EFFORTS  # noqa: E402
from community_os_contracts import CodexConnectionState as ConnectionState  # noqa: E402
from community_os_contracts import ThreadRole, TurnSpec  # noqa: E402

#: The name of the MCP server that is the ONLY ONE allowed to be active (D13).
# `wenext` added for P2: a session runs with the product's own tool server alongside whatever
# the deployment configures. It is on the allowlist so that a gate run against a session runtime
# reports what is actually configured, instead of flagging our own server as an intruder.
ALLOWED_MCP_SERVERS = frozenset({"wenext_browser", "wenext"})


@dataclass
class CheckResult:
    nazwa: str
    wymog_z_dokumentu: str
    zdane: bool
    dowod: str
    szczegoly: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateResult:
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


def shell_methods_all_rejected(rejected: Mapping[str, str], expected: Sequence[str]) -> bool:
    """True only when EVERY expected method landed an explicit allowlist rejection.

    Pulled out of the closure in `run_gate3` so it can be tested with real inputs instead of
    by grepping the source. The bug it closes: the check used a bare
    `all(o.startswith("MetodaNiedozwolona") for o in rejected.values())`, and `all()` over an
    empty mapping is True. A shell method that ran successfully raised nothing and therefore
    recorded nothing, so the control passed — quoting an empty dict as proof of rejection —
    at exactly the moment the L2222 boundary had stopped existing. Counting is what makes
    "nothing was blocked" different from "everything was blocked".
    """
    return len(rejected) == len(expected) and all(
        rejected.get(method, "").startswith("MetodaNiedozwolona") for method in expected
    )


def _run_check(result: GateResult, nazwa: str, requirement: str, fn) -> None:
    """Runs a check, turning an exception into an explicit failure carrying the error text."""
    try:
        zdane, dowod, szczegoly = fn()
    except Exception as exc:
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


def run_gate3(*, codex_home: Path, with_account: bool = False) -> GateResult:
    """Executes the full contract of gate no. 3 on the pinned binary."""
    result = GateResult()
    codex_bin = find_codex_bin()
    prepare_codex_home(codex_home)

    client = AppServerClient(codex_bin=codex_bin, codex_home=codex_home)
    client.start()
    try:
        client.initialize()
        adapter = CodexAccountAdapter(client=client)

        # --- 1. SDK contract: model, effort, output schema --------------------
        def sdk_wystawia_model_effort_schema() -> tuple[bool, str, dict]:
            contract = load_contract()
            outside_contract = methods_outside_contract(ALLOWED_METHODS)

            from openai_codex import Codex, CodexConfig

            signatures = {
                n: hasattr(Codex, n)
                for n in ("models", "account", "thread_start", "thread_resume", "logout")
            }
            # `model` and `effort` are turn parameters; `output_schema` is a requirement
            # from L5265 and lives in `TurnSpec` from contracts — we check that they can
            # be expressed at all for each of the four roles.
            spec = TurnSpec(
                role=ThreadRole.WORKER,
                model=ROUTES[ThreadRole.WORKER].model,
                effort=ROUTES[ThreadRole.WORKER].effort,
                output_schema={"type": "object", "properties": {"wynik": {"type": "string"}}},
            )
            experimental_default = CodexConfig().experimental_api
            ok = (
                not outside_contract
                and all(signatures.values())
                and spec.effort in WIRE_EFFORTS
                and bool(spec.output_schema)
            )
            return (
                ok,
                f"przypięta binarka {contract['wygenerowano_z']['codex_version']}; "
                f"allowlist w całości obecny w protokole "
                f"(spoza kontraktu: {outside_contract or 'brak'}); "
                f"SDK wystawia {sum(signatures.values())}/{len(signatures)} wymaganych metod; "
                f"effort {spec.effort!r} jest wartością drutową; output_schema niepusty",
                {
                    "sygnatury_sdk": signatures,
                    "experimental_api_domyslnie_w_sdk": experimental_default,
                    "experimental_api_w_kliencie": client.experimental_api,
                    "sha256_binarki": contract["wygenerowano_z"]["sha256_binarki"],
                },
            )

        _run_check(
            result,
            "sdk_wystawia_model_effort_schema",
            "przypięta wersja SDK wystawia wymagane model, effort i output schema (L2123)",
            sdk_wystawia_model_effort_schema,
        )

        # --- 2. model/list with full pagination ------------------------------
        def model_list_pelna_paginacja() -> tuple[bool, str, dict]:
            adapter.transition_to(ConnectionState.AUTH_PENDING)
            adapter.transition_to(ConnectionState.AUTHENTICATED)
            snapshot = adapter.sync_model_catalog()
            problems = validate_all_routes(snapshot)
            return (
                snapshot.complete and not problems,
                f"{len(snapshot.models)} modeli na {snapshot.page_count} stronie/-ach, "
                f"nextCursor=null osiągnięty: {snapshot.complete}; "
                f"cztery trasy walidują się wobec supportedReasoningEfforts: "
                f"{problems or 'tak'}",
                {
                    "content_hash": snapshot.content_hash,
                    "include_hidden": snapshot.include_hidden,
                    "modele": [m.id for m in snapshot.models],
                    "trasy": {str(r): [t.model, t.effort] for r, t in ROUTES.items()},
                },
            )

        _run_check(
            result,
            "model_list_pelna_paginacja",
            "model/list iteruje aż nextCursor=null; niedostępna kombinacja model/effort "
            "jest odrzucana przed turnem (L2187-2189, L5287)",
            model_list_pelna_paginacja,
        )

        # --- 3. four roles in separate threads -------------------------------
        def cztery_role_w_osobnych_watkach() -> tuple[bool, str, dict]:
            registry = ThreadRegistry(
                client=client,
                codex_connection_id=uuid4(),
                workspace_id=uuid4(),
                tool_manifest_version="mcp-sidecar-1",
                security_policy_version="f0-1",
                principal_generation=1,
            )
            threads = registry.start_all_roles()
            disjoint = threads_are_disjoint(threads)

            # Fail-closed on a mismatched binding — on the same, live threads.
            from community_os_contracts import ResumeRefused

            thread = threads[ThreadRole.WORKER]
            mismatched_binding = thread.binding.model_copy(
                update={"id": uuid4(), "tool_manifest_version": "mcp-sidecar-CUDZY"}
            )
            try:
                registry.resume(thread.binding, mismatched_binding, active_principal_generation=1)
            except ResumeRefused as exc:
                fail_closed, message = True, str(exc)[:160]
            else:
                fail_closed, message = False, "resume z niezgodnym bindingiem PRZESZEDŁ"

            return (
                disjoint and fail_closed,
                f"cztery role → {len({outcome.thread_id for outcome in threads.values()})} "
                "różnych thread_id "
                f"i {len({outcome.binding.id for outcome in threads.values()})} różnych bindingów; "
                f"resume przy niezgodnym tool_manifest_version: {message}",
                {
                    "watki": {str(r): outcome.thread_id for r, outcome in threads.items()},
                    "sesje": {str(r): outcome.session_id for r, outcome in threads.items()},
                },
            )

        _run_check(
            result,
            "cztery_role_w_osobnych_watkach",
            "planner, worker, verifier i post-verifier mają osobne wątki; resume przy "
            "niezgodnym bindingu kończy się fail-closed (L2265-2266)",
            cztery_role_w_osobnych_watkach,
        )

        # --- 4. no shell and exec --------------------------------------------
        def brak_shell_i_exec() -> tuple[bool, str, dict]:
            # Every method MUST land an entry in `rejected`, including the case where the
            # call succeeds. Without the `else` branch a successful shell call left nothing
            # behind, and `all()` over an empty dict is True — so the control passed exactly
            # when the allowlist had stopped blocking anything, quoting an empty dict as the
            # proof of rejection. Fifth false pass of this class in this module (D-007, D-009);
            # the length check below is the second lock on the same door.
            shell_methods = ("thread/shellCommand", "command/exec", "process/spawn")
            rejected: dict[str, str] = {}
            for method in shell_methods:
                try:
                    client.request(method, {})
                except MethodNotAllowed as exc:
                    rejected[method] = f"MetodaNiedozwolona: {str(exc)[:80]}"
                except Exception as exc:
                    rejected[method] = f"PRZESZŁA PRZEZ ALLOWLIST: {type(exc).__name__}: {exc}"
                else:
                    rejected[method] = (
                        "PRZESZŁA PRZEZ ALLOWLIST: wywołanie zwróciło wynik bez wyjątku"
                    )

            config_read = client.request("config/read", {})
            mismatches = effective_config_mismatches(config_read)
            features = config_read["config"]["features"]

            # A measured fact, not an assumed one: with the flags disabled the app-server STILL
            # executes `command/exec` at the JSON-RPC level. We record this as evidence,
            # because whether the allowlist may ever be loosened depends on it.
            try:
                raw = client._request_bypassing_allowlist(
                    "command/exec", {"command": ["/bin/echo", "wenext-gate3"]}
                )
                exec_works_despite_flags = raw.get("stdout", "").strip() == "wenext-gate3"
            except Exception as exc:
                exec_works_despite_flags = f"odmowa: {type(exc).__name__}: {exc}"

            all_rejected = shell_methods_all_rejected(rejected, shell_methods)
            return (
                all_rejected and not mismatches,
                f"metody shell/exec odrzucone przez allowlist klienta: {rejected}; "
                f"efektywna konfiguracja hardened: {mismatches or 'tak'}",
                {
                    "features_efektywne": features,
                    "web_search": config_read["config"].get("web_search"),
                    "errata_command_exec_dziala_mimo_wylaczonych_flag": exec_works_despite_flags,
                },
            )

        _run_check(
            result,
            "brak_shell_i_exec",
            "shell i unified exec wyłączone; runtime nie może wywołać thread/shellCommand "
            "ani command/exec (L2213-2222)",
            brak_shell_i_exec,
        )

        # --- 5. only the allowed MCP -----------------------------------------
        def tylko_dozwolony_mcp() -> tuple[bool, str, dict]:
            active_servers = client.request("mcpServerStatus/list", {})
            extra_servers = extra_mcp_servers(active_servers, ALLOWED_MCP_SERVERS)
            entries = active_servers.get("data") or []
            return (
                not extra_servers,
                f"aktywnych serwerów MCP: {len(entries)}; nadmiarowych: {extra_servers or 'brak'}. "
                "Sprawdzone listą aktywnych serwerów w runtime, nie deklaracją w config.",
                {"aktywne": entries, "dozwolone": sorted(ALLOWED_MCP_SERVERS)},
            )

        _run_check(
            result,
            "tylko_dozwolony_mcp",
            "Codex korzysta tylko z task-scoped Browser MCP; zbędne MCP wyłączone "
            "(L2219-2221, DoD L5179)",
            tylko_dozwolony_mcp,
        )

        # --- 6. unattended ----------------------------------------------------
        def unattended_rozstrzygniete() -> tuple[bool, str, dict]:
            konto = adapter.read_account()
            if not konto.logged_in:
                return (
                    False,
                    "wymaga człowieka: brak zalogowanego konta ChatGPT. Wielogodzinny test "
                    "schedulera (restart runtime'u, odświeżenie auth, quota exhaustion, "
                    "resume, praca bez otwartego dashboardu — L2129) nie został wykonany. "
                    "Brak potwierdzenia NIE jest wynikiem pozytywnym: bramka pozostaje "
                    "nierozstrzygnięta do czasu uruchomienia `make codex-gonogo` przez "
                    "człowieka z kontem (D20).",
                    {
                        # Read by tools/gonogo/report.py: a failed check
                        # with this marker is NOT a gate failure, only a lack of evidence.
                        # Without that distinction the report would announce NO_GO for a runtime
                        # that nobody has tested yet — and those are two different things.
                        "wymaga_czlowieka": True,
                        "account_read": {
                            "zalogowany": False,
                            "requires_openai_auth": konto.requires_openai_auth,
                        },
                        "co_trzeba_zrobic": [
                            "włączyć device code login w ustawieniach konta albo workspace'u",
                            "codex login --device-auth albo account/login/start(chatgptDeviceCode)",
                            "wielogodzinne okno z restartem runtime'u i wyczerpaniem quoty",
                        ],
                        "zastrzezenie_tos": (
                            "Warunki użycia ChatGPT zakazują udostępniania konta i zasilania "
                            "nim usług trzecich. Unattended runtime na koncie jednego "
                            "użytkownika obsługujący innych użytkowników końcowych jest poza "
                            "tymi warunkami — to jest decyzja produktowa, nie inżynierska."
                        ),
                    },
                )
            # The account IS logged in — but that is not the answer to this check's question.
            # The question is: does the runtime withstand MULTI-HOUR unattended work (L2129).
            # Reading `account/read` and the limits proves only that the server can be queried —
            # the measurement takes hours and is performed by `codex_runtime.unattended`, which
            # overwrites this check with a real result.
            #
            # An earlier version returned `True` here, which made gate3 report 6/6 before
            # the measurement had even started, and the report announced GO without evidence.
            # This is exactly the class of error this gate is meant to prevent, so the check
            # stays UNRESOLVED until the unattended run.
            limits = adapter.read_rate_limits()
            return (
                False,
                "konto zalogowane i gotowe, ale wielogodzinny pomiar bez nadzoru NIE został "
                f"jeszcze wykonany (konto {konto.auth_type}, plan {konto.plan_type}). "
                "Uruchom `python -m codex_runtime.unattended` — dopiero on rozstrzyga tę "
                "kontrolę i nadpisuje ten wpis.",
                {
                    "wymaga_czlowieka": True,
                    "gotowosc": {
                        "zalogowany": True,
                        "auth_type": konto.auth_type,
                        "plan_type": konto.plan_type,
                    },
                    "rate_limits": limits,
                },
            )

        _run_check(
            result,
            "unattended_rozstrzygniete",
            "wielogodzinna praca bez nadzoru na tym samym planie i sposobie logowania "
            "potwierdzona albo jawnie odrzucona jako NO_GO (L2129, GATE-3)",
            unattended_rozstrzygniete,
        )
    finally:
        client.close()

    del with_account
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bramka Go/No-Go nr 3 — Codex runtime")
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=REPO_ROOT / "artifacts" / "gate3",
        help="katalog wynikowy (domyślnie artifacts/gate3)",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", "")) if os.environ.get("CODEX_HOME") else None,
        help="izolowany CODEX_HOME; domyślnie katalog tymczasowy",
    )
    args = parser.parse_args(argv)

    args.artifacts.mkdir(parents=True, exist_ok=True)
    if args.codex_home is None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="wenext-gate3-") as tmp:
            result = run_gate3(codex_home=Path(tmp) / "codex-home")
    else:
        result = run_gate3(codex_home=args.codex_home)

    file_path = args.artifacts / "codex-runtime.json"
    file_path.write_text(result.to_json(), encoding="utf-8")

    unresolved = [k.nazwa for k in result.kontrole if not k.zdane]
    print(f"[gate3] zapisano {file_path}")
    print(
        f"[gate3] {len(result.kontrole) - len(unresolved)}/{len(result.kontrole)} kontroli "
        f"zdanych; niezdane: {unresolved or 'brak'}"
    )
    print(f"[gate3] czas: {datetime.now(UTC).isoformat()}")
    return 0 if result.zdane else 1


if __name__ == "__main__":
    sys.exit(main())
