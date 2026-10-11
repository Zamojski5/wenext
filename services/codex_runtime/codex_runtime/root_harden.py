"""Co hardening zapisałby na KORZENIU codex-home, pokazane przed decyzją (P4 etap B).

    make codex-root-harden PILOT_ARGS="--dry-run"   # tylko pokazuje, nic nie zapisuje
    make codex-root-harden PILOT_ARGS="--apply"     # zapisuje, i tylko przy zatrzymanym pilocie

WEJŚCIE JEST MODUŁEM PAKIETU (`python -m codex_runtime.root_harden`), bo obraz api nie zawiera
katalogu `tools/`: plik z `tools/dev` uruchamiany przez `compose exec` kończy się „can't open
file". Zmierzone na live 19.09 przy `make pilot-to-project`.

PO CO TO ISTNIEJE. Połączenia Codex z P1 etapu 2 dostają własne katalogi z hardeningiem. KORZEŃ
wolumenu `codex-home` to co innego: tam mieszka konto, którym dziś chodzi pilot, i tam pliku
`config.toml` nie ma. Zapisanie go ZMIENI zachowanie pilota w trakcie jego biegu, więc jest to
decyzja założyciela, nie skutek uboczny sprzątania.

TO POLECENIE TEJ DECYZJI NIE PODEJMUJE. Pokazuje dokładnie, co by się zapisało, czym to się różni
od stanu obecnego (brak pliku znaczy: wszystkie flagi WŁĄCZONE, bo mają `default_enabled = true`)
i które z nich zmienią to, co pilot dziś robi. Dopiero na jawne `--apply` zapisuje, i tylko wtedy,
gdy w bazie nie ma biegu pilota w stanie ACTIVE: zmiana konfiguracji pod pracującym pilotem to
zmiana zasad w trakcie gry, której nikt potem nie odtworzy z logów.

DWIE FLAGI, KTÓRE NAPRAWDĘ ZMIENIAJĄ ZACHOWANIE PILOTA, i dlatego są wypisane osobno:

  * `web_search = "disabled"` — pilot traci wyszukiwanie w internecie. Jeśli jego prompt na nim
    polega, jakość odpowiedzi spadnie natychmiast i nie będzie tego widać w żadnym teście.
  * `cli_auth_credentials_store = "file"` — poświadczenia idą do pliku w tym katalogu zamiast do
    wspólnego keyringu. To jest cel hardeningu (L2200-2203), ale wymaga PONOWNEGO ZALOGOWANIA,
    bo token z keyringu nie przeniesie się sam.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import os
import sys
from pathlib import Path

#: Flagi, które zmieniają to, co pilot dziś robi. Wypisywane osobno, bo reszta hardeningu wyłącza
#: rzeczy, których pilot i tak nie używa, a te dwie widać od pierwszej tury.
BEHAVIOUR_CHANGING = (
    (
        "web_search",
        "pilot traci wyszukiwanie w internecie; jeśli prompt na nim polega, jakość spadnie "
        "natychmiast i nie pokaże tego żaden test",
    ),
    (
        "cli_auth_credentials_store",
        "poświadczenia idą do pliku w tym katalogu zamiast do wspólnego keyringu. To jest cel "
        "hardeningu, ale wymaga PONOWNEGO ZALOGOWANIA: token z keyringu nie przeniesie się sam",
    ),
)


def _diff(current: str, proposed: str) -> str:
    lines = list(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile="stan obecny",
            tofile="po hardeningu",
            n=2,
        )
    )
    return "".join(lines) if lines else "(bez zmian)"


async def _pilot_is_running() -> tuple[bool, str]:
    """Czy w bazie jest bieg pilota w stanie ACTIVE.

    Brak DATABASE_URL to NIE jest „pilot nie biegnie": to jest brak pomiaru, i zapis zostaje
    wtedy odmówiony. Zgadywanie w tę stronę kosztuje dokładnie tyle, ile zmiana konfiguracji pod
    pracującym pilotem.
    """
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        return True, "brak DATABASE_URL, więc nie da się sprawdzić, czy pilot biegnie"
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            runs = await conn.fetchval("SELECT count(*) FROM pilot_runs WHERE status = 'ACTIVE'")
            policies = await conn.fetchval(
                "SELECT count(*) FROM publication_policies WHERE status = 'ACTIVE' "
                "AND valid_until > now()"
            )
    finally:
        await pool.close()
    if runs:
        return True, f"w bazie jest {runs} biegów pilota w stanie ACTIVE"
    if policies:
        return True, f"polityka pilota jest wciąż aktywna ({policies}); pilot może ruszyć"
    return False, "brak biegów ACTIVE i brak żywej polityki"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pokaż (a na żądanie zapisz) hardening korzenia codex-home"
    )
    parser.add_argument("--dry-run", action="store_true", help="pokaż i nie zapisuj (domyślne)")
    parser.add_argument("--apply", action="store_true", help="zapisz config.toml na korzeniu")
    parser.add_argument(
        "--root",
        default=os.environ.get("WENEXT_CODEX_HOME", "/var/wenext/codex-home"),
        help="korzeń wolumenu codex-home",
    )
    args = parser.parse_args(argv)

    from .hardening import render_config_toml

    root = Path(args.root)
    target = root / "config.toml"
    current = target.read_text("utf-8") if target.exists() else ""
    proposed = render_config_toml()

    print(f"== korzeń codex-home: {root} ==")
    if current:
        print("   config.toml ISTNIEJE; poniżej różnica wobec proponowanego hardeningu")
    else:
        print("   config.toml NIE ISTNIEJE. Brak pliku znaczy: wszystkie flagi WŁĄCZONE,")
        print("   bo mają default_enabled = true. To jest dzisiejszy stan pilota.")
    print()
    print("== proponowany config.toml ==")
    print(proposed)
    print("== różnica ==")
    print(_diff(current, proposed))

    print("== flagi, które ZMIENIĄ zachowanie pilota ==")
    for key, why in BEHAVIOUR_CHANGING:
        print(f"  {key}:")
        print(f"      {why}")
    print()

    if not args.apply:
        print("== nic nie zapisano ==")
        print('   To polecenie pokazuje decyzję, nie podejmuje jej. Zapis: PILOT_ARGS="--apply",')
        print("   i tylko przy zatrzymanym pilocie.")
        return 0

    running, why = asyncio.run(_pilot_is_running())
    if running:
        print(f"ODMOWA ZAPISU: {why}.", file=sys.stderr)
        print(
            "Zmiana konfiguracji pod pracującym pilotem to zmiana zasad w trakcie gry, "
            "której nikt potem nie odtworzy z logów. Zatrzymaj pilota i powtórz.",
            file=sys.stderr,
        )
        return 1

    root.mkdir(parents=True, exist_ok=True)
    target.write_text(proposed, encoding="utf-8")
    target.chmod(0o600)
    print(f"== zapisano {target} ({why}) ==")
    print("   Pilot przy następnym starcie zobaczy nowe flagi. Jeśli zmienił się store")
    print("   poświadczeń, konto trzeba zalogować ponownie.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
