"""Artifact ACL and retention against a REAL PostgreSQL (L5196, L3897-3898, D-017).

THREE CLAIMS, THREE WAYS THEY COULD HAVE BEEN FAKED, AND WHAT THIS FILE DOES INSTEAD.

1.  "An artifact with no retention is rejected." Faked by `pytest.raises(Exception)` — which also
    passes when a totally different constraint fires, or when the connection was already dead.
    Here the SQLSTATE is asserted: `23502`, `not_null_violation`, on the column named. And the
    same call WITH a retention is run first, so the rejection cannot be the writer being broken.

2.  "Reading without the right is refused." Faked by testing one forbidden pair. A refusal
    mechanism that refuses everything passes that. Here the FULL six-cell matrix of ACL by reader
    is written out by hand — not derived from the module under test, which would be a tautology —
    and its size is asserted before it is used.

3.  "Expired artifacts are removed." Faked by a sweeper with no `WHERE` clause and a test with
    only expired rows in the table. Here a LIVE artifact sits next to the expired one and has to
    survive, bytes included.

MEASURED, each with the mutation applied to the production code and the test SEEN FAILING before
the code was put back and the file's checksum compared against its pre-mutation value:
  * the missing retention reaching PostgreSQL rather than being filled in by the store;
  * the ACL comparison on the read path;
  * the `WHERE retention_until <= now()` of the sweeper, in both directions;
  * the checksum verification on the read path;
  * the frozen D-017 numbers;
  * the L3897 refusal to hand out a retention for a trace taken during login;
  * `trace_artifact_is_encrypted`, the schema CHECK that stops an unencrypted trace;
  * `AND organization_id = $2`, the tenant boundary of the read side.

NOT MEASURED HERE, and therefore not claimed anywhere in the storage package:
  * that anything in the product CALLS the sweeper on a schedule. `sweep` is a command; F1 wires
    no timer to it, so "retention is enforced" is true of the command and not yet of the system;
  * that the Browser Runner consults `retention_until_for` before starting a trace — the runner
    has no capture path in F1 (E1.F2), so there is no call site to measure;
  * that artifact bytes are encrypted. They are not: the control plane holds no key (D-018), the
    `encrypted` column records that honestly, and the schema's `trace_artifact_is_encrypted` is
    what stops an unencrypted trace from existing.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from control_plane.storage.artifacts import (
    READERS_BY_ACL,
    ArtifactBytesCorrupted,
    ArtifactNotFound,
    ArtifactReadRefused,
    ArtifactStore,
    ArtifactVolume,
    checksum_for,
)
from control_plane.storage.config import (
    ARTIFACT_RETENTION,
    RETENTION_THRESHOLDS,
    TRACE_AFTER_LOGIN_RETENTION,
    ArtifactAcl,
    ArtifactIsNotRecorded,
    ArtifactKind,
    BrowserPhase,
    Reader,
    retention_until_for,
)

#: The six cells of the ACL matrix, written out BY HAND. Deriving them from `READERS_BY_ACL`
#: would make this test agree with the module under test by construction — the shape that let a
#: deleted egress mechanism stay green through 36 tests.
EXPECTED_ACCESS: dict[tuple[ArtifactAcl, Reader], bool] = {
    (ArtifactAcl.OPERATOR_ONLY, Reader.OPERATOR): True,
    (ArtifactAcl.OPERATOR_ONLY, Reader.RUNNER): False,
    (ArtifactAcl.RUNNER_ONLY, Reader.OPERATOR): False,
    (ArtifactAcl.RUNNER_ONLY, Reader.RUNNER): True,
    (ArtifactAcl.OPERATOR_AND_RUNNER, Reader.OPERATOR): True,
    (ArtifactAcl.OPERATOR_AND_RUNNER, Reader.RUNNER): True,
}


async def _database_now(pool):
    """The DATABASE clock. `artifacts_retention_is_in_the_future` compares `retention_until`
    against a `created_at` the server writes, so a deadline computed from the test process's
    clock fails as a constraint violation that looks like a bug in the code under test."""
    async with pool.acquire() as connection:
        return await connection.fetchval("SELECT now()")


async def _seed_expired_artifact(pool, tenant, volume: ArtifactVolume, content: bytes) -> UUID:
    """One artifact whose retention passed ten days ago, written with raw SQL.

    Raw SQL and not `ArtifactStore.record`, because `created_at` has to be in the past for
    `artifacts_retention_is_in_the_future` to accept a `retention_until` that is also in the past.
    The subject of this test is the SWEEPER, not the writer; the writer has its own tests above
    and below.
    """
    artifact_id = uuid4()
    storage_ref = f"artifact-{artifact_id}.bin"
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO artifacts
                (id, organization_id, workspace_id, kind, storage_ref, checksum, size_bytes,
                 encrypted, acl, created_at, retention_until)
            VALUES ($1, $2, $3, 'LOG', $4, $5, $6, false, 'OPERATOR_ONLY',
                    now() - interval '40 days', now() - interval '10 days')
            """,
            artifact_id,
            tenant["organization"],
            tenant["workspace"],
            storage_ref,
            checksum_for(content),
            len(content),
        )
    volume.write(storage_ref, content)
    return artifact_id


# ---------------------------------------------------------------------------
# Retention: the value the writer may not invent, and the command that enforces it
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_an_artifact_without_a_retention_value_is_rejected(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """`retention_until = None` reaches PostgreSQL and comes back as `23502`.

    The SQLSTATE, not merely "an exception". Migration 0003 made the column `NOT NULL` with no
    `DEFAULT` on purpose — thirty days and twenty-four hours cannot be one default — so the
    absence of a retention has to be a database refusal and not a number the store picked.

    Measured: adding `retention_until = retention_until or now + ARTIFACT_RETENTION` to
    `ArtifactStore.record` makes this test fail with `DID NOT RAISE`.
    """
    store = ArtifactStore()
    volume = ArtifactVolume(tmp_path / "artifacts")
    content = b"kontrola: artefakt bez retencji"
    moment = await _database_now(pool)

    async with pool.acquire() as connection:
        accepted = await store.record(
            connection,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            kind=ArtifactKind.LOG,
            acl=ArtifactAcl.OPERATOR_ONLY,
            content=content,
            encrypted=False,
            retention_until=moment + ARTIFACT_RETENTION,
            volume=volume,
        )
    assert accepted.retention_until is not None, (
        "kontrola pozytywna: ten sam zapis z podaną retencją musi przejść, inaczej odmowa niżej "
        "nie dowodzi niczego o retencji"
    )

    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.PostgresError) as rejected:
            await store.record(
                connection,
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                kind=ArtifactKind.LOG,
                acl=ArtifactAcl.OPERATOR_ONLY,
                content=content,
                encrypted=False,
                retention_until=None,
                volume=volume,
            )
    assert rejected.value.sqlstate == "23502", (
        f"artefakt bez retencji odrzucono kodem {rejected.value.sqlstate}, a ma go odrzucać "
        "NOT NULL na `retention_until` (23502). Inny kod znaczy, że zadziałał inny warunek"
    )

    on_the_volume = sorted(path.name for path in (tmp_path / "artifacts").iterdir())
    assert len(on_the_volume) == 1, (
        f"na wolumenie leży {len(on_the_volume)} plików zamiast jednego — odrzucony INSERT "
        "zostawił osierocone bajty"
    )


@pytest.mark.db
async def test_an_artifact_past_its_retention_is_removed_by_a_command(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """Retention that nothing executes is a declaration. This runs the command.

    THE LIVE ARTIFACT IS THE MEASUREMENT. Without it, a sweeper issuing `DELETE FROM artifacts`
    with no `WHERE` would pass this test — which is the same hole that let a shortened fencing
    tuple through four writing statements unnoticed.

    Measured in both directions: deleting `WHERE retention_until <= now()` makes the live
    artifact disappear and the test fail on that assertion; replacing `<=` with a condition that
    matches nothing makes it fail on the expired one.
    """
    store = ArtifactStore()
    volume = ArtifactVolume(tmp_path / "artifacts")
    expired_bytes = b"stary log, retencja minela"
    live_bytes = b"swiezy log, retencja trwa"

    expired_id = await _seed_expired_artifact(pool, tenant, volume, expired_bytes)
    moment = await _database_now(pool)
    async with pool.acquire() as connection:
        live = await store.record(
            connection,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            kind=ArtifactKind.LOG,
            acl=ArtifactAcl.OPERATOR_ONLY,
            content=live_bytes,
            encrypted=False,
            retention_until=moment + ARTIFACT_RETENTION,
            volume=volume,
        )

    expired_path = volume.path_for(f"artifact-{expired_id}.bin")
    assert expired_path.exists(), "przed zamiatanie bajty wygasłego artefaktu mają leżeć na dysku"

    async with pool.acquire() as connection:
        removed = await store.sweep(connection, volume=volume)

    assert len(removed) >= 1, "zamiatacz nie usunął niczego, więc nie zmierzył niczego"
    assert expired_id in removed, "artefakt po retencji przeżył polecenie zamiatające"
    assert live.artifact_id not in removed, (
        "zamiatacz zabrał artefakt, którego retencja jeszcze trwa — warunek `retention_until "
        "<= now()` nie działa albo go nie ma"
    )

    async with pool.acquire() as connection:
        with pytest.raises(ArtifactNotFound):
            await store.fetch(
                connection, organization_id=tenant["organization"], artifact_id=expired_id
            )
        survivor = await store.fetch(
            connection, organization_id=tenant["organization"], artifact_id=live.artifact_id
        )
    assert survivor.artifact_id == live.artifact_id

    assert not expired_path.exists(), (
        "wiersz zniknął, a bajty zostały. Retencja, która kasuje metadane i zostawia dane, "
        "nie jest retencją"
    )
    assert volume.read(live.storage_ref) == live_bytes


# ---------------------------------------------------------------------------
# ACL on the read path
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_reading_an_artifact_without_the_right_is_refused(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """The full ACL matrix, written by hand, run against real rows.

    Measured: making `readers_admitted_by` return every reader turns the two refusing cells red;
    making it return an empty set turns the four admitting cells red. Both directions, because a
    mechanism that refuses everybody is not an ACL either.

    TWO refusing cells and four admitting ones, and the numbers are asserted rather than
    described: `OPERATOR_AND_RUNNER` admits both readers, so a six-cell matrix over three ACLs
    contains exactly two refusals. The first version of this test asserted three and failed —
    which is the assertion doing its job on its own author.
    """
    assert set(READERS_BY_ACL) == set(ArtifactAcl), (
        "tabela uprawnień nie pokrywa wszystkich wartości ACL, które dopuszcza schemat — "
        "brakująca wartość to KeyError w produkcji, nie brak uprawnienia"
    )
    assert len(EXPECTED_ACCESS) == len(ArtifactAcl) * len(Reader) == 6, (
        f"macierz ma {len(EXPECTED_ACCESS)} komórek zamiast sześciu — niepełna macierz nie jest "
        "dowodem na komórki, których w niej nie ma"
    )

    store = ArtifactStore()
    volume = ArtifactVolume(tmp_path / "artifacts")
    moment = await _database_now(pool)
    refused_cells = 0
    admitted_cells = 0

    for (acl, reader), admitted in EXPECTED_ACCESS.items():
        content = f"artefakt {acl.value} czytany przez {reader.value}".encode()
        async with pool.acquire() as connection:
            record = await store.record(
                connection,
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                kind=ArtifactKind.REPORT,
                acl=acl,
                content=content,
                encrypted=False,
                retention_until=moment + ARTIFACT_RETENTION,
                volume=volume,
            )
            if admitted:
                assert (
                    await store.read(
                        connection,
                        organization_id=tenant["organization"],
                        artifact_id=record.artifact_id,
                        reader=reader,
                        volume=volume,
                    )
                    == content
                ), f"{reader.value} ma prawo czytać {acl.value}, a nie dostał bajtów"
                admitted_cells += 1
            else:
                with pytest.raises(ArtifactReadRefused):
                    await store.read(
                        connection,
                        organization_id=tenant["organization"],
                        artifact_id=record.artifact_id,
                        reader=reader,
                        volume=volume,
                    )
                refused_cells += 1

    assert (admitted_cells, refused_cells) == (4, 2), (
        f"macierz dała {admitted_cells} zgód i {refused_cells} odmów zamiast czterech i dwóch — "
        "przeszła jakaś macierz, ale nie ta"
    )


@pytest.mark.db
async def test_artifact_bytes_that_do_not_match_the_checksum_are_refused(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """A file swapped under the row is not the artifact the row describes.

    The ACL says who may read; the checksum says whether what came back is what was written.
    Measured: deleting the comparison in `ArtifactStore.read` makes this test return the
    tampered bytes instead of raising.
    """
    store = ArtifactStore()
    volume = ArtifactVolume(tmp_path / "artifacts")
    moment = await _database_now(pool)
    content = b"oryginalny raport"

    async with pool.acquire() as connection:
        record = await store.record(
            connection,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            kind=ArtifactKind.REPORT,
            acl=ArtifactAcl.OPERATOR_AND_RUNNER,
            content=content,
            encrypted=False,
            retention_until=moment + ARTIFACT_RETENTION,
            volume=volume,
        )
        assert (
            await store.read(
                connection,
                organization_id=tenant["organization"],
                artifact_id=record.artifact_id,
                reader=Reader.OPERATOR,
                volume=volume,
            )
            == content
        ), "kontrola pozytywna: nienaruszony artefakt ma się dać przeczytać"

        volume.write(record.storage_ref, b"podmieniony raport")
        with pytest.raises(ArtifactBytesCorrupted):
            await store.read(
                connection,
                organization_id=tenant["organization"],
                artifact_id=record.artifact_id,
                reader=Reader.OPERATOR,
                volume=volume,
            )


@pytest.mark.db
async def test_an_unencrypted_trace_is_refused_by_the_database(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """`trace_artifact_is_encrypted` (0003): a trace that is not ciphertext has no right to exist.

    This is why `encrypted` is a REQUIRED argument of `record` rather than a flag defaulting to
    `true`. The control plane holds no key (D-018) and therefore cannot legitimately write a
    trace at all — which the schema, not this module, is what enforces.
    """
    store = ArtifactStore()
    volume = ArtifactVolume(tmp_path / "artifacts")
    moment = await _database_now(pool)

    async with pool.acquire() as connection:
        encrypted_trace = await store.record(
            connection,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            kind=ArtifactKind.TRACE,
            acl=ArtifactAcl.RUNNER_ONLY,
            content=b"udajemy szyfrogram",
            encrypted=True,
            retention_until=moment + TRACE_AFTER_LOGIN_RETENTION,
            volume=volume,
        )
    assert encrypted_trace.kind is ArtifactKind.TRACE, "kontrola pozytywna: trace da się zapisać"

    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.PostgresError) as rejected:
            await store.record(
                connection,
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                kind=ArtifactKind.TRACE,
                acl=ArtifactAcl.RUNNER_ONLY,
                content=b"jawny trace",
                encrypted=False,
                retention_until=moment + TRACE_AFTER_LOGIN_RETENTION,
                volume=volume,
            )
    assert rejected.value.sqlstate == "23514", (
        f"niezaszyfrowany trace odrzucono kodem {rejected.value.sqlstate}, a ma go odrzucać "
        "CHECK `trace_artifact_is_encrypted` (23514)"
    )


# ---------------------------------------------------------------------------
# The frozen numbers
# ---------------------------------------------------------------------------


def test_the_retention_thresholds_are_the_ones_frozen_in_d_017() -> None:
    """The two numbers D-017 wrote down BEFORE anything was measured.

    Copied from the decision entry by hand, exactly as `test_attempt_limit.py` does for the queue
    thresholds. A change to either value is legal, but it is an edit of D-017 and it has to break
    this test on the way — a threshold that drifts quietly makes every earlier measurement
    meaningless.
    """
    assert RETENTION_THRESHOLDS == {
        "artifact_retention_days": 30,
        "trace_after_login_retention_hours": 24,
        "kinds_never_recorded_during_login": ("SCREENSHOT", "TRACE"),
        "phases_with_no_recording": ("LOGIN", "MFA"),
    }, "tabela retencji rozjechała się z D-017 i L3897-3898"


def test_a_trace_during_login_gets_no_retention_because_it_is_never_written() -> None:
    """L3897: "trace i screenshot są wyłączone podczas loginu i MFA".

    A refusal, not a short TTL: there is no retention for a file that must not be written. The
    positive controls matter as much — a trace AFTER login gets twenty-four hours (L3898), and a
    LOG during login is unaffected, because L3897 names trace and screenshot and nothing else.

    Measured: emptying `KINDS_NEVER_RECORDED_DURING_LOGIN` makes the four refusals below return
    a value instead of raising.
    """
    moment = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

    forbidden = [
        (ArtifactKind.TRACE, BrowserPhase.LOGIN),
        (ArtifactKind.TRACE, BrowserPhase.MFA),
        (ArtifactKind.SCREENSHOT, BrowserPhase.LOGIN),
        (ArtifactKind.SCREENSHOT, BrowserPhase.MFA),
    ]
    assert len(forbidden) == 4
    for kind, phase in forbidden:
        with pytest.raises(ArtifactIsNotRecorded):
            retention_until_for(kind=kind, phase=phase, created_at=moment)

    assert retention_until_for(
        kind=ArtifactKind.TRACE, phase=BrowserPhase.AFTER_LOGIN, created_at=moment
    ) == moment + timedelta(hours=24)
    assert retention_until_for(
        kind=ArtifactKind.LOG, phase=BrowserPhase.LOGIN, created_at=moment
    ) == moment + timedelta(days=30)
    assert retention_until_for(
        kind=ArtifactKind.SCREENSHOT, phase=BrowserPhase.AFTER_LOGIN, created_at=moment
    ) == moment + timedelta(days=30)


@pytest.mark.db
async def test_an_artifact_of_another_organization_is_absent_not_forbidden(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """The tenant boundary of the read side, and it is `WHERE organization_id = $2`.

    ABSENT rather than FORBIDDEN on purpose: a refusal would confirm the artifact exists, which
    is one bit more than a caller from another organization is entitled to. Measured: deleting
    `AND organization_id = $2` from the SELECT makes this test return the row instead of raising.
    """
    store = ArtifactStore()
    volume = ArtifactVolume(tmp_path / "artifacts")
    moment = await _database_now(pool)

    async with pool.acquire() as connection:
        stranger_organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"obca-{uuid4()}"
        )
        record = await store.record(
            connection,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            kind=ArtifactKind.REPORT,
            acl=ArtifactAcl.OPERATOR_AND_RUNNER,
            content=b"raport jednej organizacji",
            encrypted=False,
            retention_until=moment + ARTIFACT_RETENTION,
            volume=volume,
        )
        mine = await store.fetch(
            connection, organization_id=tenant["organization"], artifact_id=record.artifact_id
        )
        assert mine.artifact_id == record.artifact_id, (
            "kontrola pozytywna: własna organizacja ten artefakt widzi"
        )

        with pytest.raises(ArtifactNotFound):
            await store.fetch(
                connection,
                organization_id=stranger_organization,
                artifact_id=record.artifact_id,
            )
