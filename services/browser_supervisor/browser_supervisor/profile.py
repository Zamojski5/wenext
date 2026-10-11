"""The build marker in the profile directory.

Section 14.2 (L2300-2313) says explicitly:

* "no mixing of Chrome, Chrome for Testing, Chromium and headless shell on one profile"
* "the same exact executable in all modes of a given profile"
* "no downgrade of a profile opened by a newer version"

A Chromium profile has no field of its own carrying a trustworthy version that could be
checked BEFORE launch. That is why we write our own marker on first use of the profile and
compare it on every subsequent one. The same marker is baked into the image by `fetch_chrome.py`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MARKER_FILE_NAME = "wenext-profile-build"


class ProfileBuildMismatch(Exception):
    """The profile was opened by a different browser build. The capsule refuses to start."""


@dataclass(frozen=True)
class ProfileBuild:
    """The identity of the build that is allowed to open a given profile."""

    marker: str
    platform: str

    def line(self) -> str:
        return f"{self.marker}\n{self.platform}\n"

    @classmethod
    def from_file(cls, path: Path) -> ProfileBuild:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) < 2:
            raise ProfileBuildMismatch(f"{path}: niekompletny znacznik builda")
        return cls(marker=lines[0].strip(), platform=lines[1].strip())


def marker_path(user_data_dir: Path) -> Path:
    return user_data_dir / MARKER_FILE_NAME


def ensure_build_match(user_data_dir: Path, build: ProfileBuild) -> bool:
    """Checks or creates the marker. Returns True if the profile is new.

    Raises `ProfileBuildMismatch` if the profile was previously opened by a different build —
    including when only the platform differs. `linux-arm64` and `linux64` are different
    binaries; the document requires THE SAME exact executable on a profile (L2307).
    """
    user_data_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_path(user_data_dir)

    if not marker.exists():
        # Is this really a new profile, or an old profile without a marker?
        # The presence of `Default/` or `Local State` means Chromium has already written here.
        already_used = (user_data_dir / "Local State").exists() or (
            user_data_dir / "Default"
        ).exists()
        if already_used:
            raise ProfileBuildMismatch(
                f"{user_data_dir} zawiera dane Chromium, ale nie ma znacznika builda. "
                "Nie wiadomo, którym buildem był otwierany, a zgadywanie łamie zakaz "
                "mieszania buildów (L2308). Wymagana decyzja człowieka: skasować profil "
                "albo dopisać znacznik ręcznie."
            )
        marker.write_text(build.line(), encoding="utf-8")
        return True

    stored = ProfileBuild.from_file(marker)
    if stored != build:
        raise ProfileBuildMismatch(
            f"Profil {user_data_dir} był otwierany buildem "
            f"{stored.marker} / {stored.platform}, a próbujemy go otworzyć buildem "
            f"{build.marker} / {build.platform}. Mieszanie buildów na jednym profilu jest "
            "zakazane (L2308), a downgrade profilu — również (L2311). "
            "Domyślnym recovery jest reauth na nowym profilu, nie przenoszenie cookies (L2312)."
        )
    return False


def read_image_build(chrome_dir: Path) -> ProfileBuild:
    """Reads the marker baked into the image by fetch_chrome.py."""
    return ProfileBuild.from_file(chrome_dir / "WENEXT_PROFILE_BUILD")


#: Chrome's single-instance guard on Linux: three symlinks in the user data dir. `SingletonLock`
#: points at `<hostname>-<pid>` of the process that owns the profile.
SINGLETON_FILES: tuple[str, ...] = ("SingletonLock", "SingletonSocket", "SingletonCookie")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def remove_stale_singleton_lock(user_data_dir: Path, *, hostname: str) -> tuple[str, ...]:
    """Removes Chrome's `Singleton*` symlinks left by a Chrome that is not running HERE.

    A hard stop (Docker killed, host asleep) leaves `SingletonLock -> <hostname>-<pid>` in the
    profile volume. A recreated container has another hostname, so Chrome refuses the profile
    ("in use by another Chromium process on another computer") and the capsule loops on
    `CapsuleStartFailed` (measured 2026-09-12 17:26 CEST: 5 restarts, no CDP endpoint). The
    capsule holds the lease on the profile and no Chrome of ours runs before `start()`, so a lock
    of another host, or of a pid that is dead on this host, is stale by definition.

    NOT removed: a lock of THIS host whose pid is alive. That is a real second Chrome on the
    profile and the rule "two processes on one profile are forbidden" must keep failing loudly.
    Returns the names removed (empty when nothing was, or when the lock was not a symlink).
    """
    lock = user_data_dir / "SingletonLock"
    if not lock.is_symlink():
        return ()
    target = os.readlink(lock)
    host, _, pid_text = target.rpartition("-")
    if host == hostname and pid_text.isdigit() and _pid_alive(int(pid_text)):
        return ()
    removed: list[str] = []
    for name in SINGLETON_FILES:
        path = user_data_dir / name
        if path.is_symlink() or path.exists():
            path.unlink()
            removed.append(name)
    return tuple(removed)
