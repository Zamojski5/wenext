"""Własny wątek agenta, od bramki po publikację (0023, P4 etap C).

TEN PLIK PILNUJE CZEGOŚ, CZEGO NIE PILNUJE ŻADEN INNY: publikacji, która nie jest odpowiedzią.
Odpowiedź stoi pod cudzym wątkiem i jest ograniczona jego kontekstem; własny wątek zajmuje
miejsce w społeczności sam z siebie, więc bramek jest o jedną więcej i każda z nich mówi coś
innego:

  * POWIERZENIE mówi, czy człowiek na to pozwolił (`actions` z OWN_POST);
  * KARTA GŁOSU społeczności mówi, czy własne posty są tam w ogóle mile widziane, i BRAK
    ROZSTRZYGNIĘCIA ZNACZY NIE. To jest cała różnica między „wolno" a „nie zabroniono";
  * BUDŻET liczy własne posty osobno od odpowiedzi;
  * ta sama TREŚĆ nie staje dwa razy, a skutek nieznany liczy się jak publikacja (L5335).

Co jest mierzone na dole pliku: publikator dostaje inny port, rozkłada zatwierdzoną treść na
tytuł i treść tak, jak ją złożył `draft.create`, i po publikacji potrafi odczytać własny wątek,
którego nikt nigdy nie obserwował.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from community_observer import ThreadNotFound
from community_observer.reddit_browser import SessionState
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from control_plane.approval.store import ApprovalStore
from control_plane.execution.adapter import SideEffectEvidence
from control_plane.execution.store import ExecutionStore
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe.store import ObservationStore
from control_plane.pilot.publication import (
    KIND_OWN_POST,
    KIND_REPLY,
    REQUEST_KINDS,
    PublicationStore,
    join_own_post,
    split_own_post,
)
from control_plane.pilot.publisher import (
    OUTCOME_CAPSULE,
    OUTCOME_PUBLISHED,
    OUTCOME_SKIPPED,
    PublishHandlers,
)
from control_plane.pilot.store import PilotStore
from control_plane.presence.approval import ISSUED, REFUSED, DelegatedApprover
from control_plane.presence.scope import parse_budget, parse_scope
from control_plane.presence.store import PresenceStore
from test_pilot_publisher import thread_json as _thread_json

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

ACCOUNT = "Demo_Maintainer"
COMMUNITY = "r/kubernetes"
THREAD = "1abc23"
TITLE = "Czego nauczył nas tydzień z operatorem bez testów"
BODY = "Trzy rzeczy, po kolei, bez morałów."
READY = {"mode": "INTERACTIVE", "display_only": True, "human_steer_open": False, "alive": True}


def now() -> datetime:
    return datetime.now(UTC)


async def _seed(
    pool,
    tenant,
    *,
    actions: list[str] | None = None,
    own_posts: object = True,
    budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Projekt, konto o TYM SAMYM loginie, co sesja w kapsule, społeczność i karta głosu.

    `own_posts` to DECYZJA CZŁOWIEKA (0025, kolumna na `communities`): `True`, `False`, albo
    `None` dla społeczności, o której nikt jeszcze nie zdecydował. Trzy stany, bo trzy są w
    produkcie i różnią się tym, co ma zrobić człowiek. Karta głosu jest zasiewana obok, zawsze
    ta sama, właśnie po to, żeby było widać, że o niczym nie rozstrzyga.
    """
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'bądź pomocny') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, 'REDDIT', $2) RETURNING id",
            tenant["organization"],
            ACCOUNT,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
        community_id = await conn.fetchval(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', $3, 'Reddit', 'ACCOUNT_REQUIRED', 600) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            COMMUNITY,
        )
        await conn.execute(
            """
            INSERT INTO observed_threads
                (organization_id, workspace_id, community_id, external_id, url, title, body,
                 author, created_at_source, first_observed_at, last_observed_at, raw_sha256)
            VALUES ($1, $2, $3, $4, $5, 'tytuł', 'treść', 'op', now(), now(), now(), $6)
            """,
            tenant["organization"],
            tenant["workspace"],
            community_id,
            THREAD,
            f"https://www.reddit.com/{COMMUNITY}/comments/{THREAD}/x/",
            "0" * 64,
        )
        card: dict[str, Any] = {"o_czym": "o operatorach"}
        await conn.execute(
            """
            INSERT INTO community_voice_cards
                (organization_id, workspace_id, community_id, version, window_start, window_end,
                 content, comment_count, invalid_quote_count, model, effort, prompt_name,
                 prompt_sha256)
            VALUES ($1, $2, $3, 1, now() - interval '7 days', now(), $4::jsonb, 10, 0,
                    'test', 'low', 'voice', $5)
            """,
            tenant["organization"],
            tenant["workspace"],
            community_id,
            json.dumps(card),
            "0" * 64,
        )
        if own_posts is not None:
            # Decyzja człowieka, nie klucz w karcie: karta jest obserwacją modelu (0025).
            await conn.execute(
                "UPDATE communities SET own_posts_allowed = $2, own_posts_decided_by = $3, "
                "own_posts_decided_at = now() WHERE id = $1",
                community_id,
                own_posts,
                tenant["operator"],
            )

    delegation = await PresenceStore(pool).sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        mode="AUTONOMOUS",
        scope=parse_scope(
            {
                "accounts": [str(account_id)],
                "communities": [COMMUNITY],
                "actions": ["REPLY", "OWN_POST"] if actions is None else actions,
            }
        ),
        budget=parse_budget(budget if budget is not None else {"publications_per_day": 6}),
        operator_id=tenant["operator"],
    )
    return {
        "project": project_id,
        "account": account_id,
        "community": community_id,
        "delegation": delegation,
    }


async def _propose_own_post(pool, tenant, seeded, *, title: str = TITLE, body: str = BODY) -> UUID:
    """Propozycja własnego wątku, dokładnie taka, jaką zostawia `draft.create` z kind=OWN_POST:
    cel to SPOŁECZNOŚĆ, a tytuł jedzie razem z treścią w polu, które wchodzi do odcisku."""
    store = ApprovalStore(pool)
    hash_input = ApprovalHashInput(
        organization=tenant["organization"],
        workspace=tenant["workspace"],
        assignment_version=uuid4(),
        account=seeded["account"],
        platform="REDDIT",
        action_type=SemanticCapability.SUBMIT_POST,
        target_external_id=COMMUNITY,
        target_url=f"https://www.reddit.com/{COMMUNITY}/",
        source_fingerprint=f"session:{uuid4()}",
        text_and_media=join_own_post(title, body),
        intelligence_profile_version=uuid4(),
        plan_version=seeded["project"],
        memory_snapshot=uuid4(),
        adapter_version="wenext-session-draft/1",
    )
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=seeded["account"],
    )
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=hash_input,
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=now() + timedelta(hours=6),
    )
    return revision.id


async def _propose_reply(pool, tenant, seeded, *, text: str = "odpowiedź agenta") -> UUID:
    """Propozycja odpowiedzi pod obserwowanym wątkiem, dla porównania z własnym postem."""
    store = ApprovalStore(pool)
    hash_input = ApprovalHashInput(
        organization=tenant["organization"],
        workspace=tenant["workspace"],
        assignment_version=uuid4(),
        account=seeded["account"],
        platform="REDDIT",
        action_type=SemanticCapability.SUBMIT_COMMENT,
        target_external_id=f"t3_{THREAD}",
        target_url=f"https://www.reddit.com/{COMMUNITY}/comments/{THREAD}/x/",
        source_fingerprint=f"session:{uuid4()}",
        text_and_media=text,
        intelligence_profile_version=uuid4(),
        plan_version=seeded["project"],
        memory_snapshot=uuid4(),
        adapter_version="wenext-session-draft/1",
    )
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=seeded["account"],
    )
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=hash_input,
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=now() + timedelta(hours=6),
    )
    return revision.id


def _approver(pool, tenant) -> DelegatedApprover:
    return DelegatedApprover(
        pool=pool,
        presence=PresenceStore(pool),
        approvals=ApprovalStore(pool),
        publication=PublicationStore(pool),
        organization_id=tenant["organization"],
    )


async def _requests(pool, seeded) -> list[dict[str, Any]]:
    """Wnioski TEGO projektu. Nigdy `count(*)`: baza żyje przez cały przebieg suity."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, kind, status, target_external_id, draft_sha256 "
            "FROM publication_requests WHERE project_id = $1 ORDER BY position",
            seeded["project"],
        )
    return [dict(row) for row in rows]


# -- lista rodzajów po obu stronach ---------------------------------------------------------------


async def test_the_kinds_in_the_code_are_the_kinds_the_database_accepts(pool) -> None:
    """`REQUEST_KINDS` i CHECK z 0023 to jedna lista w dwóch miejscach.

    Trzeci rodzaj dopisany po jednej stronie przejdzie po cichu: kod zacznie go wysyłać, a baza
    odrzuci pierwszą publikację tego rodzaju, albo odwrotnie, baza go przyjmie, a kod nigdy nie
    nazwie. Dlatego pytamy bazę o TREŚĆ więzu, a nie o to, czy istnieje.
    """
    async with pool.acquire() as conn:
        definition = await conn.fetchval(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE c.conname = $1 AND t.relname = $2",
            "publication_requests_kind_is_known",
            "publication_requests",
        )
    assert definition, "brak więzu publication_requests_kind_is_known na publication_requests"
    in_database = set(re.findall(r"'([A-Z_]+)'", definition))
    assert in_database == set(REQUEST_KINDS), (
        f"tylko w bazie {sorted(in_database - set(REQUEST_KINDS))}, "
        f"tylko w kodzie {sorted(set(REQUEST_KINDS) - in_database)}"
    )


# -- bramki -------------------------------------------------------------------------------------


async def test_a_signed_scope_and_a_card_that_allows_it_put_an_own_post_in_the_queue(
    pool, tenant
) -> None:
    seeded = await _seed(pool, tenant)
    revision_id = await _propose_own_post(pool, tenant, seeded)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )

    assert [one.outcome for one in considered] == [ISSUED], considered
    rows = await _requests(pool, seeded)
    assert [row["kind"] for row in rows] == [KIND_OWN_POST], rows
    assert rows[0]["target_external_id"] == COMMUNITY
    one = considered[0]
    assert one.revision_id == revision_id and one.request_id is not None
    assert str(seeded["delegation"].id) in one.reason, "decyzja wskazuje powierzenie"


async def test_a_community_nobody_decided_about_refuses_the_own_post(pool, tenant) -> None:
    """BRAK ROZSTRZYGNIĘCIA ZNACZY NIE. Społeczność, o której nikt nie zdecydował, to pytanie do
    człowieka, a nie zgoda milcząca."""
    seeded = await _seed(pool, tenant, own_posts=None)
    await _propose_own_post(pool, tenant, seeded)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )

    assert [one.outcome for one in considered] == [REFUSED], considered
    assert await _requests(pool, seeded) == []


async def test_a_no_refuses_even_though_the_person_signed_own_post_in_the_scope(
    pool, tenant
) -> None:
    """Dwie różne zgody: zakres powierzenia mówi, co agent w ogóle może, a decyzja o
    społeczności mówi, czy akurat TAM wolno. Węższa wygrywa, zawsze."""
    seeded = await _seed(pool, tenant, own_posts=False)
    await _propose_own_post(pool, tenant, seeded)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )

    assert [one.outcome for one in considered] == [REFUSED], considered
    assert await _requests(pool, seeded) == []


async def test_a_delegation_without_own_post_refuses_even_though_the_community_allows_it(
    pool, tenant
) -> None:
    """Dwie różne zgody na dwie różne rzeczy: decyzja o społeczności mówi, czy tam wypada,
    powierzenie mówi, na co pozwolił człowiek temu projektowi. Żadna nie zastępuje drugiej."""
    seeded = await _seed(pool, tenant, actions=["REPLY"])
    await _propose_own_post(pool, tenant, seeded)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )

    assert [one.outcome for one in considered] == [REFUSED], considered
    assert await _requests(pool, seeded) == []


# -- publikacja ----------------------------------------------------------------------------------


class ScriptedSource:
    """Sesja kapsuły, odczyt wątku po identyfikatorze i listing społeczności."""

    def __init__(self) -> None:
        self.threads: dict[str, Any] = {}
        self.recent: tuple[Any, ...] = ()
        self.reads: list[str] = []

    async def session_state(self) -> SessionState:
        return SessionState(logged_in=True, username=ACCOUNT, checked_at=now())

    async def read_thread_payload(self, community_key: str, external_id: str) -> Any:
        self.reads.append(external_id)
        if external_id not in self.threads:
            raise ThreadNotFound(external_id)
        return self.threads[external_id]

    async def list_recent_threads(self, community_key: str, *, since: Any = None) -> tuple:
        return self.recent


class FakeOwnPostPort:
    def __init__(self) -> None:
        self.calls: list[ApprovalHashInput] = []
        self.raise_with: Exception | None = None
        self.evidence = SideEffectEvidence(
            external_id="t3_p1z9",
            permalink="https://www.reddit.com/r/kubernetes/comments/p1z9/",
            account=ACCOUNT,
            observed_at=now(),
            raw={"from": "fake"},
        )

    async def submit(self, content: ApprovalHashInput) -> SideEffectEvidence:
        self.calls.append(content)
        if self.raise_with is not None:
            raise self.raise_with
        return self.evidence


@dataclass
class World:
    handlers: PublishHandlers
    port: FakeOwnPostPort
    reply_port: FakeOwnPostPort
    source: ScriptedSource


def _world(pool, tenant, *, own_post_port: FakeOwnPostPort | None = None) -> World:
    port = own_post_port if own_post_port is not None else FakeOwnPostPort()
    reply_port = FakeOwnPostPort()
    source = ScriptedSource()

    async def capsule_state():
        return dict(READY)

    handlers = PublishHandlers(
        store=ObservationStore(pool),
        pilot=PilotStore(pool),
        publication=PublicationStore(pool),
        approvals=ApprovalStore(pool),
        execution=ExecutionStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
        source=source,
        capsule_state=capsule_state,
        port_factory=lambda account: reply_port,
        own_post_port_factory=None if own_post_port is None else (lambda account: port),
        organization_id=tenant["organization"],
        delegations=PresenceStore(pool),
    )
    return World(handlers, port, reply_port, source)


async def test_the_publisher_sends_the_consented_title_and_body_and_never_the_reply_port(
    pool, tenant
) -> None:
    seeded = await _seed(pool, tenant)
    await _propose_own_post(pool, tenant, seeded)
    await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    world = _world(pool, tenant, own_post_port=FakeOwnPostPort())

    outcome = await world.handlers.run_once(now())

    assert outcome.outcome == OUTCOME_PUBLISHED, outcome
    assert len(world.port.calls) == 1, world.port.calls
    assert world.reply_port.calls == [], "własny post poszedł portem odpowiedzi"
    title, body = split_own_post(world.port.calls[0].text_and_media)
    assert (title, body) == (TITLE, BODY)
    assert world.port.calls[0].target_external_id == COMMUNITY
    rows = await _requests(pool, seeded)
    assert [row["status"] for row in rows] == ["PUBLISHED"], rows


async def test_without_an_own_post_actuator_the_request_waits_instead_of_going_out_as_a_reply(
    pool, tenant
) -> None:
    """Brak aktuatora własnych postów ma ZATRZYMAĆ ten wniosek, a nie przepchnąć go drugą
    ścieżką: port odpowiedzi wysłałby to jako komentarz pod nieistniejącym wątkiem."""
    seeded = await _seed(pool, tenant)
    await _propose_own_post(pool, tenant, seeded)
    await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    world = _world(pool, tenant, own_post_port=None)

    outcome = await world.handlers.run_once(now())

    assert outcome.outcome == OUTCOME_CAPSULE, outcome
    assert world.reply_port.calls == []
    rows = await _requests(pool, seeded)
    assert [row["status"] for row in rows] == ["QUEUED"], rows


async def test_the_same_text_does_not_stand_twice_as_our_thread(pool, tenant) -> None:
    """Przy odpowiedzi chroni nas „nasz komentarz już jest w tym wątku"; przy własnym poście
    adresu celu nie ma, więc odróżnia je odcisk TREŚCI.

    Limity podniesione NIE PO TO, żeby obejść budżet, tylko żeby ten test mierzył to, co ma:
    przy domyślnym jednym własnym poście na dobę i jednej publikacji na godzinę drugą propozycję
    odrzuca budżet, a strażnik powtórzeń nigdy nie dochodzi do głosu. Oba zabezpieczenia są
    potrzebne i każde ma swój test (domyślny sufit niżej).
    """
    seeded = await _seed(
        pool,
        tenant,
        budget={
            "publications_per_day": 6,
            "publications_per_hour": 4,
            "own_posts_per_day": 3,
        },
    )
    await _propose_own_post(pool, tenant, seeded)
    await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    world = _world(pool, tenant, own_post_port=FakeOwnPostPort())
    first = await world.handlers.run_once(now())
    assert first.outcome == OUTCOME_PUBLISHED, first

    # Druga propozycja o tej samej treści, zatwierdzona tą samą delegacją.
    await _propose_own_post(pool, tenant, seeded)
    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in considered] == [ISSUED], considered
    second = await world.handlers.run_once(now())

    assert second.outcome == OUTCOME_SKIPPED, second
    assert len(world.port.calls) == 1, "ta sama treść poszła drugi raz"
    rows = await _requests(pool, seeded)
    assert sorted(row["status"] for row in rows) == ["PUBLISHED", "SKIPPED"], rows


async def test_a_community_we_do_not_observe_is_skipped_rather_than_guessed(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose_own_post(pool, tenant, seeded)
    await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET target_external_id = 'r/nieobserwowany' "
            "WHERE project_id = $1",
            seeded["project"],
        )
    world = _world(pool, tenant, own_post_port=FakeOwnPostPort())

    outcome = await world.handlers.run_once(now())

    assert outcome.outcome == OUTCOME_SKIPPED, outcome
    assert world.port.calls == []


async def test_a_reply_still_goes_through_the_reply_port(pool, tenant) -> None:
    """Rozgałęzienie ma rozgałęziać w obie strony: wniosek REPLY nie może trafić do aktuatora
    własnych postów tylko dlatego, że ten istnieje."""
    seeded = await _seed(pool, tenant)
    await _propose_reply(pool, tenant, seeded)
    await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    world = _world(pool, tenant, own_post_port=FakeOwnPostPort())
    world.source.threads[THREAD] = _thread_json(THREAD)

    outcome = await world.handlers.run_once(now())

    assert outcome.outcome == OUTCOME_PUBLISHED, outcome
    assert len(world.reply_port.calls) == 1, world.reply_port.calls
    assert world.port.calls == [], "odpowiedź poszła aktuatorem własnych postów"
    rows = await _requests(pool, seeded)
    assert [row["kind"] for row in rows] == [KIND_REPLY], rows


async def test_one_own_post_a_day_is_the_default_and_it_is_the_budget_that_says_so(
    pool, tenant
) -> None:
    """DOMYŚLNIE JEDEN WŁASNY POST NA DOBĘ, niezależnie od limitu odpowiedzi.

    Zmierzone tutaj, bo to jest liczba, którą łatwo podnieść przez przypadek: odpowiedzi i
    własne posty mają osobne sufity i własny post ma zostać zatrzymany także wtedy, gdy limit
    publikacji jest jeszcze daleki. Odmowa mówi, KTÓRY sufit się wyczerpał, bo to jest różnica
    między „podnieś limit" a „poczekaj do jutra".
    """
    seeded = await _seed(pool, tenant, budget={"publications_per_day": 6})
    await _propose_own_post(pool, tenant, seeded)
    first = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in first] == [ISSUED], first

    await _propose_own_post(pool, tenant, seeded, title="Inny tytuł, ta sama doba")
    second = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )

    assert [one.outcome for one in second] == [REFUSED], second
    assert "własne posty" in second[0].reason, second[0].reason
    rows = await _requests(pool, seeded)
    assert [row["kind"] for row in rows] == [KIND_OWN_POST], rows
