"""Co leży w domach Codexa na wolumenie: wpisy serwera narzędzi, daty, obecność logowania.

PO CO TO ISTNIEJE (E9, 21.09). Do jedenastego wdrożenia każda tura pisała `[mcp_servers.wenext]`
do `config.toml` domu WSPÓLNEGO dla rozmów i pilota. Od dwunastego tura podaje ten wpis
procesowi w `--config` i do domu nie pisze nic, więc nowych wpisów nie powinno przybywać. Ta
kontrola jest dowodem na to zdanie, a nie deklaracją: przechodzi po wolumenie i mówi, ile wpisów
naprawdę zostało. Po dwóch wdrożeniach z zerem wolno skasować `reconcile_homes`
(`workspace/codex_home.py`), które te wpisy sprząta przy starcie procesu.

CZEGO TA KONTROLA NIE ROBI. Nie czyta `auth.json` ani niczego z jego wnętrza: mówi tylko, czy
plik jest. Nie zagląda do sesji, nie liczy tur i nie pyta bazy; to jest fakt o PLIKACH, mierzony
tam, gdzie te pliki leżą, czyli w kontenerze `api`:

    make codex-home-check

Kod wyjścia: 0 gdy żaden dom nie ma wpisu serwera narzędzi, 1 gdy choć jeden ma (jest co
sprzątać), 2 gdy nie było czego zmierzyć (brak korzenia wolumenu). Dwójka to „nie zmierzono", a
nie „zmierzono zero": ta różnica jest w tym repozytorium regułą, nie ozdobą.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from control_plane.workspace.codex_home import (
    HOME_ROOT_VARIABLE,
    ROOT_REF,
    TOOL_SERVER_SECTION,
    home_root,
)


@dataclass(frozen=True)
class HomeFacts:
    """Jeden dom, same fakty z dysku. Żadnego pola, którego nie da się zobaczyć w katalogu."""

    name: str
    has_config: bool
    has_tool_server_entry: bool
    config_modified_at: str | None
    has_auth: bool


def _modified_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    except OSError:
        return None


def inspect_home(path: Path, *, name: str) -> HomeFacts:
    config = path / "config.toml"
    text = ""
    if config.is_file():
        try:
            text = config.read_text(encoding="utf-8")
        except OSError:
            # Nieczytelny plik to NIE jest brak wpisu. Mówimy „jest config, nie wiadomo co w nim",
            # zamiast policzyć taki dom jako czysty.
            return HomeFacts(
                name=name,
                has_config=True,
                has_tool_server_entry=True,
                config_modified_at=_modified_at(config),
                has_auth=(path / "auth.json").is_file(),
            )
    return HomeFacts(
        name=name,
        has_config=config.is_file(),
        has_tool_server_entry=TOOL_SERVER_SECTION in text,
        config_modified_at=_modified_at(config) if config.is_file() else None,
        has_auth=(path / "auth.json").is_file(),
    )


def inspect_volume(root: Path) -> list[HomeFacts]:
    """Korzeń wolumenu i każdy katalog połączenia pod nim, w kolejności alfabetycznej.

    Korzeń jest domem założyciela, na którym stoi pilot, więc liczy się tak samo jak reszta:
    to w nim leżał wpis, który 21.09 wywrócił turę osądu.
    """
    if not root.is_dir():
        return []
    homes = [inspect_home(root, name=ROOT_REF)]
    homes += [
        inspect_home(one, name=one.name) for one in sorted(root.iterdir()) if one.is_dir()
    ]
    return homes


def render(homes: list[HomeFacts]) -> str:
    lines = [f"domów: {len(homes)}"]
    for home in homes:
        entry = "WPIS SERWERA" if home.has_tool_server_entry else "bez wpisu"
        if not home.has_config:
            config = "bez config.toml"
        else:
            config = f"config z {home.config_modified_at or 'datą nie do odczytania'}"
        auth = "z logowaniem" if home.has_auth else "bez auth.json"
        lines.append(f"  {home.name}: {entry}, {config}, {auth}")
    with_entry = [home.name for home in homes if home.has_tool_server_entry]
    if with_entry:
        lines.append(
            f"wpisów serwera narzędzi: {len(with_entry)}, w domach: {', '.join(with_entry)}. "
            "Sprząta je `reconcile_homes` przy starcie procesu; dopóki są, nie wolno go usuwać"
        )
    else:
        lines.append(
            "żaden dom nie ma wpisu serwera narzędzi: tura podaje go w argumentach procesu "
            "(E9). Dwa takie przebiegi pod rząd są warunkiem usunięcia `reconcile_homes`"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=f"korzeń wolumenu; domyślnie ze zmiennej {HOME_ROOT_VARIABLE}",
    )
    parser.add_argument("--json", action="store_true", help="wypisz fakty jako JSON")
    args = parser.parse_args(argv)

    root = args.root or home_root()
    if root is None:
        print(
            f"kontrola domów: brak {HOME_ROOT_VARIABLE} i brak --root, nie ma czego obejrzeć. "
            "To jest brak pomiaru, nie wynik zero",
            file=sys.stderr,
            flush=True,
        )
        return 2
    if not Path(root).is_dir():
        print(
            f"kontrola domów: {root} nie jest katalogiem. To jest brak pomiaru, nie wynik zero",
            file=sys.stderr,
            flush=True,
        )
        return 2

    homes = inspect_volume(Path(root))
    if args.json:
        print(json.dumps([asdict(home) for home in homes], ensure_ascii=False, indent=2))
    else:
        print(render(homes))
    return 1 if any(home.has_tool_server_entry for home in homes) else 0


if __name__ == "__main__":  # pragma: no cover - wejście modułu
    raise SystemExit(main())
