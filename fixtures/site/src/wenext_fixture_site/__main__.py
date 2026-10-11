"""Starting the server. `python -m wenext_fixture_site` or `wenext-fixture-site`."""

from __future__ import annotations

import logging

import uvicorn

from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("wenext.fixture").info(
        "start bind=%s:%s seed=%s", settings.bind_host, settings.bind_port, settings.seed
    )
    uvicorn.run(
        "wenext_fixture_site.app:app",
        host=settings.bind_host,
        port=settings.bind_port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
