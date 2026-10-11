#!/usr/bin/env python3
"""Checks that compose and the Dockerfiles have not drifted away from versions.lock.toml.

Run by `make check-versions` before every build and every start.

What it guards, all of it straight out of the Definition of Done of E1.F0 and D-014:

1. The Postgres image in compose agrees with the pin (L5180 — exact versions).
2. The set of ports published to the host is EXACTLY the allowlist in versions.lock.toml,
   every one of them bound to 127.0.0.1 (L2353, L5600, D-014).
3. No Dockerfile rewrites the Chrome for Testing or Node version on its own — everything
   reads from the lock, because a capability snapshot is valid only for the build it was
   taken on (L2580, L4381).
4. The seccomp profile exists and matches by hash.

The script is a separate file rather than a heredoc in the Makefile, because macOS has
GNU Make 3.81 — no `.ONESHELL`, so multi-line recipes fall apart into separate shells.

The compose parser below is also imported by `tests/test_published_ports.py`. ONE
implementation, exercised from two directions: this script gates every build, and the tests
run the same functions against deliberately broken synthetic files. A second copy in the
tests would drift away from the one that actually gates.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Ports whose appearance on the host is a critical error — REGARDLESS of the allowlist.
#: The allowlist is a list of conscious decisions; this is the one decision nobody may make.
BANNED_HOST_PORTS = {9222: "CDP", 5900: "VNC", 5901: "VNC"}

#: The only host address a published port may be bound to on a development host.
LOOPBACK = "127.0.0.1"

#: The ONE file in which a port may be published outside the loopback, and only for entries
#: marked `public_edge = true` in the lock (D-042). Named here rather than inferred from the
#: file name pattern: `compose.anything.yml` must not inherit the exception by being called
#: something similar.
PUBLIC_EDGE_FILE = "compose.prod.yml"

_VARIABLE_WITH_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?-([^}]*)\}")
_LONG_SYNTAX_KEY = re.compile(r"^(target|published|host_ip|protocol|mode|name|app_protocol)\s*:")
_ANCHOR = re.compile(r"&([A-Za-z0-9_.\-]+)")
_ALIAS = re.compile(r"\*([A-Za-z0-9_.\-]+)")


def compose_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Every compose file in the repository, found by globbing rather than by a constant.

    A new `compose.whatever.yml` is the obvious way around an allowlist, so the file list is
    derived from the repository instead of being trusted from a list somebody has to update.
    """
    return sorted(path for path in root.glob("*compose*.y*ml") if path.is_file())


# ---------------------------------------------------------------------------
# Compose parsing
# ---------------------------------------------------------------------------
# Text-based on purpose: PyYAML is NOT installed in every virtualenv this runs in, and
# `compose.test.yml` publishes `ports: !reset []` — a local tag that `yaml.safe_load`
# refuses outright. Anything the parser cannot classify is passed on as an entry rather
# than dropped, so an unreadable line becomes a LOUD violation instead of a silent pass.


def _parse(text: str) -> tuple[list[list], dict[str, list[str]], dict[str, str], list[tuple]]:
    """One pass over a compose file.

    Returns:
        entries       — [[owner, line number, raw entry], …] for every item under a `ports:`
        merges        — owner -> the anchor aliases it pulls in with `<<:`
        anchor_owner  — anchor name -> the owner that defined it
        modes         — [(owner, line number, network_mode value), …]

    `owner` is `("service", name)` for a service and `("block", name)` for a top-level key,
    which is what makes a `ports:` block under `x-expose: &expose` visible at all.
    """
    entries: list[list] = []
    merges: dict[str, list[str]] = {}
    anchor_owner: dict[str, str] = {}
    modes: list[tuple] = []

    in_services = False
    owner = ("block", "?")
    block_owner = owner
    ports_indent: int | None = None
    entries_in_block = 0

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if ports_indent is not None and indent > ports_indent:
            if stripped.startswith("-"):
                entries.append([block_owner, lineno, stripped[1:].strip()])
                entries_in_block += 1
            elif entries_in_block:
                # Continuation of a long-syntax entry: `published:`, `host_ip:`, `protocol:`.
                entries[-1][2] = f"{entries[-1][2]}\n{stripped}"
            else:
                entries.append([block_owner, lineno, stripped])  # shape unknown → violation
                entries_in_block += 1
            continue
        ports_indent = None

        anchor = _ANCHOR.search(stripped)

        if indent == 0:
            in_services = stripped.startswith("services:")
            owner = ("block", stripped.partition(":")[0].strip())
            if anchor:
                anchor_owner[anchor.group(1)] = owner
            continue

        if in_services and indent == 2:
            owner = ("service", stripped.partition(":")[0].strip())
            if anchor:
                anchor_owner[anchor.group(1)] = owner
            continue

        key, _, rest = stripped.partition(":")
        key = key.strip()
        value = rest.strip()

        if key == "<<":
            merges.setdefault(owner, []).extend(_ALIAS.findall(value))
            continue
        if key == "network_mode":
            modes.append((owner, lineno, value.strip("\"'")))
            continue
        if key != "ports":
            if anchor:
                anchor_owner[anchor.group(1)] = owner
            continue

        block_owner = owner
        inline = value.removeprefix("!reset").removeprefix("!override").strip()
        if not inline:
            ports_indent = indent
            entries_in_block = 0
        elif inline.startswith("[") and inline.endswith("]"):
            for item in inline[1:-1].split(","):
                if item.strip():
                    entries.append([owner, lineno, item.strip()])
        elif inline not in ("~", "null"):
            entries.append([owner, lineno, inline])  # e.g. an alias — unreadable → violation

    return entries, merges, anchor_owner, modes


def port_entries(text: str) -> tuple[list[tuple[str, int, str]], list[str]]:
    """Return (entries attributed to a service, complaints about entries that are not).

    RESOLVES YAML MERGE KEYS. Red team, 2026-08-31: the previous parser entered a `ports:`
    block only after seeing a service key at indent 2 inside `services:`, so this —

        x-expose: &expose
          ports:
            - "0.0.0.0:9222:9222"
        services:
          capsule:
            <<: *expose

    — was not misread, it was INVISIBLE. A parser that reads nothing reports a clean file,
    which is the same disease as a gate announcing GO without a measurement (D-007), one
    layer down. Ports pulled in through `<<:` are therefore attributed to the SERVICE that
    merges them, transitively (an anchor may merge another anchor) and through the list form
    `<<: [*a, *b]`.

    Ports under an anchor that NO service merges publish nothing today — and are still
    reported. Silence there would be an invitation: declare the block now, add the one-line
    `<<:` later, and the diff that actually opens the port looks like a reformatting.
    """
    entries, merges, anchor_owner, _modes = _parse(text)

    by_owner: dict[tuple, list[tuple[int, str]]] = {}
    for owner, lineno, raw in entries:
        by_owner.setdefault(owner, []).append((lineno, raw))

    reached: set[tuple] = set()

    def collect(owner: tuple, seen: frozenset) -> list[tuple[int, str]]:
        found = list(by_owner.get(owner, []))
        for alias in merges.get(owner, []):
            target = anchor_owner.get(alias)
            if target is None or target in seen:
                continue
            reached.add(target)
            found.extend(collect(target, seen | {target}))
        return found

    services = {owner for owner in (*by_owner, *merges) if owner[0] == "service"}
    attributed: list[tuple[str, int, str]] = []
    for owner in services:
        for lineno, raw in collect(owner, frozenset({owner})):
            attributed.append((owner[1], lineno, raw))

    orphans: list[str] = []
    for owner, found in by_owner.items():
        if owner[0] == "service" or owner in reached:
            continue
        for lineno, raw in found:
            orphans.append(
                f"linia {lineno}: `ports:` ({raw!r}) pod blokiem `{owner[1]}`, którego nie "
                "scala żaden serwis — martwa deklaracja portu jest zaproszeniem do dopisania "
                "jednolinijkowego `<<:` w przyszłości"
            )

    attributed.sort(key=lambda row: (row[1], row[0]))
    return attributed, sorted(orphans)


def network_mode_problems(source: str, text: str) -> list[str]:
    """Refuse `network_mode: host`. Red team, 2026-08-31 — the invariant's blind spot.

    `network_mode: host` hands the container the host's entire network namespace: every port
    it listens on is on the host, and there is not one `ports:` entry to find. An allowlist
    built over `ports:` cannot see it, because there is nothing to see. No test in this
    repository looked for it before today.

    `network_mode: service:<name>` and `none` publish nothing and stay legal.
    """
    _entries, _merges, _anchor_owner, modes = _parse(text)
    problems: list[str] = []
    for owner, lineno, value in modes:
        if value.strip() != "host":
            continue
        problems.append(
            f"{source}:{lineno} ({owner[1]}): `network_mode: host` oddaje kontenerowi CAŁĄ "
            "przestrzeń sieciową hosta. Każdy port, na którym on słucha — CDP, VNC, Postgres — "
            "jest wtedy na hoście, a pod `ports:` nie ma ani jednego wpisu do sprawdzenia. "
            "Niezmiennik z D-014 obchodzi się tą drogą bez śladu w allowliście"
        )
    return problems


def _interpolated(value: str) -> str | None:
    """Substitute `${VAR:-default}` with its default; None when the value stays unknown.

    docker compose interpolates the environment, so the literal text is not the whole truth:
    the gateway entry reads `"127.0.0.1:${WENEXT_GATEWAY_HOST_PORT:-8760}:8760"`. A variable
    with NO default makes the published port unknowable at rest, and an unknowable port
    cannot be matched against an allowlist — callers turn None into a violation, never a pass.
    """
    resolved = _VARIABLE_WITH_DEFAULT.sub(lambda match: match.group(2), value)
    return None if "$" in resolved else resolved


def host_binding(entry: str) -> tuple[str | None, int | None, int | None, str | None]:
    """Read (host address, host port, container port) out of one `ports:` entry.

    The fourth value says why the first three could not be read. Understands BOTH compose
    spellings, because a check that knew only one of them would let the other through unseen:
      - short syntax `"127.0.0.1:8760:8760"`, where a missing address means every interface,
      - long syntax (`target:` / `published:` / `host_ip:`), where a missing `host_ip` does.
    """
    if _LONG_SYNTAX_KEY.match(entry):
        fields: dict[str, str] = {}
        for line in entry.splitlines():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("\"'")
        target = fields.get("target", "")
        container = int(target) if target.isdigit() else None
        if "host_ip" not in fields:
            return (
                None,
                None,
                container,
                ("długa składnia bez `host_ip` publikuje na WSZYSTKICH interfejsach"),
            )
        published = _interpolated(fields.get("published", ""))
        if published is None:
            return (
                fields["host_ip"],
                None,
                container,
                "`published` zależy od zmiennej bez wartości domyślnej",
            )
        if not published.isdigit():
            return (
                fields["host_ip"],
                None,
                container,
                f"`published: {published!r}` to nie pojedynczy numer",
            )
        return fields["host_ip"], int(published), container, None

    resolved = _interpolated(entry.strip().strip("\"'"))
    if resolved is None:
        return (
            None,
            None,
            None,
            "zmienna bez wartości domyślnej — publikowanego portu nie da się odczytać",
        )

    parts = resolved.split("/")[0].split(":")  # `/tcp`, `/udp` — protokół nie zmienia adresu
    container = int(parts[-1]) if parts[-1].isdigit() else None
    if len(parts) < 3:
        return (
            None,
            None,
            container,
            (
                f"zapis {resolved!r} nie podaje adresu hosta — Docker publikuje wtedy "
                "na WSZYSTKICH interfejsach, nie na pętli zwrotnej"
            ),
        )
    if len(parts) > 3:
        return None, None, container, f"zapisu {resolved!r} ten test nie rozumie (IPv6?)"
    host_ip, host_port, _container_text = parts
    if not host_port.isdigit():
        return host_ip, None, container, f"port na hoście {host_port!r} nie jest numerem"
    return host_ip, int(host_port), container, None


def port_publication_problems(
    source: str,
    text: str,
    allowlist: set[tuple[str, int]],
    public_edge: set[tuple[str, int]] | None = None,
) -> tuple[int, list[tuple[str, str]], set[tuple[str, int]]]:
    """Return (entries read, one (service, message) per broken entry, ports actually published).

    The COUNT is returned so callers can assert it separately from the verdict: a parser that
    read nothing would otherwise report a clean file, and `all([]) == True` has already
    produced two false passes in this project (D-011).
    """
    problems: list[tuple[str, str]] = []
    published: set[tuple[str, int]] = set()
    edge = public_edge or set()

    attributed, orphans = port_entries(text)
    for complaint in orphans:
        problems.append(("?", f"{source}: {complaint}"))

    for service, lineno, entry in attributed:
        where = f"{source}:{lineno} ({service})"
        host_ip, host_port, container_port, why = host_binding(entry)

        banned = [p for p in (host_port, container_port) if p in BANNED_HOST_PORTS]
        if banned:
            names = ", ".join(f"{p} ({BANNED_HOST_PORTS[p]})" for p in banned)
            problems.append(
                (
                    service,
                    f"{where}: port {names} nie może być publikowany NIGDY — "
                    "ani przez allowlistę, ani przez wyjątek (L2353, L5600)",
                )
            )
            continue
        if why is not None:
            problems.append((service, f"{where}: {why}"))
        elif host_ip != LOOPBACK and not (
            source == PUBLIC_EDGE_FILE and (service, host_port) in edge
        ):
            # Wyjątek jest podwójnie zawężony: musi być TEN plik i wpis oznaczony w locku jako
            # `public_edge`. Jedno bez drugiego nadal jest wyciekiem.
            problems.append(
                (
                    service,
                    f"{where}: bindowanie do {host_ip!r}, dozwolone jest tylko {LOOPBACK}. "
                    f"Poza pętlę zwrotną wychodzi wyłącznie proxy z {PUBLIC_EDGE_FILE}, "
                    "oznaczone w versions.lock.toml jako `public_edge` (D-042): to są jedyne "
                    "drzwi z internetu i mają być widoczne w diffie",
                )
            )
        elif (service, host_port) not in allowlist:
            problems.append(
                (
                    service,
                    f"{where}: publikuje port {host_port} spoza allowlisty "
                    f"{sorted(allowlist)} — dopisz go tam świadomie albo nie publikuj",
                )
            )
        else:
            published.add((service, host_port))

    # Orphans count towards "entries read": the number exists to prove the parser was not
    # blind, and an entry it saw but could not attribute is still an entry it saw.
    return len(attributed) + len(orphans), problems, published


def public_edge_from_lock(lock: dict) -> set[tuple[str, int]]:
    """Wpisy allowlisty, które wolno wystawić poza pętlę zwrotną (D-042).

    Osobna funkcja, a nie pole w tamtym zbiorze, żeby wywołanie bez tego argumentu zachowywało
    się jak przed zmianą: domyślnie NIC nie ma prawa wyjść poza 127.0.0.1.
    """
    rows = lock.get("network", {}).get("published_ports", [])
    return {(str(row["service"]), int(row["port"])) for row in rows if row.get("public_edge")}


def allowlist_from_lock(lock: dict) -> set[tuple[str, int]]:
    """The allowlist of published ports, read from `[[network.published_ports]]`.

    A missing section yields an EMPTY set on purpose. The caller must treat empty as an
    error, not as "nothing to check" — see `check_ports`.
    """
    rows = lock.get("network", {}).get("published_ports", [])
    return {(str(row["service"]), int(row["port"])) for row in rows}


def check_ports(
    root: pathlib.Path,
    allowlist: set[tuple[str, int]],
    public_edge: set[tuple[str, int]] | None = None,
) -> list[str]:
    """The whole D-014 invariant in one place; returns a list of problems."""
    if not allowlist:
        return [
            "allowlista publikowanych portów w versions.lock.toml jest PUSTA. "
            "Reguła „zbiór publikowanych == allowlista” byłaby wtedy spełniona przez "
            "pustkę po obu stronach — to `all([]) == True` w przebraniu równości zbiorów"
        ]

    problems: list[str] = []
    published: set[tuple[str, int]] = set()
    entries_read = 0

    for path in compose_files(root):
        text = path.read_text(encoding="utf-8")
        problems.extend(network_mode_problems(path.name, text))
        count, found, ports = port_publication_problems(path.name, text, allowlist, public_edge)
        entries_read += count
        published |= ports
        problems.extend(message for _service, message in found)

    if not entries_read:
        problems.append(
            "parser nie znalazł ANI JEDNEGO wpisu pod `ports:` — albo zniknęła publikacja "
            "gatewaya, albo parser oślepł; w obu razach ta kontrola przechodziłaby pusta"
        )
    if published != allowlist:
        missing = sorted(allowlist - published)
        extra = sorted(published - allowlist)
        problems.append(
            f"zbiór publikowanych portów {sorted(published)} != allowlista {sorted(allowlist)}"
            + (f"; na allowliście, ale nikt nie publikuje: {missing}" if missing else "")
            + (f"; publikowane spoza allowlisty: {extra}" if extra else "")
        )
    return problems


# ---------------------------------------------------------------------------
# The rest of the lock
# ---------------------------------------------------------------------------


def _dockerfiles(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        path
        for path in sorted(root.rglob("Dockerfile"))
        if not {".venv", "artifacts", "node_modules", ".next"} & set(path.parts)
    ]


def main() -> int:
    lock = tomllib.loads((ROOT / "versions.lock.toml").read_text(encoding="utf-8"))
    problems: list[str] = []

    compose_path = ROOT / "docker-compose.yml"
    compose = compose_path.read_text(encoding="utf-8")

    # --- 1. the Postgres image ------------------------------------------------
    pg = lock["images"]["postgres"]
    expected = f"{pg['ref']}@{pg['digest']}"
    if expected not in compose:
        problems.append(f"docker-compose.yml nie zawiera obrazu z locka: {expected}")

    # --- 2. published ports (D-014) -------------------------------------------
    allowlist = allowlist_from_lock(lock)
    problems.extend(check_ports(ROOT, allowlist, public_edge_from_lock(lock)))

    # --- 3. versions written into the Dockerfiles instead of read from the lock -
    cft = lock["browser"]["chrome_for_testing"]["version"]
    node = lock["stack"]["js"]["node"]
    for df in _dockerfiles(ROOT):
        text = df.read_text(encoding="utf-8")
        for found_version in re.findall(r"\b15\d\.\d+\.\d+\.\d+\b", text):
            if found_version != cft:
                problems.append(f"{df.relative_to(ROOT)}: wersja CfT {found_version} != lock {cft}")
        for found_node in re.findall(r"\bnode:(\d+\.\d+\.\d+)", text):
            if found_node != node:
                problems.append(f"{df.relative_to(ROOT)}: Node {found_node} != lock {node}")

    # --- 4. the seccomp profile must exist and match by hash -------------------
    seccomp = lock["browser"]["seccomp"]
    seccomp_path = ROOT / seccomp["local_path"]
    if not seccomp_path.exists():
        problems.append(f"brak profilu seccomp: {seccomp['local_path']}")
    else:
        actual = hashlib.sha256(seccomp_path.read_bytes()).hexdigest()
        if actual != seccomp["sha256"]:
            problems.append(
                f"{seccomp['local_path']}: sha256 {actual[:16]}… != lock {seccomp['sha256'][:16]}…"
            )

    if problems:
        print("check-versions: NIEZGODNOŚCI")
        for p in problems:
            print("  -", p)
        return 1

    print(
        f"check-versions: OK (CfT {cft}, {pg['ref']}, Node {node}, "
        f"Next {lock['stack']['js']['next']})"
    )
    print("  allowlista publikowanych portów (versions.lock.toml -> [[network.published_ports]]):")
    for service, port in sorted(allowlist):
        print(f"    - {service}: {LOOPBACK}:{port}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
