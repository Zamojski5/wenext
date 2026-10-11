"""Everything the repository publishes to the host — one invariant, one file.

Moved out of `test_repo_consistency.py` because the check grew two teeth after a red-team
review and no longer fits between an ADR check and a `__all__` check.

The invariant has NEVER been "exactly one port" (D-014). It is:

    nothing reaches the host except the ports named, by service and by number, on the
    allowlist in `versions.lock.toml`, and every one of them is bound to 127.0.0.1.

The parser and the rules live in `tools/dev/check_versions.py`, and this file imports them.
One implementation, exercised from two directions: `make check-versions` runs it against the
real compose files, and the tests below run it against synthetic ones that are deliberately
broken. A second copy of the parser here would drift from the one that actually gates builds.

Two ways a port could reach the host WITHOUT this file noticing, both found by the red team
on 2026-08-31, both pinned below:

  - a `ports:` block declared under a YAML anchor (`x-expose: &expose`) and pulled into a
    service with `<<: *expose`. The previous parser only ever looked inside `services:`, so
    those entries were invisible — not misread, INVISIBLE, which reads as "no ports here".
  - `network_mode: host`, which hands the container every one of its ports without a single
    `ports:` entry to find. An allowlist over `ports:` says nothing about it.
"""

from __future__ import annotations

import pathlib
import sys
import textwrap
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Same trick `test_repo_consistency.py` already uses for `check_docs.polish_part`: the guard
# scripts live in tools/dev/ and the tests import them instead of re-implementing them.
sys.path.insert(0, str(ROOT / "tools" / "dev"))

from check_versions import (  # noqa: E402
    BANNED_HOST_PORTS,
    allowlist_from_lock,
    compose_files,
    network_mode_problems,
    port_publication_problems,
)

#: The allowlist, written out by name. `versions.lock.toml` holds the same set and
#: `make check-versions` enforces it; this is the second lock on the same door. Publishing a
#: third port therefore takes TWO conscious edits, and both show up in the diff.
#:
#: D-014: 3000 is the Next.js dashboard. The control plane itself is NOT here — it is reached
#: only from the dashboard's server side, so it publishes nothing (D-016).
#: D-042 dołożyło dwa porty proxy z nakładki produkcyjnej. Są tu z nazwy, tak jak pozostałe,
#: i mają własny test niżej: wyjątek od pętli zwrotnej ma być widoczny w dwóch miejscach, a nie
#: schowany w jednym polu locka.
EXPECTED_ALLOWLIST = {
    ("gateway", 8760),
    ("gateway-x", 8761),
    ("dashboard", 3000),
    ("caddy", 80),
    ("caddy", 443),
}


def _lock() -> dict:
    return tomllib.loads((ROOT / "versions.lock.toml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The real repository
# ---------------------------------------------------------------------------


def test_the_compose_files_this_test_reads_are_all_of_them() -> None:
    """A new compose file is the obvious way around an allowlist, so the list is derived.

    `compose_files()` globs the repository rather than trusting a constant, and this test
    pins what that glob currently finds. A new `compose.something.yml` therefore fails here
    loudly instead of publishing ports nobody checks.
    """
    found = sorted(path.name for path in compose_files(ROOT))
    assert found == [
        # Nakładka na czas wielogodzinnego pomiaru GATE-3: wyłącza healthcheck
        # kontenera `codex`, który przy oknie liczonym w godzinach wywracał VM
        # Dockera (D-012). Nie definiuje ŻADNEGO portu i nie może definiować.
        "compose.gate3-unattended.yml",
        # P7 etap C: nakładka serwerowa. JEDYNY plik, w którym coś wolno wystawić poza pętlę
        # zwrotną, i tylko dla wpisów oznaczonych w locku jako `public_edge` (D-042).
        "compose.prod.yml",
        "compose.test.yml",
        "docker-compose.yml",
    ], (
        f"zbiór plików compose w repo zmienił się: {found}. Dopisz nowy plik świadomie — "
        "dopóki tego nie zrobisz, nikt nie pilnuje jego portów"
    )


def test_published_ports_equal_the_allowlist() -> None:
    """The set published by compose is EXACTLY the set on the allowlist — both directions.

    Subset in one direction would let a stale allowlist entry rot (a port nobody publishes,
    kept "just in case", which is how an allowlist stops meaning anything). Subset in the
    other would let a new port through. So: equality.

    The entry count is asserted SEPARATELY and BEFORE the verdict. A parser that read
    nothing would otherwise report a clean file, and `all([]) == True` has already produced
    two false passes in this project (D-011).
    """
    allowlist = allowlist_from_lock(_lock())
    assert allowlist == EXPECTED_ALLOWLIST, (
        f"allowlista w versions.lock.toml ({sorted(allowlist)}) rozjechała się z listą "
        f"wypisaną w tym teście ({sorted(EXPECTED_ALLOWLIST)}) — zmiana wymaga obu edycji"
    )

    entries_read = 0
    published: set[tuple[str, int]] = set()
    problems: list[str] = []
    for path in compose_files(ROOT):
        count, found, ports = port_publication_problems(
            path.name, path.read_text(encoding="utf-8"), allowlist
        )
        entries_read += count
        published |= ports
        problems.extend(message for _service, message in found)

    assert entries_read, (
        "parser nie znalazł ANI JEDNEGO wpisu pod `ports:` w plikach compose. Albo zniknęła "
        "publikacja gatewaya, albo parser oślepł — w obu razach ten test przechodziłby pusty"
    )
    assert not problems, "niezmiennik z D-014 złamany:\n" + "\n".join(problems)
    assert published == allowlist, (
        f"publikowane {sorted(published)} != allowlista {sorted(allowlist)}. Wpis na "
        "allowliście, którego nikt nie publikuje, to allowlista, która przestaje cokolwiek "
        "znaczyć — skasuj go albo opublikuj port"
    )


def test_no_compose_file_uses_network_mode_host() -> None:
    """The live check on the real files; the synthetic proof is below."""
    problems: list[str] = []
    for path in compose_files(ROOT):
        problems.extend(network_mode_problems(path.name, path.read_text(encoding="utf-8")))
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# The failure direction — the only one a green repository never exercises
# ---------------------------------------------------------------------------


def test_the_check_catches_every_shape_that_slips_past_a_human_reader() -> None:
    """Six wrong ways to publish a port, each of which reads as harmless in review."""
    compose = textwrap.dedent(
        """\
        services:
          gateway:
            ports:
              - "127.0.0.1:${WENEXT_GATEWAY_HOST_PORT:-8760}:8760"
          bare_pair:
            ports:
              - "8760:8760"
          wildcard:
            ports:
              - "0.0.0.0:3000:3000"
          off_allowlist:
            ports:
              - "127.0.0.1:9999:8000"
          long_syntax_without_host_ip:
            ports:
              - target: 8000
                published: "8001"
                protocol: tcp
          no_default_value:
            ports:
              - "127.0.0.1:${WENEXT_UNSET_PORT}:8000"
          reset_to_nothing:
            ports: !reset []
        """
    )

    count, problems, published = port_publication_problems(
        "syntetyczny", compose, {("gateway", 8760)}
    )
    assert count == 6, f"parser zgubił wpisy `ports:` — przeczytał {count} zamiast 6"

    caught = {service for service, _message in problems}
    assert caught == {
        "bare_pair",
        "wildcard",
        "off_allowlist",
        "long_syntax_without_host_ip",
        "no_default_value",
    }, f"kontrola portów NIE wyłapała wszystkiego, wyłapała: {sorted(caught)}"
    assert published == {("gateway", 8760)}, (
        f"tylko poprawny wpis wolno policzyć jako opublikowany, a policzono: {sorted(published)}"
    )


def test_ports_hidden_behind_a_yaml_anchor_are_still_seen() -> None:
    """Red team, 2026-08-31: a `ports:` block reached only through `<<:` was INVISIBLE.

    The old parser entered a block only after seeing a service key at indent 2 inside
    `services:`. An anchor is a top-level key, so its `ports:` were never read — and a
    parser that reads nothing reports a clean file. That is the same disease as a gate that
    announces GO without a measurement (D-007), one layer down.

    Three shapes here, and all three have to be attributed to the SERVICE that merges them:
    a single merge, a merge inside a list (`<<: [*a, *b]`), and an anchor that itself merges
    another anchor.
    """
    compose = textwrap.dedent(
        """\
        x-cdp: &cdp
          ports:
            - "0.0.0.0:9222:9222"

        x-expose: &expose
          <<: *cdp
          ports:
            - "0.0.0.0:5900:5900"

        x-logging: &logging
          logging:
            driver: json-file

        services:
          capsule:
            <<: *expose
            image: nieistotny
          gateway:
            <<: [*logging, *cdp]
            ports:
              - "127.0.0.1:8760:8760"
        """
    )

    count, problems, published = port_publication_problems(
        "syntetyczny", compose, {("gateway", 8760)}
    )

    # Liczność OSOBNO i PRZED predykatem. Cztery wpisy: 9222 wciągnięte dwa razy
    # (przez capsule i przez gateway), 5900 raz, 8760 raz.
    assert count == 4, (
        f"parser widzi {count} wpisów `ports:` zamiast 4 — porty schowane za kotwicą YAML "
        "znów są dla niego niewidoczne"
    )

    caught = {service for service, _message in problems}
    assert caught == {"capsule", "gateway"}, (
        "porty wmergowane kotwicą muszą być przypisane do SERWISU, który je scala, "
        f"a wyszło: {sorted(caught)}"
    )
    assert published == {("gateway", 8760)}, sorted(published)

    joined = "\n".join(message for _service, message in problems)
    assert "9222" in joined and "5900" in joined, joined


def test_ports_under_an_anchor_nobody_merges_are_reported_not_ignored() -> None:
    """An anchor with ports that no service uses publishes nothing — and is still a trap.

    Silence here would be an invitation: declare the block now, add the one-line `<<:` later,
    and the diff that actually opens the port looks like a formatting change.
    """
    compose = textwrap.dedent(
        """\
        x-dead: &dead
          ports:
            - "0.0.0.0:9222:9222"

        services:
          gateway:
            ports:
              - "127.0.0.1:8760:8760"
        """
    )

    count, problems, published = port_publication_problems(
        "syntetyczny", compose, {("gateway", 8760)}
    )
    assert count == 2, count
    assert published == {("gateway", 8760)}, sorted(published)
    assert any("dead" in message for _service, message in problems), (
        f"martwa kotwica z portami przeszła bez śladu: {problems}"
    )


def test_network_mode_host_is_refused() -> None:
    """Red team, 2026-08-31: `network_mode: host` publishes EVERYTHING, with no `ports:`.

    An allowlist built over `ports:` entries cannot see it: there is nothing to see. The
    container simply shares the host's network namespace, and every port it listens on —
    CDP, VNC, Postgres — is on the host. No test in this repository looked for it.
    """
    compose = textwrap.dedent(
        """\
        services:
          gateway:
            ports:
              - "127.0.0.1:8760:8760"
          sneaky:
            network_mode: host
          quoted:
            network_mode: "host"
          legitimate:
            network_mode: service:gateway
        """
    )

    problems = network_mode_problems("syntetyczny", compose)
    named = " ".join(problems)

    assert len(problems) == 2, f"oczekiwano dwóch trafień, jest {len(problems)}: {problems}"
    assert "sneaky" in named and "quoted" in named, named
    assert "legitimate" not in named, (
        f"`network_mode: service:<nazwa>` nie wystawia niczego na host i ma przechodzić: {named}"
    )


def test_banned_ports_stay_banned() -> None:
    """9222 (CDP), 5900 and 5901 (VNC) are refused even from INSIDE the allowlist.

    The allowlist is a list of conscious decisions, and this is the one decision that cannot
    be made: L2353 and L5600 put CDP and VNC out of reach of the host, full stop. Without
    this, the whole invariant is one line in `versions.lock.toml` away from being void.
    """
    assert BANNED_HOST_PORTS, "lista zakazanych portów jest pusta — reguła nic nie znaczy"

    compose = textwrap.dedent(
        """\
        services:
          cdp:
            ports:
              - "127.0.0.1:9222:9222"
          vnc:
            ports:
              - "127.0.0.1:5900:5900"
          vnc_alt:
            ports:
              - "127.0.0.1:5901:5901"
          container_side_only:
            ports:
              - "127.0.0.1:8760:9222"
        """
    )

    # Allowlista, która pozwala na WSZYSTKO z powyższych. Zakaz ma wygrać mimo to.
    permissive = {("cdp", 9222), ("vnc", 5900), ("vnc_alt", 5901), ("container_side_only", 8760)}
    count, problems, _published = port_publication_problems("syntetyczny", compose, permissive)

    assert count == 4, count
    caught = {service for service, _message in problems}
    assert caught == {"cdp", "vnc", "vnc_alt", "container_side_only"}, (
        f"port zakazany przeszedł, bo ktoś wpisał go na allowlistę: wyłapano {sorted(caught)}"
    )


def test_an_empty_allowlist_is_an_error_not_a_free_pass() -> None:
    """`published == allowlist` is satisfied vacuously when both are empty.

    That is `all([]) == True` wearing a set-equality costume — the exact shape that gave this
    project two of its false passes (D-011). An empty allowlist means the rule was never
    written, so it has to be a loud error rather than a quiet yes.
    """
    assert allowlist_from_lock({}) == set(), "brak sekcji w locku to pusty zbiór, nie wyjątek"

    problems: list[str] = []
    _count, found, _published = port_publication_problems(
        "syntetyczny", 'services:\n  gateway:\n    ports:\n      - "127.0.0.1:8760:8760"\n', set()
    )
    problems.extend(message for _service, message in found)
    assert problems, "przy pustej allowliście każdy publikowany port jest naruszeniem"


def test_the_only_door_to_the_internet_is_the_production_proxy() -> None:
    """D-042 w drugim miejscu: wyjątek ma być dokładnie taki, jak go opisano, i ani szerszy.

    Trzy rzeczy naraz, bo każda osobno przechodziłaby przy zepsutej pozostałej:
      * wpisy `public_edge` w locku to DOKŁADNIE caddy 80 i 443;
      * poza `compose.prod.yml` żaden plik nie publikuje niczego poza 127.0.0.1;
      * w samej nakładce domyślny adres bindu to pętla zwrotna, więc uruchomienie jej na
        laptopie niczego nie wystawia, a wystawienie wymaga wpisu w `.env` na serwerze.
    """
    from check_versions import PUBLIC_EDGE_FILE, host_binding, port_entries, public_edge_from_lock

    edge = public_edge_from_lock(_lock())
    assert edge == {("caddy", 80), ("caddy", 443)}, sorted(edge)

    checked = 0
    for path in compose_files(ROOT):
        attributed, _orphans = port_entries(path.read_text(encoding="utf-8"))
        for service, _lineno, entry in attributed:
            checked += 1
            host_ip, host_port, _container, why = host_binding(entry)
            assert why is None, f"{path.name}: {why}"
            if path.name == PUBLIC_EDGE_FILE and (service, host_port) in edge:
                # Domyślna wartość zmiennej bindu MUSI być pętlą zwrotną.
                assert host_ip == "127.0.0.1", f"{path.name}: domyślny bind to {host_ip}"
                assert "WENEXT_EDGE_BIND" in entry, entry
                continue
            assert host_ip == "127.0.0.1", f"{path.name} ({service}): bind {host_ip}"
    assert checked >= 4, f"parser przeczytał {checked} wpisów, to za mało na dowód"
