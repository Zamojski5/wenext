"""The pictures of a post, the quote it may carry, and how well an account fits (v11 round 3, 13.10; no database).

THE FOUNDER (13.10): "render the post like real X or Reddit", with the author's photo, the first picture and our own avatar. The card
has an empty content security policy, so the SERVER fetches each picture and the card paints it inline. Held here, pure:

  * THE FETCH (`opportunities/pictures.py`): only https addresses on the platforms' image hosts are ever requested (a stranger's
    address is not aimed at, whatever it says), the small size is asked for (a profile photo at 73 pixels, a post's image as `small`),
    the answer must be a small image of a type the page may draw (never an SVG), a failing source is `None`, a picture is remembered
    for a day (and a miss for a quarter of an hour) so a redraw does not ask the platform again;
  * OUR OWN AVATAR (`account_avatar`): X accounts only, once a day per account, a handle that is not a handle is never asked about;
  * THE POST'S EXTRAS (`snapshot.py`): the first picture (a photo, else a video's or a GIF's thumbnail) and the verification mark,
    parsed defensively, and a picture on a host that is not the platform's is no picture;
  * THE QUOTE: an own post that ENDS with the address of an X post (`trailing_status`), and only the end counts;
  * THE FIT (`ranking.account_fit`): a draft the founder cannot send right now ranks behind one he can;
  * THE TOOL (`room.pictures`): app-only, takes a list of addresses, returns the inline images it could get.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from control_plane.opportunities import pictures, ranking, snapshot
from control_plane.opportunities.avatars import (
    AVATAR_HOSTS,
    MEDIA_HOSTS,
    PICTURE_HOSTS,
    safe_avatar,
    safe_picture,
)
from control_plane.tools import card_pictures, catalog

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
AVATAR = "https://pbs.twimg.com/profile_images/1/abc_200x200.jpg"
MEDIA = "https://pbs.twimg.com/media/GxYz.jpg"


class FakeClient:
    """An `httpx.AsyncClient` that answers from a table and records every address it was asked."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[str] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.asked.append(url)
        answer = self.answers.get(url)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return SimpleNamespace(status_code=404, content=b"", json=lambda: {})
        if isinstance(answer, bytes):
            return SimpleNamespace(status_code=200, content=answer, json=lambda: {})
        return SimpleNamespace(status_code=200, content=b"{}", json=lambda: answer)


@pytest.fixture(autouse=True)
def _fresh_memory():
    pictures.forget()
    yield
    pictures.forget()


# ---------------------------------------------------------------------------
# The hosts
# ---------------------------------------------------------------------------


def test_a_picture_may_come_from_the_platforms_image_hosts_and_from_nowhere_else() -> None:
    assert set(AVATAR_HOSTS) <= set(PICTURE_HOSTS) and set(MEDIA_HOSTS) <= set(PICTURE_HOSTS)
    assert safe_picture(MEDIA) == MEDIA and safe_picture("https://i.redd.it/a.png")
    assert safe_avatar("https://preview.redd.it/a.png") is None, (
        "a post's image is no profile picture"
    )
    for bad in (
        None,
        "",
        "http://pbs.twimg.com/a.jpg",
        "https://evil.example/a.jpg",
        "https://pbs.twimg.com.evil.example/a.jpg",
        "https://user@pbs.twimg.com/a.jpg",
        "https://pbs.twimg.com:8443/a.jpg",
        "data:image/png;base64,AAAA",
        "https://pbs.twimg.com/" + "a" * 2000,
    ):
        assert safe_picture(bad) is None, bad


# ---------------------------------------------------------------------------
# The fetch
# ---------------------------------------------------------------------------


def test_the_small_size_is_asked_for() -> None:
    assert pictures.variant(AVATAR) == "https://pbs.twimg.com/profile_images/1/abc_bigger.jpg"
    assert (
        pictures.variant("https://pbs.twimg.com/profile_images/1/abc_normal.png")
        == "https://pbs.twimg.com/profile_images/1/abc_bigger.png"
    )
    assert pictures.variant(MEDIA) == f"{MEDIA}?name=small&format=jpg"
    assert pictures.variant("https://pbs.twimg.com/media/GxYz?format=png&name=large") == (
        "https://pbs.twimg.com/media/GxYz?format=png&name=small"
    )
    assert pictures.variant("https://i.redd.it/x.png") == "https://i.redd.it/x.png"


def test_only_an_image_the_page_may_draw_passes_and_an_svg_never_does() -> None:
    assert pictures.sniff(JPEG) == "image/jpeg" and pictures.sniff(PNG) == "image/png"
    assert pictures.sniff(b"GIF89a....") == "image/gif"
    assert pictures.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert (
        pictures.sniff(SVG) is None
        and pictures.sniff(b"<html>") is None
        and pictures.sniff(b"") is None
    )


async def test_a_picture_becomes_an_inline_image_and_is_remembered() -> None:
    client = FakeClient({pictures.variant(AVATAR): JPEG})
    first = await pictures.fetch_picture(AVATAR, client=client)
    assert first is not None and first.startswith("data:image/jpeg;base64,")
    assert await pictures.fetch_picture(AVATAR, client=client) == first
    assert client.asked == [pictures.variant(AVATAR)], (
        "the platform is asked once, not once per redraw"
    )


async def test_an_address_off_the_hosts_is_never_requested() -> None:
    client = FakeClient()
    for bad in (
        "https://evil.example/a.png",
        "http://pbs.twimg.com/a.png",
        "file:///etc/passwd",
        "",
    ):
        assert await pictures.fetch_picture(bad, client=client) is None
    assert client.asked == [], "an address a stranger wrote is not aimed at"


async def test_a_failure_is_none_and_a_miss_is_remembered_for_a_short_while() -> None:
    client = FakeClient(
        {
            pictures.variant(MEDIA): SVG,
            "https://i.redd.it/big.png": PNG + b"\x00" * (pictures.MAX_BYTES + 1),
            "https://i.redd.it/boom.png": RuntimeError("tls"),
        }
    )
    assert await pictures.fetch_picture(MEDIA, client=client) is None, "an SVG is not an image"
    assert await pictures.fetch_picture("https://i.redd.it/big.png", client=client) is None
    assert await pictures.fetch_picture("https://i.redd.it/boom.png", client=client) is None
    assert await pictures.fetch_picture("https://i.redd.it/gone.png", client=client) is None
    before = len(client.asked)
    assert await pictures.fetch_picture(MEDIA, client=client) is None
    assert len(client.asked) == before, "a miss is not asked again at once"


async def test_a_set_of_addresses_is_deduplicated_capped_and_only_what_came_is_returned() -> None:
    client = FakeClient({pictures.variant(AVATAR): JPEG, pictures.variant(MEDIA): PNG})
    got = await pictures.fetch_pictures(
        [AVATAR, MEDIA, AVATAR, "https://evil.example/x.png", "https://i.redd.it/none.png", 5],  # type: ignore[list-item]
        client=client,
    )
    assert set(got) == {AVATAR, MEDIA} and all(v.startswith("data:image/") for v in got.values())
    many = [f"https://i.redd.it/{n}.png" for n in range(pictures.MAX_URLS + 10)]
    asked = FakeClient({})
    await pictures.fetch_pictures(many, client=asked)
    assert len(asked.asked) == pictures.MAX_URLS, "at most MAX_URLS an answer"


# ---------------------------------------------------------------------------
# Our own avatar
# ---------------------------------------------------------------------------


async def test_the_avatar_of_an_x_account_is_read_once_a_day_and_reddit_has_none() -> None:
    url = f"{snapshot.FXTWITTER_ORIGIN}/demo_founder"
    client = FakeClient({url: {"code": 200, "user": {"avatar_url": AVATAR}}})
    assert await pictures.account_avatar("X", "demo_founder", client=client) == AVATAR
    assert await pictures.account_avatar("x", "demo_founder", client=client) == AVATAR
    assert client.asked == [url], "once per account"
    assert await pictures.account_avatar("REDDIT", "Demo_Maintainer", client=client) is None
    for bad in ("", "has space", "a/b", "x" * 40, "../etc"):
        assert await pictures.account_avatar("X", bad, client=client) is None
    assert client.asked == [url], "a handle that is not a handle is never asked about"


async def test_an_avatar_off_the_hosts_or_a_failing_mirror_is_none() -> None:
    off = FakeClient(
        {f"{snapshot.FXTWITTER_ORIGIN}/a": {"user": {"avatar_url": "https://evil.example/a.jpg"}}}
    )
    assert await pictures.account_avatar("X", "a", client=off) is None
    boom = FakeClient({f"{snapshot.FXTWITTER_ORIGIN}/b": RuntimeError("down")})
    assert await pictures.account_avatar("X", "b", client=boom) is None


# ---------------------------------------------------------------------------
# What a post carries besides its text
# ---------------------------------------------------------------------------


def _tweet(**over: Any) -> dict[str, Any]:
    tweet = {
        "id": "2103587050346250341",
        "text": "Hello",
        "author": {"screen_name": "jack", "name": "Jack", "avatar_url": AVATAR},
        "likes": 3,
    }
    tweet.update(over)
    return {"code": 200, "tweet": tweet}


def _parse(payload: dict[str, Any]) -> Any:
    return snapshot.parse_fxtwitter(
        payload,
        url="https://x.com/jack/status/2103587050346250341",
        status_id="2103587050346250341",
    )


def test_the_first_picture_is_a_photo_else_a_videos_or_a_gifs_thumbnail() -> None:
    photo = _parse(
        _tweet(media={"all": [{"type": "photo", "url": MEDIA, "width": 1200, "height": 675}]})
    )
    assert photo.media == {"typ": "photo", "url": MEDIA, "szer": 1200, "wys": 675}
    video = _parse(
        _tweet(
            media={
                "all": [
                    {
                        "type": "video",
                        "url": "https://video.twimg.com/x.mp4",
                        "thumbnail_url": "https://pbs.twimg.com/ext_tw_video_thumb/1/pu/img/a.jpg",
                        "width": 640,
                        "height": 360,
                    }
                ]
            }
        )
    )
    assert video.media["typ"] == "video" and video.media["url"].startswith("https://pbs.twimg.com/")
    gif = _parse(_tweet(media={"photos": [], "videos": [{"type": "gif", "thumbnail_url": MEDIA}]}))
    assert gif.media["typ"] == "gif"
    assert _parse(_tweet()).media is None, "a post without media has no picture"


def test_a_picture_on_a_host_that_is_not_the_platforms_is_no_picture() -> None:
    off = _parse(
        _tweet(
            media={
                "all": [
                    {"type": "photo", "url": "https://evil.example/a.jpg", "width": 9, "height": 9}
                ]
            }
        )
    )
    assert off.media is None
    first_bad_then_good = _parse(
        _tweet(
            media={
                "all": [
                    {"type": "photo", "url": "http://pbs.twimg.com/x.jpg"},
                    {"type": "photo", "url": MEDIA},
                    {"type": "weird", "url": MEDIA},
                ]
            }
        )
    )
    assert first_bad_then_good.media["url"] == MEDIA, "the first one that is safe"
    nonsense = _parse(
        _tweet(media={"all": [None, 5, {"type": "photo", "url": 5}, {"type": "photo"}]})
    )
    assert nonsense.media is None and _parse(_tweet(media="x")).media is None


def test_the_verification_mark_is_read_from_either_shape_and_unknown_is_not_false() -> None:
    def author(**extra: Any) -> dict[str, Any]:
        return {"screen_name": "jack", "name": "J", **extra}

    assert _parse(_tweet(author=author(verification={"verified": True}))).verified is True
    assert _parse(_tweet(author=author(verification={"verified": False}))).verified is False
    assert _parse(_tweet(author=author(verified=True))).verified is True
    assert _parse(_tweet(author=author())).verified is None, "not known is not 'not verified'"
    assert _parse(_tweet(author=author(verification="yes", verified="yes"))).verified is None


# ---------------------------------------------------------------------------
# A quote
# ---------------------------------------------------------------------------

STATUS = "https://x.com/jack/status/2103587050346250341"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"My take. {STATUS}", (STATUS, "jack", "2103587050346250341")),
        (f"My take.\n\n{STATUS}\n", (STATUS, "jack", "2103587050346250341")),
        (f"My take. {STATUS}?s=20", (f"{STATUS}?s=20", "jack", "2103587050346250341")),
        (
            "My take. https://twitter.com/Jack_1/statuses/42",
            ("https://twitter.com/Jack_1/statuses/42", "Jack_1", "42"),
        ),
        (f"{STATUS} and then more words", None),
        (f"See {STATUS} for the thread, then {STATUS}.", None),
        ("My profile https://x.com/jack", None),
        ("https://x.com/jack/status/not-a-number", None),
        ("http://x.com/jack/status/42", None),
        ("https://evil.example/jack/status/42", None),
        ("", None),
    ],
)
def test_only_an_address_at_the_end_of_the_text_makes_it_a_quote(text: str, expected: Any) -> None:
    assert snapshot.trailing_status(text) == expected


# ---------------------------------------------------------------------------
# The fit
# ---------------------------------------------------------------------------


def test_an_account_the_founder_cannot_send_from_ranks_behind_one_he_can() -> None:
    fit = ranking.account_fit
    assert fit(in_scope=True, scope_live=True, health="polaczone") == 1.0
    assert fit(in_scope=True, scope_live=True, health="nieznana") == 1.0, (
        "unreadable is not held against it"
    )
    assert fit(in_scope=True, scope_live=True, health=None) == 1.0
    assert fit(in_scope=False, scope_live=True, health="polaczone") == ranking.FIT_NO_SCOPE
    assert fit(in_scope=True, scope_live=False, health="polaczone") == ranking.FIT_NO_SCOPE
    for lost in ("utracona", "nigdy", "czeka_na_ciebie", "inne_konto"):
        assert fit(in_scope=True, scope_live=True, health=lost) == ranking.FIT_NO_SESSION, lost
    assert fit(in_scope=True, scope_live=True, health="wstrzymane") == ranking.FIT_HELD
    assert fit(in_scope=False, scope_live=True, health="utracona") == (
        ranking.FIT_NO_SCOPE * ranking.FIT_NO_SESSION
    )
    assert ranking.FIT_HELD < ranking.FIT_NO_SESSION < ranking.FIT_NO_SCOPE < 1.0


def test_the_fits_of_a_room_follow_the_scope_and_the_health() -> None:
    from control_plane.tools.opportunity_tools import _account_fits

    scope = {
        "stan": "AKTYWNY",
        "do_wyboru": [
            {"platforma": "X", "konto": "demo_founder", "w_zakresie": True},
            {"platforma": "REDDIT", "konto": "Demo_Maintainer", "w_zakresie": True},
            {"platforma": "X", "konto": "other", "w_zakresie": False},
        ],
    }
    health = [
        {"platforma": "X", "konto": "demo_founder", "stan": "polaczone"},
        {"platforma": "REDDIT", "konto": "Demo_Maintainer", "stan": "utracona"},
    ]
    assert _account_fits(scope, health) == {
        "x:demo_founder": 1.0,
        "reddit:demo_maintainer": ranking.FIT_NO_SESSION,
        "x:other": ranking.FIT_NO_SCOPE,
    }
    assert _account_fits(None, health) == {}, "a hosted session has no scope: every draft fits"
    paused = {**scope, "stan": "WSTRZYMANY"}
    assert set(_account_fits(paused, health).values()) <= {
        ranking.FIT_NO_SCOPE,
        ranking.FIT_NO_SCOPE * ranking.FIT_NO_SESSION,
    }


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


def test_the_tool_is_app_only_and_not_in_the_catalog_the_model_reads() -> None:
    assert "room.pictures" in catalog.APP_ONLY_TOOLS
    assert "room.pictures" not in {spec.name for spec in catalog.CATALOG}
    spec = card_pictures.CARD_PICTURE_TOOL_SPECS[0]
    assert spec["input_schema"]["required"] == ["adresy"] and "writes" not in spec


async def test_the_tool_returns_the_inline_images_it_could_get(monkeypatch) -> None:
    seen: list[list[str]] = []

    async def fake(addresses: list[str], **_kw: Any) -> dict[str, str]:
        seen.append(addresses)
        return {a: "data:image/png;base64,AAAA" for a in addresses if a == AVATAR}

    monkeypatch.setattr(card_pictures, "fetch_pictures", fake)
    tools = card_pictures.CardPictureTools()
    answer = await tools._room_pictures({"adresy": [AVATAR, MEDIA]})
    assert answer["stan"] == "WYKONANE" and answer["obrazy"] == {
        AVATAR: "data:image/png;base64,AAAA"
    }
    assert seen == [[AVATAR, MEDIA]]
    from control_plane.tools.registry import ToolError

    for bad in (None, "x", [1], {"a": 1}):
        with pytest.raises(ToolError):
            await tools._room_pictures({"adresy": bad})
    many = [f"https://i.redd.it/{n}.png" for n in range(100)]
    await tools._room_pictures({"adresy": many})
    assert len(seen[-1]) == pictures.MAX_URLS
