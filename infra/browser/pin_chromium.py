#!/usr/bin/env python3
"""Verifies the Chromium that ships in the capsule's base image and writes its build marker.

The X capsule runs Chromium, not Chrome for Testing, because Google refuses a person's sign-in
inside Chrome for Testing (measured 2026-09-27, D-051). Chromium is not downloaded: the pinned
Playwright base image already carries it. What this script adds is the proof and the marker:

1. `--version` must say exactly `Chromium <version>` from `versions.lock.toml`. Another version,
   or a build that calls itself something else (Playwright has shipped Chrome for Testing under
   the name "chromium" on some platforms), stops the image build here instead of shipping a
   browser Google refuses.
2. `WENEXT_PROFILE_BUILD` next to the executable, the same marker `fetch_chrome.py` writes for
   Chrome for Testing. BrowserSupervisor compares it with the marker in the profile and refuses a
   profile written by another build (L2308, L2311).

Like `fetch_chrome.py`, it holds no version number of its own: the lock is the one place.
"""

from __future__ import annotations

import pathlib
import platform
import subprocess
import sys
import tomllib

#: The same map as `fetch_chrome.py`: the marker names the platform, because two architectures
#: are two executables even at one version (L2307).
ARCH_TO_PLATFORM = {
    "aarch64": "linux-arm64",
    "arm64": "linux-arm64",
    "x86_64": "linux64",
    "amd64": "linux64",
}


def verified_marker(pin: dict, reported: str, machine: str) -> str:
    """The marker file's content, or `ValueError` saying why this executable is not the pin."""
    platform_name = ARCH_TO_PLATFORM.get(machine.lower())
    if platform_name is None:
        raise ValueError(f"nieobsługiwana architektura: {machine}")
    expected = f"Chromium {pin['version']}"
    if reported.strip() != expected:
        raise ValueError(
            f"{pin['executable']} mówi {reported.strip()!r}, a lock przypina {expected!r}. "
            "Inny build albo Chrome for Testing pod nazwą chromium: Google odmówi w nim logowania."
        )
    return f"{pin['profile_build_marker']}\n{platform_name}\n"


def main() -> int:
    if len(sys.argv) != 2:
        print("użycie: pin_chromium.py <versions.lock.toml>", file=sys.stderr)
        return 2
    lock = tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    pin = lock["browser"]["chromium"]
    executable = pathlib.Path(pin["executable"])
    if not executable.is_file():
        print(f"BŁĄD: brak {executable} w obrazie bazowym", file=sys.stderr)
        return 1
    reported = subprocess.run(
        [str(executable), "--version"], check=True, capture_output=True, text=True
    ).stdout
    try:
        marker = verified_marker(pin, reported, platform.machine())
    except ValueError as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1
    (executable.parent / "WENEXT_PROFILE_BUILD").write_text(marker, encoding="utf-8")
    print(f"Chromium {pin['version']}: {executable}, znacznik {marker.splitlines()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
