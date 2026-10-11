"""Interactive login with a ChatGPT account — step 2 of the founder's runbook (D20).

It matches the flow from section 13.2 (L2163-2185):

    initialize → initialized → account/login/start(chatgptDeviceCode)
    → verification URL + user code → account/login/completed
    → account/read → model/list → account/rateLimits/read → READY

Three things this module does NOT do:

* it does not bypass the login or MFA — that is the scope boundary from section 4 (L243),
* it does not automate the ChatGPT page over CDP; the official path is a local app-server
  logged in with the ChatGPT method (L2121),
* it does not pretend that device code is available. **What decides is the `account/login/start`
  response, including an explicit error about an unavailable method — not an invented preflight
  endpoint** (L2187). When the method is unavailable, we print the fallback and exit with code 2.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .account_adapter import CodexAccountAdapter, LoginUnavailable, find_codex_bin
from .hardening import prepare_codex_home
from .protocol import AppServerClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Logowanie Codeksa kontem ChatGPT")
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", "/var/wenext/codex-home")),
    )
    args = parser.parse_args(argv)

    # The same way of building the client as in gate3.py — the app-server is a process
    # that has to be started and initialized, not an object built from a path.
    prepare_codex_home(args.codex_home)
    client = AppServerClient(codex_bin=find_codex_bin(), codex_home=args.codex_home)
    # `__enter__` calls start() and initialize() on its own — an extra call would be a second
    # handshake on the same process.
    with client:
        return _perform_login(CodexAccountAdapter(client=client))


def _perform_login(adapter: CodexAccountAdapter) -> int:
    state = adapter.read_account()
    if state.logged_in:
        print(f"Konto jest już zalogowane ({state.email or 'e-mail nieujawniony'}).")
        print("Aby zalogować się ponownie, usuń auth.json z CODEX_HOME.")
        return 0

    print("Rozpoczynam logowanie metodą device code…")
    try:
        login_attempt = adapter.start_device_code_login()
    except LoginUnavailable as exc:
        print(f"\nDevice code niedostępny: {exc}\n", file=sys.stderr)
        print("To NIE jest wynik negatywny bramki — to brak możliwości jej uruchomienia tą drogą.")
        print("Fallback (L2187):")
        print("    docker compose --profile codex run --rm codex codex login --device-auth")
        return 2

    print()
    print("  ┌─────────────────────────────────────────────────────────")
    print(f"  │  Otwórz:  {login_attempt.verification_url}")
    print(f"  │  Kod:     {login_attempt.user_code}")
    print("  └─────────────────────────────────────────────────────────")
    print()
    print("Czekam na potwierdzenie w przeglądarce… (Ctrl+C przerywa)")

    try:
        state = adapter.wait_for_login()
    except KeyboardInterrupt:
        adapter.cancel_login()
        print("\nPrzerwane przez użytkownika. Bramka pozostaje NIE_URUCHOMIONA.", file=sys.stderr)
        return 130
    except Exception as exc:  # pokazujemy przyczynę, nie ślad stosu
        adapter.cancel_login()
        print(f"\nLogowanie nie powiodło się: {exc}", file=sys.stderr)
        return 1

    if not state.logged_in:
        print("\nLogowanie nie zostało potwierdzone.", file=sys.stderr)
        return 1

    print(
        f"\nZalogowano. Konto: {state.email or 'e-mail nieujawniony'}, "
        f"plan: {state.plan_type or 'nieznany'}"
    )

    # The model catalog has to be COMPLETE, otherwise the router has no right to choose
    # anything and the job goes into WAITING_CAPABILITY (L2189).
    print("Synchronizuję katalog modeli (pełna paginacja do nextCursor=null)…")
    snapshot = adapter.sync_model_catalog()
    print(f"  modeli w katalogu: {len(snapshot.models)}, snapshot kompletny: {snapshot.complete}")

    if not snapshot.complete:
        print(
            "  UWAGA: snapshot niekompletny — routing pozostanie w WAITING_CAPABILITY (L2189).",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
