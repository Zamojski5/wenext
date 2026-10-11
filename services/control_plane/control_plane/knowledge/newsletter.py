"""Read-only MCP adapter for the public unknowNews archive.

Only a public topic query leaves the product, never its context or credentials. The remote
server's prompts/instructions are not installed. A shared, non-blocking file lock limits all
local MCP processes to eight requests/minute (provider limit: ten); project-scoped cached
results avoid repeated searches. The cache is ephemeral and never a source of project truth.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

ENDPOINT = "https://unknow.news/mcp/"
CACHE_TTL = 3600
MAX_REQUESTS = 8


class NewsletterUnavailable(Exception):
    pass


async def search_archive(
    query: str, limit: int, *, scope: str, client=None, cache_dir: Path | None = None
) -> dict[str, Any]:
    if not 3 <= len(query) <= 128 or type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Zapytanie: 3-128 znaków, limit: 1-10.")
    root = cache_dir or Path(tempfile.gettempdir()) / f"wenext-news-{os.getuid()}"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = hashlib.sha256(f"{scope}\n{query}\n{limit}".encode()).hexdigest()
    now = time.time()
    with (root / "state.json").open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise NewsletterUnavailable(
                "Archiwum obsługuje inne wyszukiwanie. Wróć później."
            ) from exc
        lock.seek(0)
        try:
            state = json.load(lock)
        except (ValueError, OSError):
            state = {}
        entries = {k: v for k, v in state.get("cache", {}).items() if v["at"] > now - CACHE_TTL}
        if key in entries:
            return {**entries[key]["result"], "cached": True}
        blocked_until = state.get("blocked_until", 0)
        if blocked_until > now:
            raise NewsletterUnavailable("Archiwum wymaga przerwy po limicie. Wróć później.")
        requests = [at for at in state.get("requests", []) if at > now - 60]
        if len(requests) >= MAX_REQUESTS:
            raise NewsletterUnavailable("Limit archiwum na tę minutę. Nie ponawiaj w pętli.")
        requests.append(now)

        # Reserve before the network call, including failed requests.
        def save() -> None:
            lock.seek(0)
            lock.truncate()
            json.dump(
                {"requests": requests, "cache": entries, "blocked_until": blocked_until},
                lock,
                ensure_ascii=False,
            )
            lock.flush()

        save()
        owned = client is None
        client = client or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
        try:
            response = await client.post(
                "https://unknow.news/mcp/",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "search_unknownews",
                        "arguments": {"query": query, "limit": limit},
                    },
                },
            )
            if response.status_code == 429:
                try:
                    delay = int(response.headers.get("retry-after", "60"))
                except ValueError:
                    delay = 60
                blocked_until = time.time() + min(3600, max(60, delay))
                save()
                raise NewsletterUnavailable(
                    "Limit archiwum. Odczekaj co najmniej minutę; nie ponawiaj w pętli."
                )
            response.raise_for_status()
            if len(response.content) > 1_000_000:
                raise NewsletterUnavailable("Archiwum zwróciło zbyt dużą odpowiedź.")
            envelope = response.json()
            result = envelope.get("result", {})
            if envelope.get("error") or result.get("isError"):
                raise NewsletterUnavailable("Archiwum nie wykonało wyszukiwania. Spróbuj później.")
            data = result.get("structuredContent")
            if not isinstance(data, dict):
                data = next(
                    (
                        json.loads(block["text"])
                        for block in result.get("content", [])
                        if block.get("type") == "text"
                    ),
                    {},
                )
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise NewsletterUnavailable("Archiwum zwróciło nierozpoznany format.")
            rows = []
            for entry in data["results"][:limit]:
                if not isinstance(entry, dict) or not all(
                    isinstance(entry.get(k), str) for k in ("title", "url", "description")
                ):
                    raise NewsletterUnavailable("Archiwum zwróciło niepełne dane linku.")
                rows.append(
                    {
                        k: entry[k][:6000]
                        for k in ("title", "url", "description", "issue_date", "note")
                        if isinstance(entry.get(k), str)
                    }
                )
            answer = {
                "provider": "unknow.news",
                "retrieved_at": now,
                "results": rows,
                "cached": False,
            }
            # Keep bounded metadata; no cross-project cache keys are exposed to the model.
            if len(entries) >= 128:
                entries.pop(min(entries, key=lambda k: entries[k]["at"]))
            entries[key] = {"at": now, "result": answer}
            save()
            return answer
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise NewsletterUnavailable(
                f"Nie udało się odczytać archiwum ({type(exc).__name__})."
            ) from exc
        finally:
            if owned:
                await client.aclose()
