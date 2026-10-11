"""Pages for the CDP contract (D08, scope L5158-5159).

The fixture does not test the browser here — it tests that the pages on which
BrowserSupervisor is to prove popups, new tabs and `wait_for` actually exist and have
stable markers. The CDP contract itself is closed by the `cdp_contract` suite in
`tests/` (D18).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from wenext_fixture_site.app import create_app

from .conftest import make_settings


def test_popup_page_calls_window_open(client: TestClient) -> None:
    html = client.get("/lab/popup").text
    assert "window.open('/lab/popup-child', 'wenextPopup'" in html
    assert "window.open('/lab/popup-child', '_blank')" in html
    assert 'target="_blank"' in html


def test_popup_opens_automatically_on_load(client: TestClient) -> None:
    assert "window.addEventListener('load', openPopup)" in client.get("/lab/popup?auto=1").text
    assert "window.addEventListener('load', openPopup)" not in client.get("/lab/popup").text


def test_child_window_has_stable_marker(client: TestClient) -> None:
    html = client.get("/lab/popup-child").text
    assert 'data-testid="popup-marker"' in html
    assert "wenext-popup-child-ok" in html


def test_delayed_render_has_no_content_in_html(client: TestClient) -> None:
    """`wait_for` has something to wait on only if the content is NOT in the first response."""

    html = client.get("/lab/delayed").text
    assert 'id="delayed-content"' in html
    assert "hidden" in html
    # The target content appears only in the JS code, not in the rendered element.
    assert html.count("wenext-delayed-content-ok") == 1
    assert "wenext-delayed-content-ok';" in html


def test_default_delay_comes_from_config() -> None:
    client = TestClient(create_app(make_settings(delayed_render_ms=1500)), follow_redirects=False)
    assert "}, 1500);" in client.get("/lab/delayed").text
    assert "}, 250);" in client.get("/lab/delayed?delay_ms=250").text


def test_delay_is_capped(client: TestClient) -> None:
    assert "}, 30000);" in client.get("/lab/delayed?delay_ms=999999").text


def test_lab_pages_are_reachable_without_session(client: TestClient) -> None:
    for path in ("/lab", "/lab/popup", "/lab/popup-child", "/lab/delayed"):
        assert client.get(path).status_code == 200, path
