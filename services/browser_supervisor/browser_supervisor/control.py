"""The capsule's mode controller — `INTERACTIVE` on request from the control plane (plan B, lvl 2).

`docs/OUTCOMES.md` §8 „poziom 2": a human has to log the capsule's Chrome into Reddit, and a
human needs a screen. INTERACTIVE mode (headful Chrome on Xtigervnc) is the screen; until now
nothing outside the capsule process could ask for it — `WENEXT_BROWSER_MODE=INTERACTIVE` is
refused at start on purpose (`main.py`), and the only caller of `start(INTERACTIVE)` was gate 2.
This module is the missing trigger, exposed by the reader's HTTP server (`reader.py`) behind the
same token and on the same single thread.

Why `safe_stop_done=True` is legitimate here and nowhere else: `switch_mode` demands that the
caller declare the running automation reached `SAFE_STOP` (L2380, L5533). In Outcome A there is
no automation that writes — the capsule's only driver is the reader, the reader is single-threaded,
and this call runs ON that thread, so no fetch is in flight when the browser is stopped. When a
writer appears (Outcome C), this declaration must come from the execution layer instead.

The reason stays a closed list (`E1_ALLOWED_INTERACTIVE_REASONS`, L5518). A request outside it is
a refusal, not a new category.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from community_os_contracts import CapsuleMode, InteractiveReason
from community_os_contracts.enums import E1_ALLOWED_INTERACTIVE_REASONS

from .reader import FetchFailed, ModeSwitchFailed, ModeSwitchRefused
from .supervisor import BrowserSupervisor, CapsuleStartFailed, ForbiddenInteractiveReason


class SupervisorModeController:
    """`ModeController` over one `BrowserSupervisor`."""

    def __init__(self, supervisor: BrowserSupervisor) -> None:
        self._supervisor = supervisor
        #: True while `switch_mode` runs. The service loop in `main.py` reads it: between `stop()`
        #: and the next `start()` the supervisor has no capsule for several seconds, and a loop
        #: that treated that as "Chrome died" would take the whole container down mid-switch.
        self.switching = False

    # -- what the reader needs --------------------------------------------------------

    def endpoint_http(self) -> str:
        """The CDP endpoint of the launch running NOW — it changes with every mode switch."""
        capsule = self._supervisor.current
        if capsule is None or not capsule.is_alive():
            raise FetchFailed("kapsuła nie ma działającego Chrome (między stop a start)")
        if capsule.endpoint is None:
            # A person is signing in: that launch has no DevTools endpoint on purpose (Google
            # refuses a sign-in in a browser that has one), so nothing reads until "Gotowe".
            raise FetchFailed(
                "człowiek loguje się przy ekranie kapsuły; przeglądarka nie ma w tym czasie "
                "punktu CDP, odczyty wrócą po zakończeniu logowania"
            )
        return capsule.endpoint.http_base

    def _gateway_credentials(self) -> Path:
        vnc = self._supervisor.vnc_socket
        runtime_dir = Path(vnc).parent if vnc is not None else Path("/run/wenext/capsule")
        return runtime_dir / "vnc-credentials"

    def state(self) -> Mapping[str, Any]:
        capsule = self._supervisor.current
        vnc = self._supervisor.vnc_socket
        return {
            "capsule_id": self._supervisor.capsule_id,
            "mode": capsule.mode.value if capsule is not None else None,
            "reason": capsule.reason.value if capsule is not None and capsule.reason else None,
            # D-026: INTERACTIVE for rendering only (platform refuses headless), no human.
            "display_only": bool(capsule is not None and capsule.display_only),
            "since": (
                datetime.fromtimestamp(capsule.started_at, tz=UTC).isoformat()
                if capsule is not None
                else None
            ),
            "alive": capsule is not None and capsule.is_alive(),
            "cdp": (
                capsule.endpoint.http_base
                if capsule is not None and capsule.endpoint is not None
                else None
            ),
            "vnc_ready": bool(vnc is not None and Path(vnc).exists()),
            # The gateway can attach a human only while BOTH exist: the display's socket and the
            # credentials file for that display. A file without a socket is a leftover, not a steer.
            "human_steer_open": bool(
                vnc is not None and Path(vnc).exists() and self._gateway_credentials().exists()
            ),
            "uptime_s": round(time.time() - capsule.started_at, 1) if capsule is not None else None,
        }

    def switch(
        self,
        mode: str,
        reason: str | None,
        display_only: bool = False,
        start_url: str | None = None,
    ) -> Mapping[str, Any]:
        try:
            target = CapsuleMode(mode)
        except ValueError as exc:
            raise ModeSwitchRefused(
                f"nieznany tryb {mode!r}; dozwolone: {[m.value for m in CapsuleMode]}"
            ) from exc
        interactive_reason: InteractiveReason | None = None
        if target is CapsuleMode.INTERACTIVE:
            if display_only and reason:
                raise ModeSwitchRefused(
                    "display_only wyklucza powód: człowiek przy ekranie (powód) albo sam display "
                    "bez steru (D-026), nie oba"
                )
            if not display_only and not reason:
                raise ModeSwitchRefused(
                    "tryb INTERACTIVE wymaga powodu z zamkniętej listy (L5518): "
                    f"{sorted(r.value for r in E1_ALLOWED_INTERACTIVE_REASONS)} — albo "
                    "display_only=true (odczyt headful bez steru człowieka, D-026)"
                )
            if reason:
                try:
                    interactive_reason = InteractiveReason(reason)
                except ValueError as exc:
                    raise ModeSwitchRefused(f"nieznany powód {reason!r}") from exc
                if interactive_reason not in E1_ALLOWED_INTERACTIVE_REASONS:
                    raise ModeSwitchRefused(
                        f"powód {reason} nie jest dopuszczony w E1; dozwolone: "
                        f"{sorted(r.value for r in E1_ALLOWED_INTERACTIVE_REASONS)}"
                    )
        else:
            if reason:
                raise ModeSwitchRefused("tryb HEADLESS nie przyjmuje powodu")
            if display_only:
                raise ModeSwitchRefused("display_only dotyczy tylko trybu INTERACTIVE")

        current = self._supervisor.current
        if (
            current is not None
            and current.is_alive()
            and current.mode is target
            and current.reason is interactive_reason
            and bool(current.display_only) is display_only
        ):
            return dict(self.state(), changed=False)

        self.switching = True
        try:
            self._supervisor.switch_mode(
                target,
                reason=interactive_reason,
                display_only=display_only,
                safe_stop_done=True,
                start_url=start_url,
            )
        except ForbiddenInteractiveReason as exc:
            raise ModeSwitchRefused(str(exc)) from exc
        except (CapsuleStartFailed, RuntimeError, OSError) as exc:
            raise ModeSwitchFailed(f"{type(exc).__name__}: {exc}"[:600]) from exc
        finally:
            self.switching = False
        print(
            f"kapsuła {self._supervisor.capsule_id}: tryb={target.value}"
            + (f" powód={interactive_reason.value}" if interactive_reason else "")
            + (" display_only (bez steru człowieka, D-026)" if display_only else ""),
            flush=True,
        )
        return dict(self.state(), changed=True)
