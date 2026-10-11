"""Odczyt EFEKTYWNEJ konfiguracji runtime'u, bez konta i bez tury modelu.

PO CO TO ISTNIEJE. `web_search = "disabled"` jest zmierzone: tak odpowiedział `config/read`
z żywego runtime'u (NOTES.md, errata 2). Wartość, którą wpisujemy, żeby wyszukiwanie WŁĄCZYĆ,
zmierzona nie jest, a w repozytorium nie ma schematu z enumem `WebSearchMode`. Sonda jest po to,
żeby to rozstrzygnąć odczytem zamiast założeniem: pisze `config.toml` wybranego profilu do
tymczasowego CODEX_HOME, startuje przypiętą binarkę, robi handshake i drukuje, co silnik
naprawdę ma.

PIERWSZY POMIAR, 20.09: profil z `web_search = "enabled"` dostał
`[-32603] invalid configuration: unknown variant `enabled`, expected one of `disabled`,
`cached`, `indexed`, `live``. Wartość, którą kod wpisywał, nie istniała. Tyle jest warte
zgadywanie enuma z nazwy.

DRUGI POMIAR, 21.09 (E9): czy wpis serwera narzędzi da się podać PRZY STARCIE PROCESU
(`--config mcp_servers.wenext={...}`) zamiast pisać go do `config.toml` w domu współdzielonym
przez pilota i rozmowy. Od odpowiedzi zależy droga B z `docs/E9-DOM-NA-TURE.md`. `--mcp-overrides`
sprawdza cztery przypadki i DRUKUJE, co się stało w każdym; rozstrzyga ostatni, bo on jedyny
odróżnia „binarka przyjęła napis" od „wpis naprawdę zadziałał".

Czego sonda NIE robi: nie loguje się, nie startuje tury, nie dotyka cudzego CODEX_HOME. Pisze do
katalogu, który sama tworzy, i po sobie sprząta.

    python -m codex_runtime.config_probe --web-search enabled
    python -m codex_runtime.config_probe --mcp-overrides
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .hardening import (
    REQUIRED_FEATURES,
    WEB_SEARCH_MODES,
    HardeningConfig,
    effective_config_mismatches,
    prepare_codex_home,
)
from .protocol import AppServerClient


def probe(*, web_search: str, codex_bin: Path, home: Path) -> dict[str, Any]:
    """Zapisz profil, wystartuj app-server, przeczytaj konfigurację efektywną."""
    prepare_codex_home(home, HardeningConfig(web_search=web_search))
    # `with` robi PEŁNY handshake (start + initialize + notyfikacja), tak samo jak sesje.
    # Własne `initialize` byłoby drugim handshakem, a na to prawdziwy app-server odpowiada
    # -32600 „Already initialized". Zmierzone na żywo, zanim ten komentarz powstał.
    client = AppServerClient(codex_bin=codex_bin, codex_home=home)
    with client:
        answer = client.request("config/read", {})
    config = answer.get("config", answer)
    features = config.get("features") or {}
    return {
        "poproszono": web_search,
        "web_search": config.get("web_search"),
        "features": {key: features.get(key) for key in sorted(REQUIRED_FEATURES)},
        "niezgodnosci": effective_config_mismatches(answer, web_search=web_search),
    }


def _render(result: dict[str, Any]) -> str:
    lines = [
        f"poproszono o web_search = {result['poproszono']!r}",
        f"silnik zgłasza  web_search = {result['web_search']!r}",
        "flagi [features] (mają być false):",
    ]
    lines += [f"  {key} = {value!r}" for key, value in result["features"].items()]
    if result["niezgodnosci"]:
        lines.append("NIEZGODNOŚCI:")
        lines += [f"  {one}" for one in result["niezgodnosci"]]
    else:
        lines.append("bez niezgodności: silnik ma dokładnie to, o co poproszono")
    return "\n".join(lines)


#: Nazwa serwera narzędzi, ta sama co w produkcie. Komenda jest UMYŚLNIE nieistniejąca: sonda
#: mierzy, czy binarka CZYTA wpis, a nie czy nasz serwer wstaje. Żeby to drugie sprawdzić,
#: trzeba by bazy i pliku zdolności, czyli całego produktu.
_DEAD_COMMAND = "/nonexistent/wenext-probe-mcp"
_CAPABILITY_PATH = "/tmp/wenext-probe-capability.json"

#: Wartość w jednej linii, dokładnie tak, jak wyglądałaby w wywołaniu tury: tabela z mapą `env`
#: w środku. To jest ta składnia, o którą pyta plan E9.
_TABLE = (
    "mcp_servers.wenext={"
    f'command="{_DEAD_COMMAND}",'
    'args=["-m","control_plane.tools.mcp_server"],'
    f'env={{WENEXT_SESSION_CAPABILITY_FILE="{_CAPABILITY_PATH}"}}'
    "}"
)

#: Ta sama tabela, ale z `required = true`: serwer, którego nie da się wstać, ma wtedy wywrócić
#: handshake, i to jest jedyny dowód, że wpis został NAPRAWDĘ użyty, a nie tylko przyjęty.
_REQUIRED_TABLE = _TABLE.replace("{command=", "{required=true,command=")

#: Szósty przypadek: DOKŁADNIE ten zestaw argumentów, który poleci w turze. Budujemy go tym samym
#: rendererem co produkt (`turn_config_overrides`), bo pomiar ma dotyczyć napisu, który wysyłamy,
#: a nie jego podobnego kuzyna napisanego ręcznie w sondzie.
def _production_shaped() -> tuple[str, ...]:
    from .hardening import HardeningConfig, McpServerConfig, turn_config_overrides

    return turn_config_overrides(
        HardeningConfig(
            web_search="live",
            mcp_servers=(
                McpServerConfig(
                    name="wenext",
                    command=sys.executable,
                    args=("-m", "codex_runtime.probe_mcp_stub"),
                    # Atrapa wystawia jedno narzędzie i tylko ono ma prawo przejść: gdyby
                    # `enabled_tools` było tu szersze, pomiar nie sprawdziłby, czy lista działa.
                    enabled_tools=("probe_echo",),
                    env={"WENEXT_SESSION_CAPABILITY_FILE": _MARKER},
                ),
            ),
        )
    )


#: Piąty przypadek: serwer, który NAPRAWDĘ wstaje. Komenda to ten sam interpreter, co sondy, więc
#: atrapa na pewno się zaimportuje, a `env` niesie wartość, którą atrapa odda w opisie narzędzia.
_MARKER = "/tmp/wenext-probe-zdolnosc-XYZ.json"
_LIVE_TABLE = (
    "mcp_servers.wenext={required=true,"
    f'command="{sys.executable}",'
    'args=["-m","codex_runtime.probe_mcp_stub"],'
    f'env={{WENEXT_SESSION_CAPABILITY_FILE="{_MARKER}"}}'
    "}"
)

#: Druga składnia na wypadek, gdyby pierwsza nie przeszła: osobny `--config` na każdy klucz.
_SEPARATE_KEYS = (
    f'mcp_servers.wenext.command="{_DEAD_COMMAND}"',
    'mcp_servers.wenext.args=["-m","control_plane.tools.mcp_server"]',
    f'mcp_servers.wenext.env.WENEXT_SESSION_CAPABILITY_FILE="{_CAPABILITY_PATH}"',
)


def _one_case(
    *, name: str, overrides: tuple[str, ...], codex_bin: Path, home: Path
) -> dict[str, Any]:
    """Jeden start app-servera z podanymi `--config`. Zwraca FAKTY, nie ocenę.

    Wynik ma trzy pola, bo trzy rzeczy mogą się zdarzyć osobno: proces może nie wstać (binarka
    odrzuciła składnię), handshake może paść (wpis został przeczytany i serwer jest wymagany),
    a `config/read` może wpis pokazać albo nie (to zależy od tego, co odpowiada silnik, i tego
    też nie zakładamy).
    """
    prepare_codex_home(home, HardeningConfig())
    client = AppServerClient(codex_bin=codex_bin, codex_home=home, config_overrides=overrides)
    result: dict[str, Any] = {"name": name, "overrides": list(overrides)}
    try:
        with client:
            answer = client.request("config/read", {})
            # DRUGI ODCZYT I DOPIERO ON ROZSTRZYGA. `config/read` mówi, co silnik PRZECZYTAŁ,
            # a `mcpServerStatus/list` mówi, co naprawdę URUCHOMIŁ. Pierwszy pomiar 21.09
            # pokazał, że app-server wstaje mimo `required = true` przy martwej komendzie, więc
            # sam handshake niczego o użyciu wpisu nie dowodzi.
            try:
                status = client.request("mcpServerStatus/list", {})
            except Exception as exc:
                status = {"blad": f"{type(exc).__name__}: {exc}"[:300]}
    except Exception as exc:
        result["wstalo"] = False
        result["blad"] = f"{type(exc).__name__}: {exc}"[:400]
        result["stderr"] = client.stderr[-6:]
        return result
    config = answer.get("config", answer)
    servers = config.get("mcp_servers")
    result["wstalo"] = True
    result["mcp_servers_w_odczycie"] = servers
    result["klucze_odczytu"] = sorted(config)
    # Nadpisania mają NIEŚĆ CAŁĄ KONFIGURACJĘ TURY, nie tylko serwer, więc sonda czyta też te
    # dwie rzeczy: `web_search` z profilu sesji i flagi, które dają modelowi powłokę.
    features = config.get("features") or {}
    result["top_i_flagi"] = {
        "web_search": config.get("web_search"),
        **{key: features.get(key) for key in sorted(REQUIRED_FEATURES)},
    }
    result["status_serwerow"] = status
    return result


def _render_mcp(cases: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for case in cases:
        stan = "wstało" if case["wstalo"] else "NIE wstało"
        lines.append(f"[{case['name']}] {stan}")
        for override in case["overrides"]:
            lines.append(f"    --config {override}")
        if case["wstalo"]:
            lines.append(f"    mcp_servers z config/read: {case['mcp_servers_w_odczycie']!r}")
            lines.append(f"    mcpServerStatus/list:      {case['status_serwerow']!r}")
            lines.append(f"    web_search i [features]:   {case['top_i_flagi']!r}")
        else:
            lines.append(f"    błąd: {case['blad']}")
            for line in case.get("stderr", []):
                lines.append(f"    stderr: {line}")
    return "\n".join(lines)


def _marker_came_back(case: dict[str, Any]) -> bool:
    """Czy w opisie narzędzi widać wartość, którą podaliśmy przez mapę `env`.

    Szukamy po CAŁEJ odpowiedzi zamienionej na tekst, bo kształt `mcpServerStatus/list` jest
    cudzy i może się zmienić; znacznik jest na tyle osobliwy, że przypadkowe trafienie nie grozi.
    """
    if not case.get("wstalo"):
        return False
    return _MARKER in json.dumps(case.get("status_serwerow"), ensure_ascii=False)


def probe_mcp_overrides(*, codex_bin: Path, home_root: Path) -> list[dict[str, Any]]:
    """Cztery przypadki, od kontroli do rozstrzygnięcia.

    1. bez `--config`: kontrola, że w tym środowisku app-server w ogóle wstaje;
    2. tabela w jednej wartości: składnia z planu E9;
    3. osobne klucze: składnia zapasowa;
    4. tabela z `required=true` i martwą komendą: pokazuje, czy silnik w ogóle PRÓBUJE wstać
       z tym wpisem. Uwaga z pomiaru 21.09: app-server wstaje mimo `required`, więc sam handshake
       nie jest dowodem i trzeba czytać `mcpServerStatus/list`;
    5. tabela z ŻYWĄ atrapą serwera (`probe_mcp_stub`): TO ROZSTRZYGA. Atrapa oddaje w opisie
       swojego jedynego narzędzia wartość zmiennej, którą dostała przez mapę `env`. Jeśli ta
       wartość wróci tutaj, wpis został użyty razem ze środowiskiem, czyli osobna zdolność na
       turę jest tą drogą wykonalna.
    """
    cases = []
    for name, overrides in (
        ("bez-nadpisan", ()),
        ("tabela-w-jednej-wartosci", (_TABLE,)),
        ("osobne-klucze", _SEPARATE_KEYS),
        ("tabela-required-martwa-komenda", (_REQUIRED_TABLE,)),
        ("tabela-zywy-serwer", (_LIVE_TABLE,)),
        ("zestaw-produkcyjny", _production_shaped()),
    ):
        home = Path(tempfile.mkdtemp(prefix="wenext-mcp-probe-", dir=home_root))
        try:
            cases.append(
                _one_case(name=name, overrides=overrides, codex_bin=codex_bin, home=home)
            )
        finally:
            shutil.rmtree(home, ignore_errors=True)
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-search", default="live", choices=sorted(WEB_SEARCH_MODES))
    parser.add_argument(
        "--codex-bin",
        type=Path,
        default=None,
        help="ścieżka do binarki; domyślnie ta z przypiętego pakietu",
    )
    parser.add_argument(
        "--json", action="store_true", help="wypisz wynik jako JSON zamiast tekstu dla człowieka"
    )
    parser.add_argument(
        "--mcp-overrides",
        action="store_true",
        help="zmierz, czy wpis serwera narzędzi da się podać przez --config zamiast pisać do domu",
    )
    args = parser.parse_args(argv)

    codex_bin = args.codex_bin
    if codex_bin is None:
        from .account_adapter import find_codex_bin

        codex_bin = find_codex_bin()

    if args.mcp_overrides:
        root = Path(tempfile.mkdtemp(prefix="wenext-mcp-probe-root-"))
        try:
            cases = probe_mcp_overrides(codex_bin=codex_bin, home_root=root)
        except Exception as exc:
            print(f"sonda nadpisań: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            return 2
        finally:
            shutil.rmtree(root, ignore_errors=True)
        print(json.dumps(cases, ensure_ascii=False, indent=2) if args.json else _render_mcp(cases))
        # 0 znaczy „wpis użyty razem ze środowiskiem": w przypadku z żywą atrapą wśród narzędzi
        # widać opis ze ZNACZNIKIEM, który podaliśmy przez mapę `env`. Każdy inny wynik to
        # odpowiedź „nie tą drogą" i ma być widoczny w kodzie wyjścia, a nie tylko w tekście.
        return 0 if _marker_came_back(cases[-1]) else 1

    home = Path(tempfile.mkdtemp(prefix="wenext-config-probe-"))
    try:
        result = probe(web_search=args.web_search, codex_bin=codex_bin, home=home)
    except Exception as exc:
        print(f"sonda konfiguracji: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        shutil.rmtree(home, ignore_errors=True)

    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else _render(result))
    # Kod wyjścia niesie ODPOWIEDŹ, nie tylko powodzenie uruchomienia: 0 znaczy „silnik ma to,
    # o co poproszono", 1 znaczy „uruchomiło się, ale wartość jest inna". Bez tego rozróżnienia
    # pomiar w skrypcie wyglądałby na udany za każdym razem.
    return 0 if not result["niezgodnosci"] else 1


if __name__ == "__main__":  # pragma: no cover - wejście modułu
    raise SystemExit(main())
