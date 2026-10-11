"""A separate DEK per profile, wrapped by the runner's key and written to `profile_deks`.

    "profile są szyfrowane oddzielnymi DEK; aktywna kapsuła montuje wyłącznie swój
     odszyfrowany profil" (L3914)
    "storage profili jest zaszyfrowany i dostępny tylko dla Browser Runnera" (L3894)
    "encrypted profile storage" (L5196), D-018

WHY A DEK PER PROFILE AND NOT ONE KEY FOR EVERYTHING. Two reasons, and only the first is
confidentiality. (1) A key shared by every profile means one leaked capsule reads every account's
cookies. (2) `DELETING` is defined as crypto-erasure of the key (L3900, L3903) — with one shared
key there is nothing to destroy that does not destroy everybody else's profile at the same time.
`profile_deks.erased_at` and the partial unique index above it exist for exactly that.

THE ONE TEST THAT MAKES THIS REAL, and the trap it had to avoid.
`test_the_dek_of_profile_a_does_not_decrypt_profile_b` hands profile A's key material to a
decryption of profile B's bytes — WITH PROFILE B's ASSOCIATED DATA. The obvious version of that
test, which passes the blob together with A's own identity, proves nothing about separate keys:
it fails on the associated data alone and stays green after the DEKs are made identical. The
version below fails ONLY because the keys differ, which is measured — replacing
`generate_dek_material` with a constant (so `ProfileSecretStore.create_dek` hands every profile
one key) turns that test red on the assertion about the key material itself.

FOUR PROPERTIES OF THE CRYPTO BELOW.

1.  **AES-256-GCM, because the schema says so and nothing else is allowed.**
    `profile_deks_algorithm_is_known` accepts exactly one value; migration 0003 explains that a
    column accepting an algorithm nobody implemented is where a silent downgrade lives. The
    constant below is that string, and it is written once.

2.  **A fresh random nonce per encryption, prefixed to the ciphertext.** Reusing a nonce under
    one key destroys GCM outright. The nonce is not a secret and travels with the bytes.

3.  **Associated data binds every ciphertext to the thing it belongs to.** A wrapped DEK is bound
    to `(profile, generation, wrapping key id)` and profile bytes are bound to
    `(profile, generation)`. So a wrapped DEK moved to another profile's row does not unwrap, and
    a blob copied between profile directories does not decrypt — neither of which the key length
    alone would prevent. This is a property of the code, and the associated-data half is NOT
    separately measured here; what IS measured is the key-separation half described above.

4.  **Key material never enters a `repr`.** `ProfileDek.material` is `repr=False`, like
    `WrappingKey.material`. An assertion dump in a failing test is a log line like any other.

WHAT THIS MODULE DOES NOT DO.
*   No audit rows. `control_plane.audit.AUDIT_KINDS` is a CLOSED vocabulary owned by another
    module and holds nothing for "a profile key was created" or "a profile key was destroyed".
    Inventing a kind here would be an edit to somebody else's decision, so the creation of a DEK
    currently leaves NO trace in the audit log. That is a GAP, stated rather than hidden — the
    same treatment `approval/store.py` gives the same problem.
*   No key rotation. `profile_deks.generation` and the partial unique index anticipate it; F1
    writes generation 1 and never a second live row. `create_dek` refuses a profile that already
    has a live DEK rather than pretending to rotate.
*   No decryption inside the control plane. Every function that needs plaintext takes a
    `WrappingKey`, and `keys.load_wrapping_key` hands one out only to the Browser Runner. The
    limits of that statement are spelled out in `keys.py` and are not repeated here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from uuid import UUID

import asyncpg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from control_plane.storage.keys import WrappingKey

#: The single value `profile_deks_algorithm_is_known` accepts (migration 0003).
AEAD_ALGORITHM: Final[str] = "AES-256-GCM"

#: AES-256 key length in bytes, for the DEK as well as for the key that wraps it.
DEK_LENGTH: Final[int] = 32

#: GCM's standard nonce length. Twelve bytes is the size the construction is defined for; any
#: other length goes through an extra derivation step and buys nothing.
NONCE_LENGTH: Final[int] = 12

#: Mode for the directory holding profile blobs: owner only. "katalogi mają minimalne
#: uprawnienia" (L3896).
#: NOT MEASURED: no test asserts that these modes end up on the files, and none asserts they
#: survive a container's volume mount. The constants are set; the effect is unverified.
PROFILE_DIRECTORY_MODE: Final[int] = 0o700

#: Mode for a profile blob on the volume.
PROFILE_FILE_MODE: Final[int] = 0o600


class ProfileCryptoError(Exception):
    """Base for every refusal here. Never a return value that reads like success."""


class ProfileDekNotFound(ProfileCryptoError):
    """The profile has no live DEK. Distinct from "the DEK does not open this" on purpose."""


class ProfileDekAlreadyLive(ProfileCryptoError):
    """The profile already has a DEK that has not been erased (partial unique index, 0003)."""


class ProfileDecryptionFailed(ProfileCryptoError):
    """Authentication failed: wrong key, wrong profile, or the bytes were altered.

    ONE type for all three, because GCM cannot tell them apart and a message claiming it could
    would be a lie the reader acts on.
    """


@dataclass(frozen=True)
class ProfileDek:
    """One profile's data encryption key, in the clear, in memory, for as long as it is used.

    Carries the identity it belongs to, so `encrypt_profile_bytes` cannot be called without the
    associated data being right — the binding is a property of the key object, not a parameter
    the caller has to remember.
    """

    profile_id: UUID
    generation: int
    material: bytes = field(repr=False)


def generate_dek_material() -> bytes:
    """Fresh key material from the operating system's random source.

    A named function rather than an inline call precisely so that a mutation test can replace it
    and watch `test_the_dek_of_profile_a_does_not_decrypt_profile_b` turn red.
    """
    return os.urandom(DEK_LENGTH)


def _wrap_associated_data(*, profile_id: UUID, generation: int, wrapping_key_id: str) -> bytes:
    return f"wenext-profile-dek|{profile_id}|{generation}|{wrapping_key_id}".encode()


def _storage_associated_data(*, profile_id: UUID, generation: int) -> bytes:
    return f"wenext-profile-storage|{profile_id}|{generation}".encode()


def _seal(key_material: bytes, plaintext: bytes, associated_data: bytes) -> bytes:
    nonce = os.urandom(NONCE_LENGTH)
    return nonce + AESGCM(key_material).encrypt(nonce, plaintext, associated_data)


def _open(key_material: bytes, sealed: bytes, associated_data: bytes) -> bytes:
    if len(sealed) <= NONCE_LENGTH:
        raise ProfileDecryptionFailed(
            f"zaszyfrowany blok ma {len(sealed)} bajtów, a sam nonce zajmuje {NONCE_LENGTH} — "
            "to nie jest wynik szyfrowania tym modułem"
        )
    nonce, body = sealed[:NONCE_LENGTH], sealed[NONCE_LENGTH:]
    try:
        return AESGCM(key_material).decrypt(nonce, body, associated_data)
    except InvalidTag as invalid:
        raise ProfileDecryptionFailed(
            "uwierzytelnienie AES-256-GCM nie przeszło: albo klucz jest inny, albo blok należy "
            "do innego profilu, albo bajty zostały zmienione. GCM nie odróżnia tych trzech "
            "przypadków i ten komunikat też nie udaje, że potrafi"
        ) from invalid


def wrap_dek(dek: ProfileDek, wrapping_key: WrappingKey) -> bytes:
    """The bytes that go into `profile_deks.wrapped_dek`. The plain DEK never reaches the row."""
    return _seal(
        wrapping_key.material,
        dek.material,
        _wrap_associated_data(
            profile_id=dek.profile_id,
            generation=dek.generation,
            wrapping_key_id=wrapping_key.key_id,
        ),
    )


def unwrap_dek(
    wrapped: bytes, *, wrapping_key: WrappingKey, profile_id: UUID, generation: int
) -> ProfileDek:
    """Recovers a DEK from its stored form. Refuses if the row was moved to another profile."""
    material = _open(
        wrapping_key.material,
        wrapped,
        _wrap_associated_data(
            profile_id=profile_id, generation=generation, wrapping_key_id=wrapping_key.key_id
        ),
    )
    return ProfileDek(profile_id=profile_id, generation=generation, material=material)


def encrypt_profile_bytes(dek: ProfileDek, plaintext: bytes) -> bytes:
    """The bytes that land on the volume."""
    return _seal(
        dek.material,
        plaintext,
        _storage_associated_data(profile_id=dek.profile_id, generation=dek.generation),
    )


def decrypt_profile_bytes(dek: ProfileDek, sealed: bytes) -> bytes:
    """The inverse. Raises `ProfileDecryptionFailed` rather than returning anything on failure."""
    return _open(
        dek.material,
        sealed,
        _storage_associated_data(profile_id=dek.profile_id, generation=dek.generation),
    )


class ProfileVolume:
    """The directory the encrypted profile blobs live in — the thing a stolen backup would be.

    Deliberately dumb: it writes and reads bytes and knows nothing about keys. That is what lets
    `test_profile_data_on_the_volume_is_unreadable_without_the_key` read the file back through
    the same object the runner would use, instead of through a hand-rolled `open()` that might be
    looking somewhere else.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(PROFILE_DIRECTORY_MODE)

    def path_for(self, storage_ref: str) -> Path:
        """The file a storage reference names. Refuses anything that could leave the root."""
        candidate = (self.root / storage_ref).resolve()
        if not candidate.is_relative_to(self.root.resolve()):
            raise ProfileCryptoError(
                f"uchwyt {storage_ref!r} wskazuje poza wolumen profili ({self.root}) — "
                "to jest ucieczka ze ścieżki, nie nazwa pliku"
            )
        return candidate

    def write(self, storage_ref: str, sealed: bytes) -> None:
        target = self.path_for(storage_ref)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sealed)
        target.chmod(PROFILE_FILE_MODE)

    def read(self, storage_ref: str) -> bytes:
        return self.path_for(storage_ref).read_bytes()


def storage_ref_for(profile_id: UUID) -> str:
    """The value written to `browser_profiles.encrypted_storage_ref` (column added in 0002).

    That column is `NULL` until storage exists and carries a `CHECK` refusing the empty string,
    because — migration 0002's words — `NULL` is a state and `''` is nothing.
    """
    return f"profile-{profile_id}.enc"


class ProfileSecretStore:
    """`profile_deks` and the `browser_profiles` column that points at the encrypted storage.

    THE `UPDATE 0` TRAP IS HANDLED HERE. In this driver an `UPDATE` that matches nothing returns
    the string `"UPDATE 0"` and raises NOTHING; `attach_storage_ref` therefore counts the rows and
    turns zero into a refusal. `domain/errors.py` documents this as the most common shape of this
    project's recurring disease in database code, and it is the reason the method below reads its
    own result instead of trusting the call to have done something.
    """

    async def create_dek(
        self,
        connection: asyncpg.Connection,
        *,
        organization_id: UUID,
        profile_id: UUID,
        wrapping_key: WrappingKey,
    ) -> ProfileDek:
        """Mints a NEW random DEK for this profile and stores it wrapped.

        The randomness is per call, which is the entire content of "osobny DEK per profil"
        (L3914). Two profiles created in the same transaction get two unrelated keys.
        """
        live = await connection.fetchval(
            "SELECT id FROM profile_deks WHERE profile_id = $1 AND erased_at IS NULL",
            profile_id,
        )
        if live is not None:
            raise ProfileDekAlreadyLive(
                f"profil {profile_id} ma już żywy DEK ({live}). Rotacja klucza nie jest w "
                "zakresie F1, a drugi żywy DEK znaczyłby, że schemat nie umie powiedzieć, "
                "którym z nich profil się odszyfruje"
            )
        dek = ProfileDek(profile_id=profile_id, generation=1, material=generate_dek_material())
        await connection.execute(
            """
            INSERT INTO profile_deks
                (organization_id, profile_id, wrapped_dek, wrapping_key_id, algorithm, generation)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            organization_id,
            profile_id,
            wrap_dek(dek, wrapping_key),
            wrapping_key.key_id,
            AEAD_ALGORITHM,
            dek.generation,
        )
        return dek

    async def load_dek(
        self,
        connection: asyncpg.Connection,
        *,
        organization_id: UUID,
        profile_id: UUID,
        wrapping_key: WrappingKey,
    ) -> ProfileDek:
        """Reads the live DEK back and unwraps it. Needs the runner's key; there is no other way."""
        row = await connection.fetchrow(
            """
            SELECT wrapped_dek, wrapping_key_id, generation
            FROM profile_deks
            WHERE organization_id = $1 AND profile_id = $2 AND erased_at IS NULL
            """,
            organization_id,
            profile_id,
        )
        if row is None:
            raise ProfileDekNotFound(
                f"profil {profile_id} nie ma żywego DEK-u w organizacji {organization_id}"
            )
        if row["wrapping_key_id"] != wrapping_key.key_id:
            raise ProfileCryptoError(
                f"DEK profilu {profile_id} opakowano kluczem {row['wrapping_key_id']}, a "
                f"przedstawiono {wrapping_key.key_id}. To jest inny klucz hosta, nie zły plik"
            )
        return unwrap_dek(
            bytes(row["wrapped_dek"]),
            wrapping_key=wrapping_key,
            profile_id=profile_id,
            generation=row["generation"],
        )

    async def attach_storage_ref(
        self,
        connection: asyncpg.Connection,
        *,
        organization_id: UUID,
        profile_id: UUID,
        storage_ref: str,
    ) -> None:
        """Points `browser_profiles.encrypted_storage_ref` at the blob. Zero rows is a refusal."""
        result = await connection.execute(
            """
            UPDATE browser_profiles
            SET encrypted_storage_ref = $3
            WHERE organization_id = $1 AND id = $2
            """,
            organization_id,
            profile_id,
            storage_ref,
        )
        if result != "UPDATE 1":
            raise ProfileCryptoError(
                f"powiązanie uchwytu storage z profilem {profile_id} zmieniło {result!r} zamiast "
                "jednego wiersza — profil nie istnieje albo należy do innej organizacji. "
                "Sterownik nie rzuca przy zerze dopasowań, więc odmowa musi paść tutaj"
            )

    async def provision_storage(
        self,
        connection: asyncpg.Connection,
        *,
        organization_id: UUID,
        profile_id: UUID,
        wrapping_key: WrappingKey,
        volume: ProfileVolume,
        content: bytes,
    ) -> str:
        """The whole of L3914 in one call: own DEK, wrapped in the row, blob on the volume.

        Returns the storage reference now sitting in `browser_profiles.encrypted_storage_ref`.
        The three writes are NOT atomic across the database and the file system — the row lands
        inside the caller's transaction and the file does not. Said out loud because a rollback
        after this call leaves an orphaned blob on the volume; F1 has no reconciliation for that
        and the sweeper of `artifacts.py` does not cover profiles.
        """
        dek = await self.create_dek(
            connection,
            organization_id=organization_id,
            profile_id=profile_id,
            wrapping_key=wrapping_key,
        )
        storage_ref = storage_ref_for(profile_id)
        volume.write(storage_ref, encrypt_profile_bytes(dek, content))
        await self.attach_storage_ref(
            connection,
            organization_id=organization_id,
            profile_id=profile_id,
            storage_ref=storage_ref,
        )
        return storage_ref
