"""A router dropped into `control_plane/api/` is mounted — and mounted BEHIND the boundary.

Why discovery rather than a hand-written list of `include_router` calls: phase E1.F1 is built
by several packages that land one after another (jobs, approval path, audit). Every one of
them adds routes. A central list of imports is a place to forget, and forgetting it here means
the route either does not exist or — worse — exists without the operator dependency, which is
the failure that looks like success.

The test writes a real module into the real package directory. That is the point: importing a
module the test itself constructed in memory would prove nothing about `pkgutil` walking a
directory on disk.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

import control_plane.api
import pytest
from control_plane.app import create_app, mounted_routes
from fastapi.testclient import TestClient

API_DIR = pathlib.Path(control_plane.api.__file__).parent

PROBE_ROUTER = '''\
"""Temporary module written by a test. If you are reading this in a working tree, the test
crashed before its cleanup ran — delete the file."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/__probe_router__")
async def probe() -> dict[str, str]:
    return {"probe": "ok"}
'''

PROBE_WITHOUT_ROUTER = '"""Temporary module written by a test — no `router` attribute."""\n'


def _drop_module(name: str, body: str) -> pathlib.Path:
    path = API_DIR / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    # Without this, a directory listing cached by the import system from an earlier call in
    # the same process makes the brand-new file invisible and the test passes for the wrong
    # reason (or fails for one).
    importlib.invalidate_caches()
    return path


def _remove_module(name: str, path: pathlib.Path) -> None:
    path.unlink(missing_ok=True)
    sys.modules.pop(f"control_plane.api.{name}", None)
    importlib.invalidate_caches()


def test_a_router_dropped_into_the_api_package_is_mounted() -> None:
    name = "probe_mounted"
    path = _drop_module(name, PROBE_ROUTER)
    try:
        app = create_app()
        paths = {path for path, _methods in mounted_routes(app)}
        assert "/__probe_router__" in paths, (
            "moduł z routerem wrzucony do control_plane/api/ NIE został zamontowany. "
            f"Trasy aplikacji: {sorted(paths)}"
        )

        # Mounted is only half of it: mounted BEHIND the boundary is the other half.
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/__probe_router__")
        assert response.status_code == 401, (
            f"nowa trasa odpowiada {response.status_code} bez poświadczenia — router dopisany "
            "przez kolejny pakiet dostaje granicę operatora z automatu albo nie dostaje jej "
            "wcale, a wtedy nikt tego nie zauważy"
        )
        assert response.content == b"", response.content[:120]
    finally:
        _remove_module(name, path)


def test_a_module_in_the_api_package_without_a_router_is_a_loud_error() -> None:
    """Silence is the failure mode being prevented here.

    A discovery loop that quietly skips a module with no `router` turns a typo — `routes =`
    instead of `router =` — into a route that simply is not there, with no error anywhere.
    The whole point of discovery is that nobody has to remember; that only holds if a module
    that fails to declare a router shouts.
    """
    name = "probe_without_router"
    path = _drop_module(name, PROBE_WITHOUT_ROUTER)
    try:
        with pytest.raises(RuntimeError, match="router"):
            create_app()
    finally:
        _remove_module(name, path)


def test_the_api_package_directory_is_the_one_being_scanned() -> None:
    """Guards the two tests above against scanning a stale installed copy.

    The package is installed editable, so `control_plane.api.__file__` points into the working
    tree. If that ever stops being true, the probe modules would be written somewhere the
    application never looks and both tests above would fail for a reason that has nothing to
    do with discovery.
    """
    assert API_DIR.is_dir(), API_DIR
    assert (API_DIR / "__init__.py").is_file(), API_DIR
    assert API_DIR.name == "api" and API_DIR.parent.name == "control_plane", API_DIR
