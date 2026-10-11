"""Egress policy — private and metadata addresses checked AFTER DNS resolution (L3943).

THE REQUIREMENT IS ABOUT THE ORDER OF TWO OPERATIONS, so the tests are about that order.
A host name check cannot satisfy L3943: `metadata.attacker.example` is a perfectly ordinary
name that resolves to `169.254.169.254`, and `10.0.0.1` can be reached through any name at
all. The address is only knowable after resolution, which is why the resolver is INJECTED —
the checks below run with no network and no DNS server, and one of them proves it by making
`socket.getaddrinfo` explode.

"również przy rebindingu" is the second half of the same sentence and it is a separate
property: one resolution that came back public does not authorize a later connection. Two
tests cover it — re-resolution really re-checks, and connecting to an address the resolver
never returned is refused even when that address is public.

Empty is never a pass. An empty DNS answer, an empty address list on a decision — both are
errors here. `all([])` is `True` and this repository has been bitten by it three times.
"""

from __future__ import annotations

import re
import socket

import pytest
from control_plane.policy.egress import (
    EgressDecision,
    EgressPolicy,
    EgressRejected,
    blocked_reason,
    require_public_addresses,
)
from control_plane.policy.urlpolicy import PolicyNotLoaded, UrlPolicy, UrlRejected

#: A globally routable address, used wherever a test needs the "allowed" direction.
PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700::1111"


class RecordingResolver:
    """Injected DNS. Returns the answers in order, repeating the last one; records every call.

    Recording the calls is what makes the rebinding tests real: without the call log they
    would pass against an implementation that resolves once and caches the verdict forever.
    """

    def __init__(self, *answers: tuple[str, ...]) -> None:
        self.answers = list(answers)
        self.calls: list[str] = []

    def __call__(self, host: str) -> tuple[str, ...]:
        self.calls.append(host)
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]


def _policy(resolver) -> EgressPolicy:
    return EgressPolicy(
        url_policy=UrlPolicy(allowed_origins=("https://api.example.com",)),
        resolver=resolver,
    )


# ---------------------------------------------------------------------------
# The address classification itself
# ---------------------------------------------------------------------------


def test_every_private_and_metadata_address_is_classified_as_blocked() -> None:
    """The list is explicit and every entry is here because `ipaddress` alone gets it wrong
    in a way that was MEASURED on this interpreter (CPython 3.12.4), not remembered:

      * `100.64.0.1`  — CGNAT: `is_private` is **False**, `is_global` is False.
      * `100.100.100.200` — Alibaba metadata: `is_private` **False**, `is_global` False.
      * `64:ff9b::a00:1` — NAT64 wrapping `10.0.0.1`: `is_private` **False** and
        `is_global` **True**. A check built on `is_private` lets this one straight through.
      * `::ffff:100.100.100.200`, `::ffff:100.64.0.1`, `::ffff:224.0.0.1` — see
        `THE_MAPPED_ADDRESSES_WITH_DETECTION_POWER` below.

    THIS TEST HAD ZERO DETECTION POWER FOR THE UNWRAP UNTIL 2026-08-31, AND THE PRODUCTION
    CODE WAS CORRECT THE WHOLE TIME — which is what makes it the most interesting false pass
    in this repository. Measured: deleting the `ipv4_mapped` unwrap from `blocked_reason` left
    all 49 tests of this package green. Both mapped addresses on the list then
    (`::ffff:169.254.169.254`, `::ffff:10.0.0.1`) report `is_private=True` / `is_global=False`
    on this interpreter, so the catch-all at the bottom of `blocked_reason` refuses them
    whether they were unwrapped or not. The test asserted the outcome of a DIFFERENT layer
    than the one it was named after — the same shape as the empty-DNS-answer test two
    functions down, which was rewritten for the same reason.
    """
    #: The addresses that give this test its teeth. Measured with `ip_address(...)`, printed,
    #: not remembered — for each of these the wrapped form reports `is_private=False` AND
    #: `is_global=True`, so with the unwrap removed NOTHING in `blocked_reason` refuses them:
    #: not `METADATA_ADDRESSES` (they are v6 strings there), not `BLOCKED_NETWORKS` (the v4
    #: ranges are skipped on a version mismatch, and no v6 range covers `::ffff:0:0/96`), and
    #: not the `is_global`/multicast catch-all. That is a live metadata and CGNAT bypass, and
    #: `getaddrinfo` really can answer in this form.
    THE_MAPPED_ADDRESSES_WITH_DETECTION_POWER = [
        "::ffff:100.100.100.200",  # Alibaba metadata, mapped: private=False global=True
        "::ffff:100.64.0.1",  # CGNAT, mapped: private=False global=True
        "::ffff:224.0.0.1",  # multicast, mapped: private=False global=True multicast=False
    ]
    blocked = [
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "fd00:ec2::254",  # AWS IPv6 metadata
        "100.100.100.200",  # Alibaba metadata
        "10.0.0.1",
        "172.16.0.5",
        "192.168.1.1",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "100.64.0.1",
        "198.18.0.1",
        "fe80::1",
        # Mapped, but caught by the catch-all even unwrapped — kept, because they document the
        # costume, and removed detection power is what this list is now built around.
        "::ffff:169.254.169.254",
        "::ffff:10.0.0.1",
        *THE_MAPPED_ADDRESSES_WITH_DETECTION_POWER,
        "64:ff9b::a00:1",
        "nie-jest-adresem",
    ]
    # Liczność OSOBNO i PRZED predykatem (D-011): `all([])` jest prawdą.
    assert len(blocked) >= 19, f"lista adresów ma {len(blocked)} pozycji"
    # I liczność samej podlisty, która daje temu testowi moc wykrywania. Bez tej asercji
    # ktoś usuwa trzy pozycje, licznik wyżej zjeżdża do 16 i test znów niczego nie pilnuje.
    assert len(THE_MAPPED_ADDRESSES_WITH_DETECTION_POWER) == 3, (
        f"podlista mocy wykrywania ma {len(THE_MAPPED_ADDRESSES_WITH_DETECTION_POWER)} pozycji"
    )

    allowed_through = [address for address in blocked if blocked_reason(address) is None]
    assert not allowed_through, f"adresy przepuszczone przez klasyfikację: {allowed_through}"


def test_a_globally_routable_address_is_not_blocked() -> None:
    """The other direction. Without it, `blocked_reason` returning a reason for literally
    everything would satisfy the test above and take the whole product offline.
    """
    assert blocked_reason(PUBLIC_V4) is None
    assert blocked_reason(PUBLIC_V6) is None


# ---------------------------------------------------------------------------
# The check happens AFTER resolution
# ---------------------------------------------------------------------------


def test_an_ordinary_host_name_resolving_to_metadata_is_refused() -> None:
    """The name is innocent, the address is not. This is why L3943 says "po DNS resolution"."""
    resolver = RecordingResolver(("169.254.169.254",))

    with pytest.raises(EgressRejected, match="metadata"):
        _policy(resolver).authorize("https://api.example.com/x")

    assert resolver.calls == ["api.example.com"], (
        f"resolver dostał {resolver.calls} — sprawdzenie ma dotyczyć hosta z allowlisty"
    )


def test_a_public_answer_is_authorized_and_pinned_on_the_decision() -> None:
    decision = _policy(RecordingResolver((PUBLIC_V4,))).authorize("https://api.example.com/x")

    assert decision.host == "api.example.com"
    assert decision.origin == "https://api.example.com"
    assert decision.addresses == (PUBLIC_V4,)


def test_a_split_answer_with_one_private_address_is_refused_as_a_whole() -> None:
    """A host answering [public, private] must not pass because SOME address was fine.

    This is the `any()` mirror of the `all([])` trap: "at least one address is routable" is
    the wrong question. The connection picks the address, not the check.
    """
    resolver = RecordingResolver((PUBLIC_V4, "169.254.169.254"))

    # `re.escape`, because the dots in an address are regex metacharacters and a pattern that
    # matches "1692541692 54" instead is not the assertion this test claims to make.
    with pytest.raises(EgressRejected, match=re.escape("169.254.169.254")):
        _policy(resolver).authorize("https://api.example.com/x")


def test_an_empty_dns_answer_is_a_refusal_named_by_the_resolver_layer() -> None:
    """Zero addresses means every "are all of them public?" question answers yes.

    `all(())` is `True`, and that exact shape produced three of this project's false passes,
    so the emptiness is checked separately and first.

    THIS TEST WAS REWRITTEN AFTER IT PASSED WITH THE DEFECT INJECTED. The first version went
    through `EgressPolicy.authorize` and matched only "pust". Deleting the empty-answer check
    from `require_public_addresses` did not fail it: the empty tuple flowed on and
    `EgressDecision.__post_init__` refused the empty pinned set one layer down, with a message
    that also contains "pust". A test satisfied by a layer other than the one it is named
    after is the exact false pass this file exists to prevent — measured here, in this repo,
    which is why the resolver-level function is now called directly and the message has to be
    the DNS one.
    """
    with pytest.raises(EgressRejected, match="DNS dla"):
        require_public_addresses("api.example.com", RecordingResolver(()))

    # And through the whole policy, where the refusal must STILL be the DNS one — if this
    # message came from the decision instead, the first check had been bypassed.
    with pytest.raises(EgressRejected, match="DNS dla"):
        _policy(RecordingResolver(())).authorize("https://api.example.com/x")


def test_a_decision_cannot_be_built_with_no_addresses() -> None:
    """The same rule one layer lower: an empty pinned set would make the connect-time check
    refuse everything, which is fail-closed but still a bug that must not be constructible.
    """
    with pytest.raises(EgressRejected, match="pust"):
        EgressDecision(
            url="https://api.example.com/x",
            origin="https://api.example.com",
            host="api.example.com",
            addresses=(),
        )


# ---------------------------------------------------------------------------
# Rebinding
# ---------------------------------------------------------------------------


def test_a_second_resolution_is_checked_again_so_rebinding_is_caught() -> None:
    """First answer public, second answer metadata — the second call must be refused.

    An implementation that resolves once and remembers "this host is fine" passes every other
    test in this file and fails this one.
    """
    resolver = RecordingResolver((PUBLIC_V4,), ("169.254.169.254",))
    policy = _policy(resolver)

    first = policy.authorize("https://api.example.com/x")
    assert first.addresses == (PUBLIC_V4,)

    with pytest.raises(EgressRejected, match="metadata"):
        policy.authorize("https://api.example.com/y")

    assert len(resolver.calls) == 2, (
        f"resolver został wywołany {len(resolver.calls)} raz(y) — przy jednym wywołaniu drugie "
        "żądanie jechało na zapamiętanym werdykcie, czyli rebinding nie jest sprawdzany"
    )


def test_connecting_to_an_address_the_resolver_never_returned_is_refused() -> None:
    """The connect-time half of L3943. The socket must land on a PINNED address.

    Re-checking the address class is not enough: between the check and the connect, the name
    can start answering with a different — even publicly routable — address, and then the
    thing authorized is not the thing connected to.
    """
    policy = _policy(RecordingResolver((PUBLIC_V4,)))
    decision = policy.authorize("https://api.example.com/x")

    policy.verify_connected_address(decision, PUBLIC_V4)  # the pinned one is fine

    with pytest.raises(EgressRejected, match="rebinding"):
        policy.verify_connected_address(decision, "8.8.8.8")

    with pytest.raises(EgressRejected, match="metadata"):
        policy.verify_connected_address(decision, "169.254.169.254")


# ---------------------------------------------------------------------------
# Composition with the URL policy, and no network anywhere
# ---------------------------------------------------------------------------


def test_egress_without_a_loaded_url_policy_refuses_before_resolving() -> None:
    """No policy means no DNS query either.

    Resolving first would leak the target host to the DNS server of an unauthorized request,
    and it would mean the refusal happened after the side effect rather than before it.
    """
    resolver = RecordingResolver((PUBLIC_V4,))

    with pytest.raises(PolicyNotLoaded):
        EgressPolicy(url_policy=None, resolver=resolver).authorize("https://api.example.com/x")

    assert resolver.calls == [], (
        f"resolver został wywołany ({resolver.calls}) mimo braku polityki — odmowa musi "
        "wyprzedzać zapytanie DNS, nie następować po nim"
    )


def test_a_url_outside_the_allowlist_is_refused_before_resolving() -> None:
    resolver = RecordingResolver((PUBLIC_V4,))

    with pytest.raises(UrlRejected):
        _policy(resolver).authorize("https://evil.example.com/x")

    assert resolver.calls == [], f"resolver ruszył dla nieautoryzowanego originu: {resolver.calls}"


def test_the_whole_check_runs_without_touching_the_network(monkeypatch) -> None:
    """The resolver is injected, so the suite must not need DNS — proved, not asserted.

    `socket.getaddrinfo` is replaced with something that raises. If any code path in the
    egress policy reached for real name resolution, this test would fail with that error
    instead of passing.
    """

    def explode(*_args, **_kwargs):
        raise AssertionError("polityka egress sięgnęła po prawdziwe DNS")

    monkeypatch.setattr(socket, "getaddrinfo", explode)

    decision = _policy(RecordingResolver((PUBLIC_V4,))).authorize("https://api.example.com/x")
    assert decision.addresses == (PUBLIC_V4,)
