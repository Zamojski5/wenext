"""The "agent's browser" card — orchestration over fake capsule and gateway, no Postgres.

Measured here: the status pill from (mode, steer, last session); `open_session` does the capsule
BEFORE the gateway and puts the ticket only in the URL fragment; `confirm_session` takes two
readings, returns the capsule to display-only (never HEADLESS — Reddit refuses it, D-026) and
resumes parked jobs only when the second reading says logged in; every infrastructure failure is a
`CapsuleUnavailable` the route turns into 503, never a half-rendered "fine".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

import httpx
import pytest
from community_observer import CredentialsMissing, SourceUnavailable
from control_plane.observe import capsule_session as cs
from control_plane.observe.capsule_client import (
    CapsuleClient,
    CapsuleConfig,
    CapsuleState,
    CapsuleUnavailable,
    GatewayClient,
    StreamTicket,
)

OPERATOR = UUID("00000000-0000-0000-0000-00000000aaaa")


class FakeCapsule:
    """The capsule's `/state` and `/mode` over `httpx.MockTransport`."""

    def __init__(self, mode: str = "HEADLESS", *, human: bool = False) -> None:
        self.mode = mode
        self.human = human
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if self.fail:
            return httpx.Response(502, json={"error": "mode_switch_failed", "detail": "Xtigervnc"})
        if request.url.path == "/mode":
            self.mode = body["mode"]
            self.human = bool(body.get("reason")) and not body.get("display_only", False)
        return httpx.Response(
            200,
            json={
                "mode": self.mode,
                "reason": "FIRST_LOGIN" if self.human else None,
                "display_only": self.mode == "INTERACTIVE" and not self.human,
                "alive": True,
                "vnc_ready": self.mode == "INTERACTIVE",
                "human_steer_open": self.human,
                "since": "2026-09-04T03:00:00+00:00",
            },
        )

    def client(self) -> CapsuleClient:
        return CapsuleClient(
            "http://capsule:8765",
            token="t",
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )


class FakeGateway:
    def __init__(self, *, open_streams: int = 1) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.open_streams = open_streams

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "nie"})
        if request.url.path == "/stream/revoke":
            revoked, self.open_streams = self.open_streams, 0
            return httpx.Response(200, json={"revoked": revoked})
        return httpx.Response(
            200,
            json={
                "ticket": "BILET-tajny",
                "stream_session_id": "s1",
                "role": "CONTROL",
                "ticket_expires_at": "2026-09-04T03:02:00+00:00",
            },
        )

    def client(self) -> GatewayClient:
        return GatewayClient(
            "http://gateway:8760",
            issuer_token="wystawca",
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )


@dataclass
class _State:
    logged_in: bool
    username: str | None


class FakeProbe:
    def __init__(self, readings: list) -> None:
        self.readings = list(readings)
        self.calls = 0

    async def session_state(self):
        self.calls += 1
        item = self.readings.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# -- the pill ---------------------------------------------------------------------------


def _state(mode: str, *, human: bool = False) -> CapsuleState:
    return CapsuleState(
        mode=mode,
        reason="FIRST_LOGIN" if human else None,
        display_only=mode == "INTERACTIVE" and not human,
        alive=True,
        viewer_ready=mode == "INTERACTIVE",
        human_steer_open=human,
        since=None,
    )


@pytest.mark.parametrize(
    ("state", "session", "expected"),
    [
        (None, None, cs.SIGNED_OUT),
        (_state("HEADLESS"), None, cs.SIGNED_OUT),
        (_state("INTERACTIVE"), None, cs.SIGNED_OUT),
        (_state("INTERACTIVE", human=True), None, cs.AWAITING_HUMAN),
        (_state("INTERACTIVE", human=True), cs.SessionReading(True, "x"), cs.AWAITING_HUMAN),
        (_state("INTERACTIVE"), cs.SessionReading(True, "x"), cs.SIGNED_IN),
        (_state("HEADLESS"), cs.SessionReading(True, "x"), cs.SIGNED_IN),
        (_state("INTERACTIVE"), cs.SessionReading(False, None), cs.SIGNED_OUT),
        (_state("INTERACTIVE"), cs.SessionReading(None, None, "błąd"), cs.SIGNED_OUT),
    ],
)
def test_the_pill_is_one_of_three_values(state, session, expected) -> None:
    assert cs.card_status(state, session) == expected


def test_card_json_exposes_viewer_ready_never_a_token() -> None:
    payload = cs.card_json(
        configured=True,
        state=_state("INTERACTIVE"),
        session=cs.SessionReading(True, "k"),
        error=None,
    )
    assert payload["status"] == cs.SIGNED_IN and payload["account"] == "k"
    assert payload["viewer_ready"] is True and payload["display_only"] is True
    assert payload["href"] is None
    assert "vnc" not in json.dumps(payload).lower(), "pole gotowości nazywa się viewer_ready"
    assert "token" not in json.dumps(payload).lower()


# -- GET /api/capsule ------------------------------------------------------------------------


async def test_card_state_reads_the_capsule_and_never_reddit() -> None:
    capsule = FakeCapsule("INTERACTIVE")
    memory = cs.CardMemory(last_session=cs.SessionReading(True, "Demo_Maintainer"))
    probe = FakeProbe([])
    payload = await cs.card_state(capsule.client(), memory, probe=probe)
    assert payload == {
        "configured": True,
        "status": cs.SIGNED_IN,
        "href": None,
        "account": "Demo_Maintainer",
        "since": "2026-09-04T03:00:00+00:00",
        "mode": "INTERACTIVE",
        "display_only": True,
        "viewer_ready": True,
        "human_steer_open": False,
        "session": {"logged_in": True, "username": "Demo_Maintainer", "error": None},
        "error": None,
    }
    assert probe.calls == 0, "odświeżenie karty nie kosztuje zapytania do Reddita"
    assert [c[:2] for c in capsule.calls] == [("GET", "/state")]


async def test_card_state_with_a_dead_capsule_is_still_a_card() -> None:
    capsule = FakeCapsule()
    capsule.fail = True
    payload = await cs.card_state(capsule.client(), cs.CardMemory(), probe=None)
    assert payload["configured"] is True and payload["status"] == cs.SIGNED_OUT
    assert payload["mode"] is None and "502" in payload["error"]


# -- POST /api/capsule/session ---------------------------------------------------------------


async def test_open_session_switches_the_capsule_first_then_mints_a_control_ticket() -> None:
    capsule, gateway = FakeCapsule("INTERACTIVE"), FakeGateway()
    memory = cs.CardMemory()
    result = await cs.open_session(
        capsule.client(),
        gateway.client(),
        memory,
        operator_id=OPERATOR,
        public_origin="http://127.0.0.1:8760/",
    )
    assert capsule.calls == [("POST", "/mode", {"mode": "INTERACTIVE", "reason": "FIRST_LOGIN"})]
    (issue,) = gateway.requests
    assert issue.url.path == "/browser-sessions"
    assert issue.headers["X-Wenext-Issuer-Token"] == "wystawca"
    body = json.loads(issue.content)
    assert body["role"] == "CONTROL" and body["user_id"] == str(OPERATOR)
    assert body["interaction_lease_id"] and body["interaction_lease_generation"] == 1

    assert result["status"] == cs.AWAITING_HUMAN
    assert result["href"] == "http://127.0.0.1:8760/login-capsule#ticket=BILET-tajny&plg=1&ilg=1"
    assert "?" not in result["href"], "ticket we fragmencie, nie w query stringu (L2343)"
    assert result["capsule"]["human_steer_open"] is True and result["capsule"]["viewer_ready"]
    assert memory.events == ["session_opened"]


async def test_open_session_does_not_mint_a_ticket_when_the_capsule_refuses() -> None:
    capsule, gateway = FakeCapsule(), FakeGateway()
    capsule.fail = True
    with pytest.raises(CapsuleUnavailable, match="502"):
        await cs.open_session(
            capsule.client(),
            gateway.client(),
            cs.CardMemory(),
            operator_id=OPERATOR,
            public_origin="http://127.0.0.1:8760",
        )
    assert gateway.requests == [], "bilet na ekran, który nie wstał, to martwy link"


@pytest.mark.parametrize(
    ("status", "fragment"), [(401, "wystawcy"), (503, "nie wystawia"), (500, "500")]
)
async def test_gateway_failures_are_named(status: int, fragment: str) -> None:
    capsule, gateway = FakeCapsule(), FakeGateway()
    gateway.status = status
    with pytest.raises(CapsuleUnavailable, match=fragment):
        await cs.open_session(
            capsule.client(),
            gateway.client(),
            cs.CardMemory(),
            operator_id=OPERATOR,
            public_origin="http://127.0.0.1:8760",
        )


# -- POST /api/capsule/session/confirm ------------------------------------------------------


async def test_confirm_measures_twice_returns_to_display_only_and_resumes_jobs() -> None:
    capsule = FakeCapsule("INTERACTIVE", human=True)
    probe = FakeProbe([_State(True, "Demo_Maintainer"), _State(True, "Demo_Maintainer")])
    resumed: list[int] = []

    async def resume() -> int:
        resumed.append(1)
        return 7

    memory = cs.CardMemory()
    gateway = FakeGateway(open_streams=2)
    result = await cs.confirm_session(
        capsule.client(), gateway.client(), memory, probe=probe, resume_jobs=resume
    )
    assert capsule.calls == [("POST", "/mode", {"mode": "INTERACTIVE", "display_only": True})], (
        "powrót do display_only, nie do HEADLESS — Reddit odrzuca headless"
    )
    assert probe.calls == 2
    assert result["with_human"]["logged_in"] is True
    assert result["display_only"]["logged_in"] is True
    assert result["status"] == cs.SIGNED_IN and result["account"] == "Demo_Maintainer"
    assert result["resumed_jobs"] == 7 and resumed == [1]
    assert result["capsule"]["human_steer_open"] is False
    assert memory.last_session is not None and memory.last_session.logged_in
    # Review 2026-09-04 item 1: the founder's open WebSocket is cut by the gateway, not left to the
    # tab. Exactly one revoke, with the issuer token, and its count comes back on the card.
    (revoke,) = gateway.requests
    assert revoke.url.path == "/stream/revoke" and revoke.method == "POST"
    assert revoke.headers["X-Wenext-Issuer-Token"] == "wystawca"
    assert result["revoked_streams"] == 2 and gateway.open_streams == 0


async def test_confirm_fails_closed_when_the_gateway_cannot_revoke_the_streams() -> None:
    """A steer that may still be open is not reported as closed: the gateway is asked right after
    the capsule switched, and its failure is the route's 503 — a second click repeats every step."""
    capsule = FakeCapsule("INTERACTIVE", human=True)
    probe = FakeProbe([_State(True, "Demo_Maintainer"), _State(True, "Demo_Maintainer")])
    gateway = FakeGateway()
    gateway.status = 500

    async def resume() -> int:
        raise AssertionError("bez odwołania streamu nic nie wznawiamy")

    with pytest.raises(CapsuleUnavailable, match="/stream/revoke"):
        await cs.confirm_session(
            capsule.client(), gateway.client(), cs.CardMemory(), probe=probe, resume_jobs=resume
        )
    assert [c[:2] for c in capsule.calls] == [("POST", "/mode")], "ster zamknięty przed odwołaniem"
    assert probe.calls == 1, "drugi pomiar dopiero po odwołaniu streamów"


async def test_confirm_without_a_session_resumes_nothing_and_says_signed_out() -> None:
    capsule = FakeCapsule("INTERACTIVE", human=True)
    probe = FakeProbe([CredentialsMissing("nie jest zalogowany"), CredentialsMissing("nadal nie")])

    async def resume() -> int:
        raise AssertionError("bez sesji nic nie wznawiamy")

    result = await cs.confirm_session(
        capsule.client(), FakeGateway().client(), cs.CardMemory(), probe=probe, resume_jobs=resume
    )
    assert result["status"] == cs.SIGNED_OUT and result["account"] is None
    assert result["resumed_jobs"] == 0
    assert (
        result["display_only"]["logged_in"] is False
        and "nadal nie" in result["display_only"]["error"]
    )


async def test_confirm_reports_a_platform_block_as_unknown_not_as_signed_out() -> None:
    capsule = FakeCapsule("INTERACTIVE", human=True)
    probe = FakeProbe([SourceUnavailable("403 blokada"), SourceUnavailable("403 blokada")])

    async def resume() -> int:
        raise AssertionError("nie wznawiamy przy nieznanym stanie")

    result = await cs.confirm_session(
        capsule.client(), FakeGateway().client(), cs.CardMemory(), probe=probe, resume_jobs=resume
    )
    assert result["display_only"]["logged_in"] is None and "403" in result["display_only"]["error"]
    assert result["status"] == cs.SIGNED_OUT and result["resumed_jobs"] == 0


async def test_confirm_without_an_adapter_still_closes_the_steer() -> None:
    capsule = FakeCapsule("INTERACTIVE", human=True)

    async def resume() -> int:
        return 0

    result = await cs.confirm_session(
        capsule.client(), FakeGateway().client(), cs.CardMemory(), probe=None, resume_jobs=resume
    )
    assert capsule.calls[-1][1] == "/mode" and result["capsule"]["human_steer_open"] is False
    assert "nie jest skonfigurowana w tym procesie" in result["display_only"]["error"], (
        "powód ma nazywać PROCES: ten sam produkt potrafi czytać Reddita jednym procesem i nie "
        "mieć czym zapytać drugim"
    )


# -- D-027: the clients address compose services and nothing else ------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "https://oauth.reddit.com",
        "http://www.reddit.com:8765",
        "http://capsule.example.com:8765",
        "http://127.0.0.1:8765",
        "https://capsule:8765",
    ],
)
def test_clients_refuse_a_base_url_outside_the_compose_service_list(base_url: str) -> None:
    with pytest.raises(CapsuleUnavailable, match="D-027"):
        CapsuleClient(base_url, token="t")
    with pytest.raises(CapsuleUnavailable, match="D-027"):
        GatewayClient(base_url, issuer_token="w")


def test_clients_accept_the_two_compose_services_with_or_without_a_slash() -> None:
    assert CapsuleClient("http://capsule:8765/", token="t")._base_url == "http://capsule:8765"
    assert GatewayClient("http://gateway:8760", issuer_token="w")._base_url == "http://gateway:8760"


# -- configuration -----------------------------------------------------------------------------


def test_config_is_opt_in_and_never_defaults_the_reader_url() -> None:
    assert CapsuleConfig.from_env({}) is None
    config = CapsuleConfig.from_env({"WENEXT_BROWSER_READER_URL": "http://capsule:8765"})
    assert config is not None
    assert config.gateway_url == "http://gateway:8760"
    assert config.stream_public_origin == "http://127.0.0.1:8760"
    assert config.reader_token == "" and config.gateway_issuer_token == ""


# -- D-051: the X account's own capsule -----------------------------------------------------------

SHARED = {
    "WENEXT_BROWSER_READER_URL": "http://capsule:8765",
    "WENEXT_BROWSER_READER_TOKEN": "r",
    "WENEXT_GATEWAY_ISSUER_TOKEN": "i",
}


def test_without_an_x_capsule_x_shares_reddits() -> None:
    assert CapsuleConfig.for_platform(SHARED, "X") == CapsuleConfig.from_env(SHARED)


def test_the_x_capsule_has_its_own_reader_screen_and_origin_and_the_same_tokens() -> None:
    env = {**SHARED, "WENEXT_X_BROWSER_READER_URL": "http://capsule-x:8765"}
    x = CapsuleConfig.for_platform(env, "X")
    assert x is not None
    assert x.reader_url == "http://capsule-x:8765"
    assert x.gateway_url == "http://gateway-x:8760"
    assert x.stream_public_origin == "http://127.0.0.1:8761"
    assert (x.reader_token, x.gateway_issuer_token) == ("r", "i")
    # Reddit and the platform-less card keep the capsule they had.
    assert CapsuleConfig.for_platform(env, "REDDIT") == CapsuleConfig.from_env(env)
    assert CapsuleConfig.for_platform(env, None) == CapsuleConfig.from_env(env)


def test_an_x_capsule_alone_configures_x_and_nothing_else() -> None:
    env = {
        "WENEXT_X_BROWSER_READER_URL": "http://capsule-x:8765",
        "WENEXT_BROWSER_READER_TOKEN": "r",
    }
    assert CapsuleConfig.for_platform(env, "X") is not None
    assert CapsuleConfig.for_platform(env, "REDDIT") is None


def test_the_x_capsule_and_its_screen_are_compose_services() -> None:
    assert CapsuleClient("http://capsule-x:8765/", token="t")._base_url == "http://capsule-x:8765"
    assert (
        GatewayClient("http://gateway-x:8760", issuer_token="w")._base_url
        == "http://gateway-x:8760"
    )


def test_the_tool_process_and_the_x_probe_get_the_x_capsule() -> None:
    """The `x.*` tools run in the MCP child process; without the address there they would read X
    through Reddit's capsule, where nobody signed in to X."""
    env = {**SHARED, "WENEXT_X_BROWSER_READER_URL": "http://capsule-x:8765"}
    assert cs.capsule_env(env)["WENEXT_X_BROWSER_READER_URL"] == "http://capsule-x:8765"
    probe = cs.x_probe_from_env(env)
    assert probe is not None and probe._base_url == "http://capsule-x:8765"


def test_the_viewer_url_carries_the_ticket_only_in_the_fragment() -> None:
    ticket = StreamTicket("T", "s", "2026-01-01T00:00:00+00:00", 1, 1)
    url = ticket.viewer_url("http://127.0.0.1:8760")
    assert url.startswith("http://127.0.0.1:8760/login-capsule#ticket=T")
    assert "?" not in url


def test_the_x_capsule_is_an_actuator_host_and_a_gateway_never_is() -> None:
    """27.09: the first X publication failed before anything was sent, because the actuator port
    knew only `capsule`. The X account writes through `capsule-x`; a gateway is never a writer."""
    from control_plane.execution.adapter import XCapsuleOwnPostPort, XCapsuleReplyPort

    XCapsuleReplyPort("http://capsule-x:8765", token="w", expected_account="demo_founder")
    XCapsuleOwnPostPort("http://capsule-x:8765", token="w", expected_account="demo_founder")
    for wrong in ("http://gateway-x:8760", "http://x.com", "https://capsule-x:8765"):
        with pytest.raises(ValueError, match="D-027"):
            XCapsuleReplyPort(wrong, token="w", expected_account="demo_founder")
