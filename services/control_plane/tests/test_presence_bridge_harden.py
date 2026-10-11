"""Podgląd hardeningu korzenia codex-home i jego odmowa zapisu (P4 etap B).

TO POLECENIE POKAZUJE DECYZJĘ, NIE PODEJMUJE JEJ, i tylko to jest tu mierzone:

  * `--dry-run` niczego nie zapisuje, nawet katalogu. Polecenie, które „przy okazji" zakłada
    katalog, już zmieniło stan, o który pytano.
  * `--apply` ODMAWIA, gdy pilot biegnie albo gdy nie da się sprawdzić, czy biegnie. Brak
     DATABASE_URL to nie jest „pilot nie biegnie", tylko brak pomiaru, a zgadywanie w tę stronę
     kosztuje tyle, ile zmiana konfiguracji pod pracującym pilotem.
  * przy zatrzymanym pilocie `--apply` zapisuje plik 0600.

Dlaczego to w ogóle jest osobna decyzja: korzeń wolumenu `codex-home` trzyma konto, którym dziś
chodzi pilot, i nie ma tam `config.toml`. Brak pliku znaczy, że WSZYSTKIE flagi są włączone, bo
mają `default_enabled = true`. Zapisanie hardeningu wyłącza między innymi `web_search`, co zmienia
jakość odpowiedzi pilota od pierwszej tury i nie pokaże tego żaden test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.db, pytest.mark.p4]

#: Wejście jest MODUŁEM PAKIETU, nie plikiem z `tools/dev`: obraz api nie zawiera katalogu
#: `tools/`, więc polecenie ze ścieżką do pliku pada w kontenerze (zmierzone na live 19.09).
MODULE = "codex_runtime.root_harden"
RUNTIME = Path(__file__).resolve().parents[3] / "services" / "codex_runtime"


def _run(root: Path, *args: str, dsn: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(RUNTIME),
        "WENEXT_CODEX_HOME": str(root),
    }
    if dsn is None:
        env.pop("DATABASE_URL", None)
    else:
        env["DATABASE_URL"] = dsn
    return subprocess.run(
        [sys.executable, "-m", MODULE, "--root", str(root), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_a_dry_run_shows_the_file_and_writes_nothing(tmp_path) -> None:
    root = tmp_path / "codex-home"
    done = _run(root, "--dry-run")

    assert done.returncode == 0, done.stderr
    assert 'web_search = "disabled"' in done.stdout
    assert "NIE ISTNIEJE" in done.stdout
    assert "nic nie zapisano" in done.stdout
    # Ani pliku, ani katalogu: polecenie, które „przy okazji" zakłada katalog, już zmieniło stan.
    assert not root.exists()


def test_the_dry_run_names_the_flags_that_change_the_pilots_behaviour(tmp_path) -> None:
    """Reszta hardeningu wyłącza rzeczy, których pilot nie używa. Te dwie widać od pierwszej
    tury, więc są wypisane osobno, zamiast tonąć w diffie."""
    done = _run(tmp_path / "codex-home", "--dry-run")
    assert "flagi, które ZMIENIĄ zachowanie pilota" in done.stdout
    assert "web_search" in done.stdout
    assert "PONOWNEGO ZALOGOWANIA" in done.stdout


def test_the_diff_against_no_file_shows_every_line_as_added(tmp_path) -> None:
    done = _run(tmp_path / "codex-home", "--dry-run")
    assert "--- stan obecny" in done.stdout
    assert '+web_search = "disabled"' in done.stdout


def test_apply_without_a_database_refuses_rather_than_assuming(tmp_path) -> None:
    root = tmp_path / "codex-home"
    done = _run(root, "--apply")

    assert done.returncode == 1, done.stdout
    assert "ODMOWA ZAPISU" in done.stderr
    assert "nie da się sprawdzić" in done.stderr
    assert not root.exists()


async def test_apply_refuses_while_a_pilot_policy_is_alive(pool, tenant, tmp_path) -> None:
    """Żywa polityka znaczy, że pilot może ruszyć w każdej chwili."""
    async with pool.acquire() as conn:
        community_id = await conn.fetchval(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', $3, 'Reddit', 'ACCOUNT_REQUIRED', 600) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"r/harden-{uuid4().hex[:6]}",
        )
        await conn.execute(
            """
            INSERT INTO publication_policies
                (organization_id, workspace_id, operator_id, status, valid_from, valid_until,
                 community_ids, account_handle, allowed_kinds, min_confidence, max_chars,
                 max_per_hour, max_per_day, judge_prompt_name, judge_prompt_sha256, source_text)
            VALUES ($1, $2, $3, 'ACTIVE', now() - interval '1 hour', now() + interval '2 days',
                    $4, 'Demo_Maintainer', ARRAY['REPLY'], 0.7, 1200, 2, 12, 'judge/v1', $5,
                    'słowa założyciela')
            """,
            tenant["organization"],
            tenant["workspace"],
            tenant["operator"],
            [community_id],
            "c" * 64,
        )

    root = tmp_path / "codex-home"
    done = _run(root, "--apply", dsn=os.environ["WENEXT_TEST_DSN"])
    assert done.returncode == 1, done.stdout
    # DWA POWODY SĄ POPRAWNE i test przyjmuje oba: żywa polityka znaczy, że pilot może ruszyć,
    # a bieg w stanie ACTIVE znaczy, że już biegnie. Asercja na jedno konkretne zdanie
    # przechodziła osobno i padała w pełnej suicie, gdzie inny plik zostawia bieg ACTIVE, choć
    # odmowa za każdym razem była słuszna. Testowana jest ODMOWA, nie sformułowanie.
    assert "ODMOWA ZAPISU" in done.stderr
    assert "pilot" in done.stderr.lower(), done.stderr
    assert not root.exists()


async def test_apply_writes_the_file_when_the_pilot_is_stopped(pool, tenant, tmp_path) -> None:
    """Przy zatrzymanym pilocie zapis przechodzi, a plik ma 0600: poświadczenia idą obok niego.

    Test najpierw ZATRZYMUJE wszystko, co żyje, i to nie jest sprzątanie po innych testach, tylko
    odwzorowanie prawdziwej kolejności: żeby utwardzić korzeń, zatrzymuje się pilota. Sprawdzenie
    w skrypcie jest celowo GLOBALNE, a nie per organizacja, bo korzeń `codex-home` jest jeden dla
    całego wdrożenia: pilot dowolnej organizacji chodzi na tym samym koncie.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_policies SET status = 'STOPPED', stopped_at = now(), "
            "stop_reason = 'zatrzymane przed utwardzeniem korzenia' "
            "WHERE status = 'ACTIVE'"
        )
        await conn.execute("UPDATE pilot_runs SET status = 'FINISHED' WHERE status = 'ACTIVE'")

    root = tmp_path / "codex-home"
    done = _run(root, "--apply", dsn=os.environ["WENEXT_TEST_DSN"])

    assert done.returncode == 0, done.stderr
    target = root / "config.toml"
    assert target.exists()
    assert 'web_search = "disabled"' in target.read_text("utf-8")
    assert oct(target.stat().st_mode)[-3:] == "600"
