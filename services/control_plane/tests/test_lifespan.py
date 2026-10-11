"""The application's lifespan really starts the background work.

`control_plane.runtime.start_background()` is empty in this package and is called anyway. That
is the whole reason this test exists: the reaper of expired job leases (D-017: a 10 s loop,
shorter than the 60 s TTL) lands in a later package as a task started from exactly here. If
the call is missing, the reaper will look correct in its own unit tests, be imported by the
image, and never tick inside the container — a component that silently does nothing, which is
this project's recurring failure shape rather than a new one.

Catching that after the fact costs a Docker rebuild cycle. Catching it here costs a second.
"""

from __future__ import annotations

import pathlib

from control_plane import runtime
from control_plane.app import create_app
from fastapi.testclient import TestClient


def test_lifespan_calls_start_background(monkeypatch) -> None:
    calls: list[str] = []

    async def recording_start() -> None:
        calls.append("start")

    monkeypatch.setattr(runtime, "start_background", recording_start)

    app = create_app()
    assert calls == [], "start_background zawołane przy budowie aplikacji, a nie w lifespanie"

    with TestClient(app):
        assert calls == ["start"], (
            "lifespan aplikacji NIE zawołał runtime.start_background(). Zadanie w tle "
            "dopisane przez kolejny pakiet nigdy nie ruszy w kontenerze, a testy jednostkowe "
            "tego zadania będą zielone"
        )


def test_lifespan_calls_stop_background(monkeypatch) -> None:
    """The counterpart. A task started and never stopped outlives the request loop.

    Both halves are stubs today, and both are wired today, so the later package that fills
    them in has nowhere to forget one of them.
    """
    calls: list[str] = []

    async def recording_start() -> None:
        calls.append("start")

    async def recording_stop() -> None:
        calls.append("stop")

    monkeypatch.setattr(runtime, "start_background", recording_start)
    monkeypatch.setattr(runtime, "stop_background", recording_stop)

    with TestClient(create_app()):
        pass

    assert calls == ["start", "stop"], calls


def test_start_background_is_looked_up_at_call_time() -> None:
    """`app.py` must call `runtime.start_background()`, not hold a reference from import time.

    A `from control_plane.runtime import start_background` at the top of `app.py` would freeze
    the function object at import, so the later package's real implementation — and the
    monkeypatch in the two tests above — would both be bypassed. Those tests would then stay
    green while the reaper still never ran, which is the exact false pass this file exists to
    prevent.

    Checked over the PARSE TREE, not over the text. A plain substring search also matches the
    prose in the module docstring that warns against this very import — which is how the first
    version of this test failed on the sentence explaining itself.
    """
    import ast

    module = __import__("control_plane.app", fromlist=["create_app"])
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))

    frozen = [
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("runtime")
        for alias in node.names
    ]
    assert not frozen, (
        f"app.py importuje z runtime pojedyncze nazwy: {frozen}. Ma wołać przez moduł "
        "(`runtime.start_background()`), inaczej podmiana implementacji nie ma jak zadziałać"
    )

    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "runtime.start_background" in calls, sorted(calls)
    assert "runtime.stop_background" in calls, sorted(calls)
