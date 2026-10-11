# Security policy

Wenext drives a browser that can be logged in to your Reddit and X accounts and can publish from
them once you approve a draft or sign a delegation. A vulnerability here can mean an account
takeover or a post you did not approve, so please report problems privately and not in a public
issue.

## Reporting

Use GitHub private vulnerability reporting: open the **Security** tab of this repository and
choose **Report a vulnerability**.

Please include what you found, how to reproduce it, and the commit or version you tested. The
maintainer will reply as soon as possible. Do not test against accounts or communities that are
not yours.

## What counts

- Anything that makes the agent publish without an approval or a valid delegation.
- Leaking or reading credentials: capsule sessions, tokens, `.env` values, the Codex login.
- Reaching a service that is meant to be local only (the control plane, the database, the
  browser capsule's DevTools or screen).
- Escaping the browser capsule or the read-only repository access of the plugin.

## Supported versions

This is an early project. Only the latest commit on the default branch is supported.

## Running it safely

The stack is meant to run on your own machine, bound to 127.0.0.1. Keep `.env` private, use your
own tokens (see `.env.example`), and read what you approve.
