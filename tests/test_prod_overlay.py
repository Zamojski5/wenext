"""Nakładka produkcyjna i skrypt wdrożeniowy, sprawdzane BEZ Dockera i BEZ serwera (P7 etap C).

DLACZEGO TO W OGÓLE DA SIĘ SPRAWDZIĆ BEZ SERWERA. Plik compose jest danymi, a skrypt ma tryb
próbny, który wypisuje polecenia zamiast je wykonywać. Obie te rzeczy dają się przeczytać
maszynie, a wdrożenie sprawdzane pierwszy raz w dniu awarii to wdrożenie sprawdzane najgorzej,
jak się da.

CZEGO TE TESTY PILNUJĄ, po kolei:
  * usługa bez `restart`, bez limitu zasobów albo z healthcheckiem krótszym niż pięć sekund.
    Dokładnie te trzy braki zamieniły 19 września obciążony host w dwie awarie: kontenery nie
    wracały, jeden proces zabierał maszynę, a healthcheck ogłaszał „unhealthy" na usłudze, która
    odpowiadała;
  * suma limitów większa niż maszyna z runbooka, czyli limity, które niczego nie ograniczają;
  * atrapa serwisu testowego uruchamiana na produkcji;
  * publikacja czegokolwiek poza pętlą zwrotną bez świadomej decyzji;
  * tryb próbny, który jednak coś wykonuje. To jedyny przypadek, w którym test MUSI udowodnić,
    że czegoś NIE zrobiono, więc podstawia własne `ssh` i `rsync` i sprawdza, że nikt ich nie
    zawołał.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "compose.prod.yml"
SCRIPT = ROOT / "tools" / "dev" / "deploy_remote.sh"
RUNBOOK = ROOT / "docs" / "WDROZENIE-SERWER.md"
DOCTOR = ROOT / "tools" / "dev" / "doctor.sh"

#: Maszyna z runbooka. Limity mają się w niej zmieścić, inaczej nie są limitami.
SERVER_CORES = 4.0
SERVER_MEMORY_GB = 8.0

#: Usługi, które na serwerze chodzą bez przerwy. `fixture-site` jest osobno: ma NIE chodzić.
LONG_RUNNING = ("db", "capsule", "api", "gateway", "dashboard", "caddy")


class _Loader(yaml.SafeLoader):
    """SafeLoader, który rozumie lokalny znacznik `!reset` ze specyfikacji compose.

    `yaml.safe_load` odmawia go wprost, a nakładka go używa, bo tylko `!reset` naprawdę ODBIERA
    wartość z pliku bazowego (listy compose scala). Ten sam powód, dla którego `check_versions.py`
    czyta compose tekstem: bez tej obsługi test nie przeczytałby pliku i zgłosiłby to jako błąd
    składni zamiast jako brak reguły.
    """


def _reset(loader: yaml.SafeLoader, node: yaml.Node):
    """`!reset` na liście, na mapie i na wartości pustej.

    Nakładka używa go w trzech miejscach naraz: `profiles: !reset []`, `depends_on: !reset {…}`
    i pusta lista portów. Konstruktor rozumiejący tylko listę wywala się na mapie, a test zgłasza
    wtedy „błąd składni" zamiast sprawdzić regułę, o którą mu chodzi.
    """
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_Loader.add_constructor("!reset", _reset)


def _memory_gb(value: str) -> float:
    text = str(value).strip().lower()
    if text.endswith("g"):
        return float(text[:-1])
    if text.endswith("m"):
        return float(text[:-1]) / 1024
    return float(text) / (1024**3)


@pytest.fixture(scope="module")
def overlay() -> dict:
    assert OVERLAY.exists(), OVERLAY
    return yaml.load(OVERLAY.read_text(encoding="utf-8"), Loader=_Loader)


def test_the_overlay_covers_every_long_running_service(overlay) -> None:
    """Pusta nakładka przeszłaby każdy test niżej: najpierw liczba usług, potem ich zawartość."""
    assert len(LONG_RUNNING) == 6, LONG_RUNNING
    for name in LONG_RUNNING:
        assert name in overlay["services"], f"nakładka nie mówi nic o {name}"


def test_every_service_restarts_itself(overlay) -> None:
    for name in LONG_RUNNING:
        service = overlay["services"][name]
        assert service.get("restart") == "unless-stopped", (
            f"{name} bez `restart: unless-stopped`: po awarii hosta nie wróci sam"
        )


def test_every_service_has_a_resource_limit_that_fits_the_machine(overlay) -> None:
    cpus = 0.0
    memory = 0.0
    for name in LONG_RUNNING:
        limits = overlay["services"][name].get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits, f"{name} bez limitu zasobów: jeden proces zabierze maszynę reszcie"
        cpus += float(limits["cpus"])
        memory += _memory_gb(limits["memory"])
    # Suma z zapasem na system: limity, które sumują się do całej maszyny, nie ograniczają niczego.
    assert cpus <= SERVER_CORES + 1.75, f"suma limitów procesora {cpus} nie mieści się w serwerze"
    assert memory <= SERVER_MEMORY_GB, f"suma limitów pamięci {memory} GB nie mieści się w 8 GB"


def test_no_healthcheck_is_shorter_than_the_five_seconds_that_lied(overlay) -> None:
    """Zmierzone 19.09.2026: przy obciążonym hoście test pięciosekundowy zgłaszał „unhealthy"
    na usługach, które odpowiadały, i restartował zdrowe kontenery w najgorszym momencie."""
    for name in LONG_RUNNING:
        healthcheck = overlay["services"][name].get("healthcheck", {})
        assert healthcheck, f"{name} bez healthchecku w nakładce"
        timeout = str(healthcheck["timeout"])
        assert timeout.endswith("s"), timeout
        assert int(timeout[:-1]) > 5, f"{name}: limit {timeout} to ten sam, który kłamał"


def test_every_service_rotates_its_logs(overlay) -> None:
    for name in LONG_RUNNING:
        logging = overlay["services"][name].get("logging", {})
        assert logging.get("driver") == "json-file", f"{name} bez sterownika logów"
        options = logging.get("options", {})
        assert options.get("max-size") and options.get("max-file"), (
            f"{name}: logi bez rotacji zapełnią dysk serwera i zatrzymają bazę"
        )


def test_the_test_fixture_service_does_not_run_in_production(overlay) -> None:
    fixture = overlay["services"]["fixture-site"]
    assert fixture.get("profiles") == ["fixtures"], fixture.get("profiles")
    assert fixture.get("restart") == "no"


def test_only_the_proxy_publishes_anything_and_it_defaults_to_loopback(overlay) -> None:
    published = {
        name: service["ports"]
        for name, service in overlay["services"].items()
        if service.get("ports")
    }
    assert list(published) == ["caddy"], published
    assert published["caddy"] == [
        "${WENEXT_EDGE_BIND:-127.0.0.1}:80:80",
        "${WENEXT_EDGE_BIND:-127.0.0.1}:443:443",
    ]
    # Domyślna wartość jest pętlą zwrotną, więc ta nakładka uruchomiona na laptopie niczego nie
    # wystawia. Wystawienie wymaga wpisu w `.env` na maszynie, która ma adres publiczny.
    assert all("127.0.0.1" in entry for entry in published["caddy"])


def test_the_proxy_sends_the_capsule_screen_to_the_gateway(overlay) -> None:
    caddyfile = overlay["configs"]["caddyfile"]["content"]
    assert "handle /novnc/*" in caddyfile and "reverse_proxy gateway:8760" in caddyfile
    assert "handle /login-capsule*" in caddyfile
    assert "reverse_proxy dashboard:3000" in caddyfile
    assert "Strict-Transport-Security" in caddyfile


def test_the_public_origin_is_the_domain_and_not_localhost(overlay) -> None:
    """Ticket na ekran kapsuły sprawdza DOKŁADNY origin (D-026). 127.0.0.1 na serwerze znaczy
    ticket odrzucony jako obcy."""
    api = overlay["services"]["api"]["environment"]["WENEXT_STREAM_PUBLIC_ORIGIN"]
    assert api.startswith("https://${WENEXT_PUBLIC_DOMAIN")
    gateway = overlay["services"]["gateway"]["environment"]["WENEXT_STREAM_ORIGIN"]
    assert gateway == "https://${WENEXT_PUBLIC_DOMAIN}"


def test_the_production_overlay_switches_local_login_off(overlay) -> None:
    """Wejście bez hasła jest lokalne albo go nie ma.

    Pusta wartość, nie brak wpisu: brak wpisu przepuściłby zmienną z `.env` serwera, a wtedy
    maszyna z domeną wpuszczałaby bez hasła każdego, kto dosięgnie dashboardu. Control plane
    odmawia też po swojej stronie (`identity/local_access.py` sprawdza domenę i bind brzegu),
    ale to jest ten sam warunek z dwóch stron, i tak ma zostać.
    """
    for name in ("api", "dashboard"):
        environment = overlay["services"][name].get("environment", {})
        assert "WENEXT_LOCAL_AUTOLOGIN" in environment, f"{name}: brak jawnego wyłączenia"
        assert environment["WENEXT_LOCAL_AUTOLOGIN"] == "", environment["WENEXT_LOCAL_AUTOLOGIN"]


# ---------------------------------------------------------------------------
# Skrypt wdrożeniowy
# ---------------------------------------------------------------------------


def _fake_tools(tmp_path: pathlib.Path) -> tuple[dict[str, str], pathlib.Path]:
    """PATH z podstawionymi `ssh` i `rsync`, które tylko dopisują się do pliku śladu."""
    marker = tmp_path / "wywolania.txt"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("ssh", "rsync"):
        tool = binaries / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{marker}"\nexit 0\n', encoding="utf-8")
        tool.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{binaries}:{environment['PATH']}"
    environment["REPO_ROOT"] = str(ROOT)
    return environment, marker


def _run(arguments: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=60,
    )


def test_a_dry_run_executes_absolutely_nothing(tmp_path) -> None:
    environment, marker = _fake_tools(tmp_path)
    result = _run(["--host", "serwer.test", "--user", "wenext", "--dry-run"], environment)
    assert result.returncode == 0, result.stderr
    assert not marker.exists(), f"tryb próbny coś wykonał: {marker.read_text()}"
    assert "TRYB PRÓBNY" in result.stdout


def test_the_dry_run_shows_every_step_in_the_right_order(tmp_path) -> None:
    environment, _ = _fake_tools(tmp_path)
    out = _run(["--host", "serwer.test", "--dry-run"], environment).stdout
    steps = [
        "wysyłka repozytorium",
        "sprawdzenie .env na serwerze",
        "budowa obrazów",
        "baza i migracje pojedynczo",
        "podniesienie stacku",
        "dowód: schemat i odtworzenie",
        "co dalej, RĘCZNIE",
    ]
    positions = [out.find(step) for step in steps]
    assert all(position > -1 for position in positions), list(zip(steps, positions, strict=True))
    assert positions == sorted(positions), "kroki wypisane w innej kolejności, niż się wykonują"


def test_the_local_env_file_never_travels_to_the_server(tmp_path) -> None:
    """Plik z hasłami ma powstać na serwerze. Skrypt, który wysyła lokalny `.env`, przenosi tam
    poświadczenia z laptopa i cofa cały sens tego wdrożenia."""
    environment, _ = _fake_tools(tmp_path)
    out = _run(["--host", "serwer.test", "--dry-run"], environment).stdout
    rsync_line = next(line for line in out.splitlines() if "rsync" in line)
    assert "--exclude .env" in rsync_line, rsync_line
    assert "--exclude artifacts/" in rsync_line


def test_it_checks_every_key_the_stack_needs_on_the_server(tmp_path) -> None:
    environment, _ = _fake_tools(tmp_path)
    out = _run(["--host", "serwer.test", "--dry-run"], environment).stdout
    for key in (
        "POSTGRES_PASSWORD",
        "WENEXT_BROWSER_READER_TOKEN",
        "WENEXT_BROWSER_READER_URL",
        "WENEXT_GATEWAY_ISSUER_TOKEN",
        "OPENAI_API_KEY",
        "WENEXT_PUBLIC_DOMAIN",
        "WENEXT_TLS_EMAIL",
    ):
        assert key in out, f"wdrożenie nie sprawdza {key} w .env na serwerze"


def test_migrations_go_one_by_one_and_the_proof_runs_after_them(tmp_path) -> None:
    environment, _ = _fake_tools(tmp_path)
    out = _run(["--host", "serwer.test", "--dry-run"], environment).stdout
    assert "apply_migrations.sh" in out, "migracje puszczone hurtem zamiast pojedynczo"
    assert out.find("apply_migrations.sh") < out.find("p0_restore_check.sh")


def test_without_the_dry_run_it_really_calls_ssh_and_rsync(tmp_path) -> None:
    """Druga strona tej samej reguły: tryb próbny ma nic nie robić, a normalny przebieg ma robić.
    Test bez tej pary dowodziłby tylko, że skrypt milczy."""
    environment, marker = _fake_tools(tmp_path)
    result = _run(["--host", "serwer.test", "--user", "wenext"], environment)
    assert result.returncode == 0, result.stderr
    calls = marker.read_text(encoding="utf-8")
    assert "rsync" in calls and "ssh" in calls
    assert "apply_migrations.sh" in calls
    assert "p0_restore_check.sh" in calls


def test_the_script_refuses_without_a_host(tmp_path) -> None:
    environment, _ = _fake_tools(tmp_path)
    result = _run(["--dry-run"], environment)
    assert result.returncode == 2
    assert "--host jest wymagany" in result.stderr


# ---------------------------------------------------------------------------
# Runbook i doctor
# ---------------------------------------------------------------------------


def test_the_runbook_says_what_is_not_automated() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "Czego NIE zautomatyzowano" in text
    for topic in ("Logowania kont", "kopii poza serwer", "certyfikat"):
        assert topic.split()[0] in text, topic
    # Zasada z 4 września: żadnych długich pauz w tekście, który czyta człowiek.
    for dash in ("\u2014", "\u2013"):
        assert dash not in text, dash


def test_the_runbook_covers_the_five_things_somebody_will_need_at_night() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    for section in (
        "Czego potrzeba",
        "Pierwsze uruchomienie",
        "Logowanie kapsuły",
        "Kopie zapasowe",
        "Odtworzenie po awarii",
        "Aktualizacja",
    ):
        assert section in text, section
    assert "ssh -L 8760:127.0.0.1:8760" in text, "brak drogi do ekranu kapsuły przed DNS"


def test_doctor_warns_about_the_load_that_killed_docker_twice() -> None:
    text = DOCTOR.read_text(encoding="utf-8")
    assert "obciążenie hosta" in text
    assert "Docker Desktop" in text
    # Ostrzeżenie ma być ostrzeżeniem, nie porażką: doctor odpowiada, czy host UMIE uruchomić
    # stack, a chwilowe obciążenie nie jest wadą konfiguracji.
    load_section = text.split("== obciążenie hosta ==")[1].split("== .env ==")[0]
    assert "fail=1" not in load_section
