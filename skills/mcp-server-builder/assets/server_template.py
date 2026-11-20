"""Minimal, production-shaped MCP server template (mcp-server-builder skill).

Python MCP SDK v2 (`mcp>=2.3`). Copy into your project as `server.py`, then:

    uv add "mcp[cli]>=2.3"
    uv run mcp dev server.py                                 # MCP Inspector
    uv run server.py                                         # stdio (default)
    uv run server.py --http                                  # streamable HTTP on 127.0.0.1:8000/mcp

Replace the in-memory NOTES store with your real backend. Keep tools few, typed,
read-only by default, and make every error the model should see a ToolError.
"""

from __future__ import annotations

import logging
import sys
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from pydantic import BaseModel, Field

# stdio servers speak JSON-RPC on stdout: anything else printed there corrupts the stream.
logging.basicConfig(
    stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("notes-server")

mcp = MCPServer(
    "notes",
    instructions=(
        "Read-only access to the team's engineering notes. "
        "Use search_notes to find candidates, then get_note for full text. Cite note IDs."
    ),
)

NOTES: dict[str, dict[str, str]] = {
    "NOTE-001": {"title": "Release checklist", "body": "Run evals, bump version, tag, publish."},
    "NOTE-002": {
        "title": "On-call runbook",
        "body": "Check dashboards, then traces, then recent deploys.",
    },
    "NOTE-003": {
        "title": "Eval policy",
        "body": "Every prompt change runs the regression eval set.",
    },
}


class NoteHit(BaseModel):
    note_id: str = Field(description="Stable ID to pass to get_note, e.g. NOTE-001.")
    title: str
    snippet: str = Field(description="First 80 characters of the note body.")


class SearchResult(BaseModel):
    query: str
    hits: list[NoteHit]
    truncated: bool = Field(description="True if more matches exist than were returned.")


class Note(BaseModel):
    note_id: str
    title: str
    body: str


@mcp.tool()
def search_notes(
    query: Annotated[str, Field(min_length=2, max_length=200, description="Keywords to match.")],
    limit: Annotated[int, Field(ge=1, le=20, description="Maximum hits to return.")] = 5,
) -> SearchResult:
    """Search engineering notes by keyword in title or body.

    Use this first to find relevant notes; it returns IDs and short snippets, not full text.
    """
    q = query.lower()
    matches = [
        NoteHit(note_id=nid, title=n["title"], snippet=n["body"][:80])
        for nid, n in NOTES.items()
        if q in n["title"].lower() or q in n["body"].lower()
    ]
    log.info("search_notes query=%r hits=%d", query, len(matches))
    return SearchResult(query=query, hits=matches[:limit], truncated=len(matches) > limit)


@mcp.tool()
def get_note(
    note_id: Annotated[str, Field(pattern=r"^NOTE-\d{3}$", description="ID from search_notes.")],
) -> Note:
    """Fetch the full text of one note by ID."""
    note = NOTES.get(note_id)
    if note is None:
        # ToolError messages reach the model; any other exception is hidden behind a generic error.
        raise ToolError(f"No note {note_id}. Call search_notes first to get valid IDs.")
    return Note(note_id=note_id, **note)


@mcp.resource("notes://{note_id}")
def note_resource(note_id: str) -> str:
    """A note as plain text, for clients that attach resources as context."""
    note = NOTES.get(note_id)
    if note is None:
        raise ResourceNotFoundError(f"No note {note_id}.")
    return f"# {note['title']}\n\n{note['body']}"


@mcp.prompt()
def summarize_note(note_id: str) -> str:
    """Prompt template: summarize a note for a new teammate."""
    return f"Use get_note to read {note_id}, then summarize it in three bullets for a new teammate."


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
    else:
        mcp.run()
