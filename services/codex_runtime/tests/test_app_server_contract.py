"""The protocol contract on the PINNED binary — the tests run a live app-server.

All of these tests work WITHOUT a ChatGPT account. That is the point: DoD D12 requires the
handshake, the method allowlist and the effective configuration to be verifiable offline.
"""

from __future__ import annotations

import json

import pytest
from codex_runtime import (
    ALLOWED_METHODS,
    EXPLICITLY_FORBIDDEN_METHODS,
    AppServerClient,
    MethodNotAllowed,
    RemoteError,
    load_contract,
    methods_outside_contract,
)

# ---------------------------------------------------------------------------
# The contract as an artifact in the repo
# ---------------------------------------------------------------------------


def test_contract_lives_in_the_repo_and_comes_from_the_pinned_binary():
    contract = load_contract()
    assert contract["wygenerowano_z"]["codex_version"] == "codex-cli 0.147.0"
    assert "openai-codex-cli-bin==0.147.0" in contract["wygenerowano_z"]["pakiet_pypi"]
    assert len(contract["client_requests_stabilne"]) > 50


def test_allowlist_holds_only_methods_present_in_the_pinned_version():
    """The online documentation describes `main`, not our version — we validate against the binary."""
    assert methods_outside_contract(ALLOWED_METHODS) == []


def test_methods_forbidden_by_l2222_are_real_yet_unreachable():
    contract = load_contract()
    known_methods = set(contract["client_requests_stabilne"]) | set(
        contract["client_requests_tylko_experimental"]
    )
    for method in ("thread/shellCommand", "command/exec", "process/spawn", "config/value/write"):
        assert method in known_methods, f"{method} nie istnieje w binarce — test straciłby sens"
        assert method in EXPLICITLY_FORBIDDEN_METHODS
        assert method not in ALLOWED_METHODS


def test_steering_a_running_turn_is_a_stable_method_here():
    """D-046: dopowiedzenie w trakcie pracy. Allowlista rośnie tylko o metodę, która ISTNIEJE
    w przypiętej binarce i jest stabilna, a nie experimental."""
    contract = load_contract()
    assert "turn/steer" in contract["client_requests_stabilne"]
    assert "turn/steer" not in contract["client_requests_tylko_experimental"]
    assert "turn/steer" in ALLOWED_METHODS


def test_the_steer_frame_matches_the_pinned_sdk_shape():
    """Kształt ramki PRZEPISANY z modelu SDK, nie zgadnięty.

    `expectedTurnId` jest tu najważniejsze: to warunek wstępny po stronie serwera, dzięki
    któremu zdanie skierowane do tury, która się skończyła, kończy się błędem zamiast zniknąć.
    Gdyby SDK zmieniło nazwę tego pola, ten test upadnie, zanim upadnie dopowiedzenie na żywo.
    """
    from codex_runtime.session_client import TurnHandle
    from openai_codex.generated.v2_all import TurnSteerParams

    handle = TurnHandle(thread_id="thread-1", turn_id="turn-7")
    frame = {
        "threadId": handle.thread_id,
        "expectedTurnId": handle.turn_id,
        "input": [{"type": "text", "text": "zmień kierunek"}],
    }
    params = TurnSteerParams.model_validate(frame)
    assert params.thread_id == "thread-1"
    assert params.expected_turn_id == "turn-7"


def test_initialized_is_the_only_client_notification():
    assert load_contract()["client_notifications"] == ["initialized"]


# ---------------------------------------------------------------------------
# The handshake and the allowlist on a live process
# ---------------------------------------------------------------------------


def test_handshake_completes_offline_without_login(client: AppServerClient):
    response = client.initialize_response
    assert response["codexHome"] == str(client.codex_home)
    assert response["platformFamily"] in {"unix", "windows"}
    assert client.initialized is True


def test_a_method_before_handshake_is_rejected(codex_bin, codex_home):
    client = AppServerClient(codex_bin=codex_bin, codex_home=codex_home)
    client.start()
    try:
        with pytest.raises(Exception, match="handshake"):
            client.request("model/list", {})
    finally:
        client.close()


@pytest.mark.parametrize("method", sorted(EXPLICITLY_FORBIDDEN_METHODS))
def test_method_outside_the_allowlist_never_reaches_the_wire(client: AppServerClient, method: str):
    with pytest.raises(MethodNotAllowed) as exc:
        client.request(method, {})
    assert "allowlist" in str(exc.value)


def test_shell_and_exec_are_blocked_client_side_not_server_side(client: AppServerClient):
    """THE RESULT OF A RUN, not an assumption — this is the errata to L2222.

    `command/exec` executes a shell command DESPITE `features.shell_tool=false`,
    `features.unified_exec=false` and `experimentalApi=false`. The `[features]` flags govern
    the MODEL's tools, not the JSON-RPC surface. The only boundary is the client allowlist
    (and the OS sandbox). This test pins down both facts at once, so that nobody "simplifies"
    the allowlist in the belief that config.toml is enough.
    """
    with pytest.raises(MethodNotAllowed):
        client.request("command/exec", {"command": ["/bin/echo", "wenext"]})

    raw = client._request_bypassing_allowlist(  # the test deliberately bypasses the boundary
        "command/exec", {"command": ["/bin/echo", "wenext"]}
    )
    assert raw["stdout"].strip() == "wenext", (
        "app-server przestał wykonywać command/exec — errata w NOTES.md wymaga aktualizacji"
    )


def test_experimental_api_false_rejects_an_experimental_method(client: AppServerClient):
    """DoD D12: "the test confirms the rejection of an experimental method".

    `mock/experimentalMethod` exists in the binary solely so that this gate can be checked.
    With `experimentalApi=false` the server answers with the message
    "<method> requires experimentalApi capability".
    """
    assert client.experimental_api is False
    with pytest.raises(RemoteError) as exc:
        client._request_bypassing_allowlist("mock/experimentalMethod", {})
    assert "requires experimentalApi capability" in exc.value.message


def test_experimental_api_true_lets_the_same_method_through(codex_bin, codex_home):
    """A positive control: without it the previous test could pass for any old reason."""
    client = AppServerClient(codex_bin=codex_bin, codex_home=codex_home, experimental_api=True)
    client.start()
    client.initialize()
    try:
        result = client._request_bypassing_allowlist("mock/experimentalMethod", {})
        assert "echoed" in result
    finally:
        client.close()


def test_frames_carry_no_jsonrpc_field(client: AppServerClient, monkeypatch):
    """The protocol omits `jsonrpc` on the wire — a client that sends it breaks the contract."""
    sent_frames: list[dict] = []
    oryginal = client._send

    def capture(frame):
        sent_frames.append(frame)
        oryginal(frame)

    monkeypatch.setattr(client, "_send", capture)
    client.request("model/list", {"includeHidden": False})
    assert sent_frames and all("jsonrpc" not in r for r in sent_frames)


def test_api_key_does_not_leak_into_the_app_server_process(codex_bin, codex_home, monkeypatch):
    """`OPENAI_API_KEY` would change the auth mode — the runtime is on the ChatGPT login (L2119)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-nie-powinno-tu-trafic")
    client = AppServerClient(codex_bin=codex_bin, codex_home=codex_home)
    client.start()
    client.initialize()
    try:
        konto = client.request("account/read", {})
        assert konto.get("account") is None, (
            "app-server zobaczył klucz API i uznał się za zalogowanego kluczem"
        )
    finally:
        client.close()


def test_unknown_method_is_rejected_by_the_server_too(client: AppServerClient):
    with pytest.raises(RemoteError) as exc:
        client._request_bypassing_allowlist("wenext/nieistnieje", {})
    assert "unknown variant" in exc.value.message


def test_contract_schemas_are_complete():
    from codex_runtime.protocol import CONTRACT_DIR

    required = {
        "InitializeParams.json",
        "InitializeResponse.json",
        "ModelListParams.json",
        "ModelListResponse.json",
        "GetAccountResponse.json",
        "GetAccountRateLimitsResponse.json",
        "LoginAccountParams.json",
        "LoginAccountResponse.json",
    }
    present = {p.name for p in CONTRACT_DIR.glob("*.json")}
    assert required <= present
    for nazwa in required:
        json.loads((CONTRACT_DIR / nazwa).read_text(encoding="utf-8"))


def test_the_fake_reads_config_overrides_the_way_the_real_binary_does(tmp_path) -> None:
    """Atrapa ma widzieć `--config`, bo od E9 tura podaje tędy CAŁĄ swoją konfigurację.

    Gdyby czytała dalej sam `config.toml`, testy tur byłyby zielone przy modelu BEZ narzędzi:
    serwer `wenext` nie wstałby w ogóle, a nikt by tego nie zauważył. Zmierzone na binarce
    0.147.0: wartość `--config` jest parsowana jako TOML i nakłada się na plik.
    """
    from codex_runtime.fake_app_server import _config_overrides, effective_config
    from codex_runtime.hardening import HardeningConfig, prepare_codex_home

    home = tmp_path / "home"
    prepare_codex_home(home, HardeningConfig())
    argv = [
        "--config",
        'web_search="live"',
        "--config",
        'mcp_servers.wenext={command="/bin/true",args=[],enabled=true,required=true,'
        'enabled_tools=["project.read"],env={"WENEXT_SESSION_CAPABILITY_FILE"="/tmp/a.json"}}',
        "app-server",
        "--listen",
        "stdio://",
    ]

    overrides = _config_overrides(argv)
    config = effective_config(home, overrides)

    assert len(overrides) == 2
    assert config["web_search"] == "live", "argument ma wygrać z plikiem, tak jak w binarce"
    assert config["mcp_servers"]["wenext"]["env"] == {
        "WENEXT_SESSION_CAPABILITY_FILE": "/tmp/a.json"
    }
    assert config["features"]["shell_tool"] is False, "reszta pliku zostaje nietknięta"


def test_an_override_that_is_not_toml_is_refused_rather_than_ignored(tmp_path) -> None:
    """Cicho zignorowane nadpisanie znaczyłoby turę bez narzędzi, która wygląda na zdrową."""
    import pytest as _pytest
    from codex_runtime.fake_app_server import FakeServerError, effective_config

    with _pytest.raises(FakeServerError):
        effective_config(tmp_path, ("mcp_servers.wenext={command=",))
