"""Encrypted profile storage, artifact ACL and artifact retention (L5196, L3894-3914, D-017/D-018).

Four modules, one boundary:

* `config` — the vocabulary migration 0003 already fixed (kinds, ACLs, readers) and the TWO
  frozen retention thresholds. The one place those numbers live.
* `keys` — the wrapping key of D-018: where it may sit, who may load it, and which of those
  refusals is measured end to end and which is not.
* `profile_crypto` — a separate DEK per profile (L3914), wrapped into `profile_deks`, with the
  blob on the volume and its handle in `browser_profiles.encrypted_storage_ref`.
* `artifacts` — the ACL comparison on the read path and the command that actually removes
  expired artifacts.

`__all__` lists only names this package really binds. Two packages in this repository once
exported 27 names between them that had never existed, so `from <package> import *` had been
raising the whole time with no test noticing; `test_every_all_entry_is_actually_bound` in
tests/test_repo_consistency.py is what stops that from happening again.
"""

from control_plane.storage.artifacts import (
    ARTIFACT_DIRECTORY_MODE,
    ARTIFACT_FILE_MODE,
    CHECKSUM_PREFIX,
    READERS_BY_ACL,
    ArtifactBytesCorrupted,
    ArtifactNotFound,
    ArtifactReadRefused,
    ArtifactRecord,
    ArtifactStore,
    ArtifactStoreError,
    ArtifactVolume,
    checksum_for,
    readers_admitted_by,
)
from control_plane.storage.config import (
    ARTIFACT_RETENTION,
    KINDS_NEVER_RECORDED_DURING_LOGIN,
    PHASES_WITH_NO_RECORDING,
    RETENTION_THRESHOLDS,
    TRACE_AFTER_LOGIN_RETENTION,
    ArtifactAcl,
    ArtifactIsNotRecorded,
    ArtifactKind,
    BrowserPhase,
    Reader,
    retention_until_for,
)
from control_plane.storage.keys import (
    ALLOWED_KEY_FILE_MODES,
    KEY_HOLDER,
    WRAPPING_KEY_LENGTH,
    KeyRefusal,
    Principal,
    WrappingKey,
    WrappingKeyRefused,
    key_id_for,
    load_wrapping_key,
    refusal_for_key_file,
)
from control_plane.storage.profile_crypto import (
    AEAD_ALGORITHM,
    DEK_LENGTH,
    NONCE_LENGTH,
    PROFILE_DIRECTORY_MODE,
    PROFILE_FILE_MODE,
    ProfileCryptoError,
    ProfileDecryptionFailed,
    ProfileDek,
    ProfileDekAlreadyLive,
    ProfileDekNotFound,
    ProfileSecretStore,
    ProfileVolume,
    decrypt_profile_bytes,
    encrypt_profile_bytes,
    generate_dek_material,
    storage_ref_for,
    unwrap_dek,
    wrap_dek,
)

__all__ = [
    "AEAD_ALGORITHM",
    "ALLOWED_KEY_FILE_MODES",
    "ARTIFACT_DIRECTORY_MODE",
    "ARTIFACT_FILE_MODE",
    "ARTIFACT_RETENTION",
    "CHECKSUM_PREFIX",
    "DEK_LENGTH",
    "KEY_HOLDER",
    "KINDS_NEVER_RECORDED_DURING_LOGIN",
    "NONCE_LENGTH",
    "PHASES_WITH_NO_RECORDING",
    "PROFILE_DIRECTORY_MODE",
    "PROFILE_FILE_MODE",
    "READERS_BY_ACL",
    "RETENTION_THRESHOLDS",
    "TRACE_AFTER_LOGIN_RETENTION",
    "WRAPPING_KEY_LENGTH",
    "ArtifactAcl",
    "ArtifactBytesCorrupted",
    "ArtifactIsNotRecorded",
    "ArtifactKind",
    "ArtifactNotFound",
    "ArtifactReadRefused",
    "ArtifactRecord",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactVolume",
    "BrowserPhase",
    "KeyRefusal",
    "Principal",
    "ProfileCryptoError",
    "ProfileDecryptionFailed",
    "ProfileDek",
    "ProfileDekAlreadyLive",
    "ProfileDekNotFound",
    "ProfileSecretStore",
    "ProfileVolume",
    "Reader",
    "WrappingKey",
    "WrappingKeyRefused",
    "checksum_for",
    "decrypt_profile_bytes",
    "encrypt_profile_bytes",
    "generate_dek_material",
    "key_id_for",
    "load_wrapping_key",
    "readers_admitted_by",
    "refusal_for_key_file",
    "retention_until_for",
    "storage_ref_for",
    "unwrap_dek",
    "wrap_dek",
]
