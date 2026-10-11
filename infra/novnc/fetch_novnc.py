#!/usr/bin/env python3
"""Downloads and verifies the pinned noVNC release for the Stream Gateway image.

Run while the gateway image is being built. The tag, the archive URL and its SHA-256 come
EXCLUSIVELY from `versions.lock.toml` (`[stack.capsule_packages]`) — this script holds no version
and no hash, so there is exactly one place to bump them (the same rule as `infra/browser/
fetch_chrome.py` for Chrome, DoD L5180).

Why a hash and not just a tag: noVNC is JavaScript that runs in the founder's browser next to the
WebSocket that carries keyboard and mouse into the capsule's Chrome (D-026, level 2). A tag can be
moved; a hash cannot. A mismatch is a hard failure of the build, never a warning.

What lands in the output directory: `core/` and `vendor/` only — the ES modules the viewer page
imports (`core/rfb.js` pulls the rest). Not `app/` (noVNC's full UI), not its HTML files: the one
page this gateway serves is written in `stream_gateway/app.py`, with its own CSP.
"""

from __future__ import annotations

import hashlib
import io
import pathlib
import sys
import tarfile
import tomllib
import urllib.request

KEEP_TOP_LEVEL = ("core", "vendor")
CHUNK = 1024 * 1024


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("użycie: fetch_novnc.py <versions.lock.toml> <katalog docelowy>", file=sys.stderr)
        return 2
    lock_path = pathlib.Path(argv[1])
    target = pathlib.Path(argv[2])
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock["stack"]["capsule_packages"]
    tag = packages["novnc"]
    url = packages["novnc_archive_url"]
    expected = packages["novnc_archive_sha256"].lower()
    if not url.endswith(f"/{tag}.tar.gz"):
        print(f"URL {url} nie odpowiada tagowi {tag} — lock jest niespójny", file=sys.stderr)
        return 3

    print(f"noVNC {tag}: pobieranie {url}")
    digest = hashlib.sha256()
    buffer = io.BytesIO()
    with urllib.request.urlopen(url, timeout=120) as response:
        while chunk := response.read(CHUNK):
            digest.update(chunk)
            buffer.write(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        print(
            f"SHA-256 archiwum noVNC nie zgadza się z versions.lock.toml:\n"
            f"  oczekiwano {expected}\n  pobrano    {actual}\nBuild przerwany.",
            file=sys.stderr,
        )
        return 4

    buffer.seek(0)
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    with tarfile.open(fileobj=buffer, mode="r:gz") as archive:
        for member in archive.getmembers():
            parts = pathlib.PurePosixPath(member.name).parts
            # `noVNC-1.7.0/core/rfb.js` → ('noVNC-1.7.0', 'core', 'rfb.js')
            if len(parts) < 3 or parts[1] not in KEEP_TOP_LEVEL:
                continue
            if not member.isfile():
                continue
            relative = pathlib.Path(*parts[1:])
            if ".." in relative.parts:
                print(f"odrzucono podejrzaną ścieżkę w archiwum: {member.name}", file=sys.stderr)
                return 5
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            destination.write_bytes(extracted.read())
            copied += 1
    if copied == 0 or not (target / "core" / "rfb.js").is_file():
        print("archiwum nie zawiera core/rfb.js — to nie jest noVNC", file=sys.stderr)
        return 6
    print(f"noVNC {tag}: {copied} plików w {target} (core/, vendor/), sha256 {actual[:16]}… OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
