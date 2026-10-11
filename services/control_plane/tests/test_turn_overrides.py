"""Konfiguracja tury jedzie W ARGUMENTACH PROCESU, a nie w pliku wspólnego domu (E9, 21.09).

CO TU JEST MIERZONE I DLACZEGO AKURAT TO. Dom Codexa należy do POŁĄCZENIA, a wpis serwera
narzędzi do TURY, bo niesie ścieżkę do jej pliku zdolności. Dopóki tura pisała ten wpis do
wspólnego `config.toml`, dwie rozmowy jednej organizacji nadpisywały sobie zdolność nawzajem.
Martwy wpis po turze wywracał każdą następną turę w tym domu i tak padł na live `judge.thread`
pilota; wpis ŻYWY, ale cudzy, byłby gorszy, bo dałby turze narzędzia z cudzym zasięgiem projektu.

Te testy nie potrzebują bazy, bo pytają o dwie rzeczy, które są faktem o argumentach i o pliku:
czy dwie tury niosą osobne zdolności i czy plik został nietknięty. O tym, że binarka te argumenty
przyjmuje i naprawdę z nich startuje serwer, mówi osobny pomiar na żywej binarce
(`codex_runtime.config_probe --mcp-overrides`, przypadek `zestaw-produkcyjny`).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.sessions.handlers import _turn_overrides

pytestmark = [pytest.mark.p2]


def _capability_path(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}-{uuid4().hex[:8]}.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _entry(overrides: tuple[str, ...]) -> str:
    matching = [one for one in overrides if one.startswith("mcp_servers.wenext=")]
    assert len(matching) == 1, f"ma być dokładnie jeden wpis serwera narzędzi, jest {overrides}"
    return matching[0]


def test_two_conversations_carry_two_capabilities_and_neither_sees_the_other(
    tmp_path: Path,
) -> None:
    """DWIE ROZMOWY JEDNEJ ORGANIZACJI NARAZ, i to jest cały powód istnienia tej zmiany."""
    conversation_a = _capability_path(tmp_path, "a")
    conversation_b = _capability_path(tmp_path, "b")

    a = _entry(_turn_overrides(conversation_a))
    b = _entry(_turn_overrides(conversation_b))

    assert str(conversation_a) in a and str(conversation_b) not in a
    assert str(conversation_b) in b and str(conversation_a) not in b


def test_the_shared_home_is_not_touched_at_all(tmp_path: Path) -> None:
    """Plik wspólnego domu ma zostać CO DO BAJTU taki, jaki był: tura go nie dotyka.

    Wcześniej tura przepisywała go w całości (`prepare_codex_home`), a jej `finally` zdejmowało
    z niego sekcję. Oba zapisy szły w plik, w którym w tej samej chwili mogła pracować druga
    rozmowa albo pilot.
    """
    from codex_runtime.hardening import HardeningConfig, prepare_codex_home

    home = tmp_path / "dom"
    prepare_codex_home(home, HardeningConfig())
    config = home / "config.toml"
    before = config.read_bytes()

    _turn_overrides(_capability_path(tmp_path, "tura"))

    assert config.read_bytes() == before
    assert "[mcp_servers.wenext]" not in before.decode("utf-8")


def test_the_overrides_carry_the_whole_configuration_of_the_turn(tmp_path: Path) -> None:
    """Nie sam serwer: `web_search` z profilu sesji i wyłączone flagi też mają jechać argumentami.

    Flagi `[features]` stoją wprawdzie w domu, ale dotąd zerowało je przepisanie pliku przy każdej
    turze. To są flagi, które dają modelowi powłokę, więc po przeniesieniu wpisu na argumenty
    powtarzamy je tam, zamiast ufać, że pliku nikt nie tknął.
    """
    overrides = _turn_overrides(_capability_path(tmp_path, "tura"), web_search="live")

    assert 'web_search="live"' in overrides
    assert "features.shell_tool=false" in overrides
    assert "features.unified_exec=false" in overrides


def test_the_exact_tool_list_travels_with_the_entry(tmp_path: Path) -> None:
    """L2502: dokładna lista narzędzi, nigdy pusta. Po przeprowadzce ma być tam, gdzie wpis."""
    from control_plane.tools import CATALOG

    entry = _entry(_turn_overrides(_capability_path(tmp_path, "tura")))

    assert "enabled_tools=[" in entry
    assert "required=true" in entry, "brak serwera narzędzi ma zatrzymać turę (L2503)"
    for spec in CATALOG:
        assert f'"{spec.name}"' in entry, f"narzędzie {spec.name} wypadło z listy przy przenosinach"
