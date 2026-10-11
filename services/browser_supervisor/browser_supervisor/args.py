"""The single canonical BrowserSupervisor argument set.

The E1.F0 scope requires it explicitly: "one canonical BrowserSupervisor argument set"
(L5159). This is that place. If a list of Chrome flags shows up anywhere else in the repo,
it is a bug — the contract test in `tests/` guards this.

What is NOT here and never will be:

* `--no-sandbox` — forbidden. Section 4 bans hiding automation and circumventing
  protections, and 14.4 requires a working Chromium sandbox. Chrome refuses to start as
  root without this flag, so the answer is a non-root user, not disabling the sandbox.
* automation-masking flags (`--disable-blink-features=AutomationControlled` and the like) —
  this is literally "fingerprint spoofing and automation hiding mechanisms" from L244.
* `--proxy-server` with rotation — L247.
* `--headless=old` — the old headless is a different binary with different behaviour; mixing
  builds on one profile is forbidden (L2308).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from community_os_contracts import CapsuleMode

#: Flags common to both modes. The order is stable so that the process command line
#: is comparable between runs (it goes into the evidence).
COMMON_ARGS: tuple[str, ...] = (
    # Without this Chrome shows wizards on first start and changes the profile contents.
    "--no-first-run",
    "--no-default-browser-check",
    # The capsule has no GPU and does not need one to read content.
    "--disable-gpu",
    # We do not want Chrome updating components on its own — the build is pinned (L2313).
    "--disable-component-update",
    # Without this Chrome can kill background tabs, which breaks long reads.
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    # A default, empty start page — no requests to the world at startup.
    "about:blank",
)

#: Flags for headless mode only.
HEADLESS_ARGS: tuple[str, ...] = ("--headless",)


class ForbiddenFlag(Exception):
    """Raised when someone tries to add a flag from outside the scope boundary."""


#: Flags whose presence is a critical error, not a warning.
FORBIDDEN_FLAGS: tuple[str, ...] = (
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-web-security",
    "--disable-blink-features=AutomationControlled",
    "--proxy-server",
    "--headless=old",
)


#: The screen a person sees while signing in: the geometry the capsule always had.
HUMAN_GEOMETRY: tuple[int, int] = (1280, 900)

#: The agent's own screen when nobody watches (INTERACTIVE display_only, D-026): a portrait
#: 1440x2560, a real monitor turned on its side, with the window filling it. X renders only what
#: the window shows and the X reader never scrolls (`x_reader.py`: reads only), so in the default
#: window a conversation with 29 replies read as its first post alone (measured 27.09). A taller
#: real screen, not an emulated viewport: a page taller than its screen is an anomaly a platform
#: can see, a portrait monitor is not.
DISPLAY_ONLY_GEOMETRY: tuple[int, int] = (1440, 2560)


def display_only_window_args() -> tuple[str, ...]:
    width, height = DISPLAY_ONLY_GEOMETRY
    return ("--window-position=0,0", f"--window-size={width},{height}")


def build_args(
    *,
    mode: CapsuleMode,
    user_data_dir: Path,
    cdp_host: str = "127.0.0.1",
    cdp_port: int = 0,
    extra: Sequence[str] = (),
    display_only: bool = False,
) -> list[str]:
    """Builds the complete list of Chrome arguments for the given mode.

    `cdp_port=0` means "pick a free port" — the actual number lands in
    `DevToolsActivePort` in the profile directory and we read it from there only.

    `cdp_host` defaults to the loopback. Measured: with this setting CDP does not
    answer on the container address in the Docker network, i.e. there is no public CDP (L5159).
    """
    for flag in extra:
        for forbidden in FORBIDDEN_FLAGS:
            if flag == forbidden or flag.startswith(f"{forbidden}="):
                raise ForbiddenFlag(
                    f"Flaga {flag!r} jest zakazana w tym projekcie. "
                    "Patrz granica zakresu (sekcja 4) i wymóg sandboxa (14.4)."
                )

    args: list[str] = []
    if mode is CapsuleMode.HEADLESS:
        args.extend(HEADLESS_ARGS)

    args.extend(
        [
            f"--user-data-dir={user_data_dir}",
            f"--remote-debugging-port={cdp_port}",
            f"--remote-debugging-address={cdp_host}",
        ]
    )
    args.extend(extra)
    if display_only:
        args.extend(display_only_window_args())
    # `about:blank` must stay at the end — it is the start URL, not a flag.
    args.extend(a for a in COMMON_ARGS if not a.startswith("about:"))
    args.append("about:blank")
    return args


#: The second canonical set: a PERSON at the screen signing in (INTERACTIVE with a reason).
#:
#: Measured on 2026-09-27 in the capsule: Google refused a person's sign-in ("Couldn't sign you
#: in. This browser or app may not be secure") twice, in Chrome for Testing and in Chromium, both
#: launched with the set above, while the same account on the same display passed in a Chromium
#: started with exactly these flags and nothing else. So while a human signs in, the browser runs
#: the way a person's browser runs: no DevTools endpoint (nothing can attach, nothing can read),
#: none of the reading-oriented flags, and the login page as its first tab. The same executable
#: and the same profile as every other mode (L2307, L2308); the agent gets its endpoint back when
#: the capsule returns to display-only after "Gotowe".
HUMAN_ARGS: tuple[str, ...] = (
    "--no-first-run",
    "--no-default-browser-check",
)


def build_human_args(*, user_data_dir: Path, start_url: str | None = None) -> list[str]:
    """The argument list for a human sign-in: the profile, two first-run flags, the start page."""
    return [f"--user-data-dir={user_data_dir}", *HUMAN_ARGS, start_url or "about:blank"]
