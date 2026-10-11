"""Dom Codexa nie może zostać po restarcie z wpisem serwera, który nie da się uruchomić.

ZMIERZONE NA LIVE 21.09, zaraz po dziesiątym wdrożeniu. Job `judge.thread` pilota padł na
„required MCP servers failed to initialize: wenext: handshaking with MCP server failed:
connection closed: initialize response". Przyczyna nie była w pilocie ani w tej rewizji:
`config.toml` WSPÓLNEGO domu Codexa zawierał `[mcp_servers.wenext]` z `required = true` i ze
ścieżką `/tmp/wenext-sessions/<sesja>.json`, zostawioną przez ROZMOWĘ z rana. Katalog
tymczasowy znika przy restarcie kontenera, wpis zostaje, a serwer narzędzi kończy proces przed
odpowiedzią na `initialize`. Każda następna tura w tym domu pada w całości.

Dwie rzeczy zamykają to okno i obie są tu mierzone: tura osądu nie wymaga tego serwera w ogóle
(`test_observe_turns.py`), a start procesu zdejmuje martwe wpisy (ten plik).

Bez bazy: to jest fakt o PLIKACH.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.tools.capability import SessionCapability, issue, write_capability
from control_plane.workspace.codex_home import (
    TOOL_SERVER_SECTION,
    reconcile_home,
    reconcile_homes,
)

pytestmark = [pytest.mark.p2]


def _write_old_entry(home: Path, capability_file: Path) -> None:
    """Zapisz w domu wpis serwera W PLIKU, tak jak robiły to tury sprzed E9."""
    from codex_runtime.hardening import HardeningConfig, McpServerConfig, prepare_codex_home

    prepare_codex_home(
        home,
        HardeningConfig(
            mcp_servers=(
                McpServerConfig(
                    name="wenext",
                    command="/usr/bin/python3",
                    args=("-m", "control_plane.tools.mcp_server"),
                    enabled_tools=("project.read",),
                    env={"WENEXT_SESSION_CAPABILITY_FILE": str(capability_file)},
                ),
            )
        ),
    )


def _home_with_entry(tmp_path: Path, *, expires_in_minutes: int = 30) -> tuple[Path, Path]:
    """Dom Z WPISEM W PLIKU, czyli taki, jaki zostawiały wdrożenia SPRZED E9, plus jego zdolność.

    Od 21.09 tura nie pisze już wpisu do `config.toml`, tylko podaje go procesowi w `--config`
    (`sessions/handlers.py::_turn_overrides`). Sprzątanie nie przestaje być przez to potrzebne:
    na wolumenie leżą domy z wpisami zostawionymi wcześniej i dopóki tam są, wywracają każdą turę
    w swoim domu. Ten test opisuje dokładnie tamten stan, więc pisze plik TYM SAMYM generatorem,
    którym pisała go tura; ręcznie sklejony `config.toml` przechodziłby nawet po zmianie kształtu
    sekcji i niczego by nie pilnował.
    """
    home = tmp_path / "home"
    capability_file = tmp_path / "zdolnosci" / f"{uuid4()}.json"
    capability_file.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    capability = SessionCapability(
        session_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        dsn="postgresql://nieuzywane/x",
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=expires_in_minutes),
    )
    write_capability(capability_file, capability)
    _write_old_entry(home, capability_file)
    return home, capability_file


def test_an_entry_pointing_at_a_file_that_is_gone_is_taken_off(tmp_path: Path) -> None:
    """Dokładnie stan z live: plik zdolności zniknął razem z katalogiem tymczasowym."""
    home, capability_file = _home_with_entry(tmp_path)
    capability_file.unlink()
    before = (home / "config.toml").read_text(encoding="utf-8")
    assert TOOL_SERVER_SECTION in before

    note = reconcile_home(home)

    after = (home / "config.toml").read_text(encoding="utf-8")
    assert note is not None and "nie istnieje" in note, note
    assert TOOL_SERVER_SECTION not in after
    assert "mcp_servers" not in after, after


def test_the_rest_of_the_hardening_survives_the_cleanup(tmp_path: Path) -> None:
    """Zdejmujemy JEDNĄ sekcję, a nie przepisujemy pliku od nowa.

    Przepisanie postawiłoby cały hardening na nowo i skasowałoby wszystko, co ktoś w tym domu
    dostroił ręcznie. Test porównuje z plikiem wygenerowanym BEZ serwera narzędzi, czyli z tym,
    co powinno zostać.
    """
    home, capability_file = _home_with_entry(tmp_path)
    capability_file.unlink()
    reconcile_home(home)
    cleaned = (home / "config.toml").read_text(encoding="utf-8")

    from codex_runtime.hardening import HardeningConfig, render_config_toml

    expected = render_config_toml(HardeningConfig())
    for line in expected.splitlines():
        if line.strip() and not line.strip().startswith("#"):
            assert line in cleaned, f"sprzątanie zabrało linię hardeningu: {line!r}"


def test_a_live_entry_is_left_alone(tmp_path: Path) -> None:
    """Tura, która ten wpis napisała, może właśnie trwać. Sprzątanie jej nie przerywa."""
    home, _ = _home_with_entry(tmp_path, expires_in_minutes=30)

    assert reconcile_home(home) is None
    assert TOOL_SERVER_SECTION in (home / "config.toml").read_text(encoding="utf-8")


def test_an_expired_capability_counts_as_dead_even_when_the_file_is_there(tmp_path: Path) -> None:
    """Sam przeniesienie plików zdolności poza katalog tymczasowy NIE wystarczy.

    Zdolność ma okno ważności rzędu kilkudziesięciu minut, więc po restarcie plik owszem by
    istniał, ale wygasły, a serwer wyszedłby tym samym kodem, tylko z innym zdaniem. Dlatego
    sprzątanie patrzy na WAŻNOŚĆ, a nie na obecność pliku.
    """
    home, _capability_file = _home_with_entry(tmp_path, expires_in_minutes=1)

    note = reconcile_home(home, now=datetime.now(UTC) + timedelta(hours=2))

    assert note is not None and "nie jest ważna" in note, note
    assert TOOL_SERVER_SECTION not in (home / "config.toml").read_text(encoding="utf-8")


def test_a_home_with_no_entry_is_not_rewritten(tmp_path: Path) -> None:
    """Brak wpisu to nie jest powód, żeby dotykać pliku."""
    home = tmp_path / "czysty"
    from codex_runtime.hardening import HardeningConfig, prepare_codex_home

    prepare_codex_home(home, HardeningConfig())
    before = (home / "config.toml").read_text(encoding="utf-8")

    assert reconcile_home(home) is None
    assert (home / "config.toml").read_text(encoding="utf-8") == before


def test_the_root_and_every_connection_home_are_cleaned(tmp_path: Path) -> None:
    """Na wolumenie stoi korzeń z kontem założyciela i katalog na każde połączenie.

    Na live martwy wpis był w OBU: w korzeniu (po rozmowie z rana) i w domu połączenia Wenext
    E2E. Sprzątanie idzie po katalogach, a nie po wierszach w bazie, bo ma działać także wtedy,
    gdy baza jeszcze nie wstała.
    """
    root = tmp_path / "volume"
    root.mkdir()
    capability_file = tmp_path / "znikajaca.json"
    write_capability(
        capability_file,
        issue(
            session_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            dsn="postgresql://nieuzywane/x",
        ),
    )
    _write_old_entry(root, capability_file)
    _write_old_entry(root / "polaczenie-a", capability_file)
    _write_old_entry(root / "polaczenie-b", capability_file)
    capability_file.unlink()

    notes = reconcile_homes(root)

    assert len(notes) == 3, notes
    for home in (root, root / "polaczenie-a", root / "polaczenie-b"):
        assert TOOL_SERVER_SECTION not in (home / "config.toml").read_text(encoding="utf-8")


def test_an_old_entry_is_taken_off_when_its_capability_is_gone(tmp_path: Path) -> None:
    """Niezmiennik sprzątania: wpis W PLIKU żyje dokładnie tyle, co zdolność, na którą wskazuje.

    Od E9 tura takiego wpisu nie zostawia, bo go nie pisze. Zostają jednak domy sprzed tej
    zmiany, a wpis z `required = true` celujący w skasowany plik zdolności wywraca w swoim domu
    KAŻDĄ następną turę, także cudzą. Tak padł na live 21.09 `judge.thread` pilota.
    """
    from control_plane.workspace.codex_home import strip_tool_server

    home, capability_file = _home_with_entry(tmp_path)
    config = home / "config.toml"
    assert TOOL_SERVER_SECTION in config.read_text(encoding="utf-8")

    capability_file.unlink()
    config.write_text(strip_tool_server(config.read_text(encoding="utf-8")), encoding="utf-8")

    text = config.read_text(encoding="utf-8")
    assert TOOL_SERVER_SECTION not in text
    assert 'cli_auth_credentials_store = "file"' in text, (
        "zdjęcie wpisu zabrało resztę konfiguracji; to jest ta linia, przez którą osobny "
        "CODEX_HOME w ogóle cokolwiek izoluje (L2205)"
    )
    assert "shell_tool = false" in text, "zdjęcie wpisu zabrało wyłączone flagi hardeningu"


def test_stripping_a_home_that_has_no_entry_changes_nothing(tmp_path: Path) -> None:
    """Sprzątanie domu, w którym nikt nigdy wpisu nie zostawił, nie ma prawa dotknąć pliku."""
    from codex_runtime.hardening import HardeningConfig, prepare_codex_home
    from control_plane.workspace.codex_home import strip_tool_server

    home = tmp_path / "bez-wpisu"
    prepare_codex_home(home, HardeningConfig())
    before = (home / "config.toml").read_text(encoding="utf-8")

    assert strip_tool_server(before) == before
