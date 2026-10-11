"""Which pinned browser build this capsule runs, read from `versions.lock.toml`.

Until 27.09 every capsule ran Chrome for Testing and two modules read its section of the lock
directly. Then Google refused a person's sign-in inside Chrome for Testing ("Couldn't sign you in.
This browser or app may not be secure"), measured in the capsule on 2026-09-27, while the same
sign-in, same account, same display passed in Chromium. A person who signs in to X with Google
could not connect the account at all. So a capsule now names its build (`WENEXT_BROWSER_BUILD`),
and the lock pins each build separately (D-051).

One capsule still runs exactly one build on its profile: the profile marker (`profile.py`) refuses
a profile written by another build, so switching a capsule's build means a new profile and a new
login, never a reinterpretation of the old one (L2308).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

BUILD_ENV = "WENEXT_BROWSER_BUILD"
DEFAULT_BUILD = "chrome_for_testing"
#: The sections of `[browser.*]` in the lock that name a browser a capsule may run.
KNOWN_BUILDS: tuple[str, ...] = ("chrome_for_testing", "chromium")


class UnknownBuild(Exception):
    """The capsule was told to run a build the lock does not pin. It refuses to start."""


def build_name(env: Mapping[str, str] | None = None) -> str:
    environment = os.environ if env is None else env
    name = (environment.get(BUILD_ENV) or DEFAULT_BUILD).strip()
    if name not in KNOWN_BUILDS:
        raise UnknownBuild(
            f"{BUILD_ENV}={name!r} nie jest przypiętym buildem. Dozwolone: {list(KNOWN_BUILDS)}."
        )
    return name


def expected_browser_field(lock_path: Path, build: str) -> str:
    """The `Browser` field `/json/version` must answer for this build, exactly."""
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    try:
        return lock["browser"][build]["expected_cdp"]["browser_field"]
    except KeyError as exc:
        raise UnknownBuild(f"{lock_path} nie przypina buildu {build!r} ({exc})") from exc
