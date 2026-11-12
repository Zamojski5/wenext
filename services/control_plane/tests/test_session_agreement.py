"""The reader and the writer of a platform agree on whether the browser is logged in (13.10.2026).

THE INCIDENT. `x.read` answered "the browser has no session of @demo_founder" for hours while the
actuator published replies from that account; the card said `nieznana` ("the browser did not answer
in time") in the same minutes; the Reddit card said `polaczone` while the Reddit actuator refused with
"the page does not show a logged-in account". One question, four answers.

WHAT IS HELD HERE, without a database (the half that needs one is in `test_x_tools.py`):

  * `x.read` and `x.search` tell a login WALL (Reconnect, `SESJA_UTRACONA`) from a look that could not
    tell (retry, `SESJA_NIEPEWNA`, no card, no Reconnect hint), and the second is never cached;
  * when the look could not tell but the actuator published as the account a few minutes ago, the
    read goes on (the writer vouches), and the page's own viewer is held to the account afterwards;
  * an anonymous read does not depend on the session at all;
  * the card's dot follows the same rule for X and for Reddit: an unknown reader does not contradict
    a writer that published, a definitive reader is never overridden by an older proof.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from community_observer.x_browser import XPage, XPost, XSessionState
from community_os_contracts.session_verdict import WRITER_PROOF_MAX_AGE_S
from control_plane.tools import ToolRegistry, issue
from control_plane.tools import card_connect as cc
from control_plane.tools.card_actions import (
    SESSION_HINT,
    SESSION_LOST_CODE,
    SESSION_UNKNOWN_CODE,
    SESSION_UNKNOWN_HINT,
    with_scope_hint,
)
from control_plane.tools.card_connect import (
    HEALTH_CONNECTED,
    HEALTH_LOST,
    HEALTH_UNKNOWN,
    Reading,
    health_of,
)

HANDLE = "demo_founder"
POST_URL = "https://x.com/jack/status/2103587050346250341"
LONG_DASHES = {chr(0x2013), chr(0x2014)}


def _now() -> datetime:
    return datetime.now(UTC)


def _timeout_state() -> XSessionState:
    return XSessionState(
        False, None, _now(), error="czytnik kapsuły nie odpowiedział w czasie", failure="TIMEOUT"
    )


def _unread_state() -> XSessionState:
    return XSessionState(
        False,
        None,
        _now(),
        error="Sesja jest, ale nie odczytano uchwytu konta",
        failure="UNREADABLE",
    )


def _wall_state() -> XSessionState:
    return XSessionState(False, None, _now(), error="ekran logowania", login_wall=True)


@dataclass
class _Source:
    """The X source: the identity it was scripted with, one page, a record of every read."""

    identity: XSessionState
    viewer: str | None = HANDLE
    identity_reads: int = 0
    reads: list[tuple[str, bool]] = field(default_factory=list)

    async def verify_identity(self, expected: str) -> XSessionState:
        self.identity_reads += 1
        return self.identity

    async def read(self, url: str, *, anonymous: bool = False) -> XPage:
        self.reads.append((url, anonymous))
        return self._page(anonymous)

    async def search(self, query: str, *, mode: str = "live") -> XPage:
        self.reads.append((f"search:{query}:{mode}", False))
        return self._page(False)

    def _page(self, anonymous: bool) -> XPage:
        post = XPost("1", "jack", "Jack", "the question", "post", POST_URL, None, "focal")
        return XPage(
            requested_url=POST_URL,
            final_url=POST_URL,
            capability="post",
            anonymous=anonymous,
            viewer=None if anonymous else self.viewer,
            login_wall=False,
            logged_in=not anonymous and self.viewer is not None,
            unavailable=None,
            posts=(post,),
            profile=None,
            text="",
            limitations=("Odczytano 1 post.",),
        )

    async def aclose(self) -> None:
        return None


def _registry(monkeypatch, source: _Source, *, proof_age: float | None) -> ToolRegistry:
    """A registry with no database: the account, the browser source and the writer's proof are
    scripted, the rest is the real code."""
    registry = ToolRegistry(
        capability=issue(
            session_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            dsn="postgresql://atrapa/nieuzywane",
            host="codex",
        ),
        pool=None,  # type: ignore[arg-type]
    )

    async def account() -> dict[str, Any]:
        return {"id": uuid4(), "handle": HANDLE, "state": "READY"}

    async def proof(pool, *, organization_id, platform, handle) -> float | None:
        assert platform == "X" and handle == HANDLE
        return proof_age

    registry._x_account = account  # type: ignore[method-assign]
    monkeypatch.setattr(
        "control_plane.observe.capsule_session.x_probe_from_env", lambda env=None: source
    )
    monkeypatch.setattr("control_plane.workspace.account_state.writer_proof_age_s", proof)
    return registry


# ---------------------------------------------------------------------------
# x.read and x.search: a wall is a lost session, a look that could not tell is not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unknown", [_timeout_state, _unread_state])
async def test_a_look_that_could_not_tell_is_not_a_lost_session(monkeypatch, unknown) -> None:
    """THE INCIDENT: the capsule did not answer in time, and the agent was told the browser had no
    session. Now: its own code, no card, no Reconnect, and a hint that says to retry."""
    source = _Source(unknown())
    registry = _registry(monkeypatch, source, proof_age=None)

    for call in (
        lambda: registry._x_read({"url": POST_URL}),
        lambda: registry._x_search({"query": "agent approvals"}),
    ):
        result = await call()
        assert result["stan"] == "NIEDOSTEPNE", result
        assert result["kod"] == SESSION_UNKNOWN_CODE and result["kod"] != SESSION_LOST_CODE
        assert "karty" not in result, "no login card: nobody has to log in"
        assert "nie jest wylogowanie" in result["wskazowka"]
        assert "nie proś człowieka o logowanie" in result["wskazowka"].lower()
        assert "nie udało się teraz sprawdzić sesji" in result["powod"]
        assert "brak sesji" not in result["powod"] and "nie ma teraz sesji" not in result["powod"]
        # The Reconnect hint is added for SESSION_LOST_CODE only: here nothing is added.
        assert with_scope_hint("x.read", result) == result
        assert SESSION_HINT not in result["wskazowka"]
    assert source.reads == [], "nothing is read through a browser nobody could vouch for"


async def test_an_unknown_look_is_asked_again_by_the_next_call(monkeypatch) -> None:
    """Never cached: a second call is a second look (the source already retried inside the first)."""
    source = _Source(_timeout_state())
    registry = _registry(monkeypatch, source, proof_age=None)
    await registry._x_read({"url": POST_URL})
    await registry._x_read({"url": POST_URL})
    assert source.identity_reads == 2

    # ... and a verdict that IS an answer is believed, as before: once per turn.
    good = _Source(XSessionState(True, HANDLE, _now()))
    registry = _registry(monkeypatch, good, proof_age=None)
    await registry._x_read({"url": POST_URL})
    await registry._x_search({"query": "q"})
    assert good.identity_reads == 1


async def test_a_login_wall_is_still_a_lost_session_with_the_card_and_the_code(monkeypatch) -> None:
    source = _Source(_wall_state())
    registry = _registry(monkeypatch, source, proof_age=30.0)  # even a fresh proof: the wall is NOW

    result = await registry._x_read({"url": POST_URL})

    assert result["stan"] == "NIEDOSTEPNE" and result["kod"] == SESSION_LOST_CODE
    assert [one["nazwa"] for one in result["karty"][0]["akcje"]] == ["polacz", "sprawdz"]
    assert SESSION_HINT in with_scope_hint("x.read", result)["wskazowka"]
    assert source.reads == []


async def test_the_writer_vouches_when_the_reader_could_not_tell(monkeypatch) -> None:
    """The actuator published as the account 2 minutes ago: the read goes on, and says so."""
    source = _Source(_timeout_state(), viewer=HANDLE)
    registry = _registry(monkeypatch, source, proof_age=120.0)

    result = await registry._x_read({"url": POST_URL})

    assert result["stan"] == "WYKONANE", result
    assert source.reads == [(POST_URL, False)]
    assert result["strona"]["konto_czytajace"] == HANDLE
    assert "Odczytano 1 post." in result["ograniczenia"]
    assert any("publikowało 2 min temu" in line for line in result["ograniczenia"]), result
    # The search takes the same road.
    again = await registry._x_search({"query": "agent approvals"})
    assert again["stan"] == "WYKONANE"


async def test_a_vouched_read_is_held_to_the_account_after_the_fact(monkeypatch) -> None:
    """The proof lets the read go on without a separate identity check, so the page's own viewer is
    the evidence of whose bubble it was: a different account is refused exactly as it would have
    been before the read."""
    source = _Source(_timeout_state(), viewer="SomeoneElse")
    registry = _registry(monkeypatch, source, proof_age=120.0)

    result = await registry._x_read({"url": POST_URL})

    assert result["stan"] == "NIEDOSTEPNE" and result["kod"] == SESSION_LOST_CODE
    assert "SomeoneElse" in result["powod"] and "posty" not in result


async def test_a_proof_older_than_a_quarter_of_an_hour_vouches_for_nothing(monkeypatch) -> None:
    source = _Source(_timeout_state())
    registry = _registry(monkeypatch, source, proof_age=WRITER_PROOF_MAX_AGE_S + 1)

    result = await registry._x_read({"url": POST_URL})

    assert result["kod"] == SESSION_UNKNOWN_CODE and source.reads == []


async def test_an_anonymous_read_does_not_depend_on_the_session(monkeypatch) -> None:
    """`as_visitor` reads in a fresh context with no cookies: whether the agent's browser is signed
    in is not its question, so a session nobody could check cannot stop it."""
    source = _Source(_timeout_state())
    registry = _registry(monkeypatch, source, proof_age=None)

    result = await registry._x_read({"url": POST_URL, "as_visitor": True})

    assert result["stan"] == "WYKONANE", result
    assert source.identity_reads == 0
    assert source.reads == [(POST_URL, True)]
    assert result["strona"]["jako_gosc"] is True


# ---------------------------------------------------------------------------
# The card: the same rule for X and for Reddit
# ---------------------------------------------------------------------------


def test_the_dot_follows_the_verdict_a_wall_is_lost_a_timeout_is_unknown() -> None:
    assert cc.reading_of_x(_wall_state(), 1.0).logged_in is False
    assert cc.reading_of_x(_timeout_state(), 1.0).logged_in is None
    assert cc.reading_of_x(_unread_state(), 1.0).logged_in is None
    assert cc.reading_of_x(XSessionState(True, HANDLE, _now()), 1.0).logged_in is True
    wall = cc.reading_of_x(_wall_state(), 1.0)
    timeout = cc.reading_of_x(_timeout_state(), 1.0)
    assert health_of("READY", HANDLE, wall)[0] == HEALTH_LOST
    assert health_of("READY", HANDLE, timeout)[0] == HEALTH_UNKNOWN


@pytest.mark.parametrize("platform", ["X", "REDDIT"])
async def test_an_unknown_reader_does_not_contradict_a_writer_that_published(
    monkeypatch, platform: str
) -> None:
    registry = ToolRegistry(
        capability=issue(
            session_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            dsn="postgresql://atrapa/nieuzywane",
            host="codex",
        ),
        pool=None,  # type: ignore[arg-type]
    )
    ages: dict[str, float | None] = {"value": 120.0}

    async def proof(pool, *, organization_id, platform, handle) -> float | None:
        return ages["value"]

    monkeypatch.setattr(cc, "writer_proof_age_s", proof)
    unknown = Reading(None, None, "przeglądarka agenta nie odpowiedziała na czas", 5.0)

    vouched = await registry._vouched_by_writer(platform, HANDLE, unknown)
    assert vouched.logged_in is True and vouched.handle == HANDLE and vouched.error is None
    assert health_of("READY", HANDLE, vouched)[0] == HEALTH_CONNECTED

    # No proof, or a proof that has gone stale: nothing is claimed.
    ages["value"] = None
    assert (await registry._vouched_by_writer(platform, HANDLE, unknown)) is unknown
    ages["value"] = WRITER_PROOF_MAX_AGE_S + 1
    assert (await registry._vouched_by_writer(platform, HANDLE, unknown)) is unknown

    # A definitive reading is never overridden, whatever the proof says: the reader's wall is
    # younger than any proof, and an account seen is the answer already.
    ages["value"] = 10.0
    wall = Reading(False, None, "ekran logowania", 5.0)
    seen = Reading(True, HANDLE, None, 5.0)
    assert (await registry._vouched_by_writer(platform, HANDLE, wall)) is wall
    assert (await registry._vouched_by_writer(platform, HANDLE, seen)) is seen


def test_the_card_for_an_unknown_session_only_offers_to_check_again() -> None:
    registry = ToolRegistry(
        capability=issue(
            session_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            dsn="postgresql://atrapa/nieuzywane",
            host="codex",
        ),
        pool=None,  # type: ignore[arg-type]
    )
    account = {"konto": HANDLE, "profil_osoby": None}
    unknown = registry._x_status_card(account, _timeout_state())
    assert [one["nazwa"] for one in unknown["akcje"]] == ["sprawdz"]
    assert (
        unknown["sesja"] == "nieznana" and "nie odpowiedział w czasie" in unknown["odczyt_kapsuly"]
    )
    lost = registry._x_status_card(account, _wall_state())
    assert [one["nazwa"] for one in lost["akcje"]] == ["polacz", "sprawdz"]
    assert "sesja" not in lost
    nothing = registry._x_status_card(account, None)
    assert [one["nazwa"] for one in nothing["akcje"]] == ["sprawdz"]


def test_what_we_tell_the_agent_about_an_unknown_session_has_no_long_dashes() -> None:
    assert not LONG_DASHES & set(SESSION_UNKNOWN_HINT)
    assert SESSION_UNKNOWN_CODE not in {SESSION_LOST_CODE}
    assert "nie proś człowieka o logowanie" in SESSION_UNKNOWN_HINT.lower()


# ---------------------------------------------------------------------------
# Reddit: the same three answers, the same single retry
# ---------------------------------------------------------------------------


def _transport_error() -> Exception:
    import httpx
    from community_observer.source import SourceUnavailable

    error = SourceUnavailable("czytnik kapsuły nieosiągalny (ReadTimeout) pod http://capsule:8765")
    error.__cause__ = httpx.ReadTimeout("slow")
    return error


@dataclass
class _RedditProbe:
    script: list[Any]
    calls: int = 0

    async def session_state(self) -> Any:
        answer = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_a_reddit_session_read_retries_a_transport_failure_once() -> None:
    from control_plane.observe.capsule_session import read_session

    ok = SimpleNamespace(logged_in=True, username="Demo_Maintainer")
    settled = _RedditProbe([_transport_error(), ok])
    reading = await read_session(settled, pause_s=0.0)
    assert settled.calls == 2 and reading.logged_in is True
    assert reading.username == "Demo_Maintainer"

    hung = _RedditProbe([_transport_error(), _transport_error(), ok])
    reading = await read_session(hung, pause_s=0.0)
    assert hung.calls == 2, "one retry, then the answer is what it is"
    assert reading.logged_in is None and "nieosiągalny" in (reading.error or "")


async def test_a_reddit_refusal_or_a_wall_is_not_repeated_and_keeps_its_meaning() -> None:
    from community_observer.source import CredentialsMissing, SourceUnavailable
    from control_plane.observe.capsule_session import read_session

    refused = _RedditProbe([SourceUnavailable("Reddit odpowiedział 429 na /api/me.json")])
    reading = await read_session(refused, pause_s=0.0)
    assert refused.calls == 1 and reading.logged_in is None, "a 429 is a request to stop"

    wall = _RedditProbe([CredentialsMissing("Reddit przekierował na stronę logowania")])
    reading = await read_session(wall, pause_s=0.0)
    assert wall.calls == 1 and reading.logged_in is False, "a wall is an answer"

    assert (await read_session(None)).logged_in is None
    once = _RedditProbe([_transport_error(), SimpleNamespace(logged_in=True, username="u")])
    assert (await read_session(once, retries=0)).logged_in is None and once.calls == 1
