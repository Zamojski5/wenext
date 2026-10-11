"""`make codex-first-live-check` na atrapie silnika (D-046).

PO CO TESTOWAĆ POLECENIE, KTÓRE I TAK URUCHAMIA CZŁOWIEK. Bo uruchamia je RANO, jeden raz, na
żywym koncie, i wtedy nie ma czasu na debugowanie literówki w zapytaniu o projekt. Ten test
przechodzi całą drogę polecenia poza jednym miejscem: silnikiem, którym jest tu atrapa.

CZEGO NIE DOWODZI: że na prawdziwym Codeksie model zawoła sensowne narzędzia. Dowodzi, że gdy
je zawoła, polecenie policzy karty, postęp i publikacje, i powie prawdę o wyniku.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.sessions.codex_first_livecheck import _run

pytestmark = [pytest.mark.db, pytest.mark.p2]


@pytest.fixture
def dsn() -> str:
    """Ten sam adres bazy, z którego korzysta suita. Polecenie czyta go z DATABASE_URL, bo w
    kontenerze api to jedyne miejsce, w którym on stoi."""
    import os

    value = os.environ.get("WENEXT_TEST_DSN")
    if not value:
        pytest.fail("brak WENEXT_TEST_DSN: ten test wymaga bazy")
    return value


class _Args:
    """To, co dałby argparse. Osobno, bo `main()` woła `asyncio.run`, a test już ma pętlę."""

    def __init__(self, **fields):
        self.goal = "Sprawdź, gdzie warto promować ten projekt."
        self.organization = None
        self.project = None
        self.connection = None
        self.minutes = 2
        self.tool_calls = 20
        for key, value in fields.items():
            setattr(self, key, value)


def _shim(tmp_path: Path) -> Path:
    shim = tmp_path / "codex-fake"
    shim.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m codex_runtime.fake_app_server "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def _script(tmp_path: Path, steps: list[dict]) -> Path:
    path = tmp_path / f"script-{uuid4().hex[:8]}.json"
    path.write_text(
        json.dumps({"thread_id": "th_live", "turns": [{"steps": steps, "status": "completed"}]}),
        encoding="utf-8",
    )
    return path


async def _world(pool, tenant, tmp_path, monkeypatch, *, steps: list[dict]) -> dict:
    """Organizacja, projekt, połączenie i atrapa silnika, czyli wszystko, czego cel wymaga."""
    # `home_ref` to KROPKA albo UUID (CHECK w 0014): katalog połączenia nazywa się jak jego
    # identyfikator, żeby nie dało się wskazać nim czegokolwiek innego w wolumenie.
    home_ref = str(uuid4())
    home_root = tmp_path / "codex-home"
    (home_root / home_ref).mkdir(parents=True)
    monkeypatch.setenv("WENEXT_CODEX_HOME", str(home_root))
    monkeypatch.setenv("WENEXT_CODEX_BIN", str(_shim(tmp_path)))
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(_script(tmp_path, steps)))

    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel projektu') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            "Obecność na Reddicie",
        )
        await conn.execute(
            "INSERT INTO codex_connections (organization_id, label, home_ref, status) "
            "VALUES ($1, 'konto testowe', $2, 'CONNECTED')",
            tenant["organization"],
            home_ref,
        )
    return {"project": str(project_id)}


async def test_the_check_reports_cards_progress_and_zero_publications(
    pool, tenant, tmp_path, monkeypatch, capsys, dsn
) -> None:
    monkeypatch.setenv("DATABASE_URL", dsn)
    world = await _world(
        pool,
        tenant,
        tmp_path,
        monkeypatch,
        steps=[
            {"tool": "accounts.status", "arguments": {}},
            {"tool": "communities.observe", "arguments": {"community": "r/SaaS"}},
            {"message": "Zacząłbym tutaj."},
        ],
    )

    code = await _run(_Args(organization=str(tenant["organization"]), project=world["project"]))
    printed = capsys.readouterr()

    assert code == 0, printed.out + printed.err
    assert "ZALICZONE" in printed.out
    body = printed.out.split("== wynik ==\n", 1)[1].split("\n== ", 1)[0]
    measured = json.loads(body)
    assert measured["opublikowano"] == 0, "ten pomiar nie ma prawa niczego wysłać"
    assert measured["wnioski_publikacyjne_po"] == 0
    assert "communities.observe" in measured["narzedzia"]
    assert {one["rodzaj"] for one in measured["karty"]} >= {"SPOLECZNOSC"}, measured["karty"]
    assert measured["postep"]["licznik"]["wszystkie"] >= 1
    assert measured["niezgodnosci_konfiguracji"] == []


async def test_a_turn_without_a_single_tool_is_not_passed(
    pool, tenant, tmp_path, monkeypatch, capsys, dsn
) -> None:
    """Rozmowa bez narzędzi to zwykła tura modelu. Cel ma to nazwać, a nie zaliczyć."""
    monkeypatch.setenv("DATABASE_URL", dsn)
    world = await _world(
        pool, tenant, tmp_path, monkeypatch, steps=[{"message": "Nic nie sprawdzałem."}]
    )

    code = await _run(_Args(organization=str(tenant["organization"]), project=world["project"]))
    printed = capsys.readouterr()

    assert code == 1, printed.out
    assert "nie zawołał ANI JEDNEGO narzędzia" in printed.err


async def test_a_missing_project_says_what_to_do_instead_of_creating_one(
    pool, tenant, tmp_path, monkeypatch, capsys, dsn
) -> None:
    monkeypatch.setenv("DATABASE_URL", dsn)
    await _world(pool, tenant, tmp_path, monkeypatch, steps=[{"message": "nieważne"}])

    code = await _run(
        _Args(organization=str(tenant["organization"]), project="Projekt Którego Nie Ma")
    )
    printed = capsys.readouterr()

    assert code == 3
    assert "NIE tworzy projektów" in printed.err
