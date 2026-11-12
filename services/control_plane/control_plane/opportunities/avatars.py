"""Which avatar addresses the card may load (v11).

The card is self-contained (`apps_ui`: no script, font or image from anywhere else) and declares an
EMPTY content security policy. The avatar of the author of a post is the one exception the founder
asked for (13.10: "avatar, @handle, age, text"), and it is a narrow one: ONLY an `https` address on
a host that serves profile pictures for the platform itself, on this list, on both sides: the
server drops any other address before it reaches the card (`safe_avatar`), and the card repeats the
check (`AVATAR_HOSTS` in its script, pinned equal to this list by a test) and falls back to the
initials when the image does not load. An avatar address comes from a stranger's profile, so it is
data, and a host that is not the platform's CDN is refused: an image URL is a request the viewer's
machine makes, and it must not be one a stranger can aim.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Hosts of the platforms' own image CDNs for profile pictures.
AVATAR_HOSTS: tuple[str, ...] = (
    "pbs.twimg.com",
    "abs.twimg.com",
    "styles.redditmedia.com",
    "www.redditstatic.com",
    "i.redd.it",
)

#: Hosts of the platforms' own image CDNs for the PICTURES OF A POST (the first image or the video
#: thumbnail), on top of the profile-picture hosts above. A picture too is a request the viewer's
#: machine, or here the server, makes: only these hosts, only https.
MEDIA_HOSTS: tuple[str, ...] = (
    "pbs.twimg.com",
    "preview.redd.it",
    "external-preview.redd.it",
    "a.thumbs.redditmedia.com",
    "b.thumbs.redditmedia.com",
)

#: Every host a picture of the card may come from.
PICTURE_HOSTS: tuple[str, ...] = tuple(dict.fromkeys((*AVATAR_HOSTS, *MEDIA_HOSTS)))

MAX_LENGTH = 1000


def _safe(url: str | None, hosts: tuple[str, ...]) -> str | None:
    if not url or not isinstance(url, str) or len(url) > MAX_LENGTH:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or (parts.hostname or "").lower() not in hosts:
        return None
    if parts.username or parts.password or parts.port not in (None, 443):
        return None
    return url.strip()


def safe_avatar(url: str | None) -> str | None:
    """`url` when it is an https address on an allowed profile-picture host, else `None`."""
    return _safe(url, AVATAR_HOSTS)


def safe_picture(url: str | None) -> str | None:
    """`url` when it is an https address on any host a picture of the card may come from."""
    return _safe(url, PICTURE_HOSTS)
