---
name: mcp-server-builder
description: Build, test, secure, and ship Model Context Protocol (MCP) servers in Python with the official MCP SDK v2 (MCPServer), covering tool and resource design for LLMs, typed arguments and structured output, ToolError handling, in-memory tests with Client, stdio and streamable HTTP transports, client setup for Claude Code, Cursor, and Codex, and a security checklist. Includes a tested server template. Use when creating an MCP server, exposing an internal API or dataset to AI agents, migrating a FastMCP v1 server to SDK v2, or reviewing an MCP server for tool design or security issues.
license: MIT
compatibility: Python 3.10+, mcp>=2.3 (Python SDK v2). Concepts apply to the TypeScript SDK.
metadata:
  author: kaushik-holla
  version: "1.0"
---

# MCP Server Builder

An MCP server is an API whose client is a language model. Design it the way you would onboard a new teammate to an internal system: few, well-named capabilities, clear descriptions of when to use each, typed inputs, concise outputs, and errors that tell the reader what to do next.

## When To Use

- Exposing an internal API, database, file store, or workflow to Claude Code, Cursor, Codex, or any MCP client.
- Wrapping a LangGraph agent's tools so other agents can reuse them (pairs with `langgraph-patterns` section 12).
- Upgrading a `FastMCP` (SDK v1) server to SDK v2.
- Reviewing an MCP server for tool design, reliability, or security.

## Workflow

### 1. Design the surface before writing code

Write down, for each capability: who calls it, when the model should choose it, inputs, output shape, side effects, and failure modes.

| Primitive | Use for | Controlled by |
| --- | --- | --- |
| **Tool** | Actions and queries the model decides to call (`search_orders`, `create_ticket`) | Model |
| **Resource** | Read-only content the client/user attaches as context (`notes://NOTE-001`) | Application / user |
| **Prompt** | Reusable, parameterized prompt templates the user picks (`/summarize_note`) | User |

Tool design rules:

- **5-15 tools, not 50.** Overlapping tools cause wrong-tool calls. Prefer workflow-level tools (`get_customer_overview`) over one tool per REST endpoint.
- **Name tools `verb_noun`** and write descriptions that say when to use them and what they return. Descriptions are prompts.
- **Typed, constrained arguments** (`Annotated[int, Field(ge=1, le=20)]`, `Literal[...]`, regex patterns). The SDK builds the input schema from type hints.
- **Return structured output** (Pydantic models) with IDs the model can pass to the next tool. Paginate and truncate; never dump 10 MB of JSON into context.
- **Read-only by default.** Separate destructive tools, name them plainly (`delete_ticket`), and require confirmation at the client.
- **Errors are instructions.** Raise `ToolError("No order 1234. Order IDs look like ORD-123456.")`; only `ToolError` messages reach the model, other exceptions become a generic error with the traceback in your logs.

### 2. Scaffold with uv

```bash
uv init --package notes-mcp && cd notes-mcp
uv add "mcp[cli]>=2.3"
cp /path/to/this/skill/assets/server_template.py src/notes_mcp/server.py
uv run mcp dev src/notes_mcp/server.py         # opens the MCP Inspector
```

[assets/server_template.py](assets/server_template.py) is a complete, tested starting point: two tools with constrained arguments and Pydantic output, a `ToolError` path, a resource template, a prompt, stderr logging, and stdio or HTTP startup.

```python
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("notes", instructions="Read-only access to engineering notes. Cite note IDs.")


@mcp.tool()
def get_note(note_id: Annotated[str, Field(pattern=r"^NOTE-\d{3}$")]) -> Note:
    """Fetch the full text of one note by ID."""
    ...
```

SDK v2 notes worth knowing:

- Sync `def` tools run on a worker thread (no event-loop blocking); use `async def` for async I/O clients.
- Need a value from the user mid-call? Annotate a parameter with `Resolve(...)` and return `Elicit(...)` from the resolver; it works on both protocol eras. `ctx.elicit()` only works on legacy connections.
- `ctx.info()` and protocol-level logging are deprecated. Log with the `logging` module to **stderr**. On stdio, anything printed to stdout corrupts the JSON-RPC stream.
- Transport options (`host`, `port`, `stateless_http`, `transport_security`) go to `run()` or the app builder, not the `MCPServer(...)` constructor.
- The streamable HTTP lifespan runs once at startup and is shared across requests: good for connection pools, wrong for per-user state.

### 3. Test in memory

`Client(server)` connects in process: no subprocess, no ports, millisecond tests. See [assets/test_server_template.py](assets/test_server_template.py).

```python
from mcp import Client


async def test_unknown_note_is_a_readable_tool_error() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("get_note", {"note_id": "NOTE-999"})
        assert result.is_error
        assert "search_notes" in result.content[0].text
```

```bash
cd assets && uv run --with "mcp>=2.3" --with pytest pytest -q test_server_template.py
```

Test: tool listing and descriptions, happy path `structured_content`, every `ToolError` path, argument validation, and authorization (a caller without access gets an error, not data). Then try it with a real model in the Inspector or a client and read the transcript: wrong-tool calls mean your names or descriptions need work.

### 4. Run and connect clients

Local stdio (simplest, single user):

```bash
# Claude Code
claude mcp add notes -- uv run --directory /abs/path/notes-mcp src/notes_mcp/server.py

# Codex
codex mcp add notes -- uv run --directory /abs/path/notes-mcp src/notes_mcp/server.py
```

Cursor (`.cursor/mcp.json` in the project, or `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "notes": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/notes-mcp", "src/notes_mcp/server.py"]
    }
  }
}
```

Codex config equivalent (`~/.codex/config.toml`):

```toml
[mcp_servers.notes]
command = "uv"
args = ["run", "--directory", "/abs/path/notes-mcp", "src/notes_mcp/server.py"]
```

Remote / shared (streamable HTTP at `/mcp`):

```bash
uv run src/notes_mcp/server.py --http                       # 127.0.0.1:8000/mcp
claude mcp add --transport http notes http://127.0.0.1:8000/mcp
```

For Cursor use `{"url": "http://127.0.0.1:8000/mcp"}`; for Codex use `url = "http://127.0.0.1:8000/mcp"` under `[mcp_servers.notes]`. Production HTTP servers sit behind TLS and authentication (OAuth 2.1 per the MCP authorization spec).

### 5. Migrating from FastMCP (SDK v1)

| v1 | v2 |
| --- | --- |
| `from mcp.server.fastmcp import FastMCP` | `from mcp.server import MCPServer` |
| `mcp.server.fastmcp.*` | `mcp.server.mcpserver.*` |
| `FastMCP("x", port=9000)` | `MCPServer("x")` + `mcp.run(transport="streamable-http", port=9000)` |
| `get_context()` / `ctx.fastmcp` | declare `ctx: Context` / `ctx.mcp_server` |
| `McpError` | `MCPError` (protocol error, not shown to the model) |
| `ClientSession` + transport + `initialize()` | `async with Client(url_or_server) as client` |
| `result.isError`, `structuredContent` | `result.is_error`, `result.structured_content` |
| `ctx.elicit()` | `Resolve(...)` + `Elicit(...)` |

Run the test suite after each step; the import errors are loud, the behavior changes (error visibility, lifespan scope, sync tools on threads) are not.

## Security Checklist

- [ ] **Least privilege**: the server's credentials can do only what its tools need; read-only tools use read-only credentials
- [ ] **Authorization per call**: check the caller's identity and permissions inside each tool, not only at connect time
- [ ] **No token passthrough**: never forward the client's token to downstream APIs; use the server's own scoped credentials or token exchange
- [ ] **Input validation**: constrained types, allowlisted paths (resolve and check against a root to block `../`), parameterized SQL, no `shell=True`
- [ ] **Network**: bind to `127.0.0.1` for local HTTP; keep DNS-rebinding / Host-header protection (`transport_security`) on; TLS and auth for anything remote
- [ ] **Output hygiene**: tool results are untrusted text to the model; strip or label content that could carry injected instructions; never return secrets
- [ ] **Stable tool definitions**: pin and review tool names and descriptions in tests so a dependency update cannot silently change what the model is told (tool poisoning / rug pulls)
- [ ] **Destructive actions**: separate tools, clear names, client-side confirmation, idempotency keys, audit log
- [ ] **Limits**: timeouts, pagination, max result sizes, and rate limits per caller
- [ ] **Supply chain**: lockfile committed, `uvx pip-audit` in CI, pinned SDK major version

For a full review, run the `security-auditor` agent; it covers OWASP LLM and Agentic Top 10 items such as excessive agency and tool misuse.

## Common Mistakes

- `print()` debugging in a stdio server (breaks the protocol). Log to stderr.
- One tool per REST endpoint, so the model has to orchestrate 6 calls for one question.
- Returning raw upstream errors or stack traces; the model cannot act on them and they may leak internals.
- Unbounded results (entire tables, whole files) that blow the context window.
- Testing only through a live client; use `Client(server)` in pytest for fast, deterministic coverage.
- Exposing a remote HTTP server on `0.0.0.0` without auth "just for the demo."

## References

- MCP specification: <https://modelcontextprotocol.io/specification>
- MCP Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- MCP authorization: <https://modelcontextprotocol.io/specification/latest/basic/authorization>
- MCP security best practices: <https://modelcontextprotocol.io/specification/latest/basic/security_best_practices>
- Anthropic, "Writing effective tools for agents": <https://www.anthropic.com/engineering/writing-tools-for-agents>
