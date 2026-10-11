"""The FastAPI application of the control plane (L5190: FastAPI, PostgreSQL, Next.js).

Three things are decided here and each one exists because of a specific way this could go
wrong silently:

1.  **Routers are discovered, not listed.** `control_plane/api/` is walked with `pkgutil` and
    every module's `router` is mounted with the operator dependency attached. A central list
    of imports is a place to forget, and forgetting it means a route that either does not
    exist or — worse — exists without the boundary.

2.  **A module in `api/` with no `router` is a LOUD error.** Skipping it would turn a typo
    into a missing route with no message anywhere.

3.  **The lifespan calls `runtime.start_background()`**, which is empty today. Without the
    call already in place, the lease reaper of a later package would never tick inside the
    container while passing all of its own tests.

The call is written as `runtime.start_background()` — through the module — and not as a name
imported at the top of this file. A `from .runtime import start_background` would freeze the
function object at import time, so both the later real implementation and the monkeypatch in
`tests/test_lifespan.py` would be bypassed; the test would stay green and the reaper would
still never run.
"""

from __future__ import annotations

import importlib
import pkgutil
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.routing import APIRouter

from control_plane import runtime
from control_plane.auth import OperatorRequired, deny_reason, require_operator, unauthorized

#: The package walked for routers. A string rather than the imported module so the walk always
#: sees the CURRENT contents of the directory, not a module object captured at import time.
API_PACKAGE = "control_plane.api"


def discover_routers() -> list[tuple[str, APIRouter]]:
    """Every `router` in `control_plane.api.*`, as (module name, router) pairs.

    Raises RuntimeError for a module that defines no `router`. That is the whole contract of
    the `api` package: a file lands there to add routes, so a file that adds none is either a
    typo or a module in the wrong place, and both are worth stopping for.
    """
    # A module written to disk after this process started is invisible without this — the
    # import system caches directory listings.
    importlib.invalidate_caches()
    package = importlib.import_module(API_PACKAGE)

    found: list[tuple[str, APIRouter]] = []
    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{API_PACKAGE}.{module_info.name}")
        router = getattr(module, "router", None)
        if router is None:
            raise RuntimeError(
                f"moduł {API_PACKAGE}.{module_info.name} nie definiuje `router`. Każdy moduł "
                "w tym pakiecie ma go definiować — pominięcie go po cichu zamienia literówkę "
                "w trasę, której po prostu nie ma, i nigdzie nie ma o tym ani słowa"
            )
        found.append((module_info.name, router))
    return found


def mounted_routes(app: FastAPI) -> list[tuple[str, frozenset[str]]]:
    """Every (path, HTTP methods) pair the application really serves, derived from `app.routes`.

    READ THIS BEFORE WRITING A TEST OVER `app.routes` — measured on FastAPI 0.141.1 with
    Starlette 1.6.0, not remembered from an older version:

        `include_router()` DOES NOT flatten the included routes into `app.routes`. It appends a
        single `fastapi.routing._IncludedRouter` object, which has no `path` at all and resolves
        its children lazily through `effective_candidates()`.

    So a test that walks `app.routes` looking for `.path` sees `/openapi.json`, `/docs`,
    `/docs/oauth2-redirect`, `/redoc`, `/healthz` — and NOT ONE route from any mounted router.
    Iterating naively would mean the auth-boundary test passed while proving nothing about the
    routes that actually matter: the perfect false pass, with the routes hidden by the very
    framework call that adds them.

    Anything this function cannot classify raises. A flattener that quietly drops what it does
    not understand reports a clean application, which is the same disease as a gate announcing
    GO without a measurement (D-007).
    """
    found: list[tuple[str, frozenset[str]]] = []

    def walk(items) -> None:
        for item in items:
            candidates = getattr(item, "effective_candidates", None)
            if callable(candidates):  # a folded router — recurse into its resolved children
                walk(candidates())
                continue
            path = getattr(item, "path", None)
            if path is None:
                raise RuntimeError(
                    f"nie rozpoznano obiektu trasy {item!r} ({type(item).__name__}). Ta funkcja "
                    "ma raczej paść, niż po cichu pominąć trasę — pominięta trasa wypada spod "
                    "testu granicy operatora i nikt się o tym nie dowie"
                )
            found.append((path, frozenset(getattr(item, "methods", None) or {"GET"})))

    walk(app.routes)
    return found


def create_app() -> FastAPI:
    """Build the application. A factory, not a module-level singleton.

    Tests need a fresh application after dropping a router into `api/`, and a singleton built
    at import time cannot give them one.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runtime.start_background()
        try:
            yield
        finally:
            await runtime.stop_background()

    app = FastAPI(
        title="wenext control plane",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def operator_boundary(request, call_next):
        """The enforcement point. Runs BEFORE routing, so it covers everything.

        Including `/openapi.json`, `/docs` and `/redoc`, which FastAPI adds as plain Starlette
        routes that no dependency ever reaches — and including paths that do not exist, so a
        refused caller cannot map the API by watching 404s turn into 401s.

        The WHOLE request is handed to `deny_reason`, not `request.url.path`. Since package P7
        the decision needs the session cookie and the method: a boundary that sees only the path
        can either refuse everything (nobody can ever log in) or allow by path (no credential is
        ever checked). `deny_reason` raises `TypeError` on a bare string rather than deciding on
        half the evidence.
        """
        if deny_reason(request) is not None:
            return unauthorized()
        return await call_next(request)

    @app.exception_handler(OperatorRequired)
    async def _operator_required(_request, _exc):
        """Empty body, always (D-016). `HTTPException` would render `{"detail": ...}`."""
        return unauthorized()

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """The only route outside the operator boundary.

        Registered directly on the app rather than through `api/`, because everything mounted
        from there carries `Depends(require_operator)` by construction. It reports that the
        process is up; it says nothing about the database, and it must not — a healthcheck
        that fails when PostgreSQL blinks takes the API down with it.
        """
        return {"status": "ok"}

    for _module_name, router in discover_routers():
        app.include_router(router, dependencies=[Depends(require_operator)])

    return app


# NO module-level `app = create_app()`. uvicorn is started with `--factory` (see the image's
# CMD), so the application is built when the process starts rather than when anything imports
# this module. A module-level singleton would also freeze the router discovery at import time,
# which is precisely what the discovery tests need to be able to redo.
