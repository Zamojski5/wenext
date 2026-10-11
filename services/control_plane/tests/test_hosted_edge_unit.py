"""The signed header of the edge, without a database.

WHAT THE ORIGIN TRUSTS is one header, so each way of getting a forged identity past it has a case:
an absent header, a wrong secret, a tampered body, the `none` algorithm and its relatives, an
expired or future window, a header minted to live too long, a missing `jti`, and the same header
used twice inside its minute.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from control_plane.hosted.edge import (
    MAX_LIFETIME_S,
    MIN_SECRET_CHARS,
    EdgeIdentity,
    EdgeRejected,
    EdgeVerifier,
    sign_edge_header,
)

SECRET = "s" * 48
NOW = 1_800_000_000.0


def _verifier(now: float = NOW) -> tuple[EdgeVerifier, dict]:
    clock = {"now": now}
    return EdgeVerifier(SECRET, clock=lambda: clock["now"]), clock


def _sign(**changes) -> str:
    args = {"provider": "github", "subject": "4242", "email": "a@example.com", "login": "anna"}
    args.update(changes)
    args.setdefault("now", NOW)
    return sign_edge_header(args.pop("secret", SECRET), **args)


def _refused(verifier: EdgeVerifier, header: str | None) -> str:
    with pytest.raises(EdgeRejected) as refused:
        verifier.verify(header)
    return refused.value.code


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _forge(header: dict, claims: dict, secret: str = SECRET, *, sign: bool = True) -> str:
    head = _b64(json.dumps(header).encode())
    body = _b64(json.dumps(claims).encode())
    mac = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(mac) if sign else ''}"


def _claims(**changes) -> dict:
    base = {
        "provider": "github",
        "subject": "1",
        "iat": int(NOW),
        "exp": int(NOW) + 60,
        "jti": "j-1",
    }
    return {**base, **changes}


def test_a_genuine_header_yields_the_identity() -> None:
    verifier, _ = _verifier()
    identity = verifier.verify(_sign())
    assert identity == EdgeIdentity(
        provider="github", subject="4242", email="a@example.com", login="anna"
    )


def test_email_and_login_are_optional_and_blank_means_absent() -> None:
    verifier, _ = _verifier()
    identity = verifier.verify(_sign(email=None, login="  "))
    assert identity.email is None and identity.login is None


@pytest.mark.parametrize("header", [None, "", "x", "a.b", "a.b.c.d", " . . "])
def test_an_absent_or_malformed_header_is_refused(header) -> None:
    verifier, _ = _verifier()
    assert _refused(verifier, header) in {"absent", "malformed"}


def test_a_header_signed_with_another_secret_is_refused() -> None:
    verifier, _ = _verifier()
    assert _refused(verifier, _sign(secret="t" * 48)) == "signature"


def test_a_tampered_body_is_refused() -> None:
    verifier, _ = _verifier()
    head, body, sig = _sign().split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["subject"] = "somebody-else"
    forged = f"{head}.{_b64(json.dumps(claims).encode())}.{sig}"
    assert _refused(verifier, forged) == "signature"


@pytest.mark.parametrize("alg", ["none", "None", "HS512", "RS256", "", None])
def test_only_hs256_is_accepted_whatever_the_header_claims(alg) -> None:
    verifier, _ = _verifier()
    header = {"typ": "JWT", "alg": alg}
    assert _refused(verifier, _forge(header, _claims())) == "alg"
    assert _refused(verifier, _forge(header, _claims(), sign=False)) == "alg"


def test_an_unsigned_token_with_the_right_algorithm_name_is_refused() -> None:
    verifier, _ = _verifier()
    assert _refused(verifier, _forge({"alg": "HS256"}, _claims(), sign=False)) in {
        "malformed",
        "signature",
    }


def test_the_window_is_enforced_with_a_small_leeway() -> None:
    verifier, clock = _verifier()
    header = _sign()
    clock["now"] = NOW + 60 + 20  # past exp, inside the leeway
    assert verifier.verify(header).subject == "4242"
    verifier, clock = _verifier()
    clock["now"] = NOW + 60 + 40  # past the leeway
    assert _refused(verifier, header) == "expired"
    verifier, clock = _verifier()
    clock["now"] = NOW - 100  # a header from the future
    assert _refused(verifier, header) == "not-yet"


def test_a_header_minted_to_live_longer_than_the_cap_is_refused() -> None:
    verifier, _ = _verifier()
    assert _refused(verifier, _sign(ttl=MAX_LIFETIME_S + 1)) == "lifetime"
    assert verifier.verify(_sign(ttl=MAX_LIFETIME_S, jti="edge-cap")).subject == "4242"


@pytest.mark.parametrize(
    "changes",
    [
        {"iat": None},
        {"exp": "soon"},
        {"jti": None},
        {"jti": ""},
        {"provider": None},
        {"provider": "  "},
        {"subject": 12},
        {"subject": ""},
        {"subject": "x" * 300},
    ],
)
def test_missing_or_mistyped_claims_are_refused(changes) -> None:
    verifier, _ = _verifier()
    claims = _claims(**changes)
    assert _refused(verifier, _forge({"alg": "HS256"}, claims)) in {"claims", "jti", "identity"}


def test_the_same_header_twice_is_a_replay_and_another_jti_is_not() -> None:
    verifier, _ = _verifier()
    header = _sign(jti="once")
    verifier.verify(header)
    assert _refused(verifier, header) == "replay"
    assert verifier.verify(_sign(jti="other")).subject == "4242"


def test_replay_memory_is_forgotten_after_the_window() -> None:
    verifier, clock = _verifier()
    verifier.verify(_sign(jti="a"))
    verifier.verify(_sign(jti="b"))
    assert len(verifier._seen) == 2
    clock["now"] = NOW + 600
    verifier.verify(_sign(jti="c", now=clock["now"]))
    assert list(verifier._seen) == ["c"]


def test_a_short_secret_is_refused_and_the_environment_decides_whether_the_edge_is_on() -> None:
    with pytest.raises(ValueError, match=str(MIN_SECRET_CHARS)):
        EdgeVerifier("short")
    assert EdgeVerifier.from_env({}) is None
    assert EdgeVerifier.from_env({"WENEXT_EDGE_SECRET": "  "}) is None
    assert EdgeVerifier.from_env({"WENEXT_EDGE_SECRET": SECRET}) is not None


def test_an_oversized_header_is_refused_before_it_is_parsed() -> None:
    verifier, _ = _verifier()
    assert _refused(verifier, "a" * 5000) == "size"


# ---------------------------------------------------------------------------
# The Worker's side of the contract
# ---------------------------------------------------------------------------

_VECTOR = json.loads(
    (Path(__file__).parent / "fixtures" / "edge_vector.json").read_text(encoding="utf-8")
)
_WORKER_VECTOR = (
    Path(__file__).resolve().parents[3]
    / "infra"
    / "cloudflare"
    / "mcp-edge"
    / "test"
    / "fixtures"
    / "edge-vector.json"
)


def test_the_header_the_worker_signs_is_accepted_by_the_origin() -> None:
    """`header` in the vector is what the Worker's `signEdgeHeader` produces for these inputs: its
    own test (`infra/cloudflare/mcp-edge/test/edge-token.test.ts`) asserts exactly that string.
    Here the origin verifies it, so a header the Worker mints that the origin refuses fails a test
    on one side or the other before it fails in production."""
    verifier = EdgeVerifier(_VECTOR["secret"], clock=lambda: float(_VECTOR["now"]) + 5)
    assert verifier.verify(_VECTOR["header"]) == EdgeIdentity(
        provider="github", subject="4242", email="alice@example.com", login="alice"
    )
    minted = sign_edge_header(
        _VECTOR["secret"],
        now=_VECTOR["now"],
        jti=_VECTOR["jti"],
        ttl=_VECTOR["ttl"],
        **_VECTOR["identity"],
    )
    assert minted == _VECTOR["header"]


def test_the_two_copies_of_the_vector_are_the_same_file() -> None:
    if _WORKER_VECTOR.exists():  # absent in a checkout without the infra directory
        assert json.loads(_WORKER_VECTOR.read_text(encoding="utf-8")) == _VECTOR
