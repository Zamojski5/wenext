"""Which browser a capsule runs is named, pinned and checked (D-051).

Google refuses a person's sign-in in Chrome for Testing, so the X capsule runs Chromium. What has
to hold for that to be safe:

* a capsule names its build, the default stays Chrome for Testing (Reddit's capsule is unchanged),
  and a build the lock does not pin is refused rather than guessed;
* the two builds answer different `Browser` fields, so a capsule started on the wrong binary
  fails its CDP check instead of running quietly;
* the image build refuses anything but the pinned Chromium, including Chrome for Testing
  shipped under the name "chromium".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from browser_supervisor import pinned

ROOT = Path(__file__).resolve().parents[3]
LOCK = ROOT / "versions.lock.toml"


def test_the_default_build_is_chrome_for_testing_and_unknown_builds_are_refused() -> None:
    assert pinned.build_name({}) == "chrome_for_testing"
    assert pinned.build_name({"WENEXT_BROWSER_BUILD": "chromium"}) == "chromium"
    with pytest.raises(pinned.UnknownBuild):
        pinned.build_name({"WENEXT_BROWSER_BUILD": "chrome"})


def test_the_lock_pins_both_builds_with_different_answers() -> None:
    cft = pinned.expected_browser_field(LOCK, "chrome_for_testing")
    chromium = pinned.expected_browser_field(LOCK, "chromium")
    assert cft.startswith("Chrome/") and chromium.startswith("Chrome/")
    assert cft != chromium, "kapsuła na złej binarce musi oblać sprawdzenie CDP"


def test_a_lock_without_the_build_is_a_refusal(tmp_path: Path) -> None:
    lock = tmp_path / "versions.lock.toml"
    lock.write_text('[browser.chrome_for_testing.expected_cdp]\nbrowser_field = "Chrome/1"\n')
    with pytest.raises(pinned.UnknownBuild):
        pinned.expected_browser_field(lock, "chromium")


def _pin_chromium():
    spec = importlib.util.spec_from_file_location(
        "pin_chromium", ROOT / "infra" / "browser" / "pin_chromium.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_image_build_refuses_anything_but_the_pinned_chromium() -> None:
    pin_chromium = _pin_chromium()
    pin = {
        "version": "151.0.7922.34",
        "executable": "/ms-playwright/chromium-1234/chrome-linux/chrome",
        "profile_build_marker": "chromium/151.0.7922.34",
    }
    marker = pin_chromium.verified_marker(pin, "Chromium 151.0.7922.34 \n", "aarch64")
    assert marker == "chromium/151.0.7922.34\nlinux-arm64\n"
    for wrong in ("Google Chrome for Testing 151.0.7922.34", "Chromium 151.0.7922.35", ""):
        with pytest.raises(ValueError):
            pin_chromium.verified_marker(pin, wrong, "aarch64")
    with pytest.raises(ValueError):
        pin_chromium.verified_marker(pin, "Chromium 151.0.7922.34", "riscv64")
