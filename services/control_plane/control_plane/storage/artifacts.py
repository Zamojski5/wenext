"""Artifacts: who may read them, and the command that actually enforces retention (L5196).

    "encrypted profile storage, artifacts ACL i retention" (L5196)
    "trace po zalogowaniu ma krótki TTL, ACL i szyfrowanie" (L3898)
    "`DELETING` obejmuje profil, cold snapshots, artefakty, metadata CDP oraz
     crypto-erasure klucza" (L3900)

THREE THINGS THIS MODULE IS, AND ONE IT IS NOT.

1.  **A writer that cannot invent a retention.** `record` takes `retention_until` as a required
    keyword and passes it to PostgreSQL untouched. There is no `or`, no fallback, no default.
    Migration 0003 made the column `NOT NULL` with no `DEFAULT` for the reason it states there:
    two different retentions (thirty days, twenty-four hours for a trace) cannot be one column
    default, so whoever writes the artifact decides. A code-side default would put that decision
    back in one place and hide it. `test_an_artifact_without_a_retention_value_is_rejected`
    measures this by passing `None` and asserting the SQLSTATE — `23502`, not merely "something
    was raised", because "something was raised" also passes when a completely different
    constraint fired.

2.  **A read path with exactly one door.** `read` is the only way out of this module to the
    bytes; it compares the row's `acl` against the caller's `Reader` and refuses. `ArtifactStore`
    exposes no raw fetch, so there is no second path around the comparison.

3.  **A retention command that removes things.** A retention that nothing executes is a
    declaration, and this repository has a name for declarations dressed as controls. `sweep`
    deletes expired temporary artifacts and, when handed a volume, their bytes. Documents and
    notes explicitly kept in a project's knowledge library remain while that source references
    them. Their former versions become collectible when unreferenced. The 30-day threshold for
    web caches, logs and other temporary artifacts remains unchanged.
    `test_an_artifact_past_its_retention_is_removed_by_a_command` measures it WITH A LIVE
    ARTIFACT PRESENT — without that control, a sweeper that deleted the whole table would pass.

What it is NOT: an encryptor. The `encrypted` column records whether the bytes on the volume are
ciphertext; this module never makes them so, because the only key in the system is the runner's
(D-018) and the control plane does not hold it. `encrypted` is therefore a REQUIRED argument
rather than a flag defaulting to `true`: a row claiming encryption nobody performed is worse than
a row admitting there is none. One consequence is worth knowing and is enforced by the schema,
not by this file — `trace_artifact_is_encrypted` means the control plane, having no key, simply
cannot write a `TRACE` row. `test_an_unencrypted_trace_is_refused_by_the_database` measures that.

NO AUDIT ROWS HERE, and it is a gap rather than a decision. `control_plane.audit.AUDIT_KINDS` is
a closed vocabulary owned by another module; it has nothing for "an artifact was written" or "an
artifact was destroyed by retention". Recording a deletion under an unrelated kind would make the
log lie, and adding a kind is an edit to somebody else's module. So the sweeper deletes silently.
Stated here in full, the way `approval/store.py` states the same gap, because the day somebody
asks "who deleted this artifact" the answer has to be findable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import asyncpg

from control_plane.storage.config import ArtifactAcl, ArtifactKind, Reader

#: Which readers each ACL admits. A TABLE and not an `if`, so a test can assert that every member
#: of `ArtifactAcl` has an entry — a value the schema accepts but this map does not know would
#: otherwise raise a `KeyError` at read time, in production, on an artifact somebody needs.
READERS_BY_ACL: Final[dict[ArtifactAcl, frozenset[Reader]]] = {
    ArtifactAcl.OPERATOR_ONLY: frozenset({Reader.OPERATOR}),
    ArtifactAcl.RUNNER_ONLY: frozenset({Reader.RUNNER}),
    ArtifactAcl.OPERATOR_AND_RUNNER: frozenset({Reader.OPERATOR, Reader.RUNNER}),
}

#: The prefix `artifacts_checksum_is_sha256` demands. Written once.
CHECKSUM_PREFIX: Final[str] = "sha256:"

#: Owner-only, like the profile volume. "katalogi mają minimalne uprawnienia" (L3896).
#: NOT MEASURED: no test asserts that these modes end up on the files, and none asserts they
#: survive a container's volume mount. The constants are set; the effect is unverified.
ARTIFACT_DIRECTORY_MODE: Final[int] = 0o700
ARTIFACT_FILE_MODE: Final[int] = 0o600


class ArtifactStoreError(Exception):
    """Base for every refusal here."""


class ArtifactNotFound(ArtifactStoreError):
    """No such artifact in this organization. Distinct from a refusal, on purpose.

    Merging the two would be tidier and would also mean an operator cannot tell "it expired and
    the sweeper took it" from "you are not allowed to see it" — two situations with different
    next steps.
    """


class ArtifactReadRefused(ArtifactStoreError):
    """The reader is not on the artifact's ACL."""


class ArtifactBytesCorrupted(ArtifactStoreError):
    """The bytes on the volume do not hash to the checksum stored with the row."""


@dataclass(frozen=True)
class ArtifactRecord:
    """The row as this module hands it back. Bytes are NOT in it — those come from `read`."""

    artifact_id: UUID
    organization_id: UUID
    workspace_id: UUID
    kind: ArtifactKind
    acl: ArtifactAcl
    storage_ref: str
    checksum: str
    size_bytes: int
    encrypted: bool
    created_at: datetime
    retention_until: datetime


def checksum_for(content: bytes) -> str:
    """The value written to `artifacts.checksum`, in the form the CHECK constraint demands.

    Computed from the bytes rather than accepted as an argument. A checksum a caller supplies is
    a claim about bytes nobody compared; this one is a measurement of the bytes being stored.
    """
    return CHECKSUM_PREFIX + hashlib.sha256(content).hexdigest()


class ArtifactVolume:
    """Where artifact bytes live. Same shape and the same reasoning as `ProfileVolume`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(ARTIFACT_DIRECTORY_MODE)

    def path_for(self, storage_ref: str) -> Path:
        candidate = (self.root / storage_ref).resolve()
        if not candidate.is_relative_to(self.root.resolve()):
            raise ArtifactStoreError(
                f"uchwyt {storage_ref!r} wskazuje poza wolumen artefaktów ({self.root})"
            )
        return candidate

    def write(self, storage_ref: str, content: bytes) -> None:
        target = self.path_for(storage_ref)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(ARTIFACT_FILE_MODE)

    def read(self, storage_ref: str) -> bytes:
        return self.path_for(storage_ref).read_bytes()

    def remove(self, storage_ref: str) -> bool:
        """Deletes the bytes. Returns whether there was a file to delete.

        A missing file is not an error: the row is the record of what should exist, and retention
        has to finish its pass even when somebody already removed a blob by hand.
        """
        target = self.path_for(storage_ref)
        existed = target.exists()
        target.unlink(missing_ok=True)
        return existed


def readers_admitted_by(acl: ArtifactAcl) -> frozenset[Reader]:
    """The ACL comparison, as one function, so there is one place to break in a mutation test."""
    try:
        return READERS_BY_ACL[acl]
    except KeyError as unknown:
        raise ArtifactStoreError(
            f"ACL {acl!r} nie ma wpisu w tabeli uprawnień. Baza dopuszcza wartość, której kod "
            "nie zna — to jest rozjazd schematu z modułem, nie brak uprawnienia"
        ) from unknown


_SELECT_SQL: Final[str] = """
SELECT id, organization_id, workspace_id, kind, acl, storage_ref, checksum, size_bytes,
       encrypted, created_at, retention_until
FROM artifacts
WHERE id = $1 AND organization_id = $2
"""

_INSERT_SQL: Final[str] = """
INSERT INTO artifacts
    (id, organization_id, workspace_id, kind, storage_ref, checksum, size_bytes, encrypted, acl,
     produced_by_execution_attempt_id, retention_until)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
RETURNING id, organization_id, workspace_id, kind, acl, storage_ref, checksum, size_bytes,
          encrypted, created_at, retention_until
"""

#: Retention uses one transaction. `now()` is the transaction's start time, so every
#: row in one pass is judged against ONE moment — a loop comparing against a clock that moves
#: between rows deletes a different set depending on how long the pass took.
_EXPIRE_KNOWLEDGE_CACHE_SQL: Final[str] = """
UPDATE knowledge_sources s
SET raw_artifact_id = NULL, content_hash = NULL, status = 'UNAVAILABLE',
    unavailable_reason = 'Kopia źródła wygasła. Odśwież źródło, aby ponownie je przeczytać.'
FROM artifacts a
WHERE s.raw_artifact_id = a.id
  AND s.organization_id = a.organization_id AND s.workspace_id = a.workspace_id
  AND a.retention_until <= now() AND s.kind NOT IN ('DOCUMENT', 'USER_NOTE')
"""

_SWEEP_SQL: Final[str] = """
DELETE FROM artifacts
WHERE retention_until <= now()
  AND NOT EXISTS (
      SELECT 1 FROM knowledge_sources s
       WHERE s.raw_artifact_id = artifacts.id
         AND s.organization_id = artifacts.organization_id
         AND s.workspace_id = artifacts.workspace_id
  )
RETURNING id, storage_ref
"""


def _record_from_row(row: asyncpg.Record) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=row["id"],
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        kind=ArtifactKind(row["kind"]),
        acl=ArtifactAcl(row["acl"]),
        storage_ref=row["storage_ref"],
        checksum=row["checksum"],
        size_bytes=row["size_bytes"],
        encrypted=row["encrypted"],
        created_at=row["created_at"],
        retention_until=row["retention_until"],
    )


class ArtifactStore:
    """Writing, reading and expiring artifacts. Every method takes the caller's connection.

    The connection is an argument rather than a pool held by the object for the same reason the
    approval store does it: the caller owns the transaction, and an audit row — when this module
    ever gets a vocabulary to write one — has to land inside the same one as the change.
    """

    async def record(
        self,
        connection: asyncpg.Connection,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        kind: ArtifactKind,
        acl: ArtifactAcl,
        content: bytes,
        encrypted: bool,
        retention_until: datetime | None,
        volume: ArtifactVolume | None = None,
        produced_by_execution_attempt_id: UUID | None = None,
    ) -> ArtifactRecord:
        """Writes the row, then the bytes.

        `retention_until` is typed `datetime | None` and the `None` is NOT a convenience: it is
        the value that must reach the database and be rejected there. Nothing in this method
        inspects it. See the module docstring.

        The row goes in BEFORE the bytes, so a rejected insert — a missing retention, an unknown
        kind, an unencrypted trace — leaves nothing behind on the volume.
        """
        artifact_id = uuid4()
        storage_ref = f"artifact-{artifact_id}.bin"
        row = await connection.fetchrow(
            _INSERT_SQL,
            artifact_id,
            organization_id,
            workspace_id,
            kind.value,
            storage_ref,
            checksum_for(content),
            len(content),
            encrypted,
            acl.value,
            produced_by_execution_attempt_id,
            retention_until,
        )
        if volume is not None:
            volume.write(storage_ref, content)
        return _record_from_row(row)

    async def fetch(
        self, connection: asyncpg.Connection, *, organization_id: UUID, artifact_id: UUID
    ) -> ArtifactRecord:
        """Metadata only, and deliberately so: this returns no bytes, so it grants no read.

        `organization_id` is in the WHERE clause and not checked afterwards — an artifact of
        another organization is absent, not forbidden.
        """
        row = await connection.fetchrow(_SELECT_SQL, artifact_id, organization_id)
        if row is None:
            raise ArtifactNotFound(
                f"nie ma artefaktu {artifact_id} w organizacji {organization_id}"
            )
        return _record_from_row(row)

    async def read(
        self,
        connection: asyncpg.Connection,
        *,
        organization_id: UUID,
        artifact_id: UUID,
        reader: Reader,
        volume: ArtifactVolume,
    ) -> bytes:
        """The only path from this module to artifact bytes, and it goes through the ACL.

        Two refusals, in this order: the reader is not on the ACL, or the bytes do not match the
        checksum stored beside them. The second one is not paranoia about disks — the checksum is
        also what tells a reader that the file was not swapped underneath the row.
        """
        record = await self.fetch(
            connection, organization_id=organization_id, artifact_id=artifact_id
        )
        if reader not in readers_admitted_by(record.acl):
            raise ArtifactReadRefused(
                f"artefakt {artifact_id} ma ACL {record.acl.value}, a o bajty prosi "
                f"{reader.value}. Odmowa: „artefakty mają ACL” (L3898) znaczy, że lista jest "
                "sprawdzana, a nie że jest zapisana w kolumnie"
            )
        content = volume.read(record.storage_ref)
        actual = checksum_for(content)
        if actual != record.checksum:
            raise ArtifactBytesCorrupted(
                f"artefakt {artifact_id}: bajty na wolumenie mają sumę {actual}, a wiersz mówi "
                f"{record.checksum}. Plik został podmieniony albo uszkodzony"
            )
        return content

    async def sweep(
        self, connection: asyncpg.Connection, *, volume: ArtifactVolume | None = None
    ) -> list[UUID]:
        """Remove expired temporary artifacts, preserving documents still in the library.

        Returns the identifiers removed, so a caller can report a number instead of assuming one.
        Not tenant-scoped: retention is a property of the row, not of who is asking, and a sweeper
        that skipped an organization because nobody named it would keep exactly the artifacts
        nobody is watching.
        """
        async with connection.transaction():
            await connection.execute(_EXPIRE_KNOWLEDGE_CACHE_SQL)
            rows = await connection.fetch(_SWEEP_SQL)
        if volume is not None:
            for row in rows:
                volume.remove(row["storage_ref"])
        return [row["id"] for row in rows]
