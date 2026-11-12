"""Pictures for the card, fetched by the server and handed over inline (v11, 13.10).

THE FOUNDER (13.10): "the target post rendered like real X or Reddit": the author's photo, the
post's first image, our own avatar under it. The card is one self-contained page whose content
security policy is empty, and a remote image may simply not load in the host that draws it
(Codex), so the card does not point an image at the internet. It asks the server for the pictures
it needs (`room.pictures`, an app-only tool, so the model never sees it) and the server returns
each as a SMALL `data:` URI the page can paint without any request of its own.

WHAT MAKES THAT SAFE, from the outside in:

  * ONLY ADDRESSES ON THE PLATFORMS' OWN IMAGE HOSTS (`avatars.PICTURE_HOSTS`), https, no
    credentials, no other port: the address comes from a stranger's profile or post, and a server
    that fetches whatever it is told is a proxy for whoever wrote it. Redirects are not followed;
  * BOUNDED: five seconds per picture, at most `MAX_URLS` a call, at most `MAX_BYTES` a picture
    (a profile photo is a few kilobytes, a post's image a few tens), and the answer must be an
    image the page can draw (JPEG, PNG, WebP or GIF by its first bytes; an SVG, which can carry
    script, never);
  * SMALL ON PURPOSE. The platforms resize for us: a profile photo is asked at the 73 pixel size
    (`_bigger`) and a post's image at the `small` size, so nothing here needs an image library and
    nothing large ever travels to the card;
  * REMEMBERED: a picture is kept for a day (a miss for a quarter of an hour), so a card drawn
    again does not ask the platform again and the platform does not see one request per redraw.

THE FOUNDER'S OWN AVATAR (`account_avatar`): the picture of the account a draft leaves from, for
the reply drawn under the post. For X it is the public profile read through the same mirror as the
posts (`api.fxtwitter.com/<handle>`), once a day per account; Reddit has no such public read that
does not need a session, so its accounts have none and the card draws the platform's own glyph.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from .avatars import safe_avatar, safe_picture
from .snapshot import FXTWITTER_ORIGIN

_LOG = logging.getLogger(__name__)

#: Pictures per call, bytes per picture, seconds per fetch.
MAX_URLS = 24
MAX_BYTES = 150_000
TIMEOUT = 5.0
#: How long a fetched picture, and a miss, are remembered.
HIT_TTL = 24 * 3600.0
MISS_TTL = 15 * 60.0
CACHE_SIZE = 256

_PROFILE_SIZE = re.compile(
    r"_(?:normal|bigger|mini|reasonably_small|\d{2,4}x\d{2,4})(\.[A-Za-z]+)$"
)

_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

#: url -> (when it was learnt, the data URI or `None` for a miss), oldest first.
_CACHE: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
_AVATARS: dict[str, tuple[float, str | None]] = {}


def variant(url: str) -> str:
    """The small size of `url` the platform serves: a profile picture at 73 pixels, a post's image
    at `small`. An address of another shape is returned as it is."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "pbs.twimg.com" and parts.path.startswith("/profile_images/"):
        return f"https://{host}{_PROFILE_SIZE.sub(r'_bigger\1', parts.path)}"
    if host == "pbs.twimg.com" and parts.path.startswith("/media/"):
        query = dict(parse_qsl(parts.query))
        query["name"] = "small"
        query.setdefault("format", "jpg")
        return f"https://{host}{parts.path}?{urlencode(query)}"
    return url


def sniff(body: bytes) -> str | None:
    """The image type of `body` by its first bytes, or `None` when the page may not draw it."""
    for prefix, kind in _MAGIC:
        if body.startswith(prefix):
            return kind
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


def _remember(url: str, value: str | None) -> None:
    _CACHE[url] = (time.monotonic(), value)
    _CACHE.move_to_end(url)
    while len(_CACHE) > CACHE_SIZE:
        _CACHE.popitem(last=False)


def _recalled(url: str) -> tuple[bool, str | None]:
    found = _CACHE.get(url)
    if found is None:
        return False, None
    learnt, value = found
    if time.monotonic() - learnt > (HIT_TTL if value is not None else MISS_TTL):
        _CACHE.pop(url, None)
        return False, None
    return True, value


async def _get(client: Any, url: str) -> bytes | None:
    try:
        response = await client.get(
            url, headers={"User-Agent": "wenext/1.0 (+https://getwenext.com)"}
        )
        if response.status_code != 200 or len(response.content) > MAX_BYTES:
            return None
        return bytes(response.content)
    except Exception:  # network, TLS, a changed host: a picture is best effort
        return None


async def fetch_picture(url: str, *, client: Any = None) -> str | None:
    """One picture as a `data:` URI, or `None`. Never raises."""
    address = safe_picture(url)
    if address is None:
        return None
    hit, value = _recalled(address)
    if hit:
        return value
    owned = client is None
    http = client
    try:
        if owned:
            import httpx

            http = httpx.AsyncClient(
                timeout=httpx.Timeout(TIMEOUT, connect=3.0), follow_redirects=False, trust_env=False
            )
        body = await _get(http, variant(address))
    finally:
        if owned and http is not None:
            await http.aclose()
    kind = sniff(body) if body else None
    data = f"data:{kind};base64,{base64.b64encode(body).decode('ascii')}" if kind and body else None
    _remember(address, data)
    return data


async def fetch_pictures(urls: list[str], *, client: Any = None) -> dict[str, str]:
    """Each distinct allowed address in `urls` (at most `MAX_URLS`) that could be fetched, as a
    `data:` URI. Fetched in parallel; the ones that could not are simply absent."""
    wanted = list(dict.fromkeys(url for url in urls if isinstance(url, str)))[:MAX_URLS]
    done = await asyncio.gather(*(fetch_picture(url, client=client) for url in wanted))
    return {url: data for url, data in zip(wanted, done, strict=True) if data}


async def account_avatar(platform: str, handle: str, *, client: Any = None) -> str | None:
    """The profile picture address of an account of ours, or `None`. X only (see the module
    docstring); remembered for a day per account, a miss for a quarter of an hour."""
    if platform.upper() != "X" or not re.fullmatch(r"[A-Za-z0-9_]{1,30}", handle or ""):
        return None
    key = handle.lower()
    found = _AVATARS.get(key)
    if found is not None:
        learnt, value = found
        if time.monotonic() - learnt <= (HIT_TTL if value is not None else MISS_TTL):
            return value
    value = None
    owned = client is None
    http = client
    try:
        if owned:
            import httpx

            http = httpx.AsyncClient(
                timeout=httpx.Timeout(TIMEOUT, connect=3.0), follow_redirects=False, trust_env=False
            )
        response = await http.get(
            f"{FXTWITTER_ORIGIN}/{handle}",
            headers={"User-Agent": "wenext/1.0 (+https://getwenext.com)"},
        )
        if response.status_code == 200 and len(response.content) <= 200_000:
            payload = response.json()
            user = payload.get("user") if isinstance(payload, dict) else None
            if isinstance(user, dict):
                value = safe_avatar(user.get("avatar_url"))
    except Exception:
        _LOG.info("avatar konta %s nie został odczytany", handle, exc_info=True)
    finally:
        if owned and http is not None:
            await http.aclose()
    _AVATARS[key] = (time.monotonic(), value)
    return value


def forget() -> None:
    """Empty the memory. For tests."""
    _CACHE.clear()
    _AVATARS.clear()
