"""In-memory tests for server_template.py (mcp-server-builder skill).

Run: uv run --with "mcp>=2.3" --with pytest pytest -q test_server_template.py
`Client(server)` connects in process: no subprocess, no network, fast and deterministic.
"""

from __future__ import annotations

import pytest
from mcp import Client
from server_template import mcp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_lists_expected_tools() -> None:
    async with Client(mcp) as client:
        listing = await client.list_tools()
        names = {tool.name for tool in listing.tools}
        assert names == {"search_notes", "get_note"}
        search = next(t for t in listing.tools if t.name == "search_notes")
        assert search.description
        assert "query" in search.input_schema["properties"]


async def test_search_returns_structured_hits() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("search_notes", {"query": "eval", "limit": 1})
        assert not result.is_error
        data = result.structured_content
        assert data["hits"][0]["note_id"] in {"NOTE-001", "NOTE-003"}
        assert data["truncated"] is True


async def test_unknown_note_is_a_readable_tool_error() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("get_note", {"note_id": "NOTE-999"})
        assert result.is_error
        assert "search_notes" in result.content[0].text


async def test_note_resource_reads_as_text() -> None:
    async with Client(mcp) as client:
        result = await client.read_resource("notes://NOTE-002")
        assert "On-call runbook" in result.contents[0].text


async def test_invalid_arguments_are_rejected() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("search_notes", {"query": "x"})
        assert result.is_error
