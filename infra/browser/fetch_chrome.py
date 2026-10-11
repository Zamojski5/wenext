#!/usr/bin/env python3
"""Downloads and verifies the pinned Chrome for Testing build.

Run while the capsule image is being built. The version and the hashes come EXCLUSIVELY
from `versions.lock.toml` — this script holds no version number at all, so that there is no
second place where it has to be bumped (DoD L5180: "ustalono exact browser versions").

What it does and why:

1. Picks the platform from the machine architecture. Chrome for Testing publishes
   `linux-arm64` only from 153.0.8001.0 — hence the project pin is 153.0.8010.12, the first
   line that has `linux-arm64` and `linux64` at the same time. Thanks to that, dev on Apple
   Silicon and CI on amd64 get the same exact executable (requirement L4948).

2. Verifies `size` and `md5Hash` from GCS. Chrome for Testing does not publish hashes in its
   own JSON, so we take them from the GCS JSON API and record them in the lock.

3. Writes the `WENEXT_PROFILE_BUILD` marker into the Chrome directory. BrowserSupervisor
   compares it against the marker recorded in user-data-dir and refuses to open a profile
   written by a different build — sekcja 14.2 forbids mixing Chrome / Chrome for Testing /
   Chromium / headless shell on one profile (L2308) and forbids a profile downgrade (L2311).
"""

from __future__ import annotations

import base64
import hashlib
import pathlib
import platform
import shutil
import sys
import tomllib
import urllib.request
import zipfile

#: Map from the Python architecture to the Chrome for Testing platform name.
ARCH_TO_PLATFORM = {
    "aarch64": "linux-arm64",
    "arm64": "linux-arm64",
    "x86_64": "linux64",
    "amd64": "linux64",
}

CHUNK = 1024 * 1024


def main() -> int:
    if len(sys.argv) != 3:
        print("użycie: fetch_chrome.py <versions.lock.toml> <katalog-docelowy>", file=sys.stderr)
        return 2

    lock_path = pathlib.Path(sys.argv[1])
    dest_dir = pathlib.Path(sys.argv[2])

    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    cft = lock["browser"]["chrome_for_testing"]
    version = cft["version"]

    machine = platform.machine().lower()
    platform_name = ARCH_TO_PLATFORM.get(machine)
    if platform_name is None:
        print(f"Nieobsługiwana architektura: {machine}", file=sys.stderr)
        return 1

    download = cft["downloads"].get(platform_name)
    if download is None:
        print(
            f"versions.lock.toml nie ma builda dla platformy {platform_name}. "
            f"Chrome for Testing publikuje linux-arm64 dopiero od 153.0.8001.0.",
            file=sys.stderr,
        )
        return 1

    print(f"Chrome for Testing {version} / {platform_name} ({machine})")

    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "chrome.zip"

    print(f"  pobieranie {download['url']}")
    md5 = hashlib.md5()  # noqa: S324 — MD5 only to compare with the GCS sum, not for security
    size = 0
    with urllib.request.urlopen(download["url"]) as response, zip_path.open("wb") as out_file:
        while kawalek := response.read(CHUNK):
            out_file.write(kawalek)
            md5.update(kawalek)
            size += len(kawalek)

    # --- integrity verification -----------------------------------------------
    expected_size = int(download["size"])
    if size != expected_size:
        print(f"  BŁĄD: rozmiar {size} != oczekiwany {expected_size}", file=sys.stderr)
        return 1

    md5_b64 = base64.b64encode(md5.digest()).decode("ascii")
    if md5_b64 != download["md5_base64"]:
        print(
            f"  BŁĄD: md5 {md5_b64} != oczekiwany {download['md5_base64']}",
            file=sys.stderr,
        )
        return 1
    print(f"  integralność OK ({size} B, md5 {md5_b64})")

    # --- unpacking ------------------------------------------------------------
    with zipfile.ZipFile(zip_path) as archiwum:
        archiwum.extractall(dest_dir)
    zip_path.unlink()

    extracted = dest_dir / download["unpacked_dir"]
    if not extracted.is_dir():
        print(f"  BŁĄD: brak katalogu {extracted}", file=sys.stderr)
        return 1

    # We unify the directory name so that the rest of the system need not know the platform.
    finalny = dest_dir / "chrome"
    if finalny.exists():
        shutil.rmtree(finalny)
    extracted.rename(finalny)

    # zipfile does not preserve the executable bits.
    for path in finalny.rglob("*"):
        if path.is_file() and (
            path.name in {"chrome", "chrome_crashpad_handler", "chrome_sandbox"}
            or path.suffix == ".so"
            or ".so." in path.name
        ):
            path.chmod(0o755)

    binarka = finalny / "chrome"
    if not binarka.is_file():
        print(f"  BŁĄD: brak binarki {binarka}", file=sys.stderr)
        return 1

    # --- build marker ---------------------------------------------------------
    # Compared by BrowserSupervisor against the marker in user-data-dir (L2308, L2311).
    (finalny / "WENEXT_PROFILE_BUILD").write_text(
        f"{cft['profile_build_marker']}\n{platform_name}\n", encoding="utf-8"
    )

    print(f"  gotowe: {binarka}")
    print(f"  znacznik builda: {cft['profile_build_marker']} / {platform_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
