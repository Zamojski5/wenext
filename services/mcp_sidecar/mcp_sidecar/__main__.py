"""`python -m mcp_sidecar` — entry point of the sidecar process."""

from __future__ import annotations

import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
