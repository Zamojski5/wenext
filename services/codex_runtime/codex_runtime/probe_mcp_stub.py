"""Najmniejszy możliwy serwer MCP po stdio. Istnieje wyłącznie dla sondy E9.

PO CO. Sonda `config_probe --mcp-overrides` pyta, czy wpis serwera narzędzi podany binarce przez
`--config` jest naprawdę UŻYWANY, a nie tylko przeczytany. Martwa komenda odpowiada na to tylko
w połowie: serwer pojawia się na liście, ale bez narzędzi, bo nie miał jak wstać. Ta atrapa
domyka pomiar: wstaje, ogłasza JEDNO narzędzie, a w jego opisie umieszcza wartość zmiennej
środowiskowej, którą dostała od silnika. Jeżeli ta wartość dojedzie z powrotem do sondy, to
znaczy, że mapa `env` z jednej wartości `--config` dotarła do PROCESU POTOMNEGO, czyli że
osobna zdolność na turę jest tą drogą wykonalna.

Czego ta atrapa NIE robi: nie dotyka bazy, nie czyta pliku zdolności, nie ma żadnego narzędzia,
które cokolwiek zmienia. Jedno narzędzie, które niczego nie wykonuje, wystarczy do pomiaru.
"""

from __future__ import annotations

import json
import os
import sys

#: Nazwa zmiennej, którą sonda wstrzykuje przez mapę `env`. Ta sama, której używa produkt, żeby
#: pomiar dotyczył tej samej drogi, a nie zmiennej wymyślonej na potrzeby testu.
ENV_NAME = "WENEXT_SESSION_CAPABILITY_FILE"

#: Nazwa narzędzia ogłaszanego silnikowi. Krótka i bez znaczenia produktowego.
TOOL_NAME = "probe_echo"


def _send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    seen_value = os.environ.get(ENV_NAME, "<brak>")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = frame.get("method")
        request_id = frame.get("id")
        if request_id is None:  # notyfikacja, nic nie odpowiadamy
            continue
        if method == "initialize":
            params = frame.get("params") or {}
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        # Wersję protokołu ODBIJAMY, zamiast wpisywać swoją: atrapa ma się
                        # dogadać z silnikiem, a nie narzucać mu kontrakt.
                        "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "wenext-probe-stub", "version": "0.1.0"},
                    },
                }
            )
        elif method == "tools/list":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": TOOL_NAME,
                                # TU JEST CAŁY POMIAR: opis niesie to, co proces potomny
                                # zobaczył w swoim środowisku.
                                "description": f"{ENV_NAME}={seen_value}",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ]
                    },
                }
            )
        else:
            _send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    return 0


if __name__ == "__main__":  # pragma: no cover - wejście modułu
    raise SystemExit(main())
