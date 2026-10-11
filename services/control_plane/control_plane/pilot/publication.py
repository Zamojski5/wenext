"""Publication of APPROVED pilot drafts (R4 minimal): consents into the database, and the queue.

The human's consent lives in `docs/ZGODY-PUBLIKACJI-2026-09-07.md` as a table of `pilot_reactions`
ids with the sha256 of each draft. This module moves it into the EXISTING approval path
(`ApprovalStore`: proposal → revision → decision) instead of building a second one (roadmap R4:
"nie budować drugiej ścieżki approvali"), and records one `publication_requests` row per consent in
the order of the document. Every later step (intent, attempt, evidence) hangs off that row.

What pins the consent to the text (L5218): the revision's `payload_hash` is the digest of a
14-field `ApprovalHashInput` whose `text_and_media` IS the draft. An edit goes through
`ApprovalStore.edit_into_new_revision`, which supersedes the approved revision, after which
`issue_intent` refuses (`RevisionNotGrantable`) and `authorize_and_consume_intent` refuses too.
Nothing here can publish: there is no port, no HTTP client and no browser in this file.

Digest inputs that the pilot does not model are given a stable, documented stand-in (D-028):
`assignment_version` = uuid5 of the account, `plan_version` = the pilot run, `memory_snapshot` = the
reaction itself. They are inputs of the consent hash, so they cannot change under a consent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput

from control_plane.approval.store import APPROVED_DECISION, ApprovalStore

from .store import PilotReaction, PilotStore

#: An input of the consent digest: a new actuator version needs a new consent (L2792).
ADAPTER_VERSION = "reddit-capsule-comment/1"
PLATFORM = "REDDIT"
#: How long a recorded consent stays grantable. Long enough for a queue paced at one comment an
#: hour; short enough that a draft nobody published for three days needs a fresh look.
CONSENT_TTL = timedelta(hours=72)

REQUEST_QUEUED = "QUEUED"
REQUEST_PUBLISHING = "PUBLISHING"
REQUEST_PUBLISHED = "PUBLISHED"
REQUEST_UNKNOWN = "UNKNOWN"
REQUEST_BLOCKED = "BLOCKED"
REQUEST_SKIPPED = "SKIPPED"
REQUEST_STOPPED = "STOPPED"
REQUEST_STATUSES = (
    REQUEST_QUEUED,
    REQUEST_PUBLISHING,
    REQUEST_PUBLISHED,
    REQUEST_UNKNOWN,
    REQUEST_BLOCKED,
    REQUEST_SKIPPED,
    REQUEST_STOPPED,
)

#: Tytuł własnego postu. Reddit tnie na 300 znakach, więc dłuższy tytuł nie jest tytułem, który
#: człowiek zatwierdził, tylko jego początkiem.
MAX_TITLE_CHARS = 300

#: CO ODDZIELA TYTUŁ OD TREŚCI we `text_and_media` własnego postu. Tytuł jedzie razem z treścią
#: w jednym polu, bo to pole wchodzi do odcisku zgody: osobne pole obok odcisku znaczyłoby, że
#: tytuł da się zmienić po zatwierdzeniu. Skoro jednak platforma przyjmuje dwa pola, ktoś musi je
#: z powrotem rozdzielić, i to sklejenie i to rozdzielenie stoją TUTAJ, obok siebie. Dwie kopie
#: tej zasady w dwóch plikach rozjeżdżają się przy pierwszej zmianie, a rozjechanie się akurat
#: tej oznacza post o tytule, którego nikt nie czytał.
OWN_POST_SEPARATOR = "\n\n"


def join_own_post(title: str, text: str) -> str:
    """Jedno pole z tytułu i treści. Odwrotność `split_own_post`, pilnowana testem."""
    clean = title.strip()
    if not clean:
        raise ValueError("własny post bez tytułu")
    if "\n" in clean or "\r" in clean:
        raise ValueError("tytuł w jednej linii: znak nowej linii rozdziela tytuł od treści")
    if len(clean) > MAX_TITLE_CHARS:
        raise ValueError(f"tytuł ma {len(clean)} znaków, limit {MAX_TITLE_CHARS}")
    return f"{clean}{OWN_POST_SEPARATOR}{text.strip()}"


def split_own_post(text_and_media: str) -> tuple[str, str]:
    """Tytuł i treść z pola, które weszło do odcisku zgody.

    Brak separatora NIE JEST błędem do naprawienia zgadywaniem: całość idzie wtedy jako tytuł,
    a treść zostaje pusta, i to od razu widać. Zgadywanie w drugą stronę (pierwsze zdanie jako
    tytuł) publikowałoby coś, czego człowiek nie widział w tej postaci.
    """
    head, separator, tail = text_and_media.partition(OWN_POST_SEPARATOR)
    if not separator:
        return text_and_media.strip(), ""
    return head.strip(), tail.strip()


#: ONE image under an own X post (27.09: the founder wants news shown with a screenshot). It rides
#: in `text_and_media`, the field the consent digest covers, as a last line naming the artifact
#: and the sha256 of its bytes, so what was approved is the text AND exactly these bytes. The
#: line is ours, never typed on X: `split_media` takes it off before anything reaches a composer.
_MEDIA_LINE = re.compile(r"\n\n\[obraz ([0-9a-f-]{36}) (sha256:[0-9a-f]{64})\]\Z")


@dataclass(frozen=True)
class MediaRef:
    """The image a consent names: which artifact, and the checksum its bytes must have."""

    artifact_id: UUID
    checksum: str


def join_media(text: str, media: MediaRef) -> str:
    """The consented field of a post with an image. Inverse of `split_media`, pinned by a test."""
    if not media.checksum.startswith("sha256:") or len(media.checksum) != 71:
        raise ValueError(f"suma obrazu {media.checksum!r} nie jest sha256")
    return f"{text.rstrip()}\n\n[obraz {media.artifact_id} {media.checksum}]"


def split_media(text_and_media: str) -> tuple[str, MediaRef | None]:
    """The text to type and the image the consent names, if any. A field without the line is
    all text; a malformed line is not an image and stays in the text, where it can be seen."""
    found = _MEDIA_LINE.search(text_and_media)
    if found is None:
        return text_and_media, None
    return text_and_media[: found.start()], MediaRef(UUID(found.group(1)), found.group(2))


#: The two image types the X actuator uploads, by the first bytes of the file. The capsule keeps
#: the same pair (`browser_supervisor.x_writer.IMAGE_SIGNATURES`) and checks again on its side.
IMAGE_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", b"\xff\xd8\xff"),
)


def image_type_of(content: bytes) -> str | None:
    """`image/png` or `image/jpeg` read from the bytes themselves, `None` for anything else."""
    return next((kind for kind, magic in IMAGE_SIGNATURES if content.startswith(magic)), None)


#: Rodzaje wpisu do kolejki (0023). TU JEST ŹRÓDŁO TYCH DWÓCH SŁÓW, bo tu jest kolumna, która
#: je trzyma, i CHECK, który ich pilnuje. Reszta kodu je importuje; dwie kopie tej samej pary
#: rozjeżdżają się przy pierwszym trzecim rodzaju.
KIND_REPLY = "REPLY"
KIND_OWN_POST = "OWN_POST"
REQUEST_KINDS = (KIND_REPLY, KIND_OWN_POST)

EVIDENCE_ACTUATOR = "ACTUATOR"
EVIDENCE_READBACK = "READBACK"
EVIDENCE_RECONCILIATION = "RECONCILIATION"

#: WHO did the reading (migration 0012). Until 17.09 there was only one kind of read, through the
#: capsule's logged-in profile, and `still_present = true` from it was reported as a published
#: comment. Reddit shows an author their own removed comments, so that read answers "is it there
#: for US", never "is it there for the community" (STAN-PRAC §8.22).
VIEWER_AUTHOR = "AUTHOR"
VIEWER_ANONYMOUS = "ANONYMOUS"
VIEWERS = (VIEWER_AUTHOR, VIEWER_ANONYMOUS)

#: The public visibility of a request: three values, and the third one is the honest default.
VISIBILITY_CONFIRMED = "CONFIRMED"
VISIBILITY_REMOVED = "REMOVED"
VISIBILITY_UNVERIFIED = "UNVERIFIED"
VISIBILITIES = (VISIBILITY_CONFIRMED, VISIBILITY_REMOVED, VISIBILITY_UNVERIFIED)

#: Where a removal report came from. The account's inbox (an AutoModerator reply saying the
#: comment "was automatically removed") is a different measurement than an anonymous read, so it
#: is named rather than folded into one.
REMOVAL_FROM_INBOX = "INBOX"
REMOVAL_FROM_ANONYMOUS_READ = "ANONYMOUS_READ"
REMOVAL_SOURCES = (REMOVAL_FROM_INBOX, REMOVAL_FROM_ANONYMOUS_READ)

#: WHAT is gone, which is a different question from WHERE we learned it (`REMOVAL_SOURCES`).
#: `COMMENT`: our own reply was taken down. `PARENT_POST`: the thread we replied under is gone,
#: so our reply is unreachable without anybody having judged it. Mierzone 26.09 na `t1_pblmb3d`:
#: bez tego rozróżnienia drugi przypadek czyta się jak pierwszy i podsuwa następnej sesji
#: nieprawdziwy wniosek o koncie albo o społeczności (0032).
REMOVAL_SCOPE_COMMENT = "COMMENT"
REMOVAL_SCOPE_PARENT_POST = "PARENT_POST"
REMOVAL_SCOPES = (REMOVAL_SCOPE_COMMENT, REMOVAL_SCOPE_PARENT_POST)

#: Bodies Reddit serves in place of a removed or deleted comment. The comment is still IN the
#: tree, so presence alone would read as success.
REMOVED_BODIES = frozenset({"[removed]", "[deleted]"})


@dataclass(frozen=True)
class AnonymousLook:
    """One anonymous read of our published comment: what a logged-out reader actually received.

    Constructed ONLY from a successful read. A 403, a 429 or an unreachable reader does not become
    an `AnonymousLook` with `present=False` — that would turn Reddit's rate limiter into a verdict
    that our comment was removed. A failed read leaves the request UNVERIFIED with the reason.
    """

    observed_at: datetime
    present: bool
    body: str | None
    score: int | None = None
    reply_count: int = 0

    @property
    def visible(self) -> bool:
        """Present in the tree AND not served as `[removed]` / `[deleted]`."""
        return self.present and (self.body or "").strip().lower() not in REMOVED_BODIES


def visibility_of(
    *, anonymous: AnonymousLook | None, removal_reported_at: datetime | None
) -> tuple[str, datetime | None]:
    """`(public_visibility, checked_at)` from the two inputs that can answer the question.

    The rules, in the order they are applied:

      1. a removal reported by the account's inbox is REMOVED, whatever any read says — an
         AutoModerator reply naming our comment is the platform telling us it took it down;
      2. no anonymous read at all is UNVERIFIED with no timestamp. This is the state of every
         publication made before migration 0012, and it is the whole reason the column exists:
         PUBLISHED said "sent", and nothing in the database said "seen by anyone else";
      3. an anonymous read that saw the comment is CONFIRMED;
      4. an anonymous read that did not see it, or saw `[removed]`, is REMOVED.

    Nothing here can return CONFIRMED without an `AnonymousLook`, which nothing can build without
    a successful read. That is the one property this function exists to have.
    """
    if removal_reported_at is not None:
        latest = (
            max(removal_reported_at, anonymous.observed_at)
            if anonymous is not None
            else removal_reported_at
        )
        return VISIBILITY_REMOVED, latest
    if anonymous is None:
        return VISIBILITY_UNVERIFIED, None
    if anonymous.visible:
        return VISIBILITY_CONFIRMED, anonymous.observed_at
    return VISIBILITY_REMOVED, anonymous.observed_at


class ConsentError(Exception):
    """The consent document and the database disagree. Nothing was written."""


@dataclass(frozen=True)
class ConsentRow:
    """One line of the consent table: which reaction, which exact text (sha256), which position."""

    position: int
    reaction_id: UUID
    thread_external_id: str
    draft_sha256: str
    chars: int


_ROW = re.compile(
    r"^\|\s*(?P<position>\d+)\s*\|\s*(?P<reaction>[0-9a-f-]{36})\s*\|[^|]*\|\s*"
    r"(?P<thread>[a-z0-9]{1,12})\s*\|[^|]*\|\s*(?P<sha>[0-9a-f]{64})\s*\|\s*(?P<chars>\d+)\s*\|"
)


_ACCOUNT = re.compile(r"Konto:\s*([A-Za-z0-9_-]{3,20})")


@dataclass(frozen=True)
class ConsentDocument:
    """The consent document as parsed: the account it names, its rows, and the digest of the whole
    text (so a recorded consent can be traced to the exact version of the file)."""

    account: str | None
    rows: tuple[ConsentRow, ...]
    sha256: str


def parse_consent_document(text: str) -> ConsentDocument:
    return ConsentDocument(
        account=(m.group(1) if (m := _ACCOUNT.search(text)) else None),
        rows=parse_consent_table(text),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def parse_consent_table(text: str) -> tuple[ConsentRow, ...]:
    """The data rows of the consent document's table, in document order.

    The header row (first cell `#`) and the separator row (first cell `---`) are the only table
    rows allowed to carry no data; every other table row must parse, or the document is refused.
    A silently shorter list would publish fewer drafts than the human approved without saying so.
    """
    rows: list[ConsentRow] = []
    unparsed: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells and (cells[0] == "#" or set(cells[0]) <= {"-", ":"}):
            continue
        if not cells or not cells[0].isdigit():
            unparsed.append(stripped[:120])
            continue
        match = _ROW.match(stripped)
        if match is None:
            raise ConsentError(f"wiersz zgody nie parsuje się: {stripped[:120]!r}")
        rows.append(
            ConsentRow(
                position=int(match["position"]),
                reaction_id=UUID(match["reaction"]),
                thread_external_id=match["thread"],
                draft_sha256=match["sha"],
                chars=int(match["chars"]),
            )
        )
    if unparsed:
        raise ConsentError(
            f"{len(unparsed)} wierszy tabeli zgód nie jest ani nagłówkiem, ani zgodą: {unparsed}"
        )
    if not rows:
        raise ConsentError("dokument zgody nie ma ani jednego wiersza tabeli")
    positions = [r.position for r in rows]
    if positions != list(range(1, len(rows) + 1)):
        raise ConsentError(f"pozycje zgód muszą być 1..{len(rows)} bez luk, są: {positions}")
    return tuple(rows)


def draft_sha256(draft: str) -> str:
    return hashlib.sha256(draft.encode("utf-8")).hexdigest()


def normalised_draft(text: str) -> str:
    """Two drafts that differ only in spacing or case are THE SAME DRAFT (P6).

    One spelling of that rule, used wherever "is this the same text" is asked: the publisher
    matching our comment on a thread, and `tools/registry.py` refusing a second identical
    proposal from a second session. Two spellings would agree until somebody edited one, and the
    disagreement would show up as two identical replies under one post.

    It does NOT feed a digest that a consent rests on: `draft_sha256` hashes the text as written,
    because what the person approved is the exact characters.
    """
    return " ".join(text.split()).strip().lower()


def assignment_version_for(account_id: UUID) -> UUID:
    """The pilot has no account assignment versions; the digest needs one. Stable per account."""
    return uuid5(NAMESPACE_URL, f"wenext:pilot:assignment:{account_id}")


def target_of(reaction: PilotReaction) -> tuple[str, str]:
    """`(target_external_id, target_url)` in Reddit's fullname form: a reaction to the post is a
    top-level comment on `t3_<post>`, a reaction to a comment replies to `t1_<comment>`."""
    if reaction.target_message_external_id == reaction.thread_external_id:
        return f"t3_{reaction.thread_external_id}", reaction.thread_url
    return (
        f"t1_{reaction.target_message_external_id}",
        f"{reaction.thread_url.rstrip('/')}/comment/{reaction.target_message_external_id}/",
    )


def hash_input_for(
    reaction: PilotReaction,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    account_id: UUID,
) -> ApprovalHashInput:
    """The 14 inputs of the consent digest for one pilot draft. `text_and_media` is the draft."""
    if not reaction.draft:
        raise ConsentError(f"reakcja {reaction.id} nie ma tekstu szkicu ({reaction.decision})")
    target_external_id, target_url = target_of(reaction)
    return ApprovalHashInput(
        organization=organization_id,
        workspace=workspace_id,
        assignment_version=assignment_version_for(account_id),
        account=account_id,
        platform=PLATFORM,
        action_type=SemanticCapability.SUBMIT_COMMENT,
        target_external_id=target_external_id,
        target_url=target_url,
        source_fingerprint=reaction.situation_sha256,
        text_and_media=reaction.draft,
        intelligence_profile_version=reaction.profile_version_id,
        plan_version=reaction.run_id,
        memory_snapshot=reaction.id,
        adapter_version=ADAPTER_VERSION,
    )


@dataclass(frozen=True)
class PublicationRequest:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    # 0017: pochodzenie wniosku. Albo bieg pilota (`run_id` i `reaction_id`), albo projekt
    # (`project_id` i `delegation_id`); CHECK w bazie nie dopuszcza obu naraz ani żadnego, więc
    # `from_a_project` wystarczy zapytać o jedno pole.
    run_id: UUID | None
    reaction_id: UUID | None
    project_id: UUID | None
    delegation_id: UUID | None
    # 0023: co to za wpis do kolejki. REPLY odpowiada w cudzym wątku i ma `target_external_id`
    # z przedrostkiem t1_ albo t3_ (CHECK w bazie tego pilnuje); OWN_POST zakłada własny wątek
    # i do chwili publikacji nie ma czego wskazać, więc celem jest społeczność.
    kind: str
    proposal_revision_id: UUID
    approval_decision_id: UUID
    intent_id: UUID | None
    attempt_id: UUID | None
    position: int
    status: str
    consent_source: str
    draft_sha256: str
    target_external_id: str
    target_url: str
    queued_at: datetime
    not_before: datetime | None
    last_checked_at: datetime | None
    detail: str | None
    published_at: datetime | None
    public_visibility: str
    public_visibility_checked_at: datetime | None
    removal_reported_at: datetime | None
    removal_source: str | None

    @property
    def from_a_project(self) -> bool:
        """Czy wniosek pochodzi z delegacji projektu, a nie z biegu pilota.

        Pyta o `project_id`, a nie o brak `run_id`, bo to pole dodatnio wskazuje pochodzenie:
        gdyby kiedyś doszedł trzeci rodzaj, "nie pilot" milcząco zaliczyłoby go do projektów.
        """
        return self.project_id is not None


@dataclass(frozen=True)
class PublishTarget:
    """The thread a publication request is about, whatever the request came from (0017).

    WHY THIS EXISTS. The publisher used to reach the thread through the PILOT: request to
    reaction to run to community. A request from a project has neither reaction nor run, and
    copying that path with an `if` would have given two ways to answer "which thread is this",
    one of which would eventually stop being maintained. The four things the publisher actually
    needs are here, and one query finds them for both kinds.
    """

    community_id: UUID
    thread_id: UUID
    thread_external_id: str
    thread_url: str


#: Reddit's fullnames carry their kind as a prefix (`t3_` a post, `t1_` a comment), while
#: `observed_threads.external_id` holds the bare id. Everything that matches one against the
#: other strips the prefix HERE, so the two spellings never meet anywhere else.
FULLNAME_PREFIX = "^t[13]_"


_REQUEST_COLUMNS = (
    "id, organization_id, workspace_id, run_id, reaction_id, project_id, delegation_id, "
    "kind, proposal_revision_id, "
    "approval_decision_id, intent_id, attempt_id, position, status, consent_source, draft_sha256, "
    "target_external_id, target_url, queued_at, not_before, last_checked_at, detail, published_at, "
    "public_visibility, public_visibility_checked_at, removal_reported_at, removal_source"
)


def _request_of(row: asyncpg.Record) -> PublicationRequest:
    return PublicationRequest(
        **{key: row[key] for key in _REQUEST_COLUMNS.replace(" ", "").split(",")}
    )


@dataclass(frozen=True)
class ApprovedConsent:
    """What milestone A leaves behind for one consent row. `revision_state` is read back from the
    database: a QUEUED request whose revision is SUPERSEDED is a consent that no longer authorizes
    anything, and the CLI has to say so instead of "already there"."""

    request: PublicationRequest
    created: bool
    payload_hash: str
    revision_state: str


class PublicationStore:
    """Reads and writes `publication_requests`, `execution_evidence`, `publication_observations`.
    Holds a pool, never a connection (the same rule as the other stores)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- accounts and operators (the two identities a consent needs) ----------------------------

    async def social_account_id(self, *, organization_id: UUID, platform: str, handle: str) -> UUID:
        """The `social_accounts` row of this handle, created on first use. `PROVISIONED` is the
        schema's default state; nothing here claims the account is READY, only that it exists."""
        async with self._pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM social_accounts WHERE organization_id = $1 AND platform = $2 "
                "AND external_account_id = $3",
                organization_id,
                platform,
                handle,
            )
            if existing is not None:
                return existing
            return await conn.fetchval(
                "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
                "VALUES ($1, $2, $3) RETURNING id",
                organization_id,
                platform,
                handle,
            )

    async def approved_revision_by_hash(
        self, *, organization_id: UUID, payload_hash: str
    ) -> tuple[UUID, UUID] | None:
        """`(revision_id, decision_id)` of an APPROVED revision with this exact digest, if a
        previous run of the consent batch wrote the approval and died before queueing it. The
        resume path: the same content is never approved twice."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT r.id AS revision_id, d.id AS decision_id
                FROM action_proposal_revisions r
                JOIN approval_decisions d
                  ON d.proposal_revision_id = r.id AND d.approved_payload_hash = r.payload_hash
                WHERE r.organization_id = $1 AND r.payload_hash = $2
                  AND r.state = 'APPROVED' AND d.decision = 'APPROVED'
                ORDER BY d.decided_at DESC LIMIT 1
                """,
                organization_id,
                payload_hash,
            )
        return (row["revision_id"], row["decision_id"]) if row is not None else None

    async def account_handle(self, account_id: UUID) -> str | None:
        """The platform login of a `social_accounts` row: what the capsule's page must show."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT external_account_id FROM social_accounts WHERE id = $1", account_id
            )

    async def stop_publishing(self, *, organization_id: UUID, at: datetime) -> int:
        """Flag every run of the organization that could still publish (ACTIVE, or FINISHED with
        approved drafts left): the worker publishes nothing after this."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE pilot_runs SET publishing_stopped_at = $2 "
                "WHERE organization_id = $1 AND status IN ('ACTIVE', 'FINISHED') "
                "AND publishing_stopped_at IS NULL",
                organization_id,
                at,
            )
        return int(result.split()[-1])

    async def sole_operator_id(self, *, organization_id: UUID) -> UUID:
        """The operator a recorded consent is filed under: the organization's single operator row.
        Two rows would be a question, not a default."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM operators WHERE organization_id = $1 AND disabled_at IS NULL "
                "ORDER BY created_at",
                organization_id,
            )
        if len(rows) != 1:
            raise ConsentError(
                f"organizacja {organization_id} ma {len(rows)} operatorów; zgoda wymaga dokładnie "
                "jednego albo jawnego --operator-id"
            )
        return rows[0]["id"]

    # -- requests ---------------------------------------------------------------------------------

    async def request_for_reaction(self, reaction_id: UUID) -> PublicationRequest | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_REQUEST_COLUMNS} FROM publication_requests WHERE reaction_id = $1",
                reaction_id,
            )
        return _request_of(row) if row is not None else None

    async def get_request(self, request_id: UUID) -> PublicationRequest | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_REQUEST_COLUMNS} FROM publication_requests WHERE id = $1", request_id
            )
        return _request_of(row) if row is not None else None

    async def requests(
        self, *, organization_id: UUID, run_id: UUID | None = None
    ) -> tuple[PublicationRequest, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_REQUEST_COLUMNS} FROM publication_requests "
                "WHERE organization_id = $1 AND ($2::uuid IS NULL OR run_id = $2) "
                "ORDER BY position",
                organization_id,
                run_id,
            )
        return tuple(_request_of(r) for r in rows)

    async def next_queued(self, *, organization_id: UUID) -> PublicationRequest | None:
        """The waiting request whose thread is YOUNGEST (the platform's post time), then document
        order; `not_before` must have passed. D-030: a comment is read while the post is on `new`,
        so a draft on a post from minutes ago goes out before a consent on yesterday's thread. The
        position still decides between drafts on posts of the same age.

        0017: THE JOIN ON THE PILOT CHAIN IS NOW OUTER, and it has to be. A request from a project
        has no reaction, so an inner join dropped it from this query entirely: the row would sit in
        the queue for ever while `pilot-publish-status` showed it waiting. For a pilot request
        nothing changes, because `reaction_id` and its thread are still NOT NULL there and the
        outer join finds exactly the row the inner one did.

        The age of a project request's thread is looked up by `target_external_id` in a SCALAR
        subquery rather than a second join: `(organization_id, workspace_id, community_id,
        external_id)` is what is unique in `observed_threads`, so a join on the external id alone
        may match twice and would silently duplicate the queue's head. A thread we never observed
        falls back to when the request was queued, which is the most recent thing we know about it.
        """
        columns = ", ".join(f"q.{column}" for column in _REQUEST_COLUMNS.split(", "))
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {columns} FROM publication_requests q "
                "LEFT JOIN pilot_reactions r ON r.id = q.reaction_id "
                "LEFT JOIN observed_threads t ON t.id = r.thread_id "
                "WHERE q.organization_id = $1 AND q.status = $2 "
                "AND (q.not_before IS NULL OR q.not_before <= now()) "
                "ORDER BY coalesce(t.created_at_source, (SELECT max(pt.created_at_source) "
                "FROM observed_threads pt WHERE pt.organization_id = q.organization_id "
                "AND pt.external_id = regexp_replace(q.target_external_id, '^t[13]_', '')), "
                "q.queued_at) DESC, "
                "q.position LIMIT 1",
                organization_id,
                REQUEST_QUEUED,
            )
        return _request_of(row) if row is not None else None

    async def create_request(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
        reaction_id: UUID,
        proposal_revision_id: UUID,
        approval_decision_id: UUID,
        position: int,
        consent_source: str,
        draft_sha256: str,
        target_external_id: str,
        target_url: str,
    ) -> PublicationRequest:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO publication_requests
                    (organization_id, workspace_id, run_id, reaction_id, proposal_revision_id,
                     approval_decision_id, position, status, consent_source, draft_sha256,
                     target_external_id, target_url)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                RETURNING {_REQUEST_COLUMNS}
                """,
                organization_id,
                workspace_id,
                run_id,
                reaction_id,
                proposal_revision_id,
                approval_decision_id,
                position,
                REQUEST_QUEUED,
                consent_source,
                draft_sha256,
                target_external_id,
                target_url,
            )
        return _request_of(row)

    async def target_for(self, request: PublicationRequest) -> PublishTarget | None:
        """The thread of this request. ONE query for both kinds, in three ordered attempts.

        The attempts are a `coalesce`, so the first that matches wins and the later ones are not
        even evaluated:

          1. the PILOT chain, `reaction -> thread`. First on purpose: it is what pilot requests
             have always used, it is exact, and it does not depend on anything having been
             observed since;
          2. the observed COMMENT the request replies to (`t1_...`), which knows its thread;
          3. the observed THREAD itself (`t3_...`).

        `None` means the thread is not in our tables, and the caller treats that as a request it
        cannot publish rather than as an empty thread.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT t.community_id, t.id AS thread_id, t.external_id AS thread_external_id,
                       t.url AS thread_url
                  FROM observed_threads t
                 WHERE t.organization_id = $1
                   AND t.id = coalesce(
                       (SELECT r.thread_id FROM pilot_reactions r WHERE r.id = $3),
                       (SELECT c.thread_id FROM observed_comments c
                         WHERE c.organization_id = $1
                           AND c.external_id = regexp_replace($2, '{FULLNAME_PREFIX}', '')
                         LIMIT 1),
                       (SELECT o.id FROM observed_threads o
                         WHERE o.organization_id = $1
                           AND o.external_id = regexp_replace($2, '{FULLNAME_PREFIX}', '')
                         ORDER BY o.created_at_source DESC LIMIT 1))
                """,
                request.organization_id,
                request.target_external_id,
                request.reaction_id,
            )
        if row is None:
            return None
        return PublishTarget(
            community_id=row["community_id"],
            thread_id=row["thread_id"],
            thread_external_id=row["thread_external_id"],
            thread_url=row["thread_url"],
        )

    async def create_project_request(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        delegation_id: UUID,
        proposal_revision_id: UUID,
        approval_decision_id: UUID,
        position: int,
        consent_source: str,
        draft_sha256: str,
        target_external_id: str,
        target_url: str,
        kind: str = KIND_REPLY,
        conn: asyncpg.Connection | None = None,
    ) -> PublicationRequest:
        """The same queue, entered from a delegation instead of a pilot run (P4).

        `conn` is optional and it is the point of this method existing separately: `presence/
        approval.py` counts the budget and enters the queue in ONE transaction, so two ticks
        cannot both find room for the last publication of the day. Called without it the method
        takes its own connection and behaves like `create_request`.

        `kind` DEFAULTS TO A REPLY because that is what every caller before 0023 meant, and a
        default that matches history is the one that cannot silently reclassify old callers. An
        own post has to say so, which is the same shape as the rest of this milestone: the wider
        permission is the one you ask for by name.
        """
        if kind not in REQUEST_KINDS:
            raise ValueError(f"rodzaj {kind!r} poza listą {REQUEST_KINDS}")
        sql = f"""
            INSERT INTO publication_requests
                (organization_id, workspace_id, project_id, delegation_id, proposal_revision_id,
                 approval_decision_id, position, status, consent_source, draft_sha256,
                 target_external_id, target_url, kind)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING {_REQUEST_COLUMNS}
            """
        args = (
            organization_id,
            workspace_id,
            project_id,
            delegation_id,
            proposal_revision_id,
            approval_decision_id,
            position,
            REQUEST_QUEUED,
            consent_source,
            draft_sha256,
            target_external_id,
            target_url,
            kind,
        )
        if conn is not None:
            return _request_of(await conn.fetchrow(sql, *args))
        async with self._pool.acquire() as own:
            return _request_of(await own.fetchrow(sql, *args))

    async def next_position(
        self,
        *,
        organization_id: UUID,
        conn: asyncpg.Connection | None = None,
    ) -> int:
        """Append to the queue, reusing the caller's consent transaction when supplied."""
        query = (
            "SELECT coalesce(max(position), 0) FROM publication_requests WHERE organization_id = $1"
        )
        if conn is not None:
            last = await conn.fetchval(query, organization_id)
        else:
            async with self._pool.acquire() as acquired:
                last = await acquired.fetchval(query, organization_id)
        return int(last) + 1

    async def set_status(
        self,
        request_id: UUID,
        *,
        status: str,
        detail: str | None = None,
        intent_id: UUID | None = None,
        attempt_id: UUID | None = None,
        published_at: datetime | None = None,
        not_before: datetime | None = None,
    ) -> PublicationRequest:
        if status not in REQUEST_STATUSES:
            raise ValueError(f"status {status!r} poza listą {REQUEST_STATUSES}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE publication_requests
                   SET status = $2,
                       detail = COALESCE($3, detail),
                       intent_id = COALESCE($4, intent_id),
                       attempt_id = COALESCE($5, attempt_id),
                       published_at = COALESCE($6, published_at),
                       not_before = COALESCE($7, not_before),
                       last_checked_at = now()
                 WHERE id = $1
                RETURNING {_REQUEST_COLUMNS}
                """,
                request_id,
                status,
                detail,
                intent_id,
                attempt_id,
                published_at,
                not_before,
            )
        if row is None:
            raise LookupError(f"nie ma wniosku publikacji {request_id}")
        return _request_of(row)

    async def requeue(
        self,
        request_id: UUID,
        *,
        proposal_revision_id: UUID,
        approval_decision_id: UUID,
        detail: str,
    ) -> PublicationRequest:
        """Point the request at a NEW approved revision of the same content and put it back in
        the queue. The old intent and attempt stay in their tables as history; the request row
        forgets them so the next tick issues a fresh grant."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE publication_requests
                   SET proposal_revision_id = $2,
                       approval_decision_id = $3,
                       intent_id = NULL,
                       attempt_id = NULL,
                       status = $4,
                       detail = $5,
                       published_at = NULL,
                       last_checked_at = now()
                 WHERE id = $1 AND status = $6
                RETURNING {_REQUEST_COLUMNS}
                """,
                request_id,
                proposal_revision_id,
                approval_decision_id,
                REQUEST_QUEUED,
                detail,
                REQUEST_UNKNOWN,
            )
        if row is None:
            raise LookupError(f"wniosek {request_id} nie jest w stanie UNKNOWN")
        return _request_of(row)

    async def requeue_count(self, request_id: UUID) -> int:
        """How many times this request was requeued: the operator's requeue decisions name the
        request in their reason, so the count lives in `approval_decisions`, not in a flag that a
        later status change could erase."""
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    "SELECT count(*) FROM approval_decisions WHERE reason LIKE $1",
                    f"ponowienie wniosku {request_id} %",
                )
            )

    async def stop_all_queued(self, *, organization_id: UUID, detail: str) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE publication_requests SET status = $3, detail = $4, last_checked_at = now() "
                "WHERE organization_id = $1 AND status = $2",
                organization_id,
                REQUEST_QUEUED,
                REQUEST_STOPPED,
                detail,
            )
        return int(result.split()[-1])

    # -- pace -------------------------------------------------------------------------------------

    async def attempts_since(self, *, organization_id: UUID, since: datetime) -> int:
        """Attempts that TOUCHED the platform in the window: SUBMIT_STARTED, CONFIRMED and UNKNOWN.

        D-035 (2026-09-17): a refusal used to be left out, on the reasoning that the actuator
        refused "without touching the page". That reasoning was wrong. The actuator opens the
        thread first and only then decides; a refusal for a locked thread, our own comment already
        there or an HTTP error is a page load that Reddit saw. Leaving those out let eleven runs
        cycling at the same second put four page loads on the account in ninety seconds while the
        policy said two an hour, and all four came back 403. A refusal now costs a slot: the pace
        is a promise about how often we appear at Reddit's door, not about how often we succeed.
        """
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    """
                    SELECT count(*) FROM execution_attempts a
                    WHERE a.organization_id = $1 AND a.started_at >= $2
                      AND a.status IN ('SUBMIT_STARTED', 'CONFIRMED', 'UNKNOWN')
                    """,
                    organization_id,
                    since,
                )
            )

    async def published_on_thread(self, *, organization_id: UUID, thread_url: str) -> bool:
        """Whether any request for this thread already reached the platform, or may have."""
        async with self._pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM publication_requests WHERE organization_id = $1 "
                    "AND target_url LIKE $2 || '%' "
                    "AND status IN ('PUBLISHING', 'PUBLISHED', 'UNKNOWN') "
                    "LIMIT 1",
                    organization_id,
                    thread_url.rstrip("/"),
                )
            )

    async def community_by_key(
        self, *, organization_id: UUID, workspace_id: UUID, platform: str, key: str
    ) -> UUID | None:
        """The observed community of this key, or `None`. Własny post celuje w społeczność, więc
        to jest jego odpowiednik `target_for`: wątku jeszcze nie ma, społeczność jest.

        Czwórka zakresu w całości, ta sama co w `presence/own_posts.py`: unikalność w
        `communities` stoi na `(organization_id, workspace_id, platform, community_key)` i
        pytanie węższe brałoby cudzy wiersz przy drugiej przestrzeni w organizacji.
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT id FROM communities WHERE organization_id = $1 AND workspace_id = $2 "
                "AND platform = $3 AND community_key = $4",
                organization_id,
                workspace_id,
                platform,
                key,
            )

    async def draft_already_published(self, *, organization_id: UUID, draft_sha256: str) -> bool:
        """Whether this exact text already reached the platform, or may have.

        Odpowiednik `published_on_thread` dla własnego postu: tam pilnujemy, żeby nasze konto nie
        odezwało się dwa razy w jednym wątku, tu, żeby ta sama treść nie stanęła dwa razy jako
        nasz wątek. Liczy się odcisk TREŚCI, bo przy własnym poście nie ma adresu celu, który
        odróżniałby dwa wnioski, a UNKNOWN liczy się jak publikacja: skutek nieznany to nie jest
        zgoda na powtórzenie (L5335).
        """
        async with self._pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM publication_requests WHERE organization_id = $1 "
                    "AND draft_sha256 = $2 AND kind = $3 "
                    "AND status IN ('PUBLISHING', 'PUBLISHED', 'UNKNOWN') "
                    "LIMIT 1",
                    organization_id,
                    draft_sha256,
                    KIND_OWN_POST,
                )
            )

    # -- evidence and observations --------------------------------------------------------------

    async def record_evidence(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        attempt_id: UUID,
        external_id: str,
        permalink: str,
        account: str,
        observed_at: datetime,
        source: str,
        raw: dict[str, Any],
    ) -> None:
        import json

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO execution_evidence
                    (organization_id, workspace_id, attempt_id, external_id, permalink, account,
                     observed_at, source, raw)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                ON CONFLICT (attempt_id) DO NOTHING
                """,
                organization_id,
                workspace_id,
                attempt_id,
                external_id,
                permalink,
                account,
                observed_at,
                source,
                json.dumps(raw, ensure_ascii=False),
            )

    async def evidence_for(self, attempt_id: UUID) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT external_id, permalink, account, observed_at, source, raw "
                "FROM execution_evidence WHERE attempt_id = $1",
                attempt_id,
            )
        return dict(row) if row is not None else None

    async def record_observation(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        request_id: UUID,
        observed_at: datetime,
        still_present: bool,
        score: int | None,
        reply_count: int,
        raw: dict[str, Any],
        viewer: str = VIEWER_AUTHOR,
    ) -> None:
        """One read-back of our comment. `viewer` defaults to AUTHOR because that is what every
        caller written before migration 0012 does: the capsule's Chrome carries the account's
        session, so its reads are the author's reads and must be recorded as such."""
        import json

        if viewer not in VIEWERS:
            raise ValueError(f"viewer {viewer!r} poza listą {VIEWERS}")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO publication_observations
                    (organization_id, workspace_id, request_id, observed_at, still_present, score,
                     reply_count, raw, viewer)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
                """,
                organization_id,
                workspace_id,
                request_id,
                observed_at,
                still_present,
                score,
                reply_count,
                json.dumps(raw, ensure_ascii=False),
                viewer,
            )

    async def replies_to(
        self, *, organization_id: UUID, comment_external_id: str
    ) -> list[dict[str, Any]]:
        """People's replies under our comment, as the observation store recorded them at the
        worker's read-backs: `observed_comments` rows whose parent is our comment."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT author, body, created_at_source FROM observed_comments "
                "WHERE organization_id = $1 AND parent_external_id = $2 ORDER BY created_at_source",
                organization_id,
                comment_external_id,
            )
        return [dict(r) for r in rows]

    async def latest_observation(
        self, request_id: UUID, *, viewer: str | None = None
    ) -> dict[str, Any] | None:
        """The newest read-back of this request, optionally narrowed to one viewer. `None` means
        any viewer: the pace of the author-side observation loop is unchanged by 0012."""
        if viewer is not None and viewer not in VIEWERS:
            raise ValueError(f"viewer {viewer!r} poza listą {VIEWERS}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT observed_at, still_present, score, reply_count, viewer "
                "FROM publication_observations "
                "WHERE request_id = $1 AND ($2::text IS NULL OR viewer = $2) "
                "ORDER BY observed_at DESC LIMIT 1",
                request_id,
                viewer,
            )
        return dict(row) if row is not None else None

    # -- public visibility (migration 0012) ------------------------------------------------------

    async def record_anonymous_look(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        request_id: UUID,
        look: AnonymousLook,
        raw: dict[str, Any] | None = None,
        removal_scope: str | None = None,
    ) -> str:
        """Record ONE successful anonymous read and recompute the request's public visibility.

        Returns the visibility the request now carries. The observation row and the verdict are
        written in one transaction: a verdict whose evidence row failed to land would be exactly
        the shape of a gate announcing a result it did not measure.

        `removal_scope` says WHAT is gone when the verdict is REMOVED: our own comment
        (`COMMENT`) or the whole thread it hangs under (`PARENT_POST`). It is written only
        together with a REMOVED verdict, because the column means nothing without one, and left
        as NULL whenever the caller could not tell the two apart. Measured 26.09: a reply that
        was REMOVED because Reddit's filters took down the PARENT POST reads, without this,
        exactly like a reply the community rejected, and the wrong reading already produced a
        wrong hypothesis about the account (0032).
        """
        if removal_scope is not None and removal_scope not in REMOVAL_SCOPES:
            raise ValueError(f"zakres {removal_scope!r} poza listą {REMOVAL_SCOPES}")
        import json

        payload = dict(raw or {})
        payload.setdefault("body_present", look.body is not None)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO publication_observations
                    (organization_id, workspace_id, request_id, observed_at, still_present, score,
                     reply_count, raw, viewer)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
                """,
                organization_id,
                workspace_id,
                request_id,
                look.observed_at,
                look.visible,
                look.score,
                look.reply_count,
                json.dumps(payload, ensure_ascii=False),
                VIEWER_ANONYMOUS,
            )
            reported = await conn.fetchval(
                "SELECT removal_reported_at FROM publication_requests WHERE id = $1", request_id
            )
            visibility, checked_at = visibility_of(anonymous=look, removal_reported_at=reported)
            # Zakres jedzie tylko z werdyktem REMOVED i tylko wtedy, gdy wołający go ustalił.
            # CHECK w 0032 odrzuciłby zakres przy innym werdykcie; odsiewamy go tutaj, żeby
            # pomyłka wołającego nie wywracała całej transakcji obserwacji.
            scope = removal_scope if visibility == "REMOVED" else None
            await conn.execute(
                "UPDATE publication_requests "
                "SET public_visibility = $2, public_visibility_checked_at = $3, "
                "removal_scope = CASE WHEN $2 = 'REMOVED' THEN COALESCE($4, removal_scope) "
                "ELSE NULL END WHERE id = $1",
                request_id,
                visibility,
                checked_at,
                scope,
            )
        return visibility

    async def report_removal(
        self,
        request_id: UUID,
        *,
        reported_at: datetime,
        source: str,
        detail: str | None = None,
    ) -> str:
        """Record a removal reported OUTSIDE an anonymous read (today: the account's inbox).

        Idempotent on the first report: a second AutoModerator reply about the same comment does
        not move the timestamp, because the moment we learned of the removal is the evidence.
        """
        if source not in REMOVAL_SOURCES:
            raise ValueError(f"źródło {source!r} poza listą {REMOVAL_SOURCES}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE publication_requests
                   SET removal_reported_at = COALESCE(removal_reported_at, $2),
                       removal_source = COALESCE(removal_source, $3),
                       public_visibility = $4,
                       public_visibility_checked_at = GREATEST(
                           COALESCE(public_visibility_checked_at, $2), $2
                       ),
                       detail = COALESCE($5, detail)
                 WHERE id = $1
                RETURNING public_visibility
                """,
                request_id,
                reported_at,
                source,
                VISIBILITY_REMOVED,
                detail,
            )
        if row is None:
            raise LookupError(f"nie ma wniosku publikacji {request_id}")
        return str(row["public_visibility"])

    async def request_by_comment_external_id(
        self, *, organization_id: UUID, external_id: str
    ) -> PublicationRequest | None:
        """The request whose published comment carries this platform id (`t1_…`).

        The join goes through `execution_evidence`, because that is where the comment's own id is
        recorded; `publication_requests.target_external_id` is what we REPLIED TO, not what we
        wrote, and matching on it would attribute an inbox reply to the wrong request.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {", ".join(f"q.{c}" for c in _REQUEST_COLUMNS.split(", "))}
                FROM publication_requests q
                JOIN execution_evidence e ON e.attempt_id = q.attempt_id
                WHERE q.organization_id = $1 AND e.external_id = $2
                LIMIT 1
                """,
                organization_id,
                external_id,
            )
        return _request_of(row) if row is not None else None


async def approve_reaction(
    *,
    approvals: ApprovalStore,
    publication: PublicationStore,
    reaction: PilotReaction,
    organization_id: UUID,
    workspace_id: UUID,
    account_id: UUID,
    operator_id: UUID,
    consent_source: str,
    position: int,
    now: datetime,
) -> ApprovedConsent:
    """ONE draft through the approval path: proposal, revision (DRAFT, then READY_FOR_APPROVAL),
    the operator's APPROVED decision with `consent_source` as its reason, and the publication
    request. Used by the human consent document (`approve_consents`) and by the signed policy
    (`pilot/policy.py`, D-029): the trail is the same, only who says "yes" differs, and the
    decision's reason says which.

    A reaction that already has a request is returned as existing. The resume path: an APPROVED
    revision with exactly this digest, left by a run that died before queueing, is reused, so the
    same content is never approved twice.
    """
    existing = await publication.request_for_reaction(reaction.id)
    if existing is not None:
        revision = await approvals.fetch_revision(
            existing.proposal_revision_id, organization_id=organization_id
        )
        return ApprovedConsent(
            request=existing,
            created=False,
            payload_hash=revision.payload_hash if revision is not None else "",
            revision_state=revision.state.value if revision is not None else "?",
        )
    content = hash_input_for(
        reaction,
        organization_id=organization_id,
        workspace_id=workspace_id,
        account_id=account_id,
    )
    resumed = await publication.approved_revision_by_hash(
        organization_id=organization_id, payload_hash=content.payload_hash()
    )
    if resumed is not None:
        revision_id, decision_id = resumed
    else:
        proposal_id = await approvals.create_proposal(
            organization_id=organization_id,
            workspace_id=workspace_id,
            social_account_id=account_id,
        )
        draft = await approvals.record_revision(
            organization_id=organization_id,
            workspace_id=workspace_id,
            proposal_id=proposal_id,
            revision_no=1,
            hash_input=content,
            state=ActionProposalState.DRAFT,
            expires_at=now + CONSENT_TTL,
            source_observed_at=reaction.read_at,
        )
        await approvals.move_revision(
            draft.id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            target=ActionProposalState.READY_FOR_APPROVAL,
            actor_ref=f"operator:{operator_id}",
        )
        decision, approved = await approvals.record_decision(
            revision_id=draft.id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            decision=APPROVED_DECISION,
            operator_id=operator_id,
            reason=consent_source,
        )
        revision_id, decision_id = approved.id, decision.id
    target_external_id, target_url = target_of(reaction)
    request = await publication.create_request(
        organization_id=organization_id,
        workspace_id=workspace_id,
        run_id=reaction.run_id,
        reaction_id=reaction.id,
        proposal_revision_id=revision_id,
        approval_decision_id=decision_id,
        position=position,
        consent_source=consent_source,
        draft_sha256=draft_sha256(reaction.draft or ""),
        target_external_id=target_external_id,
        target_url=target_url,
    )
    return ApprovedConsent(
        request=request,
        created=True,
        payload_hash=content.payload_hash(),
        revision_state=ActionProposalState.APPROVED.value,
    )


async def approve_consents(
    pool: asyncpg.Pool,
    *,
    rows: tuple[ConsentRow, ...],
    organization_id: UUID,
    workspace_id: UUID,
    account_handle: str,
    consent_source: str,
    operator_id: UUID | None = None,
    now: datetime,
    document_account: str | None = None,
) -> tuple[ApprovedConsent, ...]:
    """Record every consent row through the existing approval path and queue its publication.

    ALL rows are verified against the database BEFORE the first write: a reaction that does not
    exist, has no draft, or whose draft's sha256 differs from the document refuses the whole batch.
    A reaction that already has a request is reported as existing and left alone (re-running the
    command is safe). The proposal revision is created as DRAFT, moved to READY_FOR_APPROVAL, and
    approved by the operator in one `record_decision` call, so a consent never points at a revision
    that is not in the state the consent put it in.
    """
    pilot = PilotStore(pool)
    publication = PublicationStore(pool)
    approvals = ApprovalStore(pool)

    if document_account is not None and document_account.lower() != account_handle.lower():
        raise ConsentError(
            f"dokument zgody nazywa konto {document_account!r}, a start dotyczy "
            f"{account_handle!r}; konto wchodzi do digestu zgody, więc to nie jest ta sama zgoda"
        )

    reactions: dict[UUID, PilotReaction] = {}
    for row in rows:
        reaction = await pilot.get_reaction(row.reaction_id)
        run = await pilot.get_run(reaction.run_id) if reaction is not None else None
        if reaction is None or run is None or run.organization_id != organization_id:
            raise ConsentError(f"zgoda #{row.position}: nie ma reakcji {row.reaction_id}")
        if not reaction.draft:
            raise ConsentError(
                f"zgoda #{row.position}: reakcja {row.reaction_id} nie ma szkicu "
                f"({reaction.decision})"
            )
        actual = draft_sha256(reaction.draft)
        if actual != row.draft_sha256:
            raise ConsentError(
                f"zgoda #{row.position}: sha256 szkicu w bazie {actual[:12]}… różni się od zgody "
                f"{row.draft_sha256[:12]}…; zgoda dotyczy innego brzmienia, nic nie zapisano"
            )
        if reaction.thread_external_id != row.thread_external_id:
            raise ConsentError(
                f"zgoda #{row.position}: wątek {reaction.thread_external_id} zamiast "
                f"{row.thread_external_id} z dokumentu"
            )
        if len(reaction.draft) != row.chars:
            raise ConsentError(
                f"zgoda #{row.position}: szkic ma {len(reaction.draft)} znaków, dokument mówi "
                f"{row.chars}"
            )
        reactions[row.reaction_id] = reaction

    operator = operator_id or await publication.sole_operator_id(organization_id=organization_id)
    account_id = await publication.social_account_id(
        organization_id=organization_id, platform=PLATFORM, handle=account_handle
    )

    results: list[ApprovedConsent] = []
    for row in rows:
        results.append(
            await approve_reaction(
                approvals=approvals,
                publication=publication,
                reaction=reactions[row.reaction_id],
                organization_id=organization_id,
                workspace_id=workspace_id,
                account_id=account_id,
                operator_id=operator,
                consent_source=consent_source,
                position=row.position,
                now=now,
            )
        )
    return tuple(results)


__all__ = [
    "ADAPTER_VERSION",
    "CONSENT_TTL",
    "ApprovedConsent",
    "ConsentDocument",
    "ConsentError",
    "ConsentRow",
    "PublicationRequest",
    "PublicationStore",
    "approve_consents",
    "approve_reaction",
    "draft_sha256",
    "hash_input_for",
    "parse_consent_document",
    "parse_consent_table",
    "target_of",
]
