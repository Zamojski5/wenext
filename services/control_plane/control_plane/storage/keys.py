"""The key that wraps every profile DEK: where it may live, and who may load it (D-018).

    "profile są szyfrowane oddzielnymi DEK; aktywna kapsuła montuje wyłącznie swój
     odszyfrowany profil" (L3914)
    "encrypted profile storage, artifacts ACL i retention" (L5196)

D-018 IN ONE SENTENCE: the wrapping key lives in a file with mode `0600`, OUTSIDE the database
volume, mounted only into the Browser Runner. A passphrase typed at start-up was rejected because
it kills the unattended operation GATE-3 had just confirmed as the point of the product, and the
system keychain was rejected because it ties an installation to one machine and has no
counterpart inside the container the runner actually runs in.

WHAT THIS BUYS AND WHAT IT DOES NOT, because the second half is the part that gets forgotten.
Encryption of the profile protects against a COPY OF THE DATABASE VOLUME and against another
service reading the profile. It does NOT protect against somebody holding an account on the host:
that person can read the key file and everything it unlocks. D-018 records this as an explicit
limitation destined for `docs/f0-limits/`, and it is repeated here rather than left in the
decision log, because the person who will be tempted to call this "profile encryption at rest,
full stop" is reading this docstring and not that entry.

FOUR REFUSALS, AND WHICH ONES ARE MEASURED. Every check below is a REFUSAL TO START, never a
warning — a warning about key permissions is a line in a log nobody reads.

*   mode wider than `0600` — MEASURED end to end on a real file, in
    `test_profile_crypto.py::test_a_key_file_with_wider_permissions_stops_the_start`, which
    chmods `0644`, sees the refusal, chmods back and sees the key load.
*   key file on the database volume — MEASURED end to end
    (`test_a_key_file_on_the_database_volume_stops_the_start`), with a control key of identical
    mode and length placed outside the volume, so the refusal cannot be the mode's doing.
*   key file owned by a different user — MEASURED ONLY THROUGH THE PURE PREDICATE
    `refusal_for_key_file`, with a synthetic owner id. Creating a file owned by somebody else
    needs root, so the end-to-end path of this one branch is NOT measured. Said plainly because a
    docstring promising a control nobody measured is worse than no docstring: the reader stops
    looking.
*   the caller is not the Browser Runner — MEASURED, and it is a CONVENTION INSIDE THIS
    CODEBASE, not an operating-system boundary. Nothing stops a control plane process from
    passing `Principal.BROWSER_RUNNER`. What the repository can check, and what
    `test_the_control_plane_cannot_read_the_runner_key` does check, is that no module of the
    control plane outside this storage package names the loader at all. The real enforcement is
    the mount (the key is not in the control plane container) plus POSIX ownership; NEITHER of
    those is measured by this package, and the compose file carries no such mount today.

WHY A PURE PREDICATE AND A THIN WRAPPER. `refusal_for_key_file` takes the three facts a `stat`
carries and returns a refusal or `None`; `load_wrapping_key` is what reads the file and calls it.
That split is the only reason the ownership branch can be measured at all, and it keeps the
decision table in one readable place instead of spread across early returns in an I/O function.
"""

from __future__ import annotations

import hashlib
import os
import stat as stat_module
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final


class Principal(StrEnum):
    """The two processes of E1 that could conceivably ask for this key.

    There is no third member and that is deliberate: the same reasoning migration 0003 applies to
    `artifacts.acl` — in E1 the only sides are the operator's control plane and the runner.
    """

    BROWSER_RUNNER = "BROWSER_RUNNER"
    CONTROL_PLANE = "CONTROL_PLANE"


#: The only principal D-018 lets hold the wrapping key. Spelled as a constant rather than
#: compared inline, so a widening is an edit to a named thing a test can assert on.
KEY_HOLDER: Final[Principal] = Principal.BROWSER_RUNNER

#: AES-256 needs exactly this many bytes. A shorter file is a truncated key, not a weaker one, and
#: the difference matters: `AESGCM` would raise on a 31-byte key at the first encryption, which is
#: hours after the moment the operator could have fixed it.
WRAPPING_KEY_LENGTH: Final[int] = 32

#: Permission bits a key file may carry. `0600` is D-018's word; `0400` is narrower and therefore
#: also acceptable — a rule that refuses something SAFER than what it demands is a rule nobody
#: obeys for long.
ALLOWED_KEY_FILE_MODES: Final[frozenset[int]] = frozenset({0o600, 0o400})


class KeyRefusal(StrEnum):
    """Why the key was refused. Asserted on by every test in this area, and that is the point.

    A test that only asserts "some exception was raised" passes when a DIFFERENT check fired —
    which is exactly how a suite in this repository once ran 36 green tests over a mechanism that
    had been deleted. Each member below is a distinct condition, and each test names the one it
    means to trigger.
    """

    WRONG_HOLDER = "WRONG_HOLDER"
    MISSING = "MISSING"
    NOT_A_REGULAR_FILE = "NOT_A_REGULAR_FILE"
    MODE_TOO_WIDE = "MODE_TOO_WIDE"
    OWNED_BY_ANOTHER_USER = "OWNED_BY_ANOTHER_USER"
    ON_THE_DATABASE_VOLUME = "ON_THE_DATABASE_VOLUME"
    WRONG_LENGTH = "WRONG_LENGTH"


class WrappingKeyRefused(Exception):
    """Refusal to start. Carries the machine-readable reason next to the operator's message."""

    def __init__(self, refusal: KeyRefusal, detail: str) -> None:
        super().__init__(f"Odmowa startu ({refusal.value}): {detail}.")
        self.refusal = refusal


@dataclass(frozen=True)
class WrappingKey:
    """The key material, plus the identifier that says WHICH key wrapped a given DEK.

    `material` is `repr=False`, so the bytes cannot reach a traceback, a log line or a pytest
    assertion dump by accident. `test_the_wrapping_key_never_appears_in_its_own_repr` measures it.

    `key_id` is a LABELLED digest of the material, not a random name: the value goes into
    `profile_deks.wrapping_key_id`, whose whole job is to tell a future rotation which rows were
    wrapped with which key. Deriving it from the material means two hosts given the same key file
    agree on the id without anybody writing it down twice.
    """

    key_id: str
    material: bytes = field(repr=False)


#: Domain separation for the key id. Without a label the id would be a plain SHA-256 of the key,
#: which is the shape an attacker can test candidate keys against offline; with it, the digest is
#: only meaningful inside this project.
_KEY_ID_LABEL: Final[bytes] = b"wenext-profile-dek-wrapping-key-id\x00"


def key_id_for(material: bytes) -> str:
    """The identifier stored in `profile_deks.wrapping_key_id` for this key material."""
    return hashlib.sha256(_KEY_ID_LABEL + material).hexdigest()[:32]


def refusal_for_key_file(
    *,
    file_mode: int,
    owner_user_id: int,
    size_bytes: int,
    process_user_id: int,
) -> KeyRefusal | None:
    """The decision table of D-018 over the facts a `stat` returns. `None` means acceptable.

    Pure on purpose. The ownership branch cannot be reached end to end without root, so this is
    the only surface on which it can be measured at all, and it is measured with a synthetic
    `owner_user_id`.

    Order is not arbitrary: permissions come before ownership because a world-readable key is
    already lost regardless of who owns it, and length comes last because a file that is
    unreadable to begin with has no length worth reporting.
    """
    permission_bits = stat_module.S_IMODE(file_mode)
    if permission_bits not in ALLOWED_KEY_FILE_MODES:
        return KeyRefusal.MODE_TOO_WIDE
    if owner_user_id != process_user_id:
        return KeyRefusal.OWNED_BY_ANOTHER_USER
    if size_bytes != WRAPPING_KEY_LENGTH:
        return KeyRefusal.WRONG_LENGTH
    return None


def _refuse(refusal: KeyRefusal, detail: str) -> WrappingKeyRefused:
    return WrappingKeyRefused(refusal, detail)


def load_wrapping_key(
    path: Path,
    *,
    holder: Principal,
    database_volume_directories: Iterable[Path],
) -> WrappingKey:
    """Reads the wrapping key, or refuses to start.

    `database_volume_directories` has NO DEFAULT, and that is the whole difference between a check
    and a decoration. An empty default would mean "no volume declared", the containment test would
    never fire, and D-018's second requirement would quietly stop existing — the exact shape of
    failure this repository keeps finding (`all([])` is `True`). A caller with genuinely nothing
    to declare has to pass an empty iterable and can be seen doing it.

    Symlinks are resolved before the containment test. A key file that is a link INTO the database
    volume is a key file on the database volume; comparing the unresolved path would let a
    one-line `ln -s` defeat the rule.
    """
    if holder is not KEY_HOLDER:
        raise _refuse(
            KeyRefusal.WRONG_HOLDER,
            f"klucz opakowujący DEK profili należy do {KEY_HOLDER.value}, a o odczyt prosi "
            f"{holder.value}. D-018: klucz montowany jest wyłącznie do Browser Runnera",
        )

    if not path.exists():
        raise _refuse(
            KeyRefusal.MISSING,
            f"nie ma pliku {path}. Bez klucza opakowującego nie da się odszyfrować żadnego "
            "profilu — to jest odmowa startu, nie praca w trybie bez szyfrowania",
        )
    if not path.is_file():
        raise _refuse(
            KeyRefusal.NOT_A_REGULAR_FILE,
            f"{path} nie jest zwykłym plikiem. Katalog, gniazdo ani urządzenie nie są kluczem",
        )

    for volume in database_volume_directories:
        resolved_volume = Path(volume).resolve()
        if path.resolve().is_relative_to(resolved_volume):
            raise _refuse(
                KeyRefusal.ON_THE_DATABASE_VOLUME,
                f"{path} leży na wolumenie bazy danych ({resolved_volume}). Klucz obok "
                "zaszyfrowanych danych nie chroni przed kopią wolumenu, czyli przed jedyną "
                "rzeczą, przed którą D-018 każe chronić",
            )

    file_status = path.stat()
    refusal = refusal_for_key_file(
        file_mode=file_status.st_mode,
        owner_user_id=file_status.st_uid,
        size_bytes=file_status.st_size,
        process_user_id=os.getuid(),
    )
    if refusal is KeyRefusal.MODE_TOO_WIDE:
        raise _refuse(
            refusal,
            f"{path} ma prawa {stat_module.S_IMODE(file_status.st_mode):04o}, a D-018 dopuszcza "
            f"wyłącznie {sorted(format(mode, '04o') for mode in ALLOWED_KEY_FILE_MODES)}. "
            "Szersze prawa znaczą, że klucz przeczyta każdy proces na hoście",
        )
    if refusal is KeyRefusal.OWNED_BY_ANOTHER_USER:
        raise _refuse(
            refusal,
            f"{path} należy do użytkownika {file_status.st_uid}, a proces działa jako "
            f"{os.getuid()}. Plik 0600 cudzego użytkownika i tak nie da się otworzyć — odmowa ma "
            "paść tutaj, z powodem, a nie później jako błąd wejścia-wyjścia",
        )
    if refusal is KeyRefusal.WRONG_LENGTH:
        raise _refuse(
            refusal,
            f"{path} ma {file_status.st_size} bajtów, a klucz AES-256 ma dokładnie "
            f"{WRAPPING_KEY_LENGTH}. Krótszy plik to klucz ucięty, nie słabszy",
        )

    material = path.read_bytes()
    if len(material) != WRAPPING_KEY_LENGTH:
        # Reachable only if the file changed between `stat` and `read` — a race, not a typo, and
        # a truncated key must not become a working one just because it was quick.
        raise _refuse(
            KeyRefusal.WRONG_LENGTH,
            f"{path} zmienił rozmiar w trakcie odczytu ({len(material)} bajtów)",
        )
    return WrappingKey(key_id=key_id_for(material), material=material)
