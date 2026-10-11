"""Awaria jednej platformy nie zatrzymuje reszty pracy (P7 etap B, kryterium wprost z roadmapy).

TO JEST NAJWAŻNIEJSZY PLIK TEGO ETAPU, bo opisuje różnicę między usługą a skryptem. Skrypt, w
którym Reddit odpowiedział 403, przestaje działać. Usługa odnotowuje, że jeden komponent jest
niedostępny, i robi dalej wszystko, co nie zależy od tego komponentu: czyta kanały RSS, prowadzi
rozmowy, obsługuje inne projekty.

CZEGO NIE MIERZY TEN PLIK: czy publikacja na Reddicie się uda, gdy Reddit odpowiada 403. Nie uda
się i nie ma takiej potrzeby. Mierzone jest to, czy REST pracy biegnie dalej, i czy niedostępność
została ZARAPORTOWANA, a nie połknięta.

DLACZEGO PRZEZ DYSPOZYTORA, A NIE PRZEZ WYWOŁANIE HANDLERA: „inne joby biegną dalej" jest
zdaniem o kolejce. Handler wywołany osobno zawsze biegnie dalej, bo nikt go nie zatrzymał; to
dyspozytor decyduje, czy porażka jednego zadania zdejmuje z nóg pozostałe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.ops.health import COMPONENT_CAPSULE, DETECTED_BY_SELF, HealthStore

pytestmark = [pytest.mark.db, pytest.mark.p7]


def now() -> datetime:
    return datetime.now(UTC)


class PlatformUnavailable(Exception):
    """What a reader raises when the platform refuses. Not a bug: an answer."""


async def test_a_reddit_reader_failure_does_not_stop_the_rss_sweep_or_a_conversation(
    pool, tenant
) -> None:
    """TRZY ZADANIA, JEDNO PADA. Dwa pozostałe mają się wykonać, a awaria ma zostać zapisana.

    Zadania są prawdziwymi jobami w prawdziwej kolejce i biegną przez prawdziwego dyspozytora.
    Handler czytnika podnosi wyjątek dokładnie tak, jak podniósłby go odczyt z 403.
    """
    from control_plane.jobs.dispatcher import JobDispatcher

    queue = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    health = HealthStore(pool)
    ran: list[str] = []

    async def reddit_reader(claim: ClaimedJob) -> None:
        # The reader reports the outage ITSELF before failing. That is what turns "a job failed"
        # into "a component is unavailable", and it is the difference between a stack trace in a
        # log and a sentence on the status screen.
        await health.open_incident(
            organization_id=tenant["organization"],
            component=COMPONENT_CAPSULE,
            reason="Reddit odpowiedział 403 na odczyt",
            detected_by=DETECTED_BY_SELF,
        )
        ran.append("reddit")
        raise PlatformUnavailable("403")

    async def rss_sweep(claim: ClaimedJob) -> None:
        ran.append("rss")

    async def conversation_turn(claim: ClaimedJob) -> None:
        ran.append("rozmowa")

    for job_type in ("test.reddit.read", "test.rss.sweep", "test.session.turn"):
        await commands.enqueue_job(
            queue,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            job_type=job_type,
            routing_key="observe",
            priority=50,
            attempt_limit=1,
            idempotency_key=f"{job_type}:{uuid4()}",
            payload_ref="test",
        )

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={
            "test.reddit.read": reddit_reader,
            "test.rss.sweep": rss_sweep,
            "test.session.turn": conversation_turn,
        },
    )
    for _ in range(6):
        await dispatcher.tick()

    assert "reddit" in ran, "czytnik w ogóle nie ruszył, więc test nic nie mierzy"
    assert "rss" in ran, "kanały RSS stanęły przez awarię Reddita"
    assert "rozmowa" in ran, "rozmowa stanęła przez awarię Reddita"

    # I awaria została ZARAPORTOWANA, a nie połknięta.
    open_now = await health.open_incidents(organization_id=tenant["organization"])
    assert [one.component for one in open_now] == [COMPONENT_CAPSULE]
    assert "403" in open_now[0].reason


async def test_the_failed_job_is_recorded_as_failed_and_not_as_done(pool, tenant) -> None:
    """„Reszta biegnie dalej" nie może znaczyć „awaria zniknęła". Job czytnika ma zostać
    porażką w kolejce, żeby dało się ją policzyć."""
    from control_plane.jobs.dispatcher import JobDispatcher

    queue = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    enqueued = await commands.enqueue_job(
        queue,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="test.reddit.read",
        routing_key="observe",
        priority=50,
        attempt_limit=1,
        idempotency_key=f"fail:{uuid4()}",
        payload_ref="test",
    )

    async def failing(claim: ClaimedJob) -> None:
        raise PlatformUnavailable("403")

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={"test.reddit.read": failing},
    )
    for _ in range(3):
        await dispatcher.tick()

    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1", enqueued.job_id)
    assert status != "SUCCEEDED", f"job z 403 skończył jako {status}"
