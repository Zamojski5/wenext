#!/usr/bin/env python3
"""Generates infra/docker-config/config.json — a Docker config with no hanging helper.

The problem this solves:
    On the development host `~/.docker/config.json` has `"credsStore": "desktop"`.
    That helper can hang on keychain access, and then EVERY `docker pull` hangs
    indefinitely with `error getting credentials`. Measured: a pull that normally
    takes seconds does not finish after 10 minutes.

Why not simply `{"auths":{}}`:
    Because `DOCKER_CONFIG` also moves the place where Docker looks for CLI plugins.
    An empty directory = no `docker compose`, `docker buildx` etc. Measured:
    `docker: unknown command: docker compose`.

The solution:
    We copy the host configuration, remove ONLY `credsStore` and `credHelpers`,
    and point at the plugins through `cliPluginsExtraDirs`. All the images we
    use are public, so the missing helper breaks nothing.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET_CONFIG = ROOT / "infra" / "docker-config" / "config.json"

#: Keys responsible for the hanging helper. These are the only ones we remove.
KEYS_TO_STRIP = ("credsStore", "credHelpers")

#: Candidate directories with CLI plugins, in the order they are checked.
PLUGIN_DIRS = (
    pathlib.Path.home() / ".docker" / "cli-plugins",
    pathlib.Path("/Applications/Docker.app/Contents/Resources/cli-plugins"),
    pathlib.Path("/usr/local/lib/docker/cli-plugins"),
    pathlib.Path("/usr/lib/docker/cli-plugins"),
    pathlib.Path("/opt/homebrew/lib/docker/cli-plugins"),
)


def _link_host_dir(nazwa: str) -> None:
    """Creates a symlink to a Docker state directory from the host configuration.

    `DOCKER_CONFIG` moves the whole state directory, not just the credentials.
    Without symlinking `buildx/` and `contexts/` we lose the builder instances and the
    context definitions, even though `currentContext` stayed in config.json.
    """
    source = pathlib.Path.home() / ".docker" / nazwa
    link_path = TARGET_CONFIG.parent / nazwa
    if not source.is_dir():
        return
    if link_path.is_symlink() or link_path.exists():
        if link_path.is_symlink() and link_path.resolve() == source.resolve():
            return
        return
    link_path.symlink_to(source, target_is_directory=True)
    print(f"  dowiązano {nazwa}/ → {source}")


def main() -> int:
    source = pathlib.Path.home() / ".docker" / "config.json"
    config: dict = {}

    if source.exists():
        try:
            config = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"UWAGA: {source} nie jest poprawnym JSON-em; zaczynam od pustej konfiguracji.")
            config = {}

    removed_keys = [k for k in KEYS_TO_STRIP if config.pop(k, None) is not None]

    # We do not need the credentials — all the images are public.
    config["auths"] = {}

    existing_dirs = [str(p) for p in PLUGIN_DIRS if p.is_dir()]
    if existing_dirs:
        config["cliPluginsExtraDirs"] = existing_dirs
    else:
        print(
            "UWAGA: nie znalazłem katalogu wtyczek CLI Dockera. `docker compose` może nie działać."
        )

    TARGET_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    TARGET_CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    # The buildx state (builder instances) also lives under DOCKER_CONFIG. Without it
    # `docker build` ends with `no builder "desktop-linux" found`, because the context is
    # set, but the builder for it — not found. So we symlink the host state.
    _link_host_dir("buildx")
    _link_host_dir("contexts")

    print(f"Zapisano {TARGET_CONFIG.relative_to(ROOT)}")
    if removed_keys:
        print(f"  usunięto klucze wiszącego helpera: {', '.join(removed_keys)}")
    if existing_dirs:
        print(f"  wtyczki CLI: {existing_dirs[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
