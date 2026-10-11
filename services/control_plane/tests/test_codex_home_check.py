"""Kontrola domów Codexa na atrapie katalogów: co naprawdę leży na wolumenie (E9, 21.09).

PO CO. Od dwunastego wdrożenia tura nie pisze `[mcp_servers.wenext]` do wspólnego `config.toml`,
tylko podaje wpis procesowi w `--config`. Zdanie „nowych wpisów nie przybywa" ma być DOWODEM
z dysku, a nie deklaracją, i to jest ta kontrola. Od niej zależy też usunięcie `reconcile_homes`:
wolno je skasować po dwóch wdrożeniach, w których ta kontrola pokazała zero.

Bez bazy i bez Dockera: to jest fakt o plikach, więc atrapą jest katalog.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from control_plane.workspace.home_check import inspect_volume, main, render

pytestmark = [pytest.mark.p2]

_ENTRY = '[mcp_servers.wenext]\ncommand = "/usr/bin/python3"\nenv = { A = "B" }\n'


def _home(root: Path, name: str, *, entry: bool = False, auth: bool = False) -> Path:
    home = root if name == "." else root / name
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        'web_search = "disabled"\n' + (_ENTRY if entry else ""), encoding="utf-8"
    )
    if auth:
        (home / "auth.json").write_text('{"nieczytane": true}', encoding="utf-8")
    return home


def test_the_root_counts_as_a_home_like_any_other(tmp_path: Path) -> None:
    """To w korzeniu leżał wpis, który 21.09 wywrócił turę osądu pilota."""
    _home(tmp_path, ".", entry=True, auth=True)
    _home(tmp_path, "11111111-1111-1111-1111-111111111111")

    homes = inspect_volume(tmp_path)

    assert [one.name for one in homes] == [".", "11111111-1111-1111-1111-111111111111"]
    assert homes[0].has_tool_server_entry is True
    assert homes[0].has_auth is True
    assert homes[1].has_tool_server_entry is False


def test_a_volume_without_entries_exits_zero(tmp_path: Path, capsys) -> None:
    """Zero wpisów to jedyny wynik, po którym wolno myśleć o usunięciu sprzątania."""
    _home(tmp_path, ".", auth=True)
    _home(tmp_path, "22222222-2222-2222-2222-222222222222")

    code = main(["--root", str(tmp_path)])

    assert code == 0
    assert "żaden dom nie ma wpisu" in capsys.readouterr().out


def test_a_single_entry_anywhere_exits_one(tmp_path: Path, capsys) -> None:
    """Jeden wpis w jednym domu wystarczy: wywraca tury w TYM domu, a nie na całym wolumenie."""
    _home(tmp_path, ".")
    _home(tmp_path, "33333333-3333-3333-3333-333333333333", entry=True)

    code = main(["--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 1
    assert "33333333-3333-3333-3333-333333333333" in out
    assert "nie wolno go usuwać" in out


def test_a_missing_volume_is_not_measured_rather_than_clean(tmp_path: Path, capsys) -> None:
    """Brak korzenia to BRAK POMIARU (kod 2), nie „zero wpisów". Ta różnica jest tu regułą."""
    code = main(["--root", str(tmp_path / "nie-ma-takiego")])

    assert code == 2
    assert "brak pomiaru" in capsys.readouterr().err


def test_the_contents_of_auth_json_are_never_read(tmp_path: Path, capsys) -> None:
    """Kontrola mówi, czy logowanie JEST, i nic ponadto: to plik z poświadczeniami."""
    home = _home(tmp_path, ".", auth=True)
    (home / "auth.json").write_text('{"tokens": {"access_token": "SEKRET-DO-NIEPOKAZANIA"}}',
                                    encoding="utf-8")

    main(["--root", str(tmp_path), "--json"])

    captured = capsys.readouterr()
    assert "SEKRET-DO-NIEPOKAZANIA" not in captured.out + captured.err
    assert '"has_auth": true' in captured.out


def test_an_unreadable_config_counts_as_suspicious_not_as_clean(tmp_path: Path) -> None:
    """Nieczytelny plik to nie jest brak wpisu.

    Policzenie go jako czystego dałoby zielony wynik na domu, o którym nic nie wiadomo, a od tego
    wyniku zależy decyzja o usunięciu sprzątania.
    """
    home = _home(tmp_path, ".")
    (home / "config.toml").chmod(0o000)
    try:
        homes = inspect_volume(tmp_path)
    finally:
        (home / "config.toml").chmod(0o600)

    assert homes[0].has_tool_server_entry is True


def test_the_report_names_the_date_of_the_configuration(tmp_path: Path) -> None:
    """Data mówi, czy wpis jest świeży (ktoś pisze dalej), czy stary (zostało po wdrożeniach)."""
    _home(tmp_path, ".", entry=True)

    text = render(inspect_volume(tmp_path))

    assert "config z 20" in text, text
