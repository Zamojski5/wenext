"""The capsule healthcheck — used by Docker Compose.

Why this is a module and not a one-line `curl` in the YAML:

The capsule starts with `--remote-debugging-port=0`, i.e. with a port assigned by the system.
That is deliberate: a fixed port would mean two capsules on one runner collide, and the port
number is not something one is allowed to assume up front. The actual port lands in
`DevToolsActivePort` in the profile directory and we read it **from there only**.

So the healthcheck does exactly what the Gateway does when attaching: it reads the file, checks
whether the port answers, and **whether our pinned build is the one answering**. Thanks to that
"healthy" means "the capsule is ready to work", not "some process is listening on some port".
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from . import pinned
from .devtools_port import MissingDevToolsPort, StaleDevToolsPort, verify

#: The reader's token header (`reader.READER_TOKEN_HEADER`), written out so the healthcheck,
#: which runs every few seconds, does not import the whole reader; a test keeps the two equal.
READER_TOKEN_HEADER = "X-Wenext-Reader-Token"


def person_at_the_screen(env: dict[str, str] | None = None) -> bool:
    """Whether the launch running now is a person's sign-in, which has no DevTools endpoint on
    purpose (`args.HUMAN_ARGS`). The reader's `/state` says so; without a reader, nobody can tell,
    and the answer is no."""
    environment = os.environ if env is None else env
    port = (environment.get("WENEXT_READER_PORT") or "").strip()
    token = (environment.get("WENEXT_READER_TOKEN") or "").strip()
    if not port or not token:
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/state", headers={READER_TOKEN_HEADER: token}
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            state = json.load(response)
    except Exception:
        return False
    return (
        state.get("mode") == "INTERACTIVE"
        and bool(state.get("reason"))
        and state.get("alive") is True
        and state.get("cdp") is None
    )


def main() -> int:
    lock = Path(os.environ.get("WENEXT_VERSIONS_LOCK", "/opt/wenext/versions.lock.toml"))
    profil = Path(os.environ.get("WENEXT_PROFILES_DIR", "/var/wenext/profiles")) / os.environ.get(
        "WENEXT_CAPSULE_ID", "capsule-01"
    )

    try:
        expected = pinned.expected_browser_field(lock, pinned.build_name())
    except (OSError, pinned.UnknownBuild) as exc:
        print(f"nie udało się odczytać oczekiwanego builda z {lock}: {exc}", file=sys.stderr)
        return 1

    try:
        endpoint = verify(
            profil,
            expected_browser=expected,
            host=os.environ.get("WENEXT_CDP_HOST", "127.0.0.1"),
            timeout=3.0,
        )
    except (MissingDevToolsPort, StaleDevToolsPort) as exc:
        if person_at_the_screen():
            print("ok: człowiek loguje się przy ekranie, przeglądarka celowo bez CDP")
            return 0
        print(str(exc), file=sys.stderr)
        return 1

    print(f"ok: {expected} na porcie {endpoint.port}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
