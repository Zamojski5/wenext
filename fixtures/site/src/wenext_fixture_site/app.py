"""Fixture site — the FastAPI application.

Three access paths, deliberately different, because the document tells the planner to pick
the cheap one before the expensive one (L4400, L5528):

    /api/public/   PUBLIC_API_NO_ACCOUNT   no account, no session, plain JSON
    /c/            ACCOUNT_REQUIRED        threaded forum behind the login wall
    /g/            ACCOUNT_REQUIRED        professional feed behind the login wall

The same content from the same seed is visible through all three — thanks to that the cheap
path can be compared with the expensive one on identical data.

The paths that CANNOT be walked headless and that are here so that the system under test
STOPS on them (L5154): `/login`, `/mfa` (code only in the server log), `/challenge`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from .config import (
    CLIENT_COOKIE,
    FIXTURE_EPOCH,
    PENDING_COOKIE,
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    Settings,
)
from .generator import build_repository, feed_new, feed_rising
from .models import (
    ACCESS_ACCOUNT_REQUIRED,
    ACCESS_PUBLIC_API,
    ARCHETYPE_GRAPH,
    ARCHETYPE_THREADED,
    Thread,
)
from .seed_data import ALL_DIMENSIONS, coverage
from .state import SiteState, WriteAttempt

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

logger = logging.getLogger("wenext.fixture")

#: Paths behind the login wall. Everything outside them is reachable without a session.
WALLED_PREFIXES = ("/c", "/g")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _embed_json(payload: Any) -> str:
    """JSON to be embedded in `<script type="application/json">`.

    `<` is escaped so that the thread content cannot close the script tag. This is not
    protection against an attack — the content comes from the seed — only a condition of
    HTML correctness.
    """

    return _canonical_json(payload).replace("<", "\\u003c")


def _format_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _format_ago(value: datetime) -> str:
    minutes = int((FIXTURE_EPOCH - value).total_seconds() // 60)
    if minutes < 1:
        return "przed chwilą"
    if minutes < 60:
        return f"{minutes} min temu"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} godz. temu"
    return f"{hours // 24} dni temu"


def _safe_next(raw: str | None) -> str:
    """Relative paths only. A `next` with a full URL would be an open redirect."""

    if not raw:
        return "/c"
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc or not raw.startswith("/"):
        return "/c"
    return raw


class AccessMiddleware(BaseHTTPMiddleware):
    """Login wall + challenge counter. The only place where a redirect comes into being."""

    def __init__(self, app: Any, settings: Settings, state: SiteState) -> None:
        super().__init__(app)
        self.settings = settings
        self.state = state

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path
        walled = any(path == p or path.startswith(p + "/") for p in WALLED_PREFIXES)

        client_id = request.cookies.get(CLIENT_COOKIE)
        issue_client = client_id is None
        if issue_client:
            client_id = secrets.token_hex(8)

        if walled:
            session = self.state.session(request.cookies.get(SESSION_COOKIE))
            if session is None:
                target = f"/login?next={quote(path, safe='')}"
                return RedirectResponse(target, status_code=302)

            over_limit = self.state.register_hit(
                client_id or "anon",
                limit=self.settings.challenge_after,
                window_seconds=self.settings.challenge_window_seconds,
            )
            if over_limit and not path.startswith("/challenge"):
                target = f"/challenge?next={quote(path, safe='')}"
                return RedirectResponse(target, status_code=302)

        response = await call_next(request)
        if issue_client and client_id:
            response.set_cookie(CLIENT_COOKIE, client_id, httponly=True, samesite="lax", path="/")
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state = SiteState()
    repo = build_repository(settings.seed)

    app = FastAPI(
        title="wenext fixture site",
        description=(
            "Deterministyczna imitacja dwóch archetypów kanału na potrzeby E1.F0. "
            "Nie odwzorowuje żadnej istniejącej platformy."
        ),
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.site_state = state
    app.state.repo = repo
    app.add_middleware(AccessMiddleware, settings=settings, state=state)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["site_name"] = "Fixture Community"
    templates.env.globals["seed"] = settings.seed
    templates.env.filters["dt"] = _format_dt
    # The relative label is computed against FIXTURE_EPOCH, not the clock — otherwise the
    # same page would have different HTML every minute and `content_hash` would be fiction.
    templates.env.filters["ago"] = _format_ago

    def render(request: Request, name: str, ctx: dict[str, Any]) -> HTMLResponse:
        session = state.session(request.cookies.get(SESSION_COOKIE))
        base = {"request": request, "session": session, "settings": settings}
        base.update(ctx)
        return templates.TemplateResponse(request, name, base)

    # -----------------------------------------------------------------------
    # Health and metadata
    # -----------------------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "seed": settings.seed,
                "threads": len(repo.threads),
                "archetypes": [ARCHETYPE_THREADED, ARCHETYPE_GRAPH],
            }
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return render(
            request,
            "index.html",
            {
                "communities": [c for c in repo.communities if c.archetype == ARCHETYPE_THREADED],
                "thread_count": len(repo.for_archetype(ARCHETYPE_THREADED)),
                "post_count": len(repo.for_archetype(ARCHETYPE_GRAPH)),
            },
        )

    # -----------------------------------------------------------------------
    # Paths that require a human
    # -----------------------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/c") -> HTMLResponse:
        return render(request, "login.html", {"next": _safe_next(next), "error": None})

    @app.post("/login")
    def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next: str = Form("/c"),
    ) -> Response:
        target = _safe_next(next)
        if username != settings.username or password != settings.password:
            html = render(
                request,
                "login.html",
                {"next": target, "error": "Nieprawidłowy login albo hasło."},
            )
            html.status_code = 401
            return html
        token, _code = state.start_pending(username)
        response = RedirectResponse(f"/mfa?next={quote(target, safe='')}", status_code=303)
        response.set_cookie(PENDING_COOKIE, token, httponly=True, samesite="lax", path="/")
        return response

    @app.get("/mfa", response_class=HTMLResponse)
    def mfa_form(request: Request, next: str = "/c") -> Response:
        token = request.cookies.get(PENDING_COOKIE)
        if not token or token not in state.pending:
            return RedirectResponse(f"/login?next={quote(_safe_next(next), safe='')}", 302)
        # The template does not get the code. Not decoration: headless has no way to get it.
        return render(request, "mfa.html", {"next": _safe_next(next), "error": None})

    @app.post("/mfa")
    def mfa_submit(request: Request, code: str = Form(...), next: str = Form("/c")) -> Response:
        target = _safe_next(next)
        token = request.cookies.get(PENDING_COOKIE)
        session_token = state.confirm_pending(token or "", code.strip())
        if session_token is None:
            html = render(
                request,
                "mfa.html",
                {"next": target, "error": "Kod niepoprawny albo wygasł."},
            )
            html.status_code = 401
            return html
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            session_token,
            max_age=SESSION_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.delete_cookie(PENDING_COOKIE, path="/")
        return response

    @app.get("/logout")
    def logout(request: Request) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            state.sessions.pop(token, None)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/challenge", response_class=HTMLResponse)
    def challenge_form(request: Request, next: str = "/c") -> HTMLResponse:
        response = render(request, "challenge.html", {"next": _safe_next(next)})
        response.status_code = 429
        return response

    @app.post("/challenge")
    def challenge_submit(request: Request, next: str = Form("/c")) -> Response:
        client_id = request.cookies.get(CLIENT_COOKIE)
        if client_id:
            state.clear_challenge(client_id)
        return RedirectResponse(_safe_next(next), status_code=303)

    # -----------------------------------------------------------------------
    # Archetype 1 — THREADED_COMMUNITY
    # -----------------------------------------------------------------------

    @app.get("/c", response_class=HTMLResponse)
    def communities(request: Request) -> HTMLResponse:
        rows = [c for c in repo.communities if c.archetype == ARCHETYPE_THREADED]
        counts = {c.slug: len(repo.for_community(c.slug)) for c in rows}
        return render(request, "communities.html", {"communities": rows, "counts": counts})

    @app.get("/c/{slug}", response_class=HTMLResponse)
    def community_feed(request: Request, slug: str, sort: str = "new") -> Response:
        community = next((c for c in repo.communities if c.slug == slug), None)
        if community is None or community.archetype != ARCHETYPE_THREADED:
            return _not_found(request, render, f"Nie ma społeczności {slug!r}.")
        threads = repo.for_community(slug)
        ordered = feed_rising(threads) if sort == "rising" else feed_new(threads)
        session = state.session(request.cookies.get(SESSION_COOKIE))
        return render(
            request,
            "community.html",
            {
                "community": community,
                "threads": ordered,
                "sort": sort,
                # ODCZYT ZWROTNY CZŁONKOSTWA. To jest jedyne miejsce, z którego da się
                # potwierdzić, że konto NAPRAWDĘ należy do społeczności; bez niego „dołączono"
                # znaczyłoby tylko „wysłano żądanie".
                "member": state.is_member(session.username if session else None, slug),
            },
        )

    @app.post("/c/{slug}/membership")
    def community_membership(request: Request, slug: str, leave: str = Form("")) -> Response:
        """Dołącz konto do społeczności albo je wypisz.

        ZA ŚCIANĄ LOGOWANIA, jak cała `/c`: członkostwo jest czynnością KONTA, więc bez sesji
        nie ma kogo zapisać, a przekierowanie na logowanie robi tu `AccessMiddleware`.

        Zmienia stan, w odróżnieniu od komentarza, który zostaje samą próbą zapisu. Powód jest
        jeden: dowodem dołączenia ma być ODCZYT ZWROTNY, a nie odpowiedź na żądanie, więc musi
        być co odczytać. Treść z ziarna zostaje nietknięta, bo członkostwo nie jest treścią.
        """
        community = next((c for c in repo.communities if c.slug == slug), None)
        if community is None or community.archetype != ARCHETYPE_THREADED:
            return _not_found(request, render, f"Nie ma społeczności {slug!r}.")
        session = state.session(request.cookies.get(SESSION_COOKIE))
        username = session.username if session else "anon"
        leaving = leave.strip().lower() in {"1", "true", "tak"}
        if leaving:
            state.leave_community(username, slug)
        else:
            state.join_community(username, slug)
        state.record_write(
            WriteAttempt(
                kind="COMMUNITY_LEAVE" if leaving else "COMMUNITY_JOIN",
                target=slug,
                username=username,
                payload={},
                received_at=time.time(),
            )
        )
        return RedirectResponse(f"/c/{slug}?membership=1", status_code=303)

    @app.get("/c/{slug}/t/{thread_slug}", response_class=HTMLResponse)
    def thread_page(request: Request, slug: str, thread_slug: str, posted: int = 0) -> Response:
        thread = repo.by_slug(ARCHETYPE_THREADED, thread_slug)
        if thread is None or thread.community != slug:
            return _not_found(request, render, "Nie ma takiego wątku.")
        return render(
            request,
            "thread.html",
            {
                "thread": thread,
                "snapshot": _embed_json(thread.as_dict()),
                "posted": bool(posted),
            },
        )

    @app.post("/c/{slug}/t/{thread_slug}/comments")
    def thread_comment(
        request: Request,
        slug: str,
        thread_slug: str,
        body: str = Form(...),
        parent_id: str = Form(""),
    ) -> Response:
        thread = repo.by_slug(ARCHETYPE_THREADED, thread_slug)
        if thread is None or thread.community != slug:
            return _not_found(request, render, "Nie ma takiego wątku.")
        if thread.locked or thread.removed_by_moderation:
            html = render(request, "locked.html", {"thread": thread})
            html.status_code = 403
            return html
        session = state.session(request.cookies.get(SESSION_COOKIE))
        state.record_write(
            WriteAttempt(
                kind="THREAD_COMMENT",
                target=thread.id,
                username=session.username if session else "anon",
                payload={"body": body[:2000], "parent_id": parent_id},
                received_at=time.time(),
            )
        )
        return RedirectResponse(f"{thread.path}?posted=1", status_code=303)

    # -----------------------------------------------------------------------
    # Archetype 2 — PROFESSIONAL_GRAPH
    # -----------------------------------------------------------------------

    @app.get("/g", response_class=HTMLResponse)
    def graph_feed(request: Request, sort: str = "new") -> HTMLResponse:
        threads = repo.for_archetype(ARCHETYPE_GRAPH)
        ordered = feed_rising(threads) if sort == "rising" else feed_new(threads)
        return render(request, "graph_feed.html", {"threads": ordered, "sort": sort})

    @app.get("/g/p/{thread_slug}", response_class=HTMLResponse)
    def graph_post(request: Request, thread_slug: str, posted: int = 0) -> Response:
        thread = repo.by_slug(ARCHETYPE_GRAPH, thread_slug)
        if thread is None:
            return _not_found(request, render, "Nie ma takiego posta.")
        return render(
            request,
            "graph_post.html",
            {
                "thread": thread,
                "snapshot": _embed_json(thread.as_dict()),
                "posted": bool(posted),
            },
        )

    @app.post("/g/p/{thread_slug}/reactions")
    def graph_reaction(request: Request, thread_slug: str, kind: str = Form("LIKE")) -> Response:
        thread = repo.by_slug(ARCHETYPE_GRAPH, thread_slug)
        if thread is None:
            return _not_found(request, render, "Nie ma takiego posta.")
        if thread.removed_by_moderation:
            html = render(request, "locked.html", {"thread": thread})
            html.status_code = 403
            return html
        session = state.session(request.cookies.get(SESSION_COOKIE))
        state.record_write(
            WriteAttempt(
                kind="GRAPH_REACTION",
                target=thread.id,
                username=session.username if session else "anon",
                payload={"kind": kind[:40]},
                received_at=time.time(),
            )
        )
        return RedirectResponse(f"{thread.path}?posted=1", status_code=303)

    @app.post("/g/p/{thread_slug}/comments")
    def graph_comment(request: Request, thread_slug: str, body: str = Form(...)) -> Response:
        thread = repo.by_slug(ARCHETYPE_GRAPH, thread_slug)
        if thread is None:
            return _not_found(request, render, "Nie ma takiego posta.")
        if thread.locked or thread.removed_by_moderation:
            html = render(request, "locked.html", {"thread": thread})
            html.status_code = 403
            return html
        session = state.session(request.cookies.get(SESSION_COOKIE))
        state.record_write(
            WriteAttempt(
                kind="GRAPH_COMMENT",
                target=thread.id,
                username=session.username if session else "anon",
                payload={"body": body[:2000]},
                received_at=time.time(),
            )
        )
        return RedirectResponse(f"{thread.path}?posted=1", status_code=303)

    # -----------------------------------------------------------------------
    # Public path — PUBLIC_API_NO_ACCOUNT
    # -----------------------------------------------------------------------

    @app.get("/api/public/")
    def api_index() -> JSONResponse:
        return JSONResponse(
            {
                "seed": settings.seed,
                "access_mode": ACCESS_PUBLIC_API,
                "note": (
                    "Ta ścieżka nie wymaga konta ani sesji. Te same wątki są też widoczne "
                    "pod /c i /g, ale tam obowiązuje login wall."
                ),
                "endpoints": [
                    "/api/public/surfaces",
                    "/api/public/communities",
                    "/api/public/threads",
                    "/api/public/threads/{thread_id}",
                    "/api/public/quality-dimensions",
                    "/api/public/content-hash",
                ],
                "counts": {
                    ARCHETYPE_THREADED: len(repo.for_archetype(ARCHETYPE_THREADED)),
                    ARCHETYPE_GRAPH: len(repo.for_archetype(ARCHETYPE_GRAPH)),
                },
            }
        )

    @app.get("/api/public/surfaces")
    def api_surfaces() -> JSONResponse:
        return JSONResponse(
            {
                "surfaces": [
                    {
                        "surface": "forum",
                        "archetype": ARCHETYPE_THREADED,
                        "path": "/c",
                        "access_mode": ACCESS_ACCOUNT_REQUIRED,
                        "requires_human_paths": ["/login", "/mfa", "/challenge"],
                    },
                    {
                        "surface": "professional-feed",
                        "archetype": ARCHETYPE_GRAPH,
                        "path": "/g",
                        "access_mode": ACCESS_ACCOUNT_REQUIRED,
                        "requires_human_paths": ["/login", "/mfa", "/challenge"],
                    },
                    {
                        "surface": "public-api",
                        "archetype": None,
                        "path": "/api/public/",
                        "access_mode": ACCESS_PUBLIC_API,
                        "requires_human_paths": [],
                    },
                ]
            }
        )

    @app.get("/api/public/communities")
    def api_communities() -> JSONResponse:
        return JSONResponse({"communities": [c.as_dict() for c in repo.communities]})

    @app.get("/api/public/threads")
    def api_threads(
        archetype: str | None = None,
        community: str | None = None,
        sort: str = "new",
        full: int = 0,
    ) -> JSONResponse:
        threads: tuple[Thread, ...] = repo.threads
        if archetype:
            threads = tuple(t for t in threads if t.archetype == archetype)
        if community:
            threads = tuple(t for t in threads if t.community == community)
        ordered = feed_rising(threads) if sort == "rising" else feed_new(threads)
        payload = [t.as_dict() if full else t.as_summary() for t in ordered]
        return JSONResponse({"seed": settings.seed, "sort": sort, "threads": payload})

    @app.get("/api/public/threads/{thread_id}")
    def api_thread(thread_id: str) -> JSONResponse:
        thread = repo.by_id(thread_id)
        if thread is None:
            return JSONResponse({"error": f"nie ma wątku {thread_id!r}"}, status_code=404)
        return JSONResponse(thread.as_dict())

    @app.get("/api/public/quality-dimensions")
    def api_dimensions() -> JSONResponse:
        return JSONResponse(
            {
                "source": "ROADMAP_CODEX_CDP_COMMUNITY_OS.md, sekcja 22.7 (L4436-4446)",
                "dimensions": list(ALL_DIMENSIONS),
                "coverage": coverage(),
            }
        )

    @app.get("/api/public/content-hash")
    def api_content_hash() -> JSONResponse:
        payload = {
            "seed": repo.seed,
            "communities": [c.as_dict() for c in repo.communities],
            "threads": [t.as_dict() for t in repo.threads],
        }
        canonical = _canonical_json(payload)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return JSONResponse(
            {
                "seed": repo.seed,
                "algorithm": "sha256",
                "canonicalization": (
                    "json, sort_keys=True, ensure_ascii=False, separators=(',',':')"
                ),
                "content_length": len(canonical.encode("utf-8")),
                "content_hash": digest,
            }
        )

    # -----------------------------------------------------------------------
    # Pages for the CDP contract test
    # -----------------------------------------------------------------------

    @app.get("/lab", response_class=HTMLResponse)
    def lab_index(request: Request) -> HTMLResponse:
        return render(request, "lab.html", {})

    @app.get("/lab/popup", response_class=HTMLResponse)
    def lab_popup(request: Request, auto: int = 0) -> HTMLResponse:
        return render(request, "popup.html", {"auto": bool(auto)})

    @app.get("/lab/popup-child", response_class=HTMLResponse)
    def lab_popup_child(request: Request) -> HTMLResponse:
        return render(request, "popup_child.html", {})

    @app.get("/lab/delayed", response_class=HTMLResponse)
    def lab_delayed(request: Request, delay_ms: int | None = None) -> HTMLResponse:
        delay = settings.delayed_render_ms if delay_ms is None else max(0, min(delay_ms, 30000))
        return render(request, "delayed.html", {"delay_ms": delay})

    # -----------------------------------------------------------------------
    # Debug endpoints — explicitly for automated tests
    # -----------------------------------------------------------------------

    @app.get("/debug/mfa")
    def debug_mfa() -> JSONResponse:
        """The MFA code for the automated test.

        It exists solely because the fixture is meant to be testable without a human at
        the keyboard. The `/mfa` page itself does NOT show the code — that is the heart
        of the model: headless does not obtain the second factor from the screen.
        """

        return JSONResponse(
            {
                "note": "Endpoint testowy fixture'a. Kod nie występuje w HTML strony /mfa.",
                "pending": [
                    {
                        "token": token,
                        "username": p.username,
                        "code": p.code,
                        "attempts": p.attempts,
                    }
                    for token, p in state.pending.items()
                ],
            }
        )

    @app.get("/debug/writes")
    def debug_writes() -> JSONResponse:
        return JSONResponse(
            {
                "note": (
                    "Zarejestrowane próby zapisu. Fixture ich nie stosuje do treści — "
                    "seed pozostaje niezmienny, więc content_hash się nie zmienia."
                ),
                "count": len(state.writes),
                "writes": [
                    {
                        "kind": outcome.kind,
                        "target": outcome.target,
                        "username": outcome.username,
                        "payload": outcome.payload,
                    }
                    for outcome in state.writes
                ],
            }
        )

    @app.post("/debug/reset")
    def debug_reset() -> JSONResponse:
        state.reset()
        return JSONResponse({"status": "reset"})

    @app.get("/debug/seed")
    def debug_seed() -> JSONResponse:
        return JSONResponse(
            {
                "seed": settings.seed,
                "epoch": "2026-08-30T09:00:00Z",
                "threads": [
                    {
                        "id": t.id,
                        "archetype": t.archetype,
                        "illustrates": list(t.illustrates),
                        "note": t.seed_note,
                        "window_state": t.window_state,
                        "negative_signals": list(t.negative_signals),
                        "hidden_metrics": list(t.metrics.hidden_metrics),
                    }
                    for t in repo.threads
                ],
            }
        )

    return app


def _not_found(request: Request, render: Any, message: str) -> Response:
    response = render(request, "not_found.html", {"message": message})
    response.status_code = 404
    return response


app = create_app()
