"""The single-operator auth boundary is CLOSED BY DEFAULT (L5191, D-016).

The rule the tests below pin down: every route the application exposes, except `/healthz`,
answers 401 with an EMPTY body when there is no operator credential.

Two details that look like nitpicking and are not:

  * The list of routes is read from `app.routes`, never written out by hand. D-016 says it
    literally: "test negatywny na KAŻDEJ trasie zapisującej, generowany z listy tras — nie
    pisany ręcznie, bo trasa dopisana później nie dostałaby testu." A hand-written list stops
    covering the application the moment somebody adds a router, which is exactly when the
    cover is needed.
  * The count of routes is asserted SEPARATELY and BEFORE the predicate. `all([])` is `True`,
    and two of this project's six false passes were precisely that (D-011). An application
    whose routes had vanished would otherwise pass this file with flying colours.

`/healthz` staying open is not an oversight either — it is a requirement with a consequence:
the container healthcheck in `docker-compose.yml` calls it, so a `/healthz` behind the
boundary would never go green and `make up --wait` would hang on the whole stack.
"""

from __future__ import annotations

import pytest
from control_plane.app import create_app, mounted_routes
from control_plane.auth import PUBLIC_PATHS
from fastapi.testclient import TestClient

#: Substituted into `{placeholders}` so a route with path parameters can actually be called.
#: The value is irrelevant — the boundary refuses before anything parses it.
ANY_UUID = "00000000-0000-0000-0000-000000000000"


def _callable_routes(app) -> list[tuple[str, str]]:
    """Every (method, url) pair the app exposes, DERIVED from `app.routes`.

    Via `control_plane.app.mounted_routes`, which knows the thing a naive walk of `app.routes`
    gets wrong on FastAPI 0.141: an included router is folded into a single object with no
    `path`, so its routes are invisible from the outside. Read the docstring there before
    changing anything here.

    The routes FastAPI adds by itself (`/openapi.json`, `/docs`, `/redoc`) are covered on
    purpose. They are plain Starlette routes that no dependency reaches, and `/openapi.json` is
    a complete map of the private API — which is why the boundary is enforced in middleware.
    """
    pairs: list[tuple[str, str]] = []
    for path, methods in mounted_routes(app):
        url = path
        while "{" in url and "}" in url:
            start = url.index("{")
            end = url.index("}", start)
            url = url[:start] + ANY_UUID + url[end + 1 :]
        usable = set(methods) - {"HEAD", "OPTIONS"}
        pairs.append((("GET" if "GET" in usable else sorted(usable)[0]), url))
    return pairs


def test_every_route_except_health_requires_an_operator() -> None:
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    protected = [(method, url) for method, url in _callable_routes(app) if url not in PUBLIC_PATHS]

    # Liczność OSOBNO i PRZED predykatem (D-011).
    assert len(protected) >= 2, (
        f"aplikacja wystawia {len(protected)} tras poza granicą operatora — przy zerze albo "
        "jedynce ten test niczego nie dowodzi, bo `all([])` jest prawdą"
    )

    leaks: list[str] = []
    for method, url in protected:
        response = client.request(method, url)
        if response.status_code != 401:
            leaks.append(f"{method} {url}: {response.status_code}, oczekiwano 401")
        elif response.content:
            leaks.append(
                f"{method} {url}: 401, ale z ciałem {response.content[:120]!r} — D-016 wymaga "
                "pustego ciała, bez komunikatu naprowadzającego"
            )

    assert not leaks, "granica operatora przecieka:\n" + "\n".join(leaks)


def test_health_stays_open_without_a_credential() -> None:
    """The control in the other direction, without which the test above is satisfiable by
    refusing literally everything — including the route the container healthcheck calls.
    """
    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get("/healthz")

    assert response.status_code == 200, (
        f"/healthz odpowiada {response.status_code}. Healthcheck kontenera w docker-compose.yml "
        "woła właśnie tę trasę — za granicą operatora stack nigdy nie stanie się healthy "
        "i `make up --wait` wisi do timeoutu"
    )


def test_the_public_list_holds_health_and_nothing_else() -> None:
    """The exception list is one entry long and stays that way.

    An exception list nobody looks at grows by one line per hurry. This is the place where
    growing it costs a conscious edit and shows up in the diff.
    """
    assert frozenset({"/healthz"}) == PUBLIC_PATHS, (
        f"lista tras poza granicą operatora to {sorted(PUBLIC_PATHS)}. Każdy dodatkowy wpis "
        "poszerza granicę uwierzytelnienia i wymaga decyzji w docs/DECYZJE.md, nie linijki"
    )


@pytest.mark.parametrize("path", ["/", "/jobs", "/openapi.json", "/nie-ma-takiej-trasy"])
def test_an_unknown_path_is_refused_before_it_is_resolved(path: str) -> None:
    """Fail-closed means the answer does not depend on the path existing.

    A boundary that answered 404 for unknown paths and 401 for known ones would hand an
    unauthenticated caller a free map of the API — the same leak as an open `/openapi.json`,
    delivered one request at a time.
    """
    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get(path)

    assert response.status_code == 401, f"{path}: {response.status_code}"
    assert response.content == b"", response.content[:120]
