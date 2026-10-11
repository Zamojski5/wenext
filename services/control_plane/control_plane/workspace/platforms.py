"""What each platform can ACTUALLY do in this product today, written down in one place.

ROADMAP-PIVOT P1 ends with a sentence that is the whole reason this module exists: "konto
nieobsługiwanej jeszcze platformy nie udaje aktywnej integracji". A user may add an X or a
LinkedIn account — those are real identities and the model of profiles and accounts covers them
from the start (D-040) — but the screen has to say that nothing will happen on them, and it has
to say it from a fact about this repository rather than from a hopeful default.

THIS TABLE IS NOT IN THE DATABASE, ON PURPOSE. A row saying "REDDIT can comment" is a claim
about the code; kept as data it would survive the adapter being removed, and it would have to be
seeded into every new database before the first screen could be honest. Kept here, it is read
from the same tree that either has the adapter or does not.

WHAT EACH FLAG MEANS, and how each value below was decided — by looking, not by remembering:

  * `read` — there is an observation adapter, i.e. a member of
    `community_os_contracts.ObservationPlatform` with a source built by
    `control_plane.observe.wiring.build_sources`. REDDIT and HACKER_NEWS, and nothing else.
  * `comment` — the publication path can put a reply on the platform.
    `control_plane.pilot.publisher` drives exactly one: Reddit, through the capsule.
  * `own_post` — the publication path can found a thread of our own on the platform. TRUE FOR
    REDDIT SINCE 0023: `browser_supervisor.writer.CdpCommentActuator.own_post` fills the submit
    form and reads the new thread back, `POST /act/post` serves it, and
    `execution.adapter.RedditCapsuleOwnPostPort` is the only caller. False everywhere else,
    because on the other platforms nothing writes one. This flag says what the CODE can do; it
    is not permission: the delegation says whether a person allowed it, and the community's
    voice card says whether an own post belongs there at all (`presence/own_posts.py`).
  * `join_community` — the product can make OUR ACCOUNT a member of a community on the
    platform, as a person clicking „dołącz" would. FALSE EVERYWHERE TODAY: nothing in this
    repository performs it, and the capsule's actuator knows two writes (`/act/comment`,
    `/act/post`) and no third. The flag exists before the capability on purpose (E3, audyt UX
    20.09): dopóki jest fałszem, karta społeczności NIE proponuje „dołącz", zamiast proponować
    i odmawiać po kliknięciu. Obserwowanie społeczności w Wenext to co innego i nie wymaga tej
    zdolności.
  * `effect_readback` — the system can come back and look at what happened to a published
    piece. `publication_observations` is filled for Reddit only.
  * `audience_metrics` — follower counts and their change over time. TRUE ON X SINCE 27.09: the
    hourly X collection reads the account's own profile and writes the follower count to
    `audience_snapshots` (`workspace/audience.py`). False elsewhere, where nothing reads them,
    and the screen says "brak danych" instead of a zero (ZAKRES-FUNKCJONALNY §6 rule 2).

`X` SINCE 26.09 (task "integracja X"): every flag below points at a real path, and each one was
measured before it was set, not promised. `read`: the capsule renders x.com in the profile the
human logged into (`browser_supervisor.x_reader`, `/x/read`), and `community_observer.x_browser`
plus the `x.*` tools of `control_plane.tools.x_tools` read it. `comment` and `own_post`: the
capsule's `/act/x/reply` and `/act/x/post` (`browser_supervisor.x_writer`), reached only through
`execution.adapter.XCapsuleReplyPort` / `XCapsuleOwnPostPort` under a consumed intent.
`effect_readback`: the publisher reads our post back as the author and, in a fresh context, as a
stranger (`pilot.publisher`, X branch). X has no communities in the Reddit sense, so
`scoped_by_community` is false: a delegation for X names the account, the actions, the limits
and the deadline.

`LINKEDIN` is here with every flag false rather than absent. Absence would make an unsupported
platform indistinguishable from a typo, and both would be refused with the same message;
present-and-empty is what lets the product accept the account and tell the truth about it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

#: Label shown next to an account whose platform has no working integration. Polish: the founder
#: reads it (D-010).
NO_INTEGRATION_LABEL = "integracja niedostępna"


@dataclass(frozen=True)
class PlatformSupport:
    """What the product can do on one platform. Frozen: a capability edited at run time is a
    capability nobody can test against."""

    platform: str
    #: Operator-facing name. Polish text is allowed here; the key above is the code.
    label: str
    read: bool
    comment: bool
    own_post: bool
    effect_readback: bool
    audience_metrics: bool
    #: Czy produkt umie zapisać NASZE KONTO do społeczności na platformie. Domyślnie False, bo
    #: nowa platforma niczego nie umie, dopóki ktoś tego nie napisze i nie zmierzy.
    join_community: bool = False
    #: Whether a publication on this platform belongs to a COMMUNITY that a delegation lists.
    #: True on Reddit (a reply and an own post live in r/<sub>), false on X, where the place is
    #: the account and its timeline. `presence.approval` and `delegation.propose` read this flag
    #: instead of naming the platform, so a new platform decides it here, once.
    scoped_by_community: bool = True

    @property
    def integrated(self) -> bool:
        """True when the product can do ANYTHING on this platform beyond storing the name.

        Derived rather than stored: a separate boolean would be free to disagree with the flags
        it summarises, and the disagreement would show up as a screen promising work that no
        code performs.
        """
        return self.read or self.comment or self.own_post

    def as_payload(self) -> dict[str, object]:
        """The shape the dashboard receives. Flat, so the screen cannot compute a capability of
        its own — it prints what the backend measured."""
        return {
            "platform": self.platform,
            "label": self.label,
            "integrated": self.integrated,
            "read": self.read,
            "comment": self.comment,
            "own_post": self.own_post,
            "effect_readback": self.effect_readback,
            "audience_metrics": self.audience_metrics,
            "join_community": self.join_community,
            "scoped_by_community": self.scoped_by_community,
            "note": None if self.integrated else NO_INTEGRATION_LABEL,
        }


#: The closed set. Adding a platform is a deliberate edit of this tuple, visible in a diff, and
#: it is the same list the API validates `platform` against — a value outside it is a REFUSAL,
#: not a new platform, for the same reason `audit.AUDIT_KINDS` is closed: a typo must not become
#: a category.
SUPPORTED_PLATFORMS: tuple[PlatformSupport, ...] = (
    PlatformSupport(
        platform="REDDIT",
        label="Reddit",
        read=True,
        comment=True,
        own_post=True,
        effect_readback=True,
        audience_metrics=False,
    ),
    PlatformSupport(
        platform="HACKER_NEWS",
        label="Hacker News",
        read=True,
        comment=False,
        own_post=False,
        effect_readback=False,
        audience_metrics=False,
    ),
    PlatformSupport(
        platform="X",
        label="X",
        read=True,
        comment=True,
        own_post=True,
        effect_readback=True,
        audience_metrics=True,
        scoped_by_community=False,
    ),
    PlatformSupport(
        platform="LINKEDIN",
        label="LinkedIn",
        read=False,
        comment=False,
        own_post=False,
        effect_readback=False,
        audience_metrics=False,
    ),
)

#: Indexed once, read-only. `MappingProxyType` rather than a plain dict so a caller cannot edit
#: the capability matrix of the running process by accident.
PLATFORMS: Mapping[str, PlatformSupport] = MappingProxyType(
    {entry.platform: entry for entry in SUPPORTED_PLATFORMS}
)


def support_for(platform: str) -> PlatformSupport | None:
    """The capabilities of `platform`, or None when the name is not one this product knows."""
    return PLATFORMS.get(platform)


def platform_payload() -> list[dict[str, object]]:
    """The whole matrix, in declaration order, for the screen that offers a platform to pick."""
    return [entry.as_payload() for entry in SUPPORTED_PLATFORMS]
