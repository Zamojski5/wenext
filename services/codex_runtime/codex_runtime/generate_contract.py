"""Regeneration of the protocol contract from the PINNED `codex` binary.

    python -m codex_runtime.generate_contract

The online documentation (learn.chatgpt.com/docs/app-server.md) describes the `main` branch,
and not the version we deploy. The only trustworthy contract is an artifact generated
from the same binary that will go to production — hence this script and hence
`contract/app-server-methods.json` in the repo.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .account_adapter import find_codex_bin
from .protocol import CONTRACT_DIR

#: The schema fragments we keep in the repo. The full bundle is over 600 kB and changes
#: on every CLI bump — we commit what is the real input of the tests.
COPIED_SCHEMAS = (
    ("v1", "InitializeParams.json"),
    ("v1", "InitializeResponse.json"),
    ("v2", "ModelListParams.json"),
    ("v2", "ModelListResponse.json"),
    ("v2", "GetAccountParams.json"),
    ("v2", "GetAccountResponse.json"),
    ("v2", "GetAccountRateLimitsResponse.json"),
    ("v2", "LoginAccountParams.json"),
    ("v2", "LoginAccountResponse.json"),
    ("", "ClientNotification.json"),
)


def _methods(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    names = set()
    for variant in data["oneOf"]:
        field = variant["properties"]["method"]
        names.add(field.get("const") or field["enum"][0])
    return sorted(names)


def generate(target_dir: Path | None = None) -> Path:
    codex_bin = find_codex_bin()
    target = target_dir or CONTRACT_DIR
    target.mkdir(parents=True, exist_ok=True)

    version = subprocess.run(
        [str(codex_bin), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    sha = hashlib.sha256(codex_bin.read_bytes()).hexdigest()

    with tempfile.TemporaryDirectory() as tmp:
        stable_dir = Path(tmp) / "stable"
        experimental_dir = Path(tmp) / "exp"
        subprocess.run(
            [str(codex_bin), "app-server", "generate-json-schema", "--out", str(stable_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                str(codex_bin),
                "app-server",
                "generate-json-schema",
                "--experimental",
                "--out",
                str(experimental_dir),
            ],
            check=True,
            capture_output=True,
        )

        stab = _methods(stable_dir / "ClientRequest.json")
        exp = _methods(experimental_dir / "ClientRequest.json")
        notif = _methods(stable_dir / "ClientNotification.json")

        for subdir, file_path in COPIED_SCHEMAS:
            source = stable_dir / subdir / file_path if subdir else stable_dir / file_path
            (target / file_path).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    doc = {
        "_opis": (
            "Kontrakt protokołu app-server wygenerowany z PRZYPIĘTEJ binarki, "
            "nie przepisany z dokumentacji online."
        ),
        "_jak_odtworzyc": "python -m codex_runtime.generate_contract",
        "wygenerowano_z": {
            "codex_version": version,
            "pakiet_pypi": "openai-codex==0.147.0 -> openai-codex-cli-bin==0.147.0",
            "sha256_binarki": sha,
            "data": datetime.now(UTC).date().isoformat(),
        },
        "client_requests_stabilne": stab,
        "client_requests_tylko_experimental": sorted(set(exp) - set(stab)),
        "client_notifications": notif,
    }
    file_path = target / "app-server-methods.json"
    file_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return file_path


if __name__ == "__main__":
    result = generate()
    print(f"zapisano {result}")  # this is a CLI
    sys.exit(0)
