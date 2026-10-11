"""Trzy joby gotowości operacyjnej (P7 etap B).

CO TU JEST WARTE MIERZENIA:

  * **Puls dyspozytora jest dowodem, że kolejka ruszyła**, bo pisze go job, który przez tę
     kolejkę przeszedł. Strażnik pytający „czy żyjesz" umie powiedzieć tylko „odpowiedział".
  * **Dzienna kopia ZAMAWIA, a nie robi.** Job zostawia wpis w dzienniku audytu i nic poza tym:
     `pg_dump` należy tam, gdzie jest wolumen i miejsce na plik. Job, który udawałby, że zrobił
     kopię, zostawiłby operatora z wiarą w plik, którego nikt nie zapisał. Wpis odróżnia „kopii
     nie zrobiono" od „nikt jej nie zamówił", i tylko na to pytanie odpowiada.
  * **Tik, który nie może zasilić księgi kosztów, nadal bije.** Inaczej problem z rachunkiem
     wyglądałby jak awaria dyspozytora, czyli najgorsza możliwa pomyłka w raporcie dostępności.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.ops.handlers import (
    JOB_BACKUP_DAILY,
    JOB_HEARTBEAT,
    JOB_SWEEP,
    OpsHandlers,
)
from control_plane.ops.health import COMPONENT_DISPATCHER, HealthStore

pytestmark = [pytest.mark.db, pytest.mark.p7]


def _handlers(pool, tenant) -> OpsHandlers:
    return OpsHandlers(
        pool=pool,
        health=HealthStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
        organization_id=tenant["organization"],
    )


def _claim(tenant, job_type: str) -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=job_type,
        routing_key="observe",
        priority=30,
        payload_ref=f"ops/{tenant['organization']}",
        dispatch_version=1,
        attempt_limit=1,
        attempt_id=uuid4(),
        attempt_no=1,
        lease_generation=1,
        lease_owner=uuid4(),
        expires_at=None,
    )


async def test_the_three_jobs_are_all_registered(pool, tenant) -> None:
    """Mapa handlerów jest tym, co dyspozytor naprawdę wywoła. Job zadeklarowany i niewpięty
    jest jobem, który nigdy nie ruszy, a w kolejce wygląda jak czekający."""
    mapping = _handlers(pool, tenant).as_mapping()
    assert set(mapping) == {JOB_HEARTBEAT, JOB_SWEEP, JOB_BACKUP_DAILY}


async def test_the_heartbeat_job_writes_the_dispatchers_pulse(pool, tenant) -> None:
    handlers = _handlers(pool, tenant)
    await handlers.heartbeat_job(_claim(tenant, JOB_HEARTBEAT))

    health = HealthStore(pool)
    last = await health.last_beat(
        organization_id=tenant["organization"], component=COMPONENT_DISPATCHER
    )
    assert last is not None, "job przeszedł przez kolejkę, a pulsu nie ma"


async def test_the_daily_backup_job_records_that_it_asked_and_nothing_more(pool, tenant) -> None:
    """Wpis w audycie JEST całym skutkiem tego joba. Gdyby powstał też plik, znaczyłoby to, że
    `pg_dump` biegnie w procesie workera, czyli tam, gdzie nie ma ani wolumenu, ani miejsca."""
    await _handlers(pool, tenant).backup_job(_claim(tenant, JOB_BACKUP_DAILY))

    async with pool.acquire() as conn:
        kinds = await conn.fetch(
            "SELECT kind, actor_ref FROM audit_events WHERE organization_id = $1 "
            "ORDER BY occurred_at DESC LIMIT 3",
            tenant["organization"],
        )
    rows = [(row["kind"], row["actor_ref"]) for row in kinds]
    assert ("BACKUP_REQUESTED", "job:ops.backup.daily") in rows, rows


async def test_the_sweep_job_turns_silence_into_an_incident(pool, tenant) -> None:
    from datetime import timedelta

    from control_plane.ops.health import INTERVAL, MISSED_INTERVALS_BEFORE_DOWN

    health = HealthStore(pool)
    long_ago = datetime.now(UTC) - INTERVAL * (MISSED_INTERVALS_BEFORE_DOWN + 2)
    await health.beat(
        organization_id=tenant["organization"],
        component=COMPONENT_DISPATCHER,
        now=long_ago,
    )
    await _handlers(pool, tenant).sweep_job(_claim(tenant, JOB_SWEEP))

    open_now = await health.open_incidents(organization_id=tenant["organization"])
    assert [one.component for one in open_now] == [COMPONENT_DISPATCHER]
    assert open_now[0].detected_by == "MISSING_HEARTBEAT"
    assert timedelta(0) <= datetime.now(UTC) - open_now[0].started_at


async def test_a_broken_ledger_refill_does_not_stop_the_heartbeat(pool, tenant) -> None:
    """Problem z rachunkiem nie może wyglądać jak awaria dyspozytora: to jest najgorsza
    możliwa pomyłka w raporcie dostępności, bo kieruje uwagę na zupełnie nie ten komponent."""
    import control_plane.ops.handlers as module

    async def exploding(*args, **kwargs):
        raise RuntimeError("księga niedostępna")

    original = module.refill
    module.refill = exploding
    try:
        await _handlers(pool, tenant).heartbeat_job(_claim(tenant, JOB_HEARTBEAT))
    finally:
        module.refill = original

    last = await HealthStore(pool).last_beat(
        organization_id=tenant["organization"], component=COMPONENT_DISPATCHER
    )
    assert last is not None, "puls padł razem z księgą kosztów"


# ---------------------------------------------------------------------------
# Sesja, której tura umarła (awaria live z 19.09)
# ---------------------------------------------------------------------------


async def test_a_session_left_running_without_a_live_turn_is_closed_as_failed(pool, tenant) -> None:
    """AWARIA LIVE: job `session.turn` padł po wygaśnięciu lease'u o 18:48, a `agent_sessions`
    dalej mówiło RUNNING z `updated_at` z 18:38. Studio czytało stan z danych i uczciwie
    pokazywało „pracuje", bo dane tak mówiły. Sesja nie może być RUNNING bez żywego joba tury:
    to nie jest stan, tylko ślad po procesie, którego już nie ma.
    """
    from control_plane.sessions.store import (
        STATUS_FAILED,
        STATUS_RUNNING,
        SessionStore,
    )

    sessions = SessionStore(pool)
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="praca, której tura umrze",
        budget={},
    )
    await sessions.set_status(
        session.id, organization_id=tenant["organization"], status=STATUS_RUNNING
    )

    # Żadnego joba `session.turn` dla tej sesji: dokładnie stan po zabraniu go przez reapera.
    await _handlers(pool, tenant).heartbeat_job(_claim(tenant, JOB_HEARTBEAT))

    fresh = await sessions.get(organization_id=tenant["organization"], session_id=session.id)
    assert fresh is not None and fresh.status == STATUS_FAILED, fresh.status
    assert "lease" in (fresh.result_summary or "")

    events = await sessions.events(organization_id=tenant["organization"], session_id=session.id)
    details = [e.payload.get("detail", "") for e in events if e.kind == "STATUS"]
    assert any("tura przerwana" in one for one in details), details


async def test_a_session_with_a_live_turn_job_is_left_alone(pool, tenant) -> None:
    """Uzgodnienie nie może zabijać tur, które NAPRAWDĘ pracują: job w kolejce znaczy, że jest
    kto ją dokończyć."""
    from control_plane.domain import commands
    from control_plane.sessions.store import STATUS_RUNNING, SessionStore

    sessions = SessionStore(pool)
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="praca w toku",
        budget={},
    )
    await sessions.set_status(
        session.id, organization_id=tenant["organization"], status=STATUS_RUNNING
    )
    await commands.enqueue_job(
        PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="session.turn",
        routing_key="observe",
        priority=50,
        attempt_limit=1,
        idempotency_key=f"session-turn:{session.id}:zywy",
        payload_ref=f"session/{session.id}",
    )

    await _handlers(pool, tenant).heartbeat_job(_claim(tenant, JOB_HEARTBEAT))

    fresh = await sessions.get(organization_id=tenant["organization"], session_id=session.id)
    assert fresh is not None and fresh.status == STATUS_RUNNING, fresh.status


async def test_a_background_session_inside_a_running_presence_tick_is_left_alone(
    pool, tenant
) -> None:
    """27.09: a background session publishing the first X post was closed as "tura przerwana"
    mid-turn, because its turn runs inside the project's `presence.tick`, not in a
    `session.turn` job, and finished ten seconds later over a FAILED it did not deserve."""
    from control_plane.domain import commands
    from control_plane.sessions.store import STATUS_RUNNING, SessionStore

    sessions = SessionStore(pool)
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="praca w tle",
        budget={},
        kind="PRESENCE",
    )
    await sessions.set_status(
        session.id, organization_id=tenant["organization"], status=STATUS_RUNNING
    )
    transport = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    await commands.enqueue_job(
        transport,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="presence.tick",
        routing_key="observe",
        priority=50,
        attempt_limit=1,
        idempotency_key=f"presence-tick:{project_id}:teraz",
        payload_ref=f"presence/{project_id}",
    )
    batch = await transport.claim(
        worker=uuid4(), limit=1, lease_seconds=60, job_types=["presence.tick"]
    )
    assert len(batch.claims) == 1, "tik obecności jest w toku"

    await _handlers(pool, tenant).heartbeat_job(_claim(tenant, JOB_HEARTBEAT))

    fresh = await sessions.get(organization_id=tenant["organization"], session_id=session.id)
    assert fresh is not None and fresh.status == STATUS_RUNNING, fresh.status


async def test_a_reconciled_session_can_take_a_new_turn(pool, tenant) -> None:
    """Awaria jednej tury nie ma kasować rozmowy: po FAILED człowiek może wznowić."""
    from control_plane.sessions.store import STATUS_RUNNING, SessionStore

    sessions = SessionStore(pool)
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="wznawialna",
        budget={},
    )
    await sessions.set_status(
        session.id, organization_id=tenant["organization"], status=STATUS_RUNNING
    )
    await _handlers(pool, tenant).heartbeat_job(_claim(tenant, JOB_HEARTBEAT))

    claimed = await sessions.claim_for_turn(session.id, organization_id=tenant["organization"])
    assert claimed is not None, "po uzgodnieniu sesja nie przyjmuje nowej tury"


async def test_an_organisation_wide_job_reschedules_itself_instead_of_breaking_the_chain(
    pool, tenant
) -> None:
    """ZAUWAŻONE PRZEZ wenext-ce W LOGU CONTROL PLANE'U 21.09, po identyfikatorze powtórzonym
    w obu polach: `(organization_id, workspace_id)=(288e2557-…, 288e2557-…)`.

    Joby `ops.*` są ORGANIZACYJNE i mają `workspace_id IS NULL` (0003 mówi to wprost). Kod
    przedłużający łańcuch pisał `claim.workspace_id or self._organization_id`, więc dla takiego
    joba podstawiał organizację w miejsce przestrzeni, a baza odrzucała wiersz kluczem obcym
    `jobs_workspace_belongs_to_organization`. Skutek: łańcuch przestawał się przedłużać, a
    jedynym śladem była linia WARNING w logu, bo niepowodzenie zamówienia jest tam świadomie
    połykane, żeby jeden zły enqueue nie zabił bijącego serca.

    Dlaczego nie złapał tego żaden test: wszystkie budowały zajęcie z `workspace_id` przestrzeni,
    czyli z wartością, której prawdziwy job operacyjny nie ma.
    """
    claim = ClaimedJob(
        job_id=uuid4(),
        organization_id=tenant["organization"],
        # TAK WYGLĄDA PRAWDZIWY JOB OPERACYJNY: bez przestrzeni.
        workspace_id=None,
        job_type=JOB_HEARTBEAT,
        routing_key="observe",
        priority=30,
        payload_ref=f"ops/{tenant['organization']}",
        dispatch_version=1,
        attempt_limit=1,
        attempt_id=uuid4(),
        attempt_no=1,
        lease_generation=1,
        lease_owner=uuid4(),
        expires_at=None,
    )

    await _handlers(pool, tenant).heartbeat_job(claim)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT workspace_id FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_HEARTBEAT,
        )
    assert len(rows) == 1, (
        "łańcuch nie zamówił następnego biegu; bijące serce zatrzymuje się po jednym uderzeniu, "
        "a raport tygodnia mówi wtedy „okno pomiaru ma 0.0 doby” i ma tak mówić zawsze"
    )
    assert rows[0]["workspace_id"] is None, (
        "następny bieg dostał przestrzeń, choć job jest organizacyjny; to ta sama podmiana, "
        "przez którą baza odrzucała wiersz"
    )
