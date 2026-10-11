"""Tests of the canonical argument set and of the profile build marker."""

from __future__ import annotations

from pathlib import Path

import pytest
from browser_supervisor.args import (
    FORBIDDEN_FLAGS,
    ForbiddenFlag,
    build_args,
)
from browser_supervisor.profile import (
    ProfileBuild,
    ProfileBuildMismatch,
    ensure_build_match,
    marker_path,
)
from community_os_contracts import CapsuleMode

BUILD = ProfileBuild(marker="chrome-for-testing/153.0.8010.12", platform="linux-arm64")
OTHER_BUILD = ProfileBuild(marker="chrome-for-testing/152.0.7977.64", platform="linux-arm64")
OTHER_PLATFORM = ProfileBuild(marker="chrome-for-testing/153.0.8010.12", platform="linux64")


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def test_headless_mode_has_headless_flag(tmp_path: Path) -> None:
    args = build_args(mode=CapsuleMode.HEADLESS, user_data_dir=tmp_path)
    assert "--headless" in args


def test_interactive_mode_has_no_headless_flag(tmp_path: Path) -> None:
    args = build_args(mode=CapsuleMode.INTERACTIVE, user_data_dir=tmp_path)
    assert "--headless" not in args


def test_user_data_dir_and_port_zero_are_always_present(tmp_path: Path) -> None:
    args = build_args(mode=CapsuleMode.HEADLESS, user_data_dir=tmp_path)
    assert f"--user-data-dir={tmp_path}" in args
    assert "--remote-debugging-port=0" in args


def test_cdp_binds_to_loopback_by_default(tmp_path: Path) -> None:
    """No public CDP starts with the bind address (L5159)."""
    args = build_args(mode=CapsuleMode.HEADLESS, user_data_dir=tmp_path)
    assert "--remote-debugging-address=127.0.0.1" in args


@pytest.mark.parametrize("flag", FORBIDDEN_FLAGS)
def test_forbidden_flag_is_rejected(tmp_path: Path, flag: str) -> None:
    """The scope boundary (section 4) and the sandbox requirement (14.4) enforced in code."""
    with pytest.raises(ForbiddenFlag, match="zakazana"):
        build_args(mode=CapsuleMode.HEADLESS, user_data_dir=tmp_path, extra=[flag])


def test_no_canonical_argument_is_forbidden(tmp_path: Path) -> None:
    for mode in (CapsuleMode.HEADLESS, CapsuleMode.INTERACTIVE):
        args = build_args(mode=mode, user_data_dir=tmp_path)
        for forbidden in FORBIDDEN_FLAGS:
            assert not any(a.startswith(forbidden) for a in args), (
                f"kanoniczny zestaw dla {mode} zawiera zakazaną flagę {forbidden}"
            )


def test_about_blank_is_the_last_argument(tmp_path: Path) -> None:
    """The start URL has to come after the flags, otherwise Chrome treats it as a flag."""
    args = build_args(mode=CapsuleMode.HEADLESS, user_data_dir=tmp_path)
    assert args[-1] == "about:blank"
    assert args.count("about:blank") == 1


# ---------------------------------------------------------------------------
# The profile build marker
# ---------------------------------------------------------------------------


def test_new_profile_gets_a_build_marker(tmp_path: Path) -> None:
    profil = tmp_path / "capsule-01"
    assert ensure_build_match(profil, BUILD) is True
    assert marker_path(profil).exists()
    # A second call with the same build passes and is no longer "new".
    assert ensure_build_match(profil, BUILD) is False


def test_different_build_is_rejected(tmp_path: Path) -> None:
    """The ban on mixing builds on one profile (L2308)."""
    profil = tmp_path / "capsule-01"
    ensure_build_match(profil, BUILD)

    with pytest.raises(ProfileBuildMismatch, match="Mieszanie buildów"):
        ensure_build_match(profil, OTHER_BUILD)


def test_different_platform_is_rejected_too(tmp_path: Path) -> None:
    """linux-arm64 and linux64 are different binaries — the document requires THE SAME
    exact executable on a profile (L2307)."""
    profil = tmp_path / "capsule-01"
    ensure_build_match(profil, BUILD)

    with pytest.raises(ProfileBuildMismatch):
        ensure_build_match(profil, OTHER_PLATFORM)


def test_used_profile_without_marker_requires_human_decision(tmp_path: Path) -> None:
    """A profile with Chromium data but without a marker is not quietly adopted."""
    profil = tmp_path / "capsule-01"
    profil.mkdir(parents=True)
    (profil / "Local State").write_text("{}", encoding="utf-8")

    with pytest.raises(ProfileBuildMismatch, match="Wymagana decyzja człowieka"):
        ensure_build_match(profil, BUILD)


def test_only_the_agents_own_screen_gets_the_portrait_window(tmp_path) -> None:
    """27.09: X renders only what the window shows and the reader never scrolls, so a 29-reply
    conversation read as its first post. The agent's display-only window fills a portrait screen;
    headless and the person's sign-in keep what they had."""
    from browser_supervisor.args import DISPLAY_ONLY_GEOMETRY, build_human_args

    width, height = DISPLAY_ONLY_GEOMETRY
    window = ["--window-position=0,0", f"--window-size={width},{height}"]
    agent = build_args(mode=CapsuleMode.INTERACTIVE, user_data_dir=tmp_path, display_only=True)
    assert all(flag in agent for flag in window) and agent[-1] == "about:blank"
    assert height > width, "ekran pionowy: więcej odpowiedzi w jednym renderze"
    headless = build_args(mode=CapsuleMode.HEADLESS, user_data_dir=tmp_path)
    person = build_human_args(user_data_dir=tmp_path)
    assert not any(flag.startswith("--window-") for flag in headless + person)
