"""The platform capability matrix, checked against the code it claims to describe.

A table of booleans is the easiest thing in this repository to leave behind: nothing breaks when
it says REDDIT can read and the adapter has been deleted, and nothing breaks when an adapter is
added and the table still says the platform is unsupported. Both directions end with a screen
making a promise no code keeps, so both are tested — and the `read` column is compared against
`community_os_contracts.ObservationPlatform`, which is the enum an adapter actually has to join.

No database here: this is a fact about the tree.
"""

from __future__ import annotations

from community_os_contracts import ObservationPlatform
from control_plane.workspace.platforms import (
    NO_INTEGRATION_LABEL,
    PLATFORMS,
    SUPPORTED_PLATFORMS,
    platform_payload,
    support_for,
)


def test_the_matrix_lists_exactly_the_four_platforms_of_the_product() -> None:
    """Count first, then contents. A matrix that quietly emptied would pass every loop below."""
    assert len(SUPPORTED_PLATFORMS) == 4, [entry.platform for entry in SUPPORTED_PLATFORMS]
    assert sorted(PLATFORMS) == ["HACKER_NEWS", "LINKEDIN", "REDDIT", "X"], sorted(PLATFORMS)


def test_read_matches_the_platforms_that_really_have_an_observation_adapter() -> None:
    """`read: true` means there is a member of `ObservationPlatform`, in BOTH directions.

    The reverse direction is the one that matters on the day somebody adds an adapter: without
    it the new platform would stay marked as unsupported and the screen would keep telling the
    user that nothing can be read there, with every test green.
    """
    declared_readable = {entry.platform for entry in SUPPORTED_PLATFORMS if entry.read}
    adapters = {member.value for member in ObservationPlatform}
    assert adapters, "ObservationPlatform jest pusty — porównanie nic by nie znaczyło"
    assert adapters <= declared_readable, (
        f"macierz mówi, że czytamy {sorted(declared_readable)}, a adaptery obserwacji istnieją "
        f"dla {sorted(adapters)}"
    )
    # X since 26.09 is read by the AGENT through the capsule (`x.*` tools over `/x/read`), not
    # by the community-polling observation pipeline: X has no communities to poll. It is the
    # one platform allowed to be readable without an `ObservationPlatform` member, named here so
    # a third one cannot join silently.
    assert declared_readable - adapters == {"X"}, sorted(declared_readable - adapters)


def test_only_reddit_and_x_write_anything_at_all() -> None:
    """Measured against `control_plane.pilot.publisher`, which drives exactly these two.

    Reddit: replies since D-028, own threads since 0023 (`writer.CdpCommentActuator.own_post`,
    `POST /act/post`, `RedditCapsuleOwnPostPort`). X since 26.09: `x_writer`, `POST /act/x/reply`
    and `/act/x/post`, `XCapsuleReplyPort` / `XCapsuleOwnPostPort`. Audience metrics exist only
    where something reads them: on X since 27.09 (the hourly collection reads our profile);
    everywhere else the column stays empty, and that is what keeps "brak danych" from quietly
    becoming a zero.
    """
    commenting = {entry.platform for entry in SUPPORTED_PLATFORMS if entry.comment}
    posting = {entry.platform for entry in SUPPORTED_PLATFORMS if entry.own_post}
    assert commenting == {"REDDIT", "X"}, sorted(commenting)
    assert posting == {"REDDIT", "X"}, sorted(posting)
    measured = {entry.platform for entry in SUPPORTED_PLATFORMS if entry.audience_metrics}
    assert measured == {"X"}, sorted(measured)


def test_no_platform_claims_it_can_join_a_community_with_our_account() -> None:
    """Nothing anywhere in the tree signs our account up to a community, so the column is empty.

    This is the column that made "Dołączono" a lie (UX audit 20.09, point 3): observation was
    being reported as membership. The card offers `dolacz` only where this flag is true, so the
    day the flag is flipped without an actuator behind it, the product starts promising the same
    thing again — hence a test that says out loud that today the honest answer is "no platform".
    """
    joining = {entry.platform for entry in SUPPORTED_PLATFORMS if entry.join_community}
    assert joining == set(), sorted(joining)


def test_x_can_read_write_and_check_the_effect_but_has_no_communities() -> None:
    """26.09: every X flag points at a real path (see the module docstring), and a delegation for
    X is not scoped by community, because on X the place is the account and its timeline."""
    x = support_for("X")
    assert x is not None and x.integrated
    assert (x.read, x.comment, x.own_post, x.effect_readback) == (True, True, True, True)
    assert x.audience_metrics is True, "27.09: zbieranie co godzinę czyta obserwujących z profilu"
    assert x.scoped_by_community is False
    assert support_for("REDDIT").scoped_by_community is True
    assert x.as_payload()["note"] is None


def test_a_platform_with_no_integration_says_so_and_is_still_a_known_platform() -> None:
    """LinkedIn is PRESENT with everything false.

    Absent would make an unsupported platform indistinguishable from a typo, and P1 wants the
    account accepted with the truth attached, not refused.
    """
    for name in ("LINKEDIN",):
        entry = support_for(name)
        assert entry is not None, name
        assert entry.integrated is False, entry
        assert entry.as_payload()["note"] == NO_INTEGRATION_LABEL, entry.as_payload()

    reddit = support_for("REDDIT")
    assert reddit is not None and reddit.integrated is True
    assert reddit.as_payload()["note"] is None, reddit.as_payload()


def test_an_unknown_name_is_not_a_platform() -> None:
    assert support_for("MASTODON") is None
    assert support_for("") is None


def test_the_payload_carries_every_flag_the_screen_prints() -> None:
    """The dashboard must not have to derive a capability; it prints what arrives.

    So the payload of every platform carries the same closed set of keys, including the ones
    that are false — a key dropped when it is false is a key the screen cannot distinguish from
    "not measured".
    """
    payload = platform_payload()
    assert len(payload) == 4, payload
    expected = {
        "platform",
        "label",
        "integrated",
        "read",
        "comment",
        "own_post",
        "join_community",
        "effect_readback",
        "audience_metrics",
        "scoped_by_community",
        "note",
    }
    for entry in payload:
        assert set(entry) == expected, entry


def test_the_matrix_cannot_be_edited_through_the_index() -> None:
    """`PLATFORMS` is a read-only view. A process that could rewrite its own capability matrix
    at run time is a process whose screens cannot be tested against it."""
    try:
        PLATFORMS["REDDIT"] = None  # type: ignore[index]
    except TypeError:
        return
    raise AssertionError("PLATFORMS dało się nadpisać — macierz możliwości ma być tylko do odczytu")
