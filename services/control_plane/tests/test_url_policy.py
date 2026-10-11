"""URL policy of the control plane — section 19.4 of the document (L3929-3946), D-019.

WHAT IS NEW HERE AND WHY IT IS NOT IN THE SIDECAR.

`services/mcp_sidecar/mcp_sidecar/origins.py` already canonicalizes an origin, compares it
exactly and refuses the five schemes of L3944. That module is REUSED, not copied — one of the
tests below asserts object identity, so a future copy-paste fails here instead of drifting
apart silently.

Two properties the sidecar module measurably does NOT have, both read out of the file rather
than assumed:

  * `DEFAULT_PORTS = {"http": 80, "https": 443}` accepts `http` for ANY host, and
    `FIXTURE_SITE_ORIGIN` is a constant that no branch of the logic ever consults. So "tylko
    HTTPS poza jawnie testowym fixture" (L3936) is not enforced anywhere: `http://anything`
    canonicalizes happily and the fixture is in no way distinguished from a production host.
  * An empty allowlist reaches `is_allowed` as an empty set, which returns False — correct by
    accident. `UrlPolicy` makes it an error at construction, because a policy object that
    exists while authorizing nothing is a configuration bug pretending to be a working state,
    and the next person "fixes" it by widening the list.

`all([])` is `True` and has produced three false passes in this repository, so every test below
that loops over a set asserts the SIZE of that set first.

The registry tests at the bottom are tests OF THE REGISTER, not of enforcement. F1 has no
navigation, no proxy and no browser, so a test named "redirect is blocked" could only prove
that a function returned False — the exact overclaim `contracts/tests/test_invariants.py`
committed near line 259, where a test named after accounts never touches an account.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from control_plane.policy.registry import (
    ENFORCED,
    NOT_ENFORCED,
    SECTION_19_4,
    EnforcementStatus,
)
from control_plane.policy.urlpolicy import (
    PolicyNotLoaded,
    PolicyRefusal,
    UrlPolicy,
    UrlRejected,
    require_url,
)

#: The repository root, four levels up from this file:
#: <root>/services/control_plane/tests/test_url_policy.py
ROOT = Path(__file__).resolve().parents[3]
ROADMAP = ROOT / "ROADMAP_CODEX_CDP_COMMUNITY_OS.md"

#: Section 19.4 bullets, by document line. The registry is confronted with THESE lines.
SECTION_19_4_LINES = range(3933, 3945)


def _policy() -> UrlPolicy:
    """A policy with two HTTPS origins and no plaintext fixture. The default posture."""
    return UrlPolicy(allowed_origins=("https://api.example.com", "https://bücher.example"))


# ---------------------------------------------------------------------------
# Reuse, not a copy
# ---------------------------------------------------------------------------


def test_the_canonicalizer_is_the_sidecar_object_itself_not_a_second_copy() -> None:
    """L3933 has exactly one implementation in this repository.

    `mcp_sidecar` is imported inside the function, not at module level, because in a
    development checkout it is `control_plane.policy.urlpolicy` that puts the sidecar on
    `sys.path` — see the import block at the top of that module and the note about it in
    `uwagi_dla_orkiestratora`. The module-level imports in this file have already run that
    bootstrap by the time any test body executes, so this is about keeping the dependency
    visible where it matters rather than about ordering.
    """
    from control_plane.policy import urlpolicy
    from mcp_sidecar import origins

    assert urlpolicy.canonical_origin is origins.canonical_origin, (
        "control plane ma WŁASNĄ canonical_origin. Dwie kanonikalizacje to dwie definicje "
        "originu, które rozjadą się przy pierwszej poprawce w jednej z nich"
    )
    assert urlpolicy.FORBIDDEN_SCHEMES is origins.FORBIDDEN_SCHEMES, (
        "lista zakazanych schematów jest skopiowana zamiast zaimportowana"
    )


# ---------------------------------------------------------------------------
# Empty allowlist / unloaded policy — refusal, never a silent pass
# ---------------------------------------------------------------------------


def test_an_empty_allowlist_is_a_configuration_error_not_an_empty_pass() -> None:
    """The pattern this repository already uses: `TaskScopedCapability.__post_init__`
    refuses an empty `allowed_origins` for the same reason — an empty list read as "nothing
    matches" is one refactor away from being read as "no restriction".
    """
    with pytest.raises(PolicyNotLoaded, match="pusta"):
        UrlPolicy(allowed_origins=())


def test_no_loaded_policy_means_refusal() -> None:
    """`require_url(url, None)` must raise, not return the URL and not return None.

    A helper that returns `None` for "no policy" is read by the caller as falsy-but-harmless.
    Here the absence of a policy is the loudest possible state.
    """
    with pytest.raises(PolicyNotLoaded, match="polityka"):
        require_url("https://api.example.com/x", None)


def test_an_allowlist_entry_that_is_not_an_origin_stops_the_policy_from_loading() -> None:
    """Garbage in the allowlist must not become a policy that quietly allows less than
    intended. It fails at construction, where somebody is still looking.
    """
    with pytest.raises(UrlRejected):
        UrlPolicy(allowed_origins=("https://api.example.com", "nie-jest-urlem"))


# ---------------------------------------------------------------------------
# L3936 — HTTPS only, outside an EXPLICITLY marked fixture
# ---------------------------------------------------------------------------


def test_a_plaintext_origin_must_be_marked_as_a_fixture_before_it_can_be_configured() -> None:
    """This is the whole delta against the sidecar, at the strongest point: configuration.

    Putting `http://` on the allowlist without naming it a fixture is refused, so plaintext
    cannot enter the system by someone adding one line to a list of hosts.
    """
    with pytest.raises(UrlRejected, match="HTTPS"):
        UrlPolicy(allowed_origins=("http://fixture-site:8000",))

    marked = UrlPolicy(
        allowed_origins=("http://fixture-site:8000",),
        plaintext_fixture_origins=("http://fixture-site:8000",),
    )
    assert marked.require_url("http://fixture-site:8000/thread/1").scheme == "http"


def test_the_sidecar_fixture_origin_carries_no_privilege_by_itself() -> None:
    """`FIXTURE_SITE_ORIGIN` is `http://fixture-site:8000` and the sidecar never consults it.

    Here it is an ordinary string: it buys nothing until it is listed as a fixture. The
    constant is imported rather than retyped so that changing it there breaks this test.
    """
    from mcp_sidecar.origins import FIXTURE_SITE_ORIGIN

    with pytest.raises(UrlRejected, match="HTTPS"):
        UrlPolicy(allowed_origins=(FIXTURE_SITE_ORIGIN,))


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/x",
        "http://api.example.com:80/x",
        "http://bücher.example/",
    ],
)
def test_plaintext_is_refused_for_a_host_that_is_not_the_marked_fixture(url: str) -> None:
    with pytest.raises(UrlRejected):
        _policy().require_url(url)


def test_the_same_host_over_https_is_authorized() -> None:
    """The control in the other direction. Without it, a policy that refuses literally
    everything would satisfy every test above — the shape of false pass this repository has
    already produced eight times.
    """
    authorized = _policy().require_url("https://api.example.com/thread/1?x=2")

    assert authorized.origin == "https://api.example.com"
    assert authorized.scheme == "https"
    assert authorized.port == 443


# ---------------------------------------------------------------------------
# L3934 / L3935 — exact origin authorizes; eTLD+1 does not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x",  # same eTLD+1 as api.example.com
        "https://api.example.com.evil.net/x",  # suffix trick
        "https://api.example.com:8443/x",  # different port is a different origin
    ],
)
def test_a_neighbour_of_an_allowed_host_is_not_authorized(url: str) -> None:
    with pytest.raises(UrlRejected, match="allowli"):
        _policy().require_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://93.184.216.34/x",
        "https://[2606:4700::1111]/x",
        "https://[::1]/x",
    ],
)
def test_an_ip_literal_host_is_refused_because_it_has_no_dns_step(url: str) -> None:
    """A documented narrowing of F1, with two measured reasons — see the module docstring of
    `urlpolicy`.

    An IP literal skips name resolution, so the L3943 check has nothing to check; and the
    reused `canonical_origin` renders an IPv6 literal without its brackets, which makes host
    and port inseparable (`https://[2606:4700::1111]:8443` → `https://2606:4700::1111:8443`).
    Fixing that inside the sidecar is out of scope — its F0 suite has to stay green — so the
    control plane refuses instead of guessing.
    """
    # The literal is deliberately NOT on the allowlist — `UrlPolicy` refuses to load one
    # (the test below), so the only way to reach the request-time rule is a policy without it.
    with pytest.raises(UrlRejected, match="adres"):
        _policy().require_url(url)


def test_an_ip_literal_cannot_be_smuggled_in_through_the_allowlist_either() -> None:
    """The same rule at construction time. Otherwise the refusal above would be one
    `plaintext_fixture_origins`-shaped workaround away from being bypassed.
    """
    with pytest.raises(UrlRejected, match="adres"):
        UrlPolicy(allowed_origins=("https://10.0.0.1",))


def test_the_authorized_url_carries_the_normalized_host_that_egress_must_resolve() -> None:
    """The host handed to DNS has to be the SAME string the allowlist matched.

    Resolving the raw, unnormalized host would mean authorizing one name and connecting to
    another — the check would be measuring a different thing than the connection uses.
    """
    authorized = _policy().require_url("https://BÜCHER.example/katalog")

    assert authorized.host == "xn--bcher-kva.example"
    assert authorized.origin == "https://xn--bcher-kva.example"


# ---------------------------------------------------------------------------
# One name authorized, another name connected to — the class `AuthorizedUrl`
# claims to close (L3934, and the docstring of that dataclass)
# ---------------------------------------------------------------------------
#
# `AuthorizedUrl.url` is handed on VERBATIM and, in E1.F2, it is handed to a browser. Every
# browser, `fetch` in JS and Chromium over CDP parse a URL by the WHATWG rules; `urllib` does
# not. Where the two disagree about the host, `UrlPolicy` authorizes one name and the consumer
# connects to another — which is the exact sentence in the `AuthorizedUrl` docstring, and it
# was not true when that docstring was written.
#
# The tests below do NOT assert a remembered fact about WHATWG. They construct the two readings
# and assert that they DISAGREE before requiring the refusal — so a string that stops being
# ambiguous cannot silently turn these into tests of nothing.


def _urllib_host(url: str) -> str | None:
    """The host `urllib` sees — the one `canonical_origin` builds the allowlist match from."""
    return urlsplit(url.strip()).hostname


def _whatwg_host(url: str) -> str | None:
    """The host a WHATWG parser sees, modelled by the single rule that matters here.

    For a special scheme (`http`, `https`) WHATWG treats `\\` exactly like `/`, so a backslash
    ENDS the authority. Folding the string and re-parsing reproduces that with the stdlib and
    without a browser: the point of these tests is the DISAGREEMENT between two readings of one
    string, and the fold is enough to expose it.
    """
    return urlsplit(url.strip().replace("\\", "/")).hostname


#: Measured, printed from the interpreter. Each entry is one string that two conforming parsers
#: read as two DIFFERENT hosts. The first one is the live bypass: `urllib` puts `evil.com\` in
#: the userinfo and reports the allowlisted host, while a browser stops the authority at the
#: backslash and goes to `evil.com`.
AMBIGUOUS_AUTHORITIES = [
    "https://evil.com\\@api.example.com/x",  # urllib: api.example.com  WHATWG: evil.com
    "https://api.example.com:443\\@evil.com/x",  # urllib: evil.com  WHATWG: api.example.com
    "https://api.example.com\\evil.com/x",  # urllib: api.example.com\evil.com  WHATWG: api...
]


def test_the_two_parsers_really_disagree_about_these_strings() -> None:
    """The premise of the next test, asserted instead of assumed.

    Without this, `AMBIGUOUS_AUTHORITIES` could drift into a list of ordinary URLs and the
    refusal test below would keep passing while guarding nothing — the shape that left the
    IPv4-mapped unwrap in `egress.py` undefended by 36 tests.
    """
    assert len(AMBIGUOUS_AUTHORITIES) == 3, f"{len(AMBIGUOUS_AUTHORITIES)} pozycji"

    agreeing = [url for url in AMBIGUOUS_AUTHORITIES if _urllib_host(url) == _whatwg_host(url)]
    assert not agreeing, (
        f"te stringi nie są już dwuznaczne dla dwóch parserów: {agreeing}. Test niżej "
        "przestał czegokolwiek pilnować — dobierz nowe przypadki albo usuń oba testy"
    )


@pytest.mark.parametrize("url", AMBIGUOUS_AUTHORITIES)
def test_a_backslash_in_the_authority_is_refused_by_name(url: str) -> None:
    """A refusal is not enough — the REASON has to be the backslash.

    Two of these three were already refused before the fix, but for unrelated reasons (the
    host `evil.com` is not on the allowlist, the host `api.example.com\\evil.com` is not
    either). A test asserting only `pytest.raises(UrlRejected)` would therefore have passed on
    two thirds of its cases with the mechanism absent. Matching the reason is what gives all
    three cases detection power.
    """
    with pytest.raises(UrlRejected, match="ukośnik"):
        _policy().require_url(url)


def test_an_allowlist_entry_with_a_backslash_cannot_be_configured_either() -> None:
    """The same rule at construction time, like the IP-literal rule above.

    An operator who pastes such a string into configuration gets a loud error while somebody
    is still looking, instead of an entry whose canonical form is not what they read.
    """
    with pytest.raises(UrlRejected, match="ukośnik"):
        UrlPolicy(allowed_origins=("https://evil.com\\@api.example.com",))


#: Also measured: `urllib.parse` DELETES these three characters from the URL before parsing
#: (`_UNSAFE_URL_BYTES_TO_REMOVE == ['\t', '\r', '\n']`), and so does every WHATWG parser. The
#: hosts therefore AGREE — this is not a host-confusion bypass and is deliberately not claimed
#: to be one. What breaks is the other half of the same promise: `AuthorizedUrl.url` is passed
#: on verbatim, so the literal string the next layer receives is not the string any parser
#: read, and a URL carrying a CR or an LF is a request-splitting and log-splitting payload for
#: whatever writes it into a header or a log line.
URLS_WITH_CHARACTERS_EVERY_PARSER_DELETES = [
    "https://api.example.\tcom/x",  # canonicalizes to the ALLOWED origin today
    "https://api.example.com/x?a=1\t2",
    "https://api.example.com/x\ny",
    "https://api.example.com/x\ry",
    "https://api.example.com\r\nHost: evil.com/x",
]


@pytest.mark.parametrize("url", URLS_WITH_CHARACTERS_EVERY_PARSER_DELETES)
def test_a_url_carrying_a_character_parsers_delete_is_refused(url: str) -> None:
    """Refused because the string handed on verbatim is not the string that was checked.

    `https://api.example.\\tcom/x` is the case with real detection power: before the fix it
    canonicalized to `https://api.example.com`, passed the allowlist, and `AuthorizedUrl.url`
    then carried a tab into whatever came next.
    """
    with pytest.raises(UrlRejected, match="znak"):
        _policy().require_url(url)


@pytest.mark.parametrize(
    "url",
    [
        # U+FF0F FULLWIDTH SOLIDUS, written as an escape and not pasted: ruff RUF001 flags
        # it as a confusable, and confusable is exactly what this test datum is for.
        "https://evil.com\uff0f@api.example.com/x",
        "https://[api.example.com]/x",  # brackets around a name, not an IPv6 literal
    ],
)
def test_a_url_urllib_cannot_parse_at_all_is_a_policy_refusal_not_a_bare_valueerror(
    url: str,
) -> None:
    """Measured: `urlsplit` raises plain `ValueError` on both of these — from `_checknetloc`
    for the first, from the IPv6 parse for the second.

    `canonical_origin` does not catch it, and `require_url` catches only `OriginRejected`, so
    before the fix a `ValueError` escaped the policy. Everything in this design is written so
    that ONE `except PolicyRefusal` around an authorization is fail-closed; an exception
    outside that hierarchy turns a refusal into an unhandled 500 and, worse, teaches the next
    caller to write a bare `except Exception`.
    """
    with pytest.raises(PolicyRefusal):
        _policy().require_url(url)


def test_an_ordinary_url_is_still_authorized_and_survives_a_whatwg_reparse() -> None:
    """The other direction, and the invariant `AuthorizedUrl` actually promises.

    Without this test, refusing literally every URL would satisfy every test above — the false
    pass this repository has produced eight times. The second half is the real assertion: the
    string handed back is re-read by the OTHER parser and still yields the authorized host.

    Note what is deliberately NOT refused: a backslash AFTER the authority. WHATWG turns it
    into `/`, so the path differs between parsers while the ORIGIN — the thing F1 authorizes —
    is identical. Narrowing beyond the authorization boundary would break real URLs to close
    nothing.
    """
    for url in ("https://api.example.com/thread/1?x=2", "https://api.example.com/a\\b"):
        authorized = _policy().require_url(url)
        assert authorized.url == url, "AuthorizedUrl.url ma być tym, co dostał wywołujący"
        assert _whatwg_host(authorized.url) == authorized.host == "api.example.com", (
            f"{url!r}: parser WHATWG czyta z autoryzowanego stringa innego hosta niż "
            f"{authorized.host!r} — to jest dokładnie ta klasa błędu, którą docstring "
            "AuthorizedUrl deklaruje jako zamkniętą"
        )


# ---------------------------------------------------------------------------
# L3944 — schemes that must never be authorized
# ---------------------------------------------------------------------------


def test_every_scheme_named_in_l3944_is_refused() -> None:
    urls = [
        "file:///etc/passwd",
        "data:text/html,<script>1</script>",
        "javascript:alert(1)",
        "chrome://settings",
        "devtools://devtools/bundled/inspector.html",
    ]
    # Liczność OSOBNO i PRZED predykatem: `all([])` jest prawdą (D-011).
    assert len(urls) == 5, "L3944 wymienia pięć schematów — ta lista musi mieć pięć pozycji"

    policy = _policy()
    passed: list[str] = []
    for url in urls:
        try:
            policy.require_url(url)
        except UrlRejected:
            continue
        passed.append(url)

    assert not passed, f"schematy z L3944 przeszły przez politykę: {passed}"


# ---------------------------------------------------------------------------
# Register of section 19.4 (D-019) — a register, not an enforcement claim
# ---------------------------------------------------------------------------


def _document_bullets() -> dict[str, str]:
    """Section 19.4 bullets read from the document, keyed by their line anchor."""
    assert ROADMAP.exists(), (
        f"nie ma {ROADMAP} — rejestr 19.4 nie ma z czym być skonfrontowany. To jest PORAŻKA, "
        "nie pominięcie: dokument jest w repozytorium, więc jego brak to zły katalog testu"
    )
    lines = ROADMAP.read_text(encoding="utf-8").splitlines()
    bullets: dict[str, str] = {}
    for number in SECTION_19_4_LINES:
        text = lines[number - 1]
        assert text.startswith("- "), (
            f"L{number} nie jest punktem listy, tylko {text!r} — kotwice 19.4 przesunęły się "
            "w dokumencie i rejestr wskazuje na inne zdania niż opisuje"
        )
        bullets[f"L{number}"] = text.removeprefix("- ")
    return bullets


def test_every_bullet_of_section_19_4_is_present_in_the_register() -> None:
    """D-019: "żadna nie może być nieobecna. Milczenie o pozycji jest tu tym samym, czym
    kontrola bez pomiaru."
    """
    bullets = _document_bullets()
    assert len(bullets) == 12, f"sekcja 19.4 ma {len(bullets)} punktów, oczekiwano 12"

    in_register = {item.anchor: item.requirement for item in SECTION_19_4}
    assert in_register == bullets, (
        "rejestr rozjechał się z sekcją 19.4 dokumentu — porównaj kotwice i treść punktów"
    )


def test_the_register_splits_into_enforced_and_not_enforced_with_nothing_left_over() -> None:
    assert len(SECTION_19_4) == 12
    assert len(ENFORCED) + len(NOT_ENFORCED) == len(SECTION_19_4), (
        "pozycja bez statusu wypadła z obu list — a to jest dokładnie ten stan, "
        "którego D-019 zakazuje"
    )
    assert len(NOT_ENFORCED) == 6, (
        f"pozycji NIE_EGZEKWOWANE jest {len(NOT_ENFORCED)}, ma być 6 (L3937-3942)"
    )


def test_the_six_unenforced_items_are_the_browser_ones_and_name_a_later_phase() -> None:
    """No test here pretends any of these six is blocked. F1 has no surface on which they
    could be blocked; the register says so and names the phase where they get one.
    """
    assert len(NOT_ENFORCED) == 6

    assert [item.anchor for item in NOT_ENFORCED] == [f"L{n}" for n in range(3937, 3943)]
    for item in NOT_ENFORCED:
        assert item.status is EnforcementStatus.NOT_ENFORCED
        assert item.phase == "E1.F2", f"{item.anchor}: faza docelowa {item.phase!r}"
        assert item.enforced_by == "", (
            f"{item.anchor}: pozycja niewyegzekwowana wskazuje na {item.enforced_by!r} — "
            "wskazanie punktu egzekwowania dla czegoś, czego się nie egzekwuje, to fałszywe "
            "zaliczenie w dokumentacji"
        )
        assert item.rationale, f"{item.anchor}: brak uzasadnienia"


def test_every_enforced_item_names_a_callable_that_really_exists() -> None:
    """The register may not point at a phantom.

    Two packages in this repository exported 27 names that had never existed; D-005 cited one
    of them as proof that a requirement was met. A register naming a non-existent enforcement
    point would be the same lie, one document further on.
    """
    assert len(ENFORCED) == 6, f"pozycji EGZEKWOWANE jest {len(ENFORCED)}, oczekiwano 6"

    missing: list[str] = []
    for item in ENFORCED:
        assert item.phase == "E1.F1", f"{item.anchor}: {item.phase!r}"
        assert item.enforced_by, f"{item.anchor}: brak punktu egzekwowania"
        # `enforced_by` is relative to `control_plane.policy`, e.g. "urlpolicy.UrlPolicy.require_url".
        module_name, _, attribute_path = item.enforced_by.partition(".")
        target = importlib.import_module(f"control_plane.policy.{module_name}")
        for part in attribute_path.split("."):
            target = getattr(target, part, None)
            if target is None:
                missing.append(f"{item.anchor}: {item.enforced_by}")
                break
        else:
            if not callable(target):
                missing.append(f"{item.anchor}: {item.enforced_by} nie jest wywoływalne")

    assert not missing, f"rejestr wskazuje na nieistniejący kod: {missing}"


def test_an_item_narrower_than_the_document_sentence_says_so_out_loud() -> None:
    """L3943 names an egress proxy and L3944 names top-level navigation. F1 has neither.

    The mechanism is enforced at the only surface F1 has — authorization — and the register
    has to carry that limitation at the verdict, not in a footnote (docs/KONWENCJE.md).
    """
    narrower = {
        item.anchor: item.caveat for item in SECTION_19_4 if item.anchor in {"L3943", "L3944"}
    }
    assert len(narrower) == 2, f"oczekiwano dwóch pozycji z zastrzeżeniem, jest {len(narrower)}"

    silent = [anchor for anchor, caveat in narrower.items() if not caveat]
    assert not silent, (
        f"{silent} twierdzi EGZEKWOWANE bez zastrzeżenia, choć w F1 nie ma ani proxy egress, "
        "ani nawigacji — nazwa szersza niż treść"
    )
