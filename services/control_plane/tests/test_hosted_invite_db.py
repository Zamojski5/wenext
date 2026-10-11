"""The administrator's command line (`python -m control_plane.hosted.invite`) against a database.

WHAT IT MUST GET RIGHT, because the person it invites cannot ask us what went wrong:

  * the two commands it prints are the ones a person pastes, with the token in them, and the
    token is NOT in the database (only its SHA-256) and NOT in `list`;
  * one invitation is one self-contained project: organization, workspace, project, a placeholder
    account per platform that the draft path needs, and an owner whose password nobody has;
  * a second invitation for the same address is refused rather than half-created;
  * revoking is exact: a short or ambiguous prefix is refused, never "close enough".
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from control_plane.hosted import invite
from control_plane.hosted.invite import build_parser, install_commands
from control_plane.hosted.provision import PLACEHOLDER_ACCOUNTS
from control_plane.hosted.tokens import TOKEN_PREFIX, looks_like_a_token

pytestmark = [pytest.mark.db, pytest.mark.p2]


def _email() -> str:
    return f"p{int(datetime.now(UTC).timestamp() * 1e6)}@example.com"


async def _run(pool, *argv: str) -> int:
    return await invite._dispatch(pool, build_parser().parse_args(list(argv)))


async def test_create_prints_the_two_commands_and_stores_only_the_hash(
    pool, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("WENEXT_PUBLIC_DOMAIN", "wenext.example.test")
    email = _email()

    code = await _run(pool, "create", "--email", email, "--name", "Acme Tracker")

    assert code == 0
    out = capsys.readouterr().out
    commands = [line.strip() for line in out.splitlines() if "mcp add" in line]
    assert len(commands) == 2
    codex, claude = commands
    assert codex.startswith("codex mcp add wenext --url https://wenext.example.test/mcp/wnx_")
    assert claude.startswith(
        "claude mcp add --transport http wenext https://wenext.example.test/mcp/wnx_"
    )
    token = codex.rsplit("/mcp/", 1)[1]
    assert claude.endswith(token)
    assert looks_like_a_token(token) and token.startswith(TOKEN_PREFIX)
    assert (codex, claude) == install_commands("https://wenext.example.test", token)

    # The database holds the digest and not the token, anywhere.
    row = await pool.fetchrow(
        "SELECT * FROM hosted_tokens WHERE token_sha256 = $1",
        hashlib.sha256(token.encode()).hexdigest(),
    )
    assert row is not None and row["label"] == email and row["revoked_at"] is None
    assert not await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM hosted_tokens WHERE row(hosted_tokens.*)::text LIKE $1)",
        f"%{token}%",
    )

    # One self-contained project, in review mode, with its placeholder account per platform.
    project = await pool.fetchrow(
        "SELECT id, organization_id, workspace_id, name, mode FROM projects WHERE id = $1",
        row["project_id"],
    )
    assert project["name"] == "Acme Tracker" and project["mode"] == "REVIEW"
    accounts = await pool.fetch(
        "SELECT a.platform, a.external_account_id, a.state FROM project_account_assignments x"
        " JOIN social_accounts a ON a.id = x.social_account_id"
        " WHERE x.project_id = $1 AND x.unassigned_at IS NULL ORDER BY a.platform",
        project["id"],
    )
    assert [(a["platform"], a["external_account_id"]) for a in accounts] == [
        ("REDDIT", PLACEHOLDER_ACCOUNTS["REDDIT"]),
        ("X", PLACEHOLDER_ACCOUNTS["X"]),
    ]
    assert {a["state"] for a in accounts} == {"PROVISIONED"}, "nobody is signed in to a placeholder"


async def test_the_owner_exists_but_nobody_can_sign_in_as_them(pool, monkeypatch, capsys) -> None:
    from control_plane.identity.store import IdentityStore

    monkeypatch.setenv("WENEXT_PUBLIC_DOMAIN", "wenext.example.test")
    email = _email()
    assert await _run(pool, "create", "--email", email, "--name", "Acme") == 0
    capsys.readouterr()

    user = await pool.fetchrow("SELECT id, credential_hash FROM users WHERE email = $1", email)
    assert user["credential_hash"].startswith("$argon2id$")
    membership = await pool.fetchrow(
        "SELECT role, revoked_at FROM memberships WHERE user_id = $1", user["id"]
    )
    assert membership["role"] == "OWNER" and membership["revoked_at"] is None
    store = IdentityStore(pool)
    for guess in ("", "password", "hosted", email, "wnx_" + "A" * 43):
        assert await store.verify_login(email=email, password=guess) is None


async def test_a_second_invitation_for_the_same_address_is_refused_and_leaves_no_token(
    pool, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("WENEXT_PUBLIC_DOMAIN", "wenext.example.test")
    email = _email()
    assert await _run(pool, "create", "--email", email, "--name", "One") == 0
    capsys.readouterr()

    code = await _run(pool, "create", "--email", email, "--name", "Two")

    assert code == invite.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "mcp add" not in captured.out, "a refused invitation prints no command"
    assert await pool.fetchval("SELECT count(*) FROM hosted_tokens WHERE label = $1", email) == 1


async def test_without_an_address_nothing_is_created(pool, monkeypatch, capsys) -> None:
    monkeypatch.delenv("WENEXT_PUBLIC_DOMAIN", raising=False)
    email = _email()

    code = await _run(pool, "create", "--email", email, "--name", "Nowhere")

    assert code == invite.EXIT_BAD_CONFIG
    assert not await pool.fetchval("SELECT EXISTS (SELECT 1 FROM users WHERE email = $1)", email)
    assert "WENEXT_PUBLIC_DOMAIN" in capsys.readouterr().err


async def test_base_url_overrides_the_domain_for_a_local_try_out(pool, monkeypatch, capsys) -> None:
    monkeypatch.setenv("WENEXT_PUBLIC_DOMAIN", "wenext.example.test")
    code = await _run(
        pool,
        "create",
        "--email",
        _email(),
        "--name",
        "Local",
        "--base-url",
        "http://127.0.0.1:8780/",
    )
    assert code == 0
    assert "codex mcp add wenext --url http://127.0.0.1:8780/mcp/wnx_" in capsys.readouterr().out


async def test_list_and_revoke_are_exact(pool, monkeypatch, capsys) -> None:
    monkeypatch.setenv("WENEXT_PUBLIC_DOMAIN", "wenext.example.test")
    first, second = _email(), _email()
    for email in (first, second):
        assert await _run(pool, "create", "--email", email, "--name", "Acme") == 0
    out = capsys.readouterr().out
    tokens = [line.rsplit("/mcp/", 1)[1] for line in out.splitlines() if "codex mcp add" in line]

    assert await _run(pool, "list") == 0
    listing = capsys.readouterr().out
    assert first in listing and second in listing
    assert all(token not in listing for token in tokens), "list never shows a token"

    digest = hashlib.sha256(tokens[0].encode()).hexdigest()
    # Too short, not hex, unknown: refused.
    for bad in (digest[:4], "zzzzzzzz", "0" * 12):
        assert await _run(pool, "revoke", "--id", bad) == invite.EXIT_REFUSED
        capsys.readouterr()
    assert await pool.fetchval(
        "SELECT revoked_at IS NULL FROM hosted_tokens WHERE token_sha256 = $1", digest
    )

    assert await _run(pool, "revoke", "--id", digest[:10]) == 0
    assert "1" in capsys.readouterr().out
    assert not await pool.fetchval(
        "SELECT revoked_at IS NULL FROM hosted_tokens WHERE token_sha256 = $1", digest
    )
    # Revoking it again finds nothing alive.
    assert await _run(pool, "revoke", "--id", digest[:10]) == invite.EXIT_REFUSED
    capsys.readouterr()

    # By label, the second one.
    assert await _run(pool, "revoke", "--email", second.upper()) == 0
    capsys.readouterr()
    assert await _run(pool, "list") == 0
    final = capsys.readouterr().out
    assert final.count("cofnięty") >= 2


async def test_a_deletion_request_purges_the_hosted_rows_of_an_organization(
    pool, monkeypatch
) -> None:
    """`hosted_tokens` and `external_identities` point at the project, and the purge deletes the
    project: without them in `PURGE_ORDER` the purge ends in a foreign key violation."""
    from control_plane.identity.data import DataStore

    monkeypatch.setenv("WENEXT_PUBLIC_DOMAIN", "wenext.example.test")
    invitation = await invite.create_invitation(pool, email=_email(), name="Doomed")
    user_id = await pool.fetchval("SELECT id FROM users WHERE email = $1", invitation.email)
    await pool.execute(
        "INSERT INTO external_identities (provider, subject, organization_id, workspace_id,"
        " project_id) VALUES ('github', $1, $2, $3, $4)",
        invitation.email,
        invitation.organization_id,
        invitation.workspace_id,
        invitation.project_id,
    )
    store = DataStore(pool)
    await store.request_deletion(organization_id=invitation.organization_id, requested_by=user_id)

    done = await store.purge(
        organization_id=invitation.organization_id,
        now=datetime.now(UTC) + timedelta(days=30),
    )

    assert done is not None
    for table in ("hosted_tokens", "external_identities", "projects"):
        assert not await pool.fetchval(
            f"SELECT count(*) FROM {table} WHERE organization_id = $1", invitation.organization_id
        ), table
