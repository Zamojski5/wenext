"""Stream sessions expire, are counted, and can be revoked — review 2026-09-04, item 1.

Before this, an exchanged ticket became a cookie that the gateway honoured for as long as the
process lived: no idle expiry on the server side, no way for the control plane to say "the human is
done", no ceiling on how many could exist. The steer's credentials file disappearing stopped NEW
connections, and nothing stopped the one already open. `StreamSessions` is the registry that makes
the cookie a string the server can stop believing; these tests measure each of its three properties
with a clock passed in, not slept on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from stream_gateway import app as module
from stream_gateway.app import ALLOWED_ORIGIN, SESSION_IDLE_TTL, SESSION_LIMIT, StreamSessions, app

ISSUER_TOKEN = "issuer-test-token"
T0 = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch: pytest.MonkeyPatch):
    module.registry = type(module.registry)()
    module._sessions.clear()
    monkeypatch.setattr(module, "ISSUER_TOKEN", ISSUER_TOKEN)
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, headers={module.ISSUER_TOKEN_HEADER: ISSUER_TOKEN})


def _exchange(client: TestClient) -> str:
    """One full ticket flow; returns the value of the `wenext_stream` cookie."""
    payload = {
        "browser_session_id": str(uuid4()),
        "user_id": str(uuid4()),
        "role": "VIEW_ONLY",
        "profile_lease_generation": 3,
    }
    ticket = client.post("/browser-sessions", json=payload).json()["ticket"]
    response = client.post(
        "/stream/exchange",
        json={"ticket": ticket, "current_profile_lease_generation": 3},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200, response.text
    header = response.headers["set-cookie"]
    return header.split("wenext_stream=", 1)[1].split(";", 1)[0]


# -- the registry alone ----------------------------------------------------------------------


def test_the_ttl_is_at_most_the_length_of_a_human_steer() -> None:
    """The review asked for `≤ czas steru człowieka, np. 15 min odnawiane aktywnością`."""
    assert timedelta(minutes=15) == SESSION_IDLE_TTL
    assert 1 <= SESSION_LIMIT <= 16


def test_an_idle_session_expires_and_activity_renews_it() -> None:
    sessions = StreamSessions(ttl=timedelta(minutes=15), limit=8)
    key = sessions.open("rekord", "csrf", now=T0)

    assert sessions.get(key, now=T0 + timedelta(minutes=15)) == ("rekord", "csrf"), (
        "dokładnie na granicy TTL sesja jeszcze żyje"
    )
    assert sessions.touch(key, now=T0 + timedelta(minutes=14))
    assert sessions.alive(key, now=T0 + timedelta(minutes=28)), "aktywność odnowiła sesję"
    assert not sessions.alive(key, now=T0 + timedelta(minutes=29, seconds=1))
    assert sessions.get(key, now=T0 + timedelta(minutes=30)) is None
    assert len(sessions) == 0, "wygasła sesja jest usunięta, nie tylko niewidoczna"
    assert not sessions.touch(key, now=T0 + timedelta(minutes=30)), "nie da się odnowić zmarłej"


def test_the_limit_evicts_the_least_recently_active_session() -> None:
    sessions = StreamSessions(ttl=timedelta(hours=1), limit=3)
    first = sessions.open("a", "1", now=T0)
    second = sessions.open("b", "2", now=T0 + timedelta(seconds=1))
    third = sessions.open("c", "3", now=T0 + timedelta(seconds=2))
    # `first` is the oldest by opening time but the most recently ACTIVE — activity is what counts.
    sessions.touch(first, now=T0 + timedelta(seconds=3))

    fourth = sessions.open("d", "4", now=T0 + timedelta(seconds=4))

    assert len(sessions) == 3
    assert not sessions.alive(second, now=T0 + timedelta(seconds=5)), "najdłużej bezczynna wypada"
    assert all(sessions.alive(k, now=T0 + timedelta(seconds=5)) for k in (first, third, fourth))


def test_revocation_is_per_key_and_wholesale() -> None:
    sessions = StreamSessions()
    keys = [sessions.open(str(i), "c", now=T0) for i in range(3)]

    assert sessions.revoke(keys[0]) is True
    assert sessions.revoke(keys[0]) is False, "drugie odwołanie tego samego klucza nic nie znajduje"
    assert sessions.revoke_all() == 2
    assert len(sessions) == 0 and sessions.revoke_all() == 0
    assert sessions.get(keys[1], now=T0) is None


def test_no_key_and_an_unknown_key_are_the_same_answer() -> None:
    sessions = StreamSessions()
    sessions.open("r", "c", now=T0)
    assert sessions.get(None, now=T0) is None
    assert sessions.get("nie-ma-takiego", now=T0) is None


# -- through the HTTP layer ----------------------------------------------------------------------


def test_the_cookie_lifetime_equals_the_server_side_ttl(client: TestClient) -> None:
    """The browser is told the same TTL the server enforces — neither side lies to the other."""
    payload = {
        "browser_session_id": str(uuid4()),
        "user_id": str(uuid4()),
        "role": "VIEW_ONLY",
        "profile_lease_generation": 3,
    }
    ticket = client.post("/browser-sessions", json=payload).json()["ticket"]
    response = client.post(
        "/stream/exchange",
        json={"ticket": ticket, "current_profile_lease_generation": 3},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert f"Max-Age={int(SESSION_IDLE_TTL.total_seconds())}" in response.headers["set-cookie"]
    assert len(module._sessions) == 1


def test_revoke_requires_the_issuer_token(client: TestClient) -> None:
    _exchange(client)

    without = TestClient(app).post("/stream/revoke")
    assert without.status_code == 401
    wrong = TestClient(app).post(
        "/stream/revoke", headers={module.ISSUER_TOKEN_HEADER: "nie-ten-token"}
    )
    assert wrong.status_code == 401
    assert len(module._sessions) == 1, "odrzucone żądanie nic nie odwołało"


def test_revoke_without_a_configured_issuer_is_503_not_open(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _exchange(client)
    monkeypatch.setattr(module, "ISSUER_TOKEN", "")
    response = client.post("/stream/revoke")
    assert response.status_code == 503
    assert len(module._sessions) == 1


def test_revoke_cuts_every_session_and_reports_how_many(client: TestClient) -> None:
    key_a = _exchange(client)
    key_b = _exchange(client)
    assert key_a != key_b and len(module._sessions) == 2

    response = client.post("/stream/revoke")

    assert response.status_code == 200
    assert response.json() == {"revoked": 2}
    assert len(module._sessions) == 0
    assert module._sessions.get(key_a, now=datetime.now(UTC)) is None
    assert client.post("/stream/revoke").json() == {"revoked": 0}, "druga runda odwołuje zero"


def test_a_revoked_cookie_no_longer_opens_the_stream(client: TestClient) -> None:
    """The cookie is still in the browser; the server has stopped believing it (close 4401)."""
    key = _exchange(client)
    assert client.post("/stream/revoke").json() == {"revoked": 1}

    client.cookies.set("wenext_stream", key)
    with pytest.raises(Exception), client.websocket_connect("/stream/ws"):  # noqa: B017
        pass


def test_an_expired_cookie_no_longer_opens_the_stream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _exchange(client)
    late = datetime.now(UTC) + SESSION_IDLE_TTL + timedelta(seconds=1)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return late if tz is not None else late.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Frozen)
    assert module._sessions.get(key, now=late) is None
    client.cookies.set("wenext_stream", key)
    with pytest.raises(Exception), client.websocket_connect("/stream/ws"):  # noqa: B017
        pass
