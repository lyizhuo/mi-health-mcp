import asyncio
import socket
import threading
from datetime import datetime

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from mi_health.api.app import create_app
from mi_health.mcp_server import create_mcp
from mi_health.models import DailyActivity
from mi_health.services import QueryService
from tests.conftest import make_key


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_mcp_tools_over_streamable_http(ctx):
    settings, db, store = ctx
    token = make_key(db, "read")
    db.upsert_daily([
        DailyActivity(
            id="d1", provider="mi_fitness", source_type="cloud_session", user_id="u1",
            date="2026-11-03", steps=1234, distance_m=1000, active_kcal=50,
            collected_at=datetime(2026, 11, 3, 12, 0),
        )
    ])
    settings = settings.__class__(**{**settings.__dict__, "allowed_hosts": ["127.0.0.1", "127.0.0.1:*", "testserver"]})
    service = QueryService(db, store, settings)
    mcp = create_mcp(service, settings.allowed_hosts)
    app = create_app(settings, db, store, mcp)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started

    try:
        async with (
            streamablehttp_client(
                f"http://127.0.0.1:{port}/mcp/", headers={"Authorization": f"Bearer {token}"}
            ) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 14
            result = await session.call_tool(
                "get_daily_summary",
                {"start_date": "2026-11-03", "end_date": "2026-11-03"},
            )
            assert not result.isError
            assert "1234" in result.content[0].text
    finally:
        server.should_exit = True
        thread.join(timeout=5)
