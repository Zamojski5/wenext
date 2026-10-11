"""`CodexExecutionAdapter` — running turns on the Python SDK (13.1, 13.3, 14.6).

The split from L2107-2115: the Account Adapter talks to the App Server over JSON-RPC, the
Execution Adapter runs threads through the stable Python SDK. Both use THE SAME binary and the
same `CODEX_HOME`, but never at the same time — `CodexConnectionSupervisor` guards that (L2159).

Four things this adapter does before every turn and without which the turn does not start:

1. It checks that the hardening is ACTUAL (`config/read`), and not declared.
2. It starts the task-scoped MCP sidecar BEFORE the Codex runtime (L2501).
3. It checks that the thread binding matches the sidecar's capability (L2503).
4. It validates the model/effort route against the model catalog (L5287).

After the turn the sidecar is closed unconditionally (L2505) — also when the turn raised.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from community_os_contracts import CodexRunThread, ThreadRole, TurnSpec

from .account_adapter import ModelCatalogSnapshot
from .hardening import (
    HardeningConfig,
    effective_config_mismatches,
    prepare_codex_home,
    verify_permissions,
)
from .routing import Route, validate_route


class TurnFailClosed(Exception):
    """A turn aborted before execution or during it, with an explicit reason (L2503)."""


class SidecarMissing(TurnFailClosed):
    """The MCP sidecar did not start. The turn does NOT move without tools (L2503)."""


class BindingMismatch(TurnFailClosed):
    """The sidecar's capability does not match the thread binding (L2503)."""


class EnvelopeExceeded(TurnFailClosed):
    """`BoundedRunEnvelope` exhausted — deadline, or the turn, tool or byte limit."""


@dataclass(frozen=True)
class BoundedRunEnvelope:
    """The hard limits of a single run. Required BEFORE dispatch.

    Without the envelope a long-running runtime has no boundary other than the plan limit —
    and that means the first boundary is the exhaustion of the user's limit window.
    """

    deadline: datetime
    max_turns: int
    max_tool_calls: int
    max_output_bytes: int
    heartbeat_s: float

    def __post_init__(self) -> None:
        for nazwa, value in (
            ("max_turns", self.max_turns),
            ("max_tool_calls", self.max_tool_calls),
            ("max_output_bytes", self.max_output_bytes),
        ):
            if value <= 0:
                raise ValueError(f"{nazwa} musi być dodatnie, jest {value}")
        if self.heartbeat_s <= 0:
            raise ValueError("heartbeat_s musi być dodatni")
        if self.deadline.tzinfo is None:
            raise ValueError("deadline musi być świadomy strefy czasowej")

    @classmethod
    def default(cls, *, minutes: int = 20) -> BoundedRunEnvelope:
        return cls(
            deadline=datetime.now(UTC) + timedelta(minutes=minutes),
            max_turns=8,
            max_tool_calls=64,
            max_output_bytes=512_000,
            heartbeat_s=30.0,
        )


@dataclass
class EnvelopeUsage:
    """The current usage of the envelope. Checked before every step, not after the fact."""

    turns: int = 0
    tool_calls: int = 0
    output_bytes: int = 0

    def enforce(self, envelope: BoundedRunEnvelope) -> None:
        teraz = datetime.now(UTC)
        if teraz >= envelope.deadline:
            raise EnvelopeExceeded(f"deadline {envelope.deadline.isoformat()} minął")
        if self.turns > envelope.max_turns:
            raise EnvelopeExceeded(f"max_turns={envelope.max_turns} przekroczone")
        if self.tool_calls > envelope.max_tool_calls:
            raise EnvelopeExceeded(f"max_tool_calls={envelope.max_tool_calls} przekroczone")
        if self.output_bytes > envelope.max_output_bytes:
            raise EnvelopeExceeded(f"max_output_bytes={envelope.max_output_bytes} przekroczone")


class Sidecar(Protocol):
    """The per-turn MCP sidecar contract. The implementation is in D13 (`services/mcp_sidecar`)."""

    @property
    def nazwa(self) -> str: ...

    @property
    def pid(self) -> int | None: ...

    @property
    def is_alive(self) -> bool: ...

    @property
    def command(self) -> tuple[str, ...]: ...

    @property
    def enabled_tools(self) -> tuple[str, ...]: ...

    def binding_fingerprint(self) -> tuple: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...


@dataclass
class CodexExecutionAdapter:
    """Runs turns in an isolated `CODEX_HOME` with a hardened `config.toml`.

    `sidecar_factory` gets the thread binding and returns a sidecar for THIS turn. The factory
    is injected, because the capability comes from the Control Plane, and not from this process
    and even less from the prompt (L2495).
    """

    codex_home: Path
    codex_bin: Path
    sidecar_factory: Callable[[tuple], Sidecar]
    hardening: HardeningConfig | None = None
    _prepared: bool = field(default=False, init=False)

    def prepare(self) -> Path:
        """Creates an isolated `CODEX_HOME` 0700 with a hardened `config.toml` 0600 (L2199-2201)."""
        prepare_codex_home(self.codex_home, self.hardening)
        violations = verify_permissions(self.codex_home)
        if violations:
            raise TurnFailClosed(
                "CODEX_HOME nie spełnia wymagań izolacji z L2199-2201: " + "; ".join(violations)
            )
        self._prepared = True
        return self.codex_home

    def build_sdk_config(self) -> Any:
        """`CodexConfig` with isolation and an EXPLICIT `experimental_api=False`.

        SDK 0.147.0 sets `experimental_api: bool = True` BY DEFAULT — checked
        in the installed package. Hardening without an explicit `False` would be illusory.
        `CodexConfig` has no `codex_home` field; the isolation goes through `env`.
        The SDK does `os.environ.copy()` and only then applies `env`, so `OPENAI_API_KEY`
        from the process environment would change the auth mode — we cut it out explicitly with
        an empty value and additionally from `os.environ` in `protocol.AppServerClient`.
        """
        from openai_codex import CodexConfig  # optional dependency in tests

        srodowisko = {"CODEX_HOME": str(self.codex_home)}
        if "OPENAI_API_KEY" in os.environ:
            # The key cannot be removed from the environment copy through `env`, so we overwrite
            # it with an empty value; Codex treats an empty key as no key.
            srodowisko["OPENAI_API_KEY"] = ""
        return CodexConfig(
            codex_bin=str(self.codex_bin),
            env=srodowisko,
            client_name="wenext-codex-runtime",
            client_title="wenext Codex runtime",
            experimental_api=False,
        )

    def verify_hardening(self, config_read: dict[str, Any]) -> None:
        """A read, not an assumption (DoD D12)."""
        mismatches = effective_config_mismatches(config_read)
        if mismatches:
            raise TurnFailClosed(
                "efektywna konfiguracja runtime'u nie jest hardened: " + "; ".join(mismatches)
            )

    # -- turn -----------------------------------------------------------------

    def prepare_turn(
        self,
        *,
        role: ThreadRole,
        spec: TurnSpec,
        snapshot: ModelCatalogSnapshot,
        binding_fingerprint: tuple,
        envelope: BoundedRunEnvelope,
    ) -> tuple[Sidecar, Route]:
        """Everything that has to succeed BEFORE anything is sent to the model.

        The order is a requirement, not a style: the sidecar starts before the Codex runtime
        (L2501), and a binding mismatch ends the turn before the model gets any prompt at all.
        """
        if not self._prepared:
            raise TurnFailClosed("CODEX_HOME nie został przygotowany — wywołaj `przygotuj()`")
        if spec.role is not role:
            raise BindingMismatch(
                f"TurnSpec.role={spec.role} ≠ rola wątku {role} — to jest mismatch bindingu"
            )
        EnvelopeUsage().enforce(envelope)

        route = validate_route(role, snapshot)
        if spec.model != route.model or spec.effort != route.effort:
            raise BindingMismatch(
                f"TurnSpec żąda {spec.model}/{spec.effort}, a stała trasa roli {role} to "
                f"{route.model}/{route.effort} — trasy w F0 są zamrożone (L5266)"
            )

        sidecar = self.sidecar_factory(binding_fingerprint)
        try:
            sidecar.start()
        except Exception as exc:
            raise SidecarMissing(
                f"task-scoped sidecar MCP nie wystartował: {exc}. Turn nie rusza — "
                "brak sidecara kończy turn fail-closed (L2503)."
            ) from exc
        if not sidecar.is_alive:
            raise SidecarMissing("sidecar zakończył się natychmiast po starcie — turn fail-closed")
        if sidecar.binding_fingerprint() != binding_fingerprint:
            sidecar.stop()
            raise BindingMismatch(
                "capability sidecara nie odpowiada bindingowi wątku — turn fail-closed (L2503)"
            )
        return sidecar, route

    def close_turn(self, sidecar: Sidecar) -> None:
        """Closes the sidecar after the turn (L2505). Unconditionally, also after an exception."""
        sidecar.stop()

    def build_run_thread(
        self,
        *,
        binding_id: UUID,
        automation_run_id: UUID,
        job_attempt_id: UUID,
        attempt: int,
        prompt_version_id: UUID,
        router_policy_version: str,
        route: Route,
        effective_model: str | None,
        effort_observable: bool,
        effective_effort: str | None,
        started_at: datetime,
        finished_at: datetime | None = None,
    ) -> CodexRunThread:
        """Run metadata. `effective_effort` without observability is forbidden (L5642)."""
        return CodexRunThread(
            id=uuid4(),
            thread_security_binding_id=binding_id,
            automation_run_id=automation_run_id,
            job_attempt_id=job_attempt_id,
            attempt=attempt,
            prompt_version_id=prompt_version_id,
            router_policy_version=router_policy_version,
            requested_model=route.model,
            requested_effort=route.effort,
            effective_model=effective_model,
            effort_observable=effort_observable,
            effective_effort=effective_effort,
            started_at=started_at,
            finished_at=finished_at,
        )
