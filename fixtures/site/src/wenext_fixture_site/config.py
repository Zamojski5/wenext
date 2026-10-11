"""Fixture site configuration. Everything from environment variables, nothing from a file.

`WENEXT_FIXTURE_SEED` is the only input of the content generator. The same seed = the same
content down to the byte; hence no `datetime.now()` and no unseeded `random` in the generator.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

#: Time zero of the fixture. This is NOT `now()` — if it were, two runs would produce
#: different timestamps and `content_hash` would stop being stable.
FIXTURE_EPOCH = datetime(2026, 8, 30, 9, 0, 0, tzinfo=UTC)

DEFAULT_SEED = 20260830

#: Credentials of a fictional account. This is not a secret — the fixture protects nothing,
#: it models only the existence of the login wall.
DEFAULT_USERNAME = "fixture-user"
DEFAULT_PASSWORD = "fixture-pass"

SESSION_COOKIE = "wenext_fixture_session"
PENDING_COOKIE = "wenext_fixture_pending"
CLIENT_COOKIE = "wenext_fixture_client"

#: The session cookie has to survive a browser restart — the profile handover test
#: headful→headless (DoD L5176) rests on the profile carrying a persistent cookie.
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} nie jest liczbą całkowitą") from exc


@dataclass(frozen=True)
class Settings:
    seed: int
    bind_host: str
    bind_port: int
    username: str
    password: str
    #: After this many requests for pages behind the login wall within the window
    #: `challenge_window_seconds` the session gets a challenge screen. 0 disables it.
    challenge_after: int
    challenge_window_seconds: int
    #: Render delay on `/lab/delayed`, in milliseconds.
    delayed_render_ms: int

    @classmethod
    def from_env(cls) -> Settings:
        bind = os.environ.get("WENEXT_FIXTURE_BIND", "0.0.0.0:8000")
        host, _, port = bind.rpartition(":")
        return cls(
            seed=_int_env("WENEXT_FIXTURE_SEED", DEFAULT_SEED),
            bind_host=host or "0.0.0.0",
            bind_port=int(port or "8000"),
            username=os.environ.get("WENEXT_FIXTURE_USERNAME", DEFAULT_USERNAME),
            password=os.environ.get("WENEXT_FIXTURE_PASSWORD", DEFAULT_PASSWORD),
            challenge_after=_int_env("WENEXT_FIXTURE_CHALLENGE_AFTER", 120),
            challenge_window_seconds=_int_env("WENEXT_FIXTURE_CHALLENGE_WINDOW_SECONDS", 10),
            delayed_render_ms=_int_env("WENEXT_FIXTURE_DELAYED_RENDER_MS", 2000),
        )
