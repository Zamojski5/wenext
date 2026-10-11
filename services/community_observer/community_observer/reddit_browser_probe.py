"""One real read of a subreddit THROUGH the capsule's Chrome, written down as an artifact.

    docker compose exec api python -m community_observer.reddit_browser_probe r/kubernetes

This is the live proof for D-025 (`docs/OUTCOMES.md` §5 row 3b). It runs where the reader is
reachable — inside the compose network — and does three things, in order: reports the session
(`/api/me.json`), lists the newest threads, fetches one thread with its comments. Every raw body is
saved next to the report so the offline tests can run on what Reddit REALLY sent to a real Chrome,
and the report carries the numbers and hashes a reader of `docs/OUTCOMES.md` §8 needs.

The exit code is 0 only when both reads succeeded. A partial run writes the report with the error
and exits 1: an artifact that says "failed at step 2" is evidence, an absent artifact is not.

Nothing in the database is touched — enabling a community is the founder's decision.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .reddit_browser import (
    ENV_READER_TOKEN,
    ENV_READER_URL,
    HttpBrowserReader,
    RedditBrowserSource,
    SessionState,
    require_login_from_env,
)
from .source import SourceError

ARTIFACT_DIR_NAME = "reddit-browser"
REPORT_NAME = "probe.json"


class _RecordingReader:
    """Wraps the HTTP reader and keeps every raw body, keyed by the URL path, for the fixtures."""

    def __init__(self, inner: HttpBrowserReader) -> None:
        self._inner = inner
        self.raw: dict[str, dict[str, Any]] = {}
        self.prepared: list[dict[str, Any]] = []

    async def prepare(self) -> Mapping[str, Any]:
        """Delegates D-026 to the real reader — and records what the capsule said.

        Measured 2026-09-04 02:58 UTC: a wrapper WITHOUT this method made the adapter think the
        reader had no mode surface, the capsule stayed HEADLESS and Reddit answered 403 while the
        production wiring (the bare `HttpBrowserReader`) would have switched. A probe that behaves
        differently from production measures the probe, not the system.
        """
        state = await self._inner.prepare()
        self.prepared.append(dict(state))
        return state

    async def read(self, url: str):
        page = await self._inner.read(url)
        self.raw[url] = {
            "status": page.status,
            "final_url": page.final_url,
            "content_type": page.content_type,
            "body": page.body,
        }
        return page

    async def aclose(self) -> None:
        await self._inner.aclose()


def _session_json(state: SessionState) -> dict[str, Any]:
    return {
        "zalogowany": state.logged_in,
        "konto": state.username,
        "sprawdzono": state.checked_at.isoformat(),
    }


async def run_probe(
    community_key: str,
    *,
    reader_url: str,
    token: str,
    artifacts_dir: Path,
    listing_limit: int,
    require_login: bool,
) -> int:
    started = datetime.now(UTC)
    inner = HttpBrowserReader(reader_url, token=token)
    recording = _RecordingReader(inner)
    # `require_login` is written into the report and defaults to the production setting
    # (`WENEXT_REDDIT_REQUIRE_LOGIN`), so the probe exercises the same login gate the dispatcher
    # does. The first probes (2026-09-03/04) ran with `False` on purpose — nobody could log the
    # capsule in yet; that is recorded in their artifacts.
    source = RedditBrowserSource(recording, require_login=require_login)
    report: dict[str, Any] = {
        "kontrola": "reddit_przez_przegladarke_kapsuly",
        "decyzja": "D-025",
        "rozpoczeto": started.isoformat(),
        "czytnik": reader_url,
        "community": community_key,
        "wymagane_logowanie": require_login,
        "kapsula_przed": None,
        "sesja": None,
        "listing": None,
        "watek": None,
        "blad": None,
        "zdane": False,
    }
    out_dir = artifacts_dir / ARTIFACT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        # The capsule's mode BEFORE the adapter touches it (D-026 review, point 5): the artifact
        # has to show the transition, not only the end state.
        report["kapsula_przed"] = dict(await inner.state())
        report["sesja"] = _session_json(await source.session_state())
        threads = await source.list_recent_threads(community_key, since=None, limit=listing_limit)
        report["listing"] = {
            "liczba_watkow": len(threads),
            "najstarszy_created_at": min((t.created_at for t in threads), default=None),
            "najnowszy_created_at": max((t.created_at for t in threads), default=None),
            "identyfikatory": [t.external_id for t in threads],
            "hashe_raw": [t.raw_sha256 for t in threads],
            "tytuly": [t.title[:80] for t in threads[:5]],
        }
        for key in ("najstarszy_created_at", "najnowszy_created_at"):
            value = report["listing"][key]
            report["listing"][key] = value.isoformat() if value is not None else None
        if not threads:
            raise SourceError(f"listing {community_key} zwrócił zero wątków — nie ma czego pobrać")
        chosen = threads[0]
        snapshot = await source.fetch_thread(community_key, chosen.external_id)
        report["watek"] = {
            "external_id": snapshot.thread.external_id,
            "url": snapshot.thread.url,
            "liczba_komentarzy": len(snapshot.comments),
            "drzewo_obciete": snapshot.comments_truncated,
            "num_comments_platformy": (
                snapshot.thread.metrics.comment_count.value
                if snapshot.thread.metrics.comment_count.is_known
                else None
            ),
            "hash_raw": snapshot.thread.raw_sha256,
        }
        report["zdane"] = True
    except SourceError as exc:
        report["blad"] = {"klasa": type(exc).__name__, "komunikat": str(exc)[:600]}
    except Exception as exc:
        report["blad"] = {"klasa": type(exc).__name__, "komunikat": str(exc)[:600]}
    finally:
        report["zakonczono"] = datetime.now(UTC).isoformat()
        for index, (url, raw) in enumerate(recording.raw.items()):
            path = out_dir / f"raw_{index}.json"
            path.write_text(json.dumps({"url": url, **raw}, ensure_ascii=False), encoding="utf-8")
        report["surowe_odpowiedzi"] = len(recording.raw)
        # D-026: what the capsule reported when the adapter asked it to be headful.
        report["kapsula_po_prepare"] = recording.prepared
        (out_dir / REPORT_NAME).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        await recording.aclose()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["zdane"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="jeden odczyt subreddita przez Chrome kapsuły")
    parser.add_argument("community_key", nargs="?", default="r/kubernetes", help="np. r/kubernetes")
    parser.add_argument("--reader-url", default=os.environ.get(ENV_READER_URL, ""))
    parser.add_argument("--token", default=os.environ.get(ENV_READER_TOKEN, ""))
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path(os.environ.get("WENEXT_ARTIFACTS_DIR", "/var/wenext/artifacts")),
    )
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument(
        "--require-login",
        dest="require_login",
        action=argparse.BooleanOptionalAction,
        default=require_login_from_env(os.environ),
        help="bramka logowania jak w produkcji (domyślnie z WENEXT_REDDIT_REQUIRE_LOGIN)",
    )
    args = parser.parse_args(argv)
    if not args.reader_url:
        print(f"brak adresu czytnika: ustaw {ENV_READER_URL} albo --reader-url", file=sys.stderr)
        return 2
    return asyncio.run(
        run_probe(
            args.community_key,
            reader_url=args.reader_url,
            token=args.token,
            artifacts_dir=args.artifacts,
            listing_limit=args.limit,
            require_login=args.require_login,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
