"""Encrypted profile storage against a REAL PostgreSQL and a real file system (L5196, L3914, D-018).

WHAT THIS FILE HAD TO AVOID, because the repository has the receipts.

*   **A test that only checks the encryptor returned something.** That proves nothing: a function
    returning `plaintext` unchanged passes it. Every assertion below about confidentiality READS
    THE BYTES BACK OFF THE VOLUME and looks for the marker that was put in.
*   **A test whose datum is caught by an earlier condition.** The associated data of this module
    binds a blob to its profile, so the obvious "A's key does not open B" test fails on the
    associated data and stays green after the two DEKs are made identical. The test below hands
    A's KEY MATERIAL to a decryption carrying B's IDENTITY, so the only remaining difference is
    the key — measured: making `generate_dek_material` constant turns it red.
*   **`all([])`.** Every scan asserts its CARDINALITY before it asserts a property.

WHICH GUARANTEES ARE MEASURED HERE, AND WHICH ARE NOT.

Measured end to end, each with a mutation seen failing:
  * profile bytes on the volume carry no plaintext, and the right key brings them back;
  * profile A's DEK does not open profile B;
  * a key file at `0644` refuses the start, and the same file at `0600` does not;
  * a key file on the database volume refuses the start, directly and through a symlink;
  * no control plane module outside this storage package names the key loader;
  * key material stays out of every `repr`;
  * the wrapped DEK stored in `profile_deks` is not the DEK;
  * a second live DEK for one profile is refused BY NAME, not by a constraint code;
  * an `UPDATE` matching no row is a refusal and not the driver's silent `"UPDATE 0"`;
  * a storage handle containing `../` does not write outside the volume.

NOT measured, stated so that nobody reads the list above as covering it:
  * that the compose file mounts the key ONLY into the Browser Runner — there is no such mount
    in the repository today, so there is nothing to assert against;
  * that a key file owned by ANOTHER user is refused end to end — creating one needs root. Only
    the decision table `refusal_for_key_file` is measured for that branch, with a synthetic
    owner id;
  * that the directory modes (`0700`/`0600`) this module sets survive a container's volume
    mount — that is a property of the deployment, not of this code;
  * that anything protects the profile from somebody holding an account on the host. D-018 says
    plainly that nothing here does.
"""

from __future__ import annotations

import ast
import os
import pathlib
from uuid import UUID, uuid4

import control_plane
import pytest
from control_plane.storage.keys import (
    KeyRefusal,
    Principal,
    WrappingKey,
    WrappingKeyRefused,
    load_wrapping_key,
    refusal_for_key_file,
)
from control_plane.storage.profile_crypto import (
    DEK_LENGTH,
    NONCE_LENGTH,
    ProfileCryptoError,
    ProfileDecryptionFailed,
    ProfileDek,
    ProfileDekAlreadyLive,
    ProfileSecretStore,
    ProfileVolume,
    decrypt_profile_bytes,
    generate_dek_material,
    storage_ref_for,
)

#: A byte sequence that has no business appearing anywhere near a disk. Deliberately shaped like
#: the thing a stolen profile would actually give away.
SECRET_MARKER = b"li_at=AQEDATSESSION-COOKIE-DO-NOT-LEAK-8f21c4"

#: Key material fixed rather than random: these tests measure a mechanism, not secrecy, and a
#: fixed key makes a failure reproducible instead of "sometimes".
_KEY_MATERIAL = bytes(range(32))


def _write_key_file(directory: pathlib.Path, name: str = "wrapping.key") -> pathlib.Path:
    """A key file the loader accepts: regular, `0600`, owned by us, exactly 32 bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(_KEY_MATERIAL)
    path.chmod(0o600)
    return path


def _runner_key(directory: pathlib.Path, database_volume: pathlib.Path) -> WrappingKey:
    return load_wrapping_key(
        _write_key_file(directory),
        holder=Principal.BROWSER_RUNNER,
        database_volume_directories=[database_volume],
    )


async def _social_account(pool, organization_id: UUID) -> UUID:
    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'linkedin', $2)
            RETURNING id
            """,
            organization_id,
            f"external-{uuid4()}",
        )


async def _browser_profile(pool, organization_id: UUID) -> UUID:
    """One `browser_profiles` row with its own social account.

    Its own account and not a shared one: `browser_profiles_one_active_per_account` (0001) allows
    at most one non-retired profile per account, so two profiles sharing an account is a
    constraint violation rather than the two-profile case this file needs.
    """
    account_id = await _social_account(pool, organization_id)
    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            INSERT INTO browser_profiles
                (organization_id, social_account_id, state, profile_build_marker, profile_platform)
            VALUES ($1, $2, 'AVAILABLE', 'chrome-for-testing@153.0.8010.12', 'linkedin')
            RETURNING id
            """,
            organization_id,
            account_id,
        )


# ---------------------------------------------------------------------------
# The key file: three refusals and one acceptance
# ---------------------------------------------------------------------------


def test_a_key_file_with_wider_permissions_stops_the_start(tmp_path: pathlib.Path) -> None:
    """`0644` is a refusal to start, `0600` is not — the same file, twice (D-018).

    THE POSITIVE CONTROL IS THE POINT. A test that only saw the refusal would also pass against a
    loader that refuses every file, which is not a control but a broken program. So the file is
    loaded successfully first, widened, refused, narrowed and loaded again.

    The refusal is asserted BY REASON. `WrappingKeyRefused` has six reasons; asserting only that
    "an exception was raised" would pass when the length check or the volume check fired instead,
    which is precisely how a suite in this repository ran 36 green tests over a deleted
    mechanism.
    """
    database_volume = tmp_path / "pgdata"
    database_volume.mkdir()
    path = _write_key_file(tmp_path / "secrets")

    before = load_wrapping_key(
        path, holder=Principal.BROWSER_RUNNER, database_volume_directories=[database_volume]
    )
    assert before.material == _KEY_MATERIAL, "kontrola pozytywna: plik 0600 ma się wczytać"

    path.chmod(0o644)
    with pytest.raises(WrappingKeyRefused) as refused:
        load_wrapping_key(
            path, holder=Principal.BROWSER_RUNNER, database_volume_directories=[database_volume]
        )
    assert refused.value.refusal is KeyRefusal.MODE_TOO_WIDE, (
        f"plik 0644 odrzucono z powodu {refused.value.refusal}, a miał go odrzucić warunek "
        "praw dostępu — inny powód znaczy, że tego warunku ten test nie zmierzył"
    )

    path.chmod(0o600)
    after = load_wrapping_key(
        path, holder=Principal.BROWSER_RUNNER, database_volume_directories=[database_volume]
    )
    assert after.material == _KEY_MATERIAL, "po przywróceniu 0600 klucz ma się znowu wczytać"
    assert after.key_id == before.key_id


def test_a_key_file_on_the_database_volume_stops_the_start(tmp_path: pathlib.Path) -> None:
    """A key next to the data it protects is not protection (D-018), symlinks included.

    The control key has IDENTICAL mode and length and sits outside the volume, so the refusal
    below cannot be the mode check or the length check doing the work.
    """
    database_volume = tmp_path / "pgdata"
    outside = _write_key_file(tmp_path / "secrets")
    inside = _write_key_file(database_volume, name="wrapping.key")

    control = load_wrapping_key(
        outside, holder=Principal.BROWSER_RUNNER, database_volume_directories=[database_volume]
    )
    assert control.material == _KEY_MATERIAL, "kontrola pozytywna: klucz poza wolumenem bazy"

    with pytest.raises(WrappingKeyRefused) as refused:
        load_wrapping_key(
            inside, holder=Principal.BROWSER_RUNNER, database_volume_directories=[database_volume]
        )
    assert refused.value.refusal is KeyRefusal.ON_THE_DATABASE_VOLUME

    link = tmp_path / "secrets" / "link-to-the-volume.key"
    link.symlink_to(inside)
    with pytest.raises(WrappingKeyRefused) as through_link:
        load_wrapping_key(
            link, holder=Principal.BROWSER_RUNNER, database_volume_directories=[database_volume]
        )
    assert through_link.value.refusal is KeyRefusal.ON_THE_DATABASE_VOLUME, (
        "dowiązanie symboliczne do pliku na wolumenie bazy to plik na wolumenie bazy — "
        "porównanie nierozwiniętej ścieżki dałoby się obejść jednym `ln -s`"
    )


def test_a_key_file_owned_by_another_user_is_refused_by_the_decision_table() -> None:
    """The one branch of D-018 that has no end-to-end test, measured where it can be.

    A file owned by somebody else cannot be created without root, so this asserts on the pure
    decision table `refusal_for_key_file` with a synthetic owner id — and says so rather than
    letting the reader assume the loader was exercised. The positive control is the same call
    with the owner matching the process.
    """
    process_user_id = os.getuid()
    regular_file_at_0600 = 0o100600

    assert (
        refusal_for_key_file(
            file_mode=regular_file_at_0600,
            owner_user_id=process_user_id,
            size_bytes=32,
            process_user_id=process_user_id,
        )
        is None
    ), "kontrola pozytywna: własny plik 0600 o poprawnej długości ma przejść"

    assert (
        refusal_for_key_file(
            file_mode=regular_file_at_0600,
            owner_user_id=process_user_id + 1,
            size_bytes=32,
            process_user_id=process_user_id,
        )
        is KeyRefusal.OWNED_BY_ANOTHER_USER
    )


def test_the_control_plane_cannot_read_the_runner_key(tmp_path: pathlib.Path) -> None:
    """Two halves of one claim, and neither is the operating system.

    HALF ONE, in code: `load_wrapping_key` refuses any holder other than the Browser Runner. That
    is a convention this package enforces on its own callers — nothing stops a process from
    passing `BROWSER_RUNNER`, and this docstring will not pretend otherwise.

    HALF TWO, structural, and the one with teeth: NO module of the control plane outside this
    storage package so much as names the loader. That is checkable, it is what D-018's "control
    plane NIE MA dostępu do klucza runnera" means for this codebase, and it fails the moment
    somebody wires the key into a route. Measured: adding one reference to
    `storage/artifacts.py` turns this red.

    The real boundary — the key not being mounted into the control plane container, and POSIX
    ownership underneath it — is NOT measured by this package. There is no such mount in the
    repository yet.
    """
    database_volume = tmp_path / "pgdata"
    database_volume.mkdir()
    path = _write_key_file(tmp_path / "secrets")

    runner_key = load_wrapping_key(
        path, holder=Principal.BROWSER_RUNNER, database_volume_directories=[database_volume]
    )
    assert runner_key.material == _KEY_MATERIAL, "kontrola pozytywna: runner klucz dostaje"

    with pytest.raises(WrappingKeyRefused) as refused:
        load_wrapping_key(
            path, holder=Principal.CONTROL_PLANE, database_volume_directories=[database_volume]
        )
    assert refused.value.refusal is KeyRefusal.WRONG_HOLDER

    package_root = pathlib.Path(control_plane.__file__).resolve().parent
    allowed = {
        package_root / "storage" / "__init__.py",
        package_root / "storage" / "keys.py",
        package_root / "storage" / "profile_crypto.py",
    }
    forbidden_names = {"load_wrapping_key", "WrappingKey", "key_id_for"}

    scanned: list[pathlib.Path] = []
    offenders: dict[str, str] = {}
    for module_path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in module_path.parts or module_path in allowed:
            continue
        scanned.append(module_path)
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            seen = None
            if isinstance(node, ast.Name):
                seen = node.id
            elif isinstance(node, ast.Attribute):
                seen = node.attr
            elif isinstance(node, ast.alias):
                seen = node.name.rsplit(".", maxsplit=1)[-1]
            if seen in forbidden_names:
                offenders[str(module_path.relative_to(package_root))] = seen

    # Cardinality BEFORE the property: an empty scan makes the assertion below vacuously true,
    # and `all([])` has produced four false passes in this repository already.
    assert len(scanned) >= 25, (
        f"skaner obejrzał tylko {len(scanned)} modułów control plane — przy tak małej liczbie "
        "brak trafień nie jest dowodem, tylko pustym zbiorem"
    )
    assert offenders == {}, (
        f"moduły control plane spoza pakietu storage sięgają po klucz runnera: {offenders}. "
        "D-018: control plane NIE MA dostępu do tego klucza"
    )


def test_the_wrapping_key_never_appears_in_its_own_repr(tmp_path: pathlib.Path) -> None:
    """Key material must not reach a traceback, a log line or a pytest assertion dump."""
    database_volume = tmp_path / "pgdata"
    database_volume.mkdir()
    key = _runner_key(tmp_path / "secrets", database_volume)
    dek = ProfileDek(profile_id=uuid4(), generation=1, material=b"D" * DEK_LENGTH)

    assert repr(_KEY_MATERIAL) not in repr(key)
    assert "\\x00\\x01\\x02" not in repr(key)
    assert key.key_id in repr(key), (
        "identyfikator klucza ma zostać — bez niego repr jest bezużyteczny"
    )
    assert repr(b"D" * DEK_LENGTH) not in repr(dek)
    assert str(dek.profile_id) in repr(dek)


# ---------------------------------------------------------------------------
# The profile on the volume
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_profile_data_on_the_volume_is_unreadable_without_the_key(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """The bytes a stolen backup would contain, read back and searched for the secret.

    THE ASSERTION IS ON THE BYTES, not on the return value of the encryptor. `SECRET_MARKER` goes
    in, the file comes off the volume, and the marker must not be in it. Measured: making
    `encrypt_profile_bytes` return its plaintext turns this red on that one assertion.

    Three more things are checked here because each is a separate way this could be theatre:
    the row in `browser_profiles` really points at the blob (`encrypted_storage_ref`), a WRONG
    key does not open it, and the RIGHT key does.
    """
    database_volume = tmp_path / "pgdata"
    database_volume.mkdir()
    wrapping_key = _runner_key(tmp_path / "secrets", database_volume)
    volume = ProfileVolume(tmp_path / "profiles")
    store = ProfileSecretStore()

    profile_id = await _browser_profile(pool, tenant["organization"])
    content = b"cookies.sqlite\x00" + SECRET_MARKER + b"\x00Local State"

    async with pool.acquire() as connection:
        storage_ref = await store.provision_storage(
            connection,
            organization_id=tenant["organization"],
            profile_id=profile_id,
            wrapping_key=wrapping_key,
            volume=volume,
            content=content,
        )

    assert storage_ref == storage_ref_for(profile_id)

    on_the_volume = volume.read(storage_ref)
    assert len(on_the_volume) > NONCE_LENGTH, (
        "na wolumenie nie ma nic albo jest sam nonce — nie ma czego przeszukiwać, więc brak "
        "markera nie byłby dowodem"
    )
    assert SECRET_MARKER not in on_the_volume, (
        "jawny sekret leży na wolumenie profili. To jest dokładnie ta kopia wolumenu, przed "
        "którą D-018 każe chronić"
    )
    assert content not in on_the_volume
    assert on_the_volume == (tmp_path / "profiles" / storage_ref).read_bytes(), (
        "ProfileVolume.read czyta inny plik niż ten, który leży na dysku"
    )

    async with pool.acquire() as connection:
        stored_ref = await connection.fetchval(
            "SELECT encrypted_storage_ref FROM browser_profiles WHERE id = $1", profile_id
        )
    assert stored_ref == storage_ref, (
        "browser_profiles.encrypted_storage_ref nie wskazuje na blok — kolumna z migracji 0002 "
        "została pusta, więc nikt nie znajdzie tych bajtów"
    )

    stranger = ProfileDek(profile_id=profile_id, generation=1, material=generate_dek_material())
    with pytest.raises(ProfileDecryptionFailed):
        decrypt_profile_bytes(stranger, on_the_volume)

    async with pool.acquire() as connection:
        dek = await store.load_dek(
            connection,
            organization_id=tenant["organization"],
            profile_id=profile_id,
            wrapping_key=wrapping_key,
        )
    assert decrypt_profile_bytes(dek, on_the_volume) == content, (
        "kontrola pozytywna: z właściwym kluczem profil ma się odszyfrować w całości"
    )


@pytest.mark.db
async def test_the_dek_of_profile_a_does_not_decrypt_profile_b(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """The whole reason for a DEK per profile (L3914), tested so that only the key can save it.

    THE TRAP THIS AVOIDS. Associated data binds every blob to its profile, so decrypting B's blob
    with an object carrying A's identity fails on the associated data and would stay green after
    the two DEKs were made identical. The attempt below therefore carries B's identity and only
    A's key material — the position of somebody who knows everything about profile B except its
    key.

    Measured: replacing `generate_dek_material` with a constant makes both assertions fail.
    """
    database_volume = tmp_path / "pgdata"
    database_volume.mkdir()
    wrapping_key = _runner_key(tmp_path / "secrets", database_volume)
    volume = ProfileVolume(tmp_path / "profiles")
    store = ProfileSecretStore()

    profile_a = await _browser_profile(pool, tenant["organization"])
    profile_b = await _browser_profile(pool, tenant["organization"])
    content_b = b"profil B: " + SECRET_MARKER

    async with pool.acquire() as connection:
        await store.provision_storage(
            connection,
            organization_id=tenant["organization"],
            profile_id=profile_a,
            wrapping_key=wrapping_key,
            volume=volume,
            content=b"profil A: nic ciekawego",
        )
        ref_b = await store.provision_storage(
            connection,
            organization_id=tenant["organization"],
            profile_id=profile_b,
            wrapping_key=wrapping_key,
            volume=volume,
            content=content_b,
        )
        dek_a = await store.load_dek(
            connection,
            organization_id=tenant["organization"],
            profile_id=profile_a,
            wrapping_key=wrapping_key,
        )
        dek_b = await store.load_dek(
            connection,
            organization_id=tenant["organization"],
            profile_id=profile_b,
            wrapping_key=wrapping_key,
        )

    assert dek_a.material != dek_b.material, (
        "dwa profile dostały ten sam DEK — „osobne DEK per profil” (L3914) nie istnieje, "
        "a crypto-erasure jednego konta zniszczyłoby wszystkie pozostałe"
    )

    sealed_b = volume.read(ref_b)
    assert decrypt_profile_bytes(dek_b, sealed_b) == content_b, (
        "kontrola pozytywna: własnym DEK-iem profil B musi się otwierać"
    )

    a_material_under_b_identity = ProfileDek(
        profile_id=profile_b, generation=dek_b.generation, material=dek_a.material
    )
    with pytest.raises(ProfileDecryptionFailed):
        decrypt_profile_bytes(a_material_under_b_identity, sealed_b)


@pytest.mark.db
async def test_the_wrapped_dek_in_postgres_is_not_the_dek(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """`profile_deks.wrapped_dek` holds a wrapped key, and the schema comment says it must.

    Without this, "wrapped" is a column name. The row is read back as raw bytes and the DEK is
    searched for inside it.
    """
    database_volume = tmp_path / "pgdata"
    database_volume.mkdir()
    wrapping_key = _runner_key(tmp_path / "secrets", database_volume)
    store = ProfileSecretStore()
    profile_id = await _browser_profile(pool, tenant["organization"])

    async with pool.acquire() as connection:
        dek = await store.create_dek(
            connection,
            organization_id=tenant["organization"],
            profile_id=profile_id,
            wrapping_key=wrapping_key,
        )
        row = await connection.fetchrow(
            "SELECT wrapped_dek, wrapping_key_id, algorithm, generation FROM profile_deks "
            "WHERE profile_id = $1",
            profile_id,
        )

    wrapped = bytes(row["wrapped_dek"])
    assert len(wrapped) == NONCE_LENGTH + DEK_LENGTH + 16, (
        f"opakowany DEK ma {len(wrapped)} bajtów zamiast nonce + klucz + znacznik AES-GCM"
    )
    assert dek.material not in wrapped, "w kolumnie leży goły DEK, nie opakowany"
    assert row["algorithm"] == "AES-256-GCM"
    assert row["wrapping_key_id"] == wrapping_key.key_id
    assert row["generation"] == 1


# ---------------------------------------------------------------------------
# The three smaller refusals of the store, each of which was a docstring claim first
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_a_second_live_dek_for_one_profile_is_refused(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """One live DEK per profile — and the refusal has to be the store's, with a name.

    The partial unique index `profile_deks_one_live_per_profile` would also stop the second row,
    as a `23505` from the driver. That is a different thing to hand a caller: a constraint name
    instead of the sentence "rotation is not in F1's scope". Measured: deleting the check in
    `create_dek` makes this test fail with `UniqueViolationError` instead of the named refusal.
    """
    database_volume = tmp_path / "pgdata"
    database_volume.mkdir()
    wrapping_key = _runner_key(tmp_path / "secrets", database_volume)
    store = ProfileSecretStore()
    profile_id = await _browser_profile(pool, tenant["organization"])

    async with pool.acquire() as connection:
        first = await store.create_dek(
            connection,
            organization_id=tenant["organization"],
            profile_id=profile_id,
            wrapping_key=wrapping_key,
        )
    assert first.generation == 1, "kontrola pozytywna: pierwszy DEK ma powstać"

    async with pool.acquire() as connection:
        with pytest.raises(ProfileDekAlreadyLive):
            await store.create_dek(
                connection,
                organization_id=tenant["organization"],
                profile_id=profile_id,
                wrapping_key=wrapping_key,
            )


@pytest.mark.db
async def test_pointing_the_storage_handle_at_a_missing_profile_is_a_refusal(
    pool, tenant, tmp_path: pathlib.Path
) -> None:
    """The `UPDATE 0` trap, measured rather than described.

    In this driver an `UPDATE` matching no row returns the STRING `"UPDATE 0"` and raises
    nothing, so a store that trusted the call would report a profile as provisioned while
    `encrypted_storage_ref` stayed `NULL` and nobody could ever find the bytes. Measured:
    deleting the row count check makes this test fail with `DID NOT RAISE`.
    """
    store = ProfileSecretStore()
    profile_id = await _browser_profile(pool, tenant["organization"])

    async with pool.acquire() as connection:
        await store.attach_storage_ref(
            connection,
            organization_id=tenant["organization"],
            profile_id=profile_id,
            storage_ref=storage_ref_for(profile_id),
        )
        stored = await connection.fetchval(
            "SELECT encrypted_storage_ref FROM browser_profiles WHERE id = $1", profile_id
        )
    assert stored == storage_ref_for(profile_id), (
        "kontrola pozytywna: istniejący profil dostaje uchwyt"
    )

    async with pool.acquire() as connection:
        with pytest.raises(ProfileCryptoError):
            await store.attach_storage_ref(
                connection,
                organization_id=tenant["organization"],
                profile_id=uuid4(),
                storage_ref="profile-nieistniejacy.enc",
            )


def test_a_storage_handle_that_escapes_the_volume_is_refused(tmp_path: pathlib.Path) -> None:
    """`../` in a storage reference is an escape from the volume, not a file name.

    Measured: returning the candidate path without the containment test makes this write land
    outside the volume root and the test fail with `DID NOT RAISE`.
    """
    volume = ProfileVolume(tmp_path / "profiles")
    volume.write("profile-ok.enc", b"blok")
    assert (tmp_path / "profiles" / "profile-ok.enc").read_bytes() == b"blok", (
        "kontrola pozytywna: zwykły uchwyt ma działać"
    )

    with pytest.raises(ProfileCryptoError):
        volume.write("../poza-wolumenem.enc", b"blok")
    assert not (tmp_path / "poza-wolumenem.enc").exists()
