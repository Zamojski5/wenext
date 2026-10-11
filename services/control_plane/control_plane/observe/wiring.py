"""Building the observe module from the environment — used by the runtime and by the API.

One place, so the process that runs the handlers and the process that answers the operator
agree on which adapters exist and whether a model is configured. Reading the environment happens
HERE and nowhere else in the module.

Variables:

    WENEXT_CODEX_HOME             directory with the founder's Codex login (`auth.json`); when
                                  absent there is no model and the picture/judge jobs park in
                                  WAITING_CAPABILITY — observation itself keeps running
    WENEXT_CODEX_BIN              optional path to the `codex` binary; default: the SDK's own
    WENEXT_BROWSER_READER_URL     the capsule's page reader (D-025), e.g. `http://capsule:8765`;
                                  when set, REDDIT is read through the capsule's Chrome — the
                                  founder's logged-in session — and the OAuth adapter is not built
    WENEXT_BROWSER_READER_TOKEN   shared with the capsule; an empty token is a visible 401, not a
                                  fallback to the OAuth adapter
    WENEXT_REDDIT_REQUIRE_LOGIN   `true` (default): an anonymous capsule session parks the job in
                                  WAITING_CAPABILITY instead of reading anonymously
    WENEXT_REDDIT_CLIENT_ID, WENEXT_REDDIT_CLIENT_SECRET,
    WENEXT_REDDIT_USERNAME, WENEXT_REDDIT_PASSWORD   the Reddit *script* app (D-023); used only
                                  when no reader URL is set; when absent the Reddit adapter is
                                  still registered and says so
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

import asyncpg
from community_observer import (
    CommunitySource,
    HackerNewsSource,
    RedditCredentials,
    RedditSource,
    build_reddit_browser_source,
)
from community_os_contracts import ObservationPlatform

from control_plane.jobs.dispatcher import Handler
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore

from .handlers import ObserveHandlers
from .store import ObservationStore
from .turns import CodexTurnRunner, ModelTurnPort

CODEX_HOME_VARIABLE = "WENEXT_CODEX_HOME"
CODEX_BIN_VARIABLE = "WENEXT_CODEX_BIN"


def build_sources(
    env: Mapping[str, str] | None = None,
) -> dict[ObservationPlatform, CommunitySource]:
    """Every adapter that exists, configured or not. An unconfigured Reddit adapter is registered
    on purpose: it raises `CredentialsMissing` with the variable names, which the handler turns
    into a parked job the operator can see — a missing entry would look like "no such platform".

    REDDIT has two transports and exactly one is built: the capsule's browser when
    `WENEXT_BROWSER_READER_URL` is set (D-025), the OAuth Data API otherwise (D-023). Not both —
    two adapters for one platform would make "which account read this" depend on a fallback.
    """
    env = os.environ if env is None else env
    reddit: CommunitySource | None = build_reddit_browser_source(env)
    if reddit is None:
        reddit = RedditSource(RedditCredentials.from_env(env))
    return {
        ObservationPlatform.HACKER_NEWS: HackerNewsSource(),
        ObservationPlatform.REDDIT: reddit,
    }


def build_turns(
    env: Mapping[str, str] | None = None, *, codex_home: Path | None = None
) -> ModelTurnPort | None:
    """The turn runner, on ONE `CODEX_HOME`.

    `codex_home` is the hook P1 etap 2 leaves for P2: since migration 0014 a deployment may hold
    several Codex connections, each with its own directory under the volume
    (`control_plane.workspace.codex_home.resolve_home`), and a session will name the connection
    it runs on. Passing the directory explicitly is how that reaches this factory.

    OMITTING IT KEEPS TODAY'S BEHAVIOUR EXACTLY. Without the argument the runner is built on
    `WENEXT_CODEX_HOME`, which is the volume ROOT and the account the live pilot runs on. That is
    deliberate and not a fallback to be tidied away later: the observe and pilot handlers have no
    connection to name yet, and changing the path under them would move the pilot to a directory
    with no `auth.json`.
    """
    env = os.environ if env is None else env
    home = codex_home if codex_home is not None else _path_or_none(env.get(CODEX_HOME_VARIABLE))
    if home is None:
        return None
    codex_bin = env.get(CODEX_BIN_VARIABLE)
    return CodexTurnRunner(codex_home=home, codex_bin=Path(codex_bin) if codex_bin else None)


def _path_or_none(value: str | None) -> Path | None:
    return Path(value) if value else None


def build_handlers(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    env: Mapping[str, str] | None = None,
) -> dict[str, Handler]:
    """The handler map the dispatcher runs. Empty in F1 (D-013); four entries since Outcome A, five
    since the voice card, six with the 24 h pilot (`pilot.cycle`)."""
    from control_plane.pilot.handlers import PilotGuard, PilotHandlers
    from control_plane.pilot.store import PilotStore

    store = ObservationStore(pool)
    pilot_store = PilotStore(pool)
    queue = PostgresJobTransport(JobStore(pool), organization_id=organization_id)
    sources = build_sources(env)
    turns = build_turns(env)
    handlers = ObserveHandlers(
        store=store,
        queue=queue,
        sources=sources,
        turns=turns,
        organization_id=organization_id,
        pilot_guard=PilotGuard(pilot_store, queue, organization_id=organization_id),
    )
    from control_plane.pilot.policy import PolicyStore

    pilot = PilotHandlers(
        store=store,
        pilot=pilot_store,
        queue=queue,
        sources=sources,
        turns=turns,
        organization_id=organization_id,
        policies=PolicyStore(pool),
    )
    publish = build_publish_handlers(pool, organization_id=organization_id, env=env)
    codex = build_codex_connection_handlers(pool, organization_id=organization_id, env=env)
    sessions = build_session_handlers(pool, organization_id=organization_id, env=env)
    knowledge = build_knowledge_handlers(pool, organization_id=organization_id, env=env)
    identity = build_identity_handlers(pool, organization_id=organization_id, env=env)
    ops = build_ops_handlers(pool, organization_id=organization_id)
    presence = build_presence_handlers(
        pool, organization_id=organization_id, env=env, turns=sessions
    )
    # P8 etap B: przepisanie dokumentów projektu po zmianie w rejestrze. Nic nie bierze ze
    # środowiska, bo rutyna domykania nie może być włączona w połowie wdrożeń.
    from control_plane.documents.handlers import build_document_handlers

    documents = build_document_handlers(pool, organization_id=organization_id)
    x_trends = build_x_trend_handlers(pool, organization_id=organization_id, env=env)
    return {
        **handlers.as_mapping(),
        **pilot.as_mapping(),
        **publish.as_mapping(),
        **codex.as_mapping(),
        **sessions.as_mapping(),
        **knowledge.as_mapping(),
        **presence.as_mapping(),
        **identity.as_mapping(),
        **ops.as_mapping(),
        **documents.as_mapping(),
        **x_trends.as_mapping(),
    }


def build_x_trend_handlers(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    env: Mapping[str, str] | None = None,
):
    """The hourly X collection (27.09): the feed and the agent's queries, read without a model.

    The source is built per run from the same factory the `x.*` tools use, so the collection reads
    through the X capsule exactly as the agent does, and stays off when the capsule is not
    configured (the handler then skips and says so).
    """
    from control_plane.observe.capsule_session import x_probe_from_env
    from control_plane.presence.store import PresenceStore
    from control_plane.presence.x_trends import XTrendHandlers, XTrendStore

    return XTrendHandlers(
        pool=pool,
        store=XTrendStore(pool),
        presence=PresenceStore(pool),
        source_factory=lambda: x_probe_from_env(env),
        organization_id=organization_id,
    )


def build_ops_handlers(pool: asyncpg.Pool, *, organization_id: UUID):
    """The three operational jobs of P7 etap B: heartbeat, sweep and the daily backup request.

    Nothing here comes from the environment, on purpose: a deployment cannot end up with its
    availability reporting half configured. A component that is not running simply never beats,
    and the sweep turns that silence into an incident with a beginning and a reason.
    """
    from control_plane.ops.handlers import OpsHandlers
    from control_plane.ops.health import HealthStore

    return OpsHandlers(
        pool=pool,
        health=HealthStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=organization_id),
        organization_id=organization_id,
    )


def build_identity_handlers(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    env: Mapping[str, str] | None = None,
):
    """The two jobs of P7: building a customer's export, and purging an organization.

    `home_root` may be absent and then the purge removes no Codex directory and says nothing: a
    deployment with no volume has none to remove. The volume for the export file is resolved
    LAZILY inside the handler, for the reason `artifact_volume` spells out - a directory created
    while assembling the handler map takes the whole dispatcher down on a host that cannot write
    there.
    """
    from control_plane.identity.handlers import IdentityHandlers
    from control_plane.workspace.codex_home import home_root

    env = os.environ if env is None else env
    queue = PostgresJobTransport(JobStore(pool), organization_id=organization_id)
    return IdentityHandlers(
        pool=pool,
        organization_id=organization_id,
        queue=queue,
        home_root=home_root(env),
    )


def build_presence_handlers(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    env: Mapping[str, str] | None = None,
    turns=None,
):
    """The `presence.tick` job (P4): one cycle of the agent working with nobody watching.

    `turns` is passed in rather than built here, and that is not a saving: the presence tick runs
    the session turn ITSELF, so the two have to be the same object with the same engine, the same
    Codex homes and the same tool server. Built twice they would be two engines racing for one
    connection's `CODEX_HOME`.

    Everything else this needs is a store over the pool that is already open. There is no
    environment variable to forget, on purpose: a deployment cannot end up with presence half on.
    """
    from control_plane.approval.store import ApprovalStore
    from control_plane.pilot.publication import PublicationStore
    from control_plane.presence.approval import DelegatedApprover
    from control_plane.presence.collector import EventCollector
    from control_plane.presence.handlers import PresenceHandlers
    from control_plane.presence.store import PresenceStore
    from control_plane.sessions.store import SessionStore

    if turns is None:
        turns = build_session_handlers(pool, organization_id=organization_id, env=env)
    presence_store = PresenceStore(pool)
    publication = PublicationStore(pool)
    return PresenceHandlers(
        pool=pool,
        presence=presence_store,
        sessions=SessionStore(pool),
        turns=turns,
        approver=DelegatedApprover(
            pool=pool,
            presence=presence_store,
            approvals=ApprovalStore(pool),
            publication=publication,
            organization_id=organization_id,
        ),
        collector=EventCollector(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=organization_id),
        organization_id=organization_id,
    )


def build_knowledge_handlers(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    env: Mapping[str, str] | None = None,
):
    """The two RSS jobs of P3: sweeping for due feeds, and re-reading one feed.

    Nothing here calls a model and nothing here needs a credential. The fetcher is the real one,
    so the address checks of `knowledge/fetch.py` apply to a feed exactly as to a pasted page.
    """
    from control_plane.knowledge.handlers import KnowledgeHandlers
    from control_plane.knowledge.ingest import Ingestor, artifact_volume

    env = os.environ if env is None else env
    queue = PostgresJobTransport(JobStore(pool), organization_id=organization_id)
    return KnowledgeHandlers(
        pool=pool,
        # The volume is built at the first write, not here. `build_handlers` runs at process
        # start; a directory that cannot be created would otherwise take the whole dispatcher
        # down, and with it observation, the pilot, publication and the sessions.
        ingestor=Ingestor(pool=pool, volume_factory=lambda: artifact_volume(env)),
        organization_id=organization_id,
        queue=queue,
    )


def build_session_handlers(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    env: Mapping[str, str] | None = None,
):
    """The `session.turn` job (P2): one turn of an agent session on its Codex connection.

    Two things come from the environment and both may be absent, in which case a turn does not
    start and the session SAYS what is missing rather than answering anyway:

      * the DSN (`DATABASE_URL`), which the session's tool server needs. The tool server runs as
        a child of the app-server, which runs in this container, so "the database" here means
        the same database this process is already connected to;
      * the `CODEX_HOME` of the session's connection, resolved per session through
        `workspace/codex_home.py`. A session without a connection is a session that cannot run,
        and `codex_home_for` returning `None` is how the handler learns that.
    """
    import os

    from control_plane.sessions.handlers import SessionTurnHandlers
    from control_plane.sessions.store import SessionStore
    from control_plane.workspace.codex_home import home_root, resolve_home

    env = os.environ if env is None else env
    dsn = (env.get("DATABASE_URL") or "").strip()
    root = home_root(env)

    async def codex_home_for(session) -> Path | None:
        if root is None:
            return None
        if session.codex_connection_id is None:
            return None
        async with pool.acquire() as conn:
            home_ref = await conn.fetchval(
                "SELECT home_ref FROM codex_connections "
                "WHERE organization_id = $1 AND id = $2 AND disconnected_at IS NULL",
                organization_id,
                session.codex_connection_id,
            )
        if not home_ref:
            return None
        return resolve_home(root, str(home_ref))

    # `WENEXT_CODEX_BIN` działa tu tak samo, jak przy turach modelu w tym samym module: gdy jest
    # ustawiona, tura biegnie NA TEJ binarce, a gdy jej nie ma, klient bierze binarkę z
    # przypiętego pakietu. Jedna zmienna, dwie ścieżki silnika, bez trzeciego sposobu.
    codex_bin = (env.get(CODEX_BIN_VARIABLE) or "").strip()

    return SessionTurnHandlers(
        pool=pool,
        sessions=SessionStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=organization_id),
        organization_id=organization_id,
        dsn=dsn,
        codex_home_for=codex_home_for,
        codex_bin=Path(codex_bin) if codex_bin else None,
    )


def build_codex_connection_handlers(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    env: Mapping[str, str] | None = None,
):
    """The two jobs of a Codex connection: waiting out a device-code login, and re-reading state.

    Both the engine and the volume root may be absent, and then the handlers write a stated
    reason onto the connection instead of raising. A deployment with no `WENEXT_CODEX_HOME` has
    nowhere to keep a connection, and the screen has to say that in words — a failed job in the
    queue is not something the person adding a connection can interpret.
    """
    from control_plane.workspace.codex_home import home_root
    from control_plane.workspace.connections import CodexConnectionStore
    from control_plane.workspace.engine import AppServerEngine
    from control_plane.workspace.handlers import CodexConnectionHandlers

    env = os.environ if env is None else env
    root = home_root(env)
    codex_bin = env.get(CODEX_BIN_VARIABLE)
    return CodexConnectionHandlers(
        store=CodexConnectionStore(pool),
        engine=AppServerEngine(codex_bin=Path(codex_bin) if codex_bin else None),
        home_root=root,
        organization_id=organization_id,
    )


def build_media_loader(
    pool: asyncpg.Pool, *, organization_id: UUID, env: Mapping[str, str] | None = None
):
    """The publisher's reader of a consented image (27.09): bytes and type, or a refusal.

    Read as the RUNNER through the artifact ACL, from the volume this deployment keeps material
    on (the one `browser.inspect` wrote the screenshot to), then held against the checksum the
    CONSENT names, not only the one stored beside the bytes: an artifact replaced under the same
    id after the approval is not the image that was approved. Anything that is not PNG or JPEG
    is refused here, before the capsule is asked. Every refusal says nothing was sent.
    """
    from control_plane.execution.adapter import PublicationRefused
    from control_plane.knowledge.ingest import artifact_volume
    from control_plane.pilot.publication import MediaRef, image_type_of
    from control_plane.storage.artifacts import ArtifactStore, ArtifactStoreError, checksum_for
    from control_plane.storage.config import Reader

    async def load(media: MediaRef) -> tuple[bytes, str]:
        try:
            async with pool.acquire() as conn:
                content = await ArtifactStore().read(
                    conn,
                    organization_id=organization_id,
                    artifact_id=media.artifact_id,
                    reader=Reader.RUNNER,
                    volume=artifact_volume(env),
                )
        except (ArtifactStoreError, OSError) as exc:
            raise PublicationRefused(
                f"obrazu {media.artifact_id} nie da się odczytać ({exc}); nic nie wysłano"[:300]
            ) from exc
        if checksum_for(content) != media.checksum:
            raise PublicationRefused(
                "obraz różni się od zatwierdzonego (inna suma kontrolna); nic nie wysłano"
            )
        image_type = image_type_of(content)
        if image_type is None:
            raise PublicationRefused(
                "zatwierdzony plik nie jest obrazem PNG ani JPEG; nic nie wysłano"
            )
        return content, image_type

    return load


def build_publish_handlers(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    env: Mapping[str, str] | None = None,
):
    """The publication worker (R4 minimal, D-028) over the same stores and the same sources.

    Three things come from the environment and each may be absent, in which case the worker does
    not publish and says why: the capsule reader URL (`WENEXT_BROWSER_READER_URL`, the Reddit
    source that checks the session and reads the thread back), the capsule state reader (the same
    URL, `CapsuleClient.state`) and the actuator token (`WENEXT_BROWSER_WRITER_TOKEN`, the ONLY
    thing that lets `RedditCapsuleCommentPort` exist). The port is built per request with the
    account the consent names, never with a default account.
    """
    from control_plane.approval.store import ApprovalStore
    from control_plane.execution.adapter import (
        ENV_WRITER_TOKEN,
        RedditCapsuleCommentPort,
        RedditCapsuleOwnPostPort,
        XCapsuleOwnPostPort,
        XCapsuleReplyPort,
    )
    from control_plane.execution.store import ExecutionStore
    from control_plane.observe.capsule_client import CapsuleClient, CapsuleConfig
    from control_plane.observe.capsule_session import x_probe_from_env
    from control_plane.pilot.publication import PublicationStore
    from control_plane.pilot.publisher import PublishHandlers
    from control_plane.pilot.store import PilotStore
    from control_plane.presence.store import PresenceStore

    env = os.environ if env is None else env
    sources = build_sources(env)
    reddit = sources.get(ObservationPlatform.REDDIT)
    source = reddit if hasattr(reddit, "read_thread_payload") else None
    # P0 (STAN-PRAC 8.22): the same capsule source answers "does an anonymous reader see the
    # comment" and "did the account inbox report a removal". Both are optional ports of the
    # publisher; without them every request stays UNVERIFIED, which the publisher asserts
    # rather than assumes. The OAuth adapter has neither method, so it wires nothing here.
    anonymous = source if hasattr(source, "read_thread_payload_anonymously") else None
    removals = source if hasattr(source, "read_removal_reports") else None
    capsule = CapsuleConfig.from_env(env)
    writer_token = (env.get(ENV_WRITER_TOKEN) or "").strip()

    capsule_state = None
    port_factory = None
    own_post_port_factory = None
    x_port_factory = None
    x_own_post_port_factory = None
    # X (26.09): one reader for the identity check, the post before a reply, the readback as the
    # author and the look as a stranger. Since D-051 the X account's own capsule (Chromium) when
    # `WENEXT_X_BROWSER_READER_URL` is set, Reddit's capsule otherwise; the same writer token.
    x_source = x_probe_from_env(env)
    x_capsule = CapsuleConfig.for_platform(env, "X")
    x_capsule_state = None
    if x_capsule is not None and x_capsule != capsule:
        x_client = CapsuleClient(x_capsule.reader_url, token=x_capsule.reader_token)

        async def read_x_state() -> Mapping[str, object]:
            return (await x_client.state()).to_json()

        x_capsule_state = read_x_state
    if x_capsule is not None and writer_token:

        def make_x_port(account: str) -> XCapsuleReplyPort:
            return XCapsuleReplyPort(
                x_capsule.reader_url, token=writer_token, expected_account=account
            )

        load_media = build_media_loader(pool, organization_id=organization_id, env=env)

        def make_x_own_post_port(account: str) -> XCapsuleOwnPostPort:
            return XCapsuleOwnPostPort(
                x_capsule.reader_url,
                token=writer_token,
                expected_account=account,
                media_loader=load_media,
            )

        x_port_factory = make_x_port
        x_own_post_port_factory = make_x_own_post_port
    if capsule is not None:
        client = CapsuleClient(capsule.reader_url, token=capsule.reader_token)

        async def read_state() -> Mapping[str, object]:
            return (await client.state()).to_json()

        capsule_state = read_state
        if writer_token:

            def make_port(account: str) -> RedditCapsuleCommentPort:
                return RedditCapsuleCommentPort(
                    capsule.reader_url, token=writer_token, expected_account=account
                )

            def make_own_post_port(account: str) -> RedditCapsuleOwnPostPort:
                return RedditCapsuleOwnPostPort(
                    capsule.reader_url, token=writer_token, expected_account=account
                )

            port_factory = make_port
            own_post_port_factory = make_own_post_port

    from control_plane.pilot.policy import PolicyAutoApprover, PolicyStore

    autoapprover = PolicyAutoApprover(
        store=ObservationStore(pool),
        pilot=PilotStore(pool),
        publication=PublicationStore(pool),
        approvals=ApprovalStore(pool),
        policies=PolicyStore(pool),
        turns=build_turns(env),
        organization_id=organization_id,
    )
    return PublishHandlers(
        store=ObservationStore(pool),
        pilot=PilotStore(pool),
        publication=PublicationStore(pool),
        approvals=ApprovalStore(pool),
        execution=ExecutionStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=organization_id),
        source=source,
        anonymous=anonymous,
        removals=removals,
        capsule_state=capsule_state,
        port_factory=port_factory,
        own_post_port_factory=own_post_port_factory,
        organization_id=organization_id,
        autoapprover=autoapprover,
        # 0017: one queue holds both kinds of request, so the publisher has to be able to read
        # the signature a project's request stands on. Without this it would stop those requests
        # with a reason instead of publishing them (`PublishHandlers._governance`).
        delegations=PresenceStore(pool),
        x_source=x_source,
        x_port_factory=x_port_factory,
        x_own_post_port_factory=x_own_post_port_factory,
        x_capsule_state=x_capsule_state,
    )
