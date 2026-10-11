"""Sonda konfiguracji na atrapie app-servera (D-046).

CO TO SPRAWDZA. Atrapa odpowiada na `config/read` tym, co NAPRAWDĘ stoi w `config.toml` jej
CODEX_HOME, więc przejście sondy przez atrapę mierzy całą drogę: profil, generator, plik, odczyt.
Czego nie mierzy i test tego nie udaje: jakie wartości przyjmuje PRAWDZIWA binarka. To
rozstrzyga uruchomienie sondy na żywym runtime i już raz rozstrzygnęło: token `"enabled"`,
który tu stał, nie istnieje w silniku.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest
from codex_runtime.config_probe import main, probe


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    """Shim, który udaje binarkę `codex` i odpala atrapę app-servera."""
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps({"thread_id": "th_probe", "turns": []}, ensure_ascii=False), encoding="utf-8"
    )
    shim = tmp_path / "codex-fake"
    shim.write_text(
        "#!/bin/sh\n"
        f'WENEXT_FAKE_APP_SERVER_SCRIPT="{script}" exec "{sys.executable}" '
        '-m codex_runtime.fake_app_server "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def test_the_probe_reports_the_value_the_runtime_actually_has(tmp_path, fake_bin) -> None:
    result = probe(web_search="live", codex_bin=fake_bin, home=tmp_path / "home")

    assert result["poproszono"] == "live"
    assert result["web_search"] == "live", result
    assert set(result["features"].values()) == {False}, result["features"]
    assert result["niezgodnosci"] == []


def test_the_hardened_profile_reads_back_hardened(tmp_path, fake_bin) -> None:
    result = probe(web_search="disabled", codex_bin=fake_bin, home=tmp_path / "home")

    assert result["web_search"] == "disabled"
    assert result["niezgodnosci"] == []


def test_the_exit_code_carries_the_answer_not_just_the_run(tmp_path, fake_bin, capsys) -> None:
    """Zero znaczy „silnik ma to, o co poproszono". Gdyby kod wyjścia mówił tylko tyle, że proces
    wystartował, pomiar w skrypcie wychodziłby udany zawsze."""
    code = main(["--web-search", "live", "--codex-bin", str(fake_bin), "--json"])
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert printed["web_search"] == "live"


def test_a_binary_that_is_not_there_fails_loudly(tmp_path, capsys) -> None:
    code = main(["--codex-bin", str(tmp_path / "nie-ma-takiej"), "--json"])

    assert code == 2
    assert "sonda konfiguracji" in capsys.readouterr().err


def test_a_second_handshake_is_refused_the_way_the_real_binary_refuses_it(
    tmp_path, fake_bin
) -> None:
    """Ta atrapa musi odmawiać drugiego `initialize`, bo prawdziwy app-server odmawia.

    Sonda w pierwszej wersji wołała `initialize` po `with`, czyli po handshake'u, i na atrapie
    przechodziło to bez słowa. Na żywym kontenerze padło od razu: `-32600 Already initialized`.
    Test pilnuje granicy, o którą się rozbiło: dwa handshake'y to błąd, nie zapas ostrożności.
    """
    from codex_runtime.protocol import AppServerClient, RemoteError

    client = AppServerClient(codex_bin=fake_bin, codex_home=tmp_path / "home")
    with client, pytest.raises(RemoteError) as caught:
        client.initialize()
    assert "Already initialized" in str(caught.value)


def test_config_overrides_reach_the_binary_before_the_subcommand(tmp_path) -> None:
    """KLEJ, a nie sama funkcja: kolejność argumentów decyduje o wszystkim.

    `--config` musi stać PRZED `app-server`, bo to opcja programu, nie podkomendy. Shim recordeduje
    swoje arguments do pliku, więc test czyta to, co naprawdę dostał proces, zamiast ufać
    składaniu listy w pamięci. Zmierzone na binarce 0.147.0 21.09: przy tej kolejności silnik
    zgłasza wpis w `config/read` i uruchamia serwer, przy odwrotnej nie zna opcji.
    """
    from codex_runtime.protocol import AppServerClient

    recorded = tmp_path / "arguments.txt"
    shim = tmp_path / "codex-echo"
    shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{recorded}"\nexit 0\n', encoding="utf-8")
    shim.chmod(0o755)

    client = AppServerClient(
        codex_bin=shim,
        codex_home=tmp_path,
        config_overrides=('mcp_servers.wenext={command="/bin/true"}',),
    )
    client.start()
    # Shim kończy się sam, więc zamknięcie bywa już niepotrzebne; interesują nas jego arguments.
    with contextlib.suppress(Exception):
        client.close()

    arguments = recorded.read_text(encoding="utf-8").splitlines()
    assert arguments[:2] == ["--config", 'mcp_servers.wenext={command="/bin/true"}']
    assert arguments.index("app-server") > arguments.index("--config")


def test_without_overrides_nothing_is_added(tmp_path) -> None:
    """Domyślnie ma być dokładnie tak, jak było: żadnego `--config` w wywołaniu."""
    from codex_runtime.protocol import AppServerClient

    recorded = tmp_path / "arguments.txt"
    shim = tmp_path / "codex-echo"
    shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{recorded}"\nexit 0\n', encoding="utf-8")
    shim.chmod(0o755)

    client = AppServerClient(codex_bin=shim, codex_home=tmp_path)
    client.start()
    with contextlib.suppress(Exception):
        client.close()

    assert recorded.read_text(encoding="utf-8").splitlines() == [
        "app-server",
        "--listen",
        "stdio://",
    ]


def test_the_marker_decides_only_when_the_environment_came_back() -> None:
    """Rozstrzygnięcie sondy stoi na ZNACZNIKU ze środowiska, nie na tym, że serwer się pokazał.

    Serwer z martwą komendą też jest na liście (zmierzone 21.09), więc sama obecność niczego nie
    dowodzi; dowodzi dopiero opis narzędzia z wartością podaną przez mapę `env`.
    """
    from codex_runtime.config_probe import _MARKER, _marker_came_back

    martwy = {
        "wstalo": True,
        "status_serwerow": {"data": [{"name": "wenext", "serverInfo": None, "tools": {}}]},
    }
    zywy = {
        "wstalo": True,
        "status_serwerow": {
            "data": [{"name": "wenext", "tools": {"probe_echo": {"description": _MARKER}}}]
        },
    }

    assert not _marker_came_back(martwy)
    assert not _marker_came_back({"wstalo": False})
    assert _marker_came_back(zywy)
