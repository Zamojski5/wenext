"""A research history larger than 64 KiB must survive resuming its engine thread."""

import asyncio
import sys

from codex_runtime.session_client import AsyncAppServerClient


def test_resume_reads_large_history_and_keeps_the_transport_alive(tmp_path):
    asyncio.run(_exercise(tmp_path))


async def _exercise(tmp_path):
    executable = tmp_path / "app-server"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """
import json,sys
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request: continue
    result={'thread': {'id': 'same-thread', 'turns': [{'text': 'x' * 300000}]}} if request['method']=='thread/resume' else {}
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
"""
    )
    executable.chmod(0o700)

    async def sink(*_):
        pass

    client = AsyncAppServerClient(codex_bin=executable, codex_home=tmp_path, sink=sink)
    try:
        await client.start()
        await client.initialize()
        assert await client.resume_thread("same-thread") == "same-thread"
        # The next request must still be read, rather than timing out on a dead reader.
        assert await client.read_config() == {}
    finally:
        await client.close()
