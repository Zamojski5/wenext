"""Egress policy — private and metadata addresses refused AFTER DNS resolution (L3943).

THE REQUIREMENT IS ABOUT AN ORDER OF OPERATIONS, so this module is built around that order.
A name-based check cannot satisfy L3943: `metadata.attacker.example` is an unremarkable name
that resolves to `169.254.169.254`, and `10.0.0.1` is reachable through any name at all. The
address only exists after resolution.

THE RESOLVER IS INJECTED. Not for elegance — so the tests need no network, no DNS server and
no container. One of them replaces `socket.getaddrinfo` with a function that raises and then
runs the whole authorization, which is the only way to prove that no code path here quietly
reaches for real name resolution.

"również przy rebindingu" is the second half of L3943 and a genuinely separate property:

  * `authorize()` resolves and checks EVERY time. Nothing caches a per-host verdict, because
    "this host was fine a minute ago" is precisely what a rebinding attack relies on.
  * `verify_connected_address()` is the connect-time half. The socket must land on an address
    that came back in the answer that was checked — even a publicly routable address is
    refused when the resolver never returned it, because otherwise the thing authorized is not
    the thing connected to.

WHY THE ADDRESS LIST IS EXPLICIT AND NOT JUST `ipaddress` PROPERTIES. Measured on CPython
3.12.4, printed from the interpreter, not remembered:

    100.64.0.1        is_private=False  is_global=False   (CGNAT)
    100.100.100.200   is_private=False  is_global=False   (Alibaba metadata)
    64:ff9b::a00:1    is_private=False  is_global=True    (NAT64 wrapping 10.0.0.1)
    224.0.0.1         is_private=False  is_global=True    (multicast)
    ::ffff:10.0.0.1   is_private=True   ipv4_mapped=10.0.0.1

So a filter written as `if ip.is_private` lets CGNAT, both non-AWS metadata services, NAT64
and multicast straight through. The checks below are therefore layered: named metadata
addresses first (for a message that says what was hit), then an explicit range list, then
`is_global`/multicast as the catch-all. Each layer is redundant with the next on purpose.

WHY THE `ipv4_mapped` UNWRAP IS LOAD-BEARING AND NOT A CONVENIENCE. Wrapping a v4 address in
the v6 mapped form does not merely disguise it — for part of the space it flips the very
properties the catch-all reads. Measured on this interpreter, same session as the table above:

    ::ffff:100.100.100.200   is_private=False  is_global=True   (Alibaba metadata!)
    ::ffff:100.64.0.1        is_private=False  is_global=True   (CGNAT)
    ::ffff:224.0.0.1         is_private=False  is_global=True   is_multicast=False
    ::ffff:169.254.169.254   is_private=True   is_global=False  (this one the catch-all holds)

Without the unwrap the first three reach NOTHING that refuses them: `METADATA_ADDRESSES` is
keyed on v4 text, `BLOCKED_NETWORKS` skips v4 ranges on a version mismatch and no v6 range
covers `::ffff:0:0/96`, and the catch-all sees `is_global=True`. Until 2026-08-31 the test
guarding this line listed only the fourth kind of address, so deleting the unwrap kept all 49
tests of this package green — a correct mechanism with a test of zero detection power, which is
why `THE_MAPPED_ADDRESSES_WITH_DETECTION_POWER` in the test file is now named after the job.
Do not "simplify" the unwrap away, and do not delete those three addresses from the test.

EMPTY IS NEVER A PASS. An empty DNS answer makes every "are all addresses public?" question
answer yes, because `all(())` is `True` — the shape behind three of this project's false
passes. Emptiness is checked separately, before the predicate, in both places it can occur.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Protocol

from control_plane.policy.urlpolicy import PolicyRefusal, UrlPolicy, require_url

__all__ = [
    "BLOCKED_NETWORKS",
    "METADATA_ADDRESSES",
    "EgressDecision",
    "EgressPolicy",
    "EgressRejected",
    "Resolver",
    "blocked_reason",
    "require_public_addresses",
]


class EgressRejected(PolicyRefusal):
    """The target resolves somewhere the runner must not reach, or resolves to nothing.

    A subclass of `PolicyRefusal` so that one `except` around an authorization covers the URL
    rules and the address rules together — a caller cannot handle half the policy by accident.
    """


#: Cloud instance-metadata endpoints, named so the refusal message says "metadata" rather than
#: "link-local". Every one of them is caught by the ranges below as well; this layer exists for
#: the operator reading the error, and redundancy in a security check is not waste.
METADATA_ADDRESSES: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS, GCP, Azure, DigitalOcean, OpenStack
        "169.254.170.2",  # ECS task metadata
        "fd00:ec2::254",  # AWS IPv6 metadata
        "100.100.100.200",  # Alibaba Cloud
    }
)

#: Ranges refused with a reason. Written out rather than derived, because a name in this list
#: is greppable and a property lookup is not — and because `ipaddress` genuinely misclassifies
#: three of these (see the module docstring).
BLOCKED_NETWORKS: tuple[tuple[IPv4Network | IPv6Network, str], ...] = tuple(
    (ip_network(cidr), description)
    for cidr, description in (
        ("0.0.0.0/8", "adres nieokreślony / ta sieć"),
        ("10.0.0.0/8", "sieć prywatna"),
        ("100.64.0.0/10", "CGNAT — `is_private` tego NIE łapie"),
        ("127.0.0.0/8", "pętla zwrotna"),
        ("169.254.0.0/16", "link-local (tu mieszka metadata)"),
        ("172.16.0.0/12", "sieć prywatna"),
        ("192.0.0.0/24", "przydział protokołu IETF"),
        ("192.0.2.0/24", "TEST-NET-1"),
        ("192.168.0.0/16", "sieć prywatna"),
        ("198.18.0.0/15", "sieć testów wydajnościowych"),
        ("198.51.100.0/24", "TEST-NET-2"),
        ("203.0.113.0/24", "TEST-NET-3"),
        ("224.0.0.0/4", "multicast"),
        ("240.0.0.0/4", "zarezerwowane"),
        ("255.255.255.255/32", "rozgłoszeniowy"),
        ("::/128", "adres nieokreślony"),
        ("::1/128", "pętla zwrotna"),
        ("64:ff9b::/96", "prefiks NAT64 — opakowuje IPv4 i `is_global` mówi na to True"),
        ("64:ff9b:1::/48", "lokalny prefiks NAT64"),
        ("100::/64", "discard-only"),
        ("2001::/32", "Teredo"),
        ("2001:db8::/32", "dokumentacyjny"),
        ("2002::/16", "6to4 — opakowuje IPv4"),
        ("fc00::/7", "unique-local (tu mieszka metadata AWS IPv6)"),
        ("fe80::/10", "link-local"),
        ("ff00::/8", "multicast"),
    )
)


class Resolver(Protocol):
    """Name resolution, injected.

    Returns EVERY address the name answers with — not the first one. A resolver that returns
    only one address would hide exactly the case this module has to refuse: an answer mixing a
    routable address with a private one.
    """

    def __call__(self, host: str) -> Sequence[str]: ...


def blocked_reason(address: str) -> str | None:
    """The reason this address must not be reached, or None when it may be.

    Returns a REASON rather than a bool so the refusal can say what was hit. An address that
    does not parse is refused too: an unparseable address is not a permissive default, it is
    something nobody can classify.
    """
    try:
        ip = ip_address(address)
    except ValueError:
        return f"{address!r} nie jest adresem IP — nie da się go zaklasyfikować, więc odmowa"

    # An IPv4-mapped IPv6 address has to be unwrapped BEFORE the v4 rules can see it:
    # `::ffff:169.254.169.254` is metadata wearing an IPv6 costume.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    if str(ip) in METADATA_ADDRESSES:
        return f"{ip} to endpoint metadata instancji — nigdy nie jest celem egress"

    for network, description in BLOCKED_NETWORKS:
        if ip.version == network.version and ip in network:
            return f"{ip} należy do {network} ({description})"

    # Catch-all, deliberately after the named layers: whatever the lists above missed, this
    # refuses anyway. `is_multicast` is asked separately because 224.0.0.1 reports
    # `is_global=True` on this interpreter.
    if not ip.is_global or ip.is_multicast:
        return f"{ip} nie jest adresem trasowalnym globalnie"

    return None


def require_public_addresses(host: str, resolver: Resolver) -> tuple[str, ...]:
    """Resolves the host and refuses unless EVERY answer is publicly routable.

    Every, not any. "At least one address was fine" is the wrong question, because the
    connection picks the address and the check does not: a host answering
    [93.184.216.34, 169.254.169.254] hands the socket a coin flip.

    The empty answer is checked FIRST and SEPARATELY. With no addresses, the loop below has
    nothing to refuse and the function would return an empty pinned set — a pass built out of
    `all(())`, which is the exact bug that produced three false passes in this repository.
    """
    answer = tuple(resolver(host))
    if not answer:
        raise EgressRejected(
            f"DNS dla {host!r} zwrócił pustą odpowiedź. To jest ODMOWA, nie zaliczenie: przy "
            "zerze adresów każde pytanie 'czy wszystkie są publiczne?' odpowiada tak"
        )

    blocked = [(address, blocked_reason(address)) for address in answer]
    refused = [(address, reason) for address, reason in blocked if reason is not None]
    if refused:
        raise EgressRejected(
            f"host {host!r} rozwiązuje się na adresy zakazane (L3943): "
            + "; ".join(f"{address} — {reason}" for address, reason in refused)
            + f". Cała odpowiedź DNS: {list(answer)}"
        )
    return answer


@dataclass(frozen=True)
class EgressDecision:
    """A resolution that passed, pinned.

    `addresses` is the whole point: the caller connects to one of THESE, and
    `verify_connected_address` refuses anything else. Handing back only "yes, allowed" would
    leave the window between the check and the connect open, which is where rebinding lives.
    """

    url: str
    origin: str
    host: str
    addresses: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.addresses:
            raise EgressRejected(
                f"decyzja egress dla {self.host!r} ma pustą listę adresów. Fail-closed by to "
                "przeżyło (żaden adres nie przejdzie przypinania), ale taki obiekt nie ma "
                "prawa dać się zbudować — pusty zbiór jest tu błędem, nie stanem"
            )


class EgressPolicy:
    """Composes the URL policy with the address check, in that order.

    The order is load-bearing. `authorize()` refuses an unauthorized URL BEFORE resolving it:
    resolving first would leak the target to a DNS server on behalf of a request that was
    never allowed, and would put the refusal after the side effect instead of before it.
    """

    def __init__(self, url_policy: UrlPolicy | None, resolver: Resolver) -> None:
        #: Typed as optional and NOT defaulted. Wiring reads it from configuration, where
        #: "absent" is a real state — and `require_url` turns that state into a refusal.
        self.url_policy = url_policy
        #: No default resolver on purpose. A default that reached `socket.getaddrinfo` would
        #: make every test that forgot to pass one silently depend on the network.
        self.resolver = resolver

    def authorize(self, url: str) -> EgressDecision:
        """Full authorization: origin, scheme, then the resolved addresses. Raises or returns.

        Resolves on EVERY call. No per-host memo, no TTL cache — a remembered verdict is a
        rebinding hole, and it is the one this method would be most tempting to add.
        """
        authorized = require_url(url, self.url_policy)
        addresses = require_public_addresses(authorized.host, self.resolver)
        return EgressDecision(
            url=authorized.url,
            origin=authorized.origin,
            host=authorized.host,
            addresses=addresses,
        )

    def verify_connected_address(self, decision: EgressDecision, address: str) -> None:
        """The connect-time half of L3943 — call this with the address the socket really used.

        Two checks, both needed. The class check catches an address that turned private
        between resolution and connect; the pinning check catches a swap to a DIFFERENT
        routable address, which the class check cannot see and which is enough to reach a
        service that trusts the host name.
        """
        reason = blocked_reason(address)
        if reason is not None:
            raise EgressRejected(
                f"połączenie do {address!r} dla {decision.host!r} odrzucone: {reason}"
            )

        pinned = {_normalized(candidate) for candidate in decision.addresses}
        if _normalized(address) not in pinned:
            raise EgressRejected(
                f"adres {address!r} nie był w sprawdzonej odpowiedzi DNS dla "
                f"{decision.host!r} ({sorted(pinned)}) — to jest DNS rebinding. Publiczny "
                "adres też się nie kwalifikuje: autoryzowano inny cel, niż zostałby użyty"
            )


def _normalized(address: str) -> str:
    """Textual form comparison is not safe for IPv6 — `::1` and `0:0:0:0:0:0:0:1` are one
    address written two ways. Everything is compared through `ip_address` instead.
    """
    try:
        return str(ip_address(address))
    except ValueError:
        return address
