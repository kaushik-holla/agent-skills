---
name: langgraph-patterns
description: Canonical LangGraph 1.x patterns for typed State and reducers, conditional routers, bounded loops, interrupt/resume with Command, checkpointers (InMemory, SQLite, Postgres), runtime context for dependency injection, structured outputs, tool and MCP wiring, create_agent with middleware, subgraphs, Send fan-out, v2 streaming, retries, and LangSmith tracing, plus a common-mistakes catalog and testing patterns. Use when designing, implementing, debugging, or upgrading any LangGraph node, router, or graph, or when migrating off deprecated APIs such as create_react_agent. Reference; not a runbook.
license: MIT
metadata:
  author: kaushik-holla
  version: "2.0"
  langgraph: ">=1.2,<2"
---

# LangGraph Patterns

Canonical, copy-paste recipes for LangGraph >= 1.2 (verified against 1.2.x in November 2026). Every snippet here is a *minimal working pattern* you can extend; do not paste them into production verbatim without adapting names and types to the project.

> The snippets use a recurring **example** graph (a research assistant with clarity -> research -> validator -> synthesis nodes) purely to make the patterns concrete. Swap in your own domain. `myapp` is a placeholder for your package name.

## When To Use

- Designing a new graph (consult sections 1-6 first).
- Implementing a node, router, or interrupt (sections 3-9).
- Wiring models, tools, providers, or MCP servers (sections 10-13).
- Adding subgraphs, fan-out parallelism, streaming, retries, or tracing (sections 14-18).
- Debugging a graph that "feels off": [references/common-mistakes.md](references/common-mistakes.md).
- Writing tests against a graph: [references/testing.md](references/testing.md).

## 0. Install and Imports

Pin minimum versions in `pyproject.toml` (see the `python-quality` skill for the full template):

```toml
[project]
dependencies = [
  "langgraph>=1.2,<2",
  "langchain>=1.4,<2",          # init_chat_model, create_agent, langchain.mcp
  "langchain-openai>=1.0,<2",   # or langchain-anthropic, langchain-google-genai, ...
  "pydantic>=2.11",
]

[project.optional-dependencies]
sqlite = ["langgraph-checkpoint-sqlite>=2.0"]      # only if you persist via SqliteSaver
postgres = ["langgraph-checkpoint-postgres>=2.0"]  # production persistence
```

Imports cheat-sheet (all from the **public** API):

```python
from typing import Annotated, Any, Literal, TypedDict
import operator

from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, RetryPolicy, Send, interrupt
from langgraph.runtime import Runtime
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver  # optional extra
```

If an import path is not in the list above, double-check it; internal modules move between minor versions. `MemorySaver` still works as an alias, but `InMemorySaver` is the current name.

## 1. Typed State with `add_messages` Reducer

The standard `State` shape. The `Annotated[..., add_messages]` reducer is what enables append-style conversation history; without it, every node's return value **overwrites** prior messages.

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    clarity_status: Literal["clear", "needs_clarification"]
    confidence_score: int
    validation_result: Literal["sufficient", "insufficient"]
    attempts: int
    research_findings: dict[str, Any]
```

Why this matters: without the reducer, the conversation forgets itself after every node return.

## 2. Pydantic State Variant

Use Pydantic State when you want validation on every update (catches mistyped fields early, at runtime, not silently). Slightly more boilerplate but excellent for catch-typos-fast. With `version="v2"` (section 7), outputs are coerced back to your model type.

```python
class State(BaseModel):
    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)
    clarity_status: Literal["clear", "needs_clarification"] = "needs_clarification"
    confidence_score: int = 0
    validation_result: Literal["sufficient", "insufficient"] = "insufficient"
    attempts: int = 0
    research_findings: dict[str, Any] = Field(default_factory=dict)
```

Why this matters: an unknown field name (typo) raises immediately; TypedDict accepts it silently.

## 3. Node Signature Pattern

A node is a function (sync or async) of `State -> dict` returning only the **fields it changed**. Returning the whole state can cause reducers to re-apply.

```python
def clarity_node(state: State) -> dict[str, Any]:
    last = state["messages"][-1]
    is_clear = "company" in last.content.lower()  # placeholder logic
    return {
        "clarity_status": "clear" if is_clear else "needs_clarification",
        "messages": [AIMessage(content="Got it." if is_clear else "Which company?")],
    }
```

Why this matters: partial updates avoid double-applying reducers (especially `add_messages`).

## 4. Building a `StateGraph`

Bind state, add nodes, wire edges, compile **once** at module scope. Per-request compile is a common performance bug.

```python
def build_graph(checkpointer=None):
    g = StateGraph(State, context_schema=Context)  # Context: see section 11
    g.add_node("clarity", clarity_node)
    g.add_node("research", research_node, retry_policy=RetryPolicy(max_attempts=3))
    g.add_node("validator", validator_node)
    g.add_node("synthesis", synthesis_node)

    g.add_edge(START, "clarity")
    g.add_conditional_edges(
        "clarity", route_after_clarity, {"research": "research", "interrupt": "clarity"}
    )
    g.add_conditional_edges(
        "research", route_after_research, {"validator": "validator", "synthesis": "synthesis"}
    )
    g.add_conditional_edges(
        "validator", route_after_validator, {"research": "research", "synthesis": "synthesis"}
    )
    g.add_edge("synthesis", END)

    return g.compile(checkpointer=checkpointer or InMemorySaver())


GRAPH = build_graph()  # compiled once at import
```

Set the recursion limit explicitly at invoke time for graphs with loops (`config={"recursion_limit": 25, ...}`); the default changed in 1.0.x and is a safety net, not a design.

Why this matters: compile is non-trivial; doing it per-request adds latency and risks divergent graph instances.

## 5. Conditional Edges with `Literal`

Router signature uses `Literal[...]` of exact node names. Mismatches between router output and `path_map` keys are silent runtime errors.

```python
def route_after_clarity(state: State) -> Literal["research", "interrupt"]:
    return "research" if state["clarity_status"] == "clear" else "interrupt"


def route_after_research(state: State) -> Literal["validator", "synthesis"]:
    return "synthesis" if state["confidence_score"] >= 6 else "validator"
```

Why this matters: `Literal[...]` makes mypy verify the router only returns valid keys. (`route_after_validator` is in section 6.)

## 6. Bounded-Loop Pattern (Validator -> Research)

The attempt counter lives in `State`. Increment in the **worker** node (here, `research_node`), check in the **router**. Doing it the other way around is the #1 reason loops don't terminate.

```python
MAX_ATTEMPTS = 3


def research_node(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    attempts = state.get("attempts", 0) + 1
    findings = runtime.context.search.search(state["messages"][-1].content)
    return {
        "attempts": attempts,
        "research_findings": findings,
        "confidence_score": score_confidence(findings),
        "messages": [AIMessage(content=f"Research attempt {attempts} complete.")],
    }


def route_after_validator(state: State) -> Literal["research", "synthesis"]:
    if state["validation_result"] == "sufficient":
        return "synthesis"
    if state["attempts"] >= MAX_ATTEMPTS:
        return "synthesis"  # give the user *some* answer, flagged as low-confidence
    return "research"
```

Why this matters: state mutations from router functions are discarded by LangGraph; counters incremented there never advance.

## 7. `interrupt()` and `Command(resume=...)`

`interrupt()` pauses the graph and surfaces a payload to the caller. The caller collects human input out-of-band and resumes with `Command(resume=<value>)`.

```python
def clarity_node(state: State) -> dict[str, Any]:
    last = state["messages"][-1].content
    if not contains_company_name(last):
        clarification = interrupt(
            {
                "question": "Which company are you asking about?",
                "context": last,
            }
        )
        # On resume, `clarification` holds the value passed to Command(resume=...)
        return {
            "messages": [HumanMessage(content=clarification)],
            "clarity_status": "clear",
        }
    return {"clarity_status": "clear"}
```

Invoking and resuming with the typed v2 output format (LangGraph >= 1.1):

```python
config = {"configurable": {"thread_id": "session-42"}, "recursion_limit": 25}
result = GRAPH.invoke(
    {"messages": [HumanMessage(content="Tell me about stocks")]},
    config,
    context=CONTEXT,
    version="v2",
)
if result.interrupts:  # tuple[Interrupt, ...], empty if none
    question = result.interrupts[0].value["question"]
    # ...collect the human's answer, then:
    final = GRAPH.invoke(Command(resume="Acme Corp"), config, context=CONTEXT, version="v2")
    print(final.value["clarity_status"])  # .value is your state (dict / model / dataclass)
```

Caveats:

- The node that called `interrupt()` re-executes from its start on resume. Any side effect before the `interrupt()` call runs twice. Move side effects after, or guard with a state flag.
- `thread_id` must match between the original invoke and the resume invoke.
- `interrupt()` requires a checkpointer to be attached to the compiled graph.
- Without `version="v2"`, `invoke` returns a plain dict with interrupts under `"__interrupt__"`. Dict-style access on a v2 `GraphOutput` (`result["__interrupt__"]`) works but is deprecated and will be removed in v3; use `.value` / `.interrupts`.

## 8. Checkpointers: InMemory vs SQLite vs Postgres

> Packaging note: `SqliteSaver` and `PostgresSaver` live in separate packages (`langgraph-checkpoint-sqlite`, `langgraph-checkpoint-postgres`). `InMemorySaver` ships with the base `langgraph` package. Keep checkpoint packages current: a 2026 advisory fixed unsafe msgpack deserialization in checkpoint loading. Never point a checkpointer at a database an untrusted party can write to.

| Use case | Checkpointer |
| --- | --- |
| Unit tests, demos, single-process dev | `InMemorySaver()` |
| Local CLI with persistence across restarts | `SqliteSaver(sqlite3.connect(path, check_same_thread=False))` |
| Multi-process / production | `PostgresSaver` / `AsyncPostgresSaver` (`langgraph.checkpoint.postgres[.aio]`) |

```python
import sqlite3

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver


def make_checkpointer(settings: Settings):
    if settings.persist_checkpoints:
        conn = sqlite3.connect(settings.checkpoint_db_path, check_same_thread=False)
        return SqliteSaver(conn)
    return InMemorySaver()


graph = build_graph(checkpointer=make_checkpointer(SETTINGS))
```

`SqliteSaver.from_conn_string(...)` and `PostgresSaver.from_conn_string(...)` are **context managers**: use them as `with PostgresSaver.from_conn_string(DB_URI) as cp: cp.setup(); ...`. Assigning the call result directly to a variable is a common bug. Call `.setup()` once per database to create tables.

For memory that spans threads (user preferences, learned facts), pass a `store=` (for example `InMemoryStore`, or the Postgres store) to `compile()`; checkpointers are per-thread only.

Why this matters: `InMemorySaver` evaporates between processes; multi-worker setups will lose state across requests.

## 9. Multi-Turn Conversation with `thread_id`

Each conversation is a `thread_id`. Reusing the same thread_id replays the conversation context.

```python
def chat_turn(graph, user_text: str, thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(
        {"messages": [HumanMessage(content=user_text)]}, config, context=CONTEXT, version="v2"
    ).value


reply1 = chat_turn(GRAPH, "What's Acme's recent news?", "user-123")
reply2 = chat_turn(GRAPH, "And their competitors?", "user-123")  # has context
```

Why this matters: random per-call thread IDs lose context; deterministic per-user/session IDs preserve it.

### History Pruning

Long conversations exceed the model's context window (and cost scales with every turn). Trim before the model call with the built-in helper rather than hand-rolled slicing:

```python
from langchain_core.messages import trim_messages
from langchain_core.messages.utils import count_tokens_approximately


def call_model(state: State) -> dict[str, Any]:
    window = trim_messages(
        state["messages"],
        strategy="last",
        max_tokens=8_000,
        token_counter=count_tokens_approximately,
        include_system=True,
        start_on="human",
    )
    return {"messages": [llm.invoke(window)]}
```

This trims what the model *sees* while the checkpoint keeps full history. To actually delete history from state, return `RemoveMessage(id=...)` entries for the `add_messages` reducer.

## 10. Structured Output with `with_structured_output`

For any LLM response a router will consume, use a Pydantic schema. Free-form text + regex is the #1 source of intermittent routing bugs. See the `prompt-engineering` skill for schema design rules.

```python
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError


class ClarityDecision(BaseModel):
    status: Literal["clear", "needs_clarification"]
    reason: str = Field(..., description="One sentence justification.")


llm = init_chat_model(SETTINGS.llm_model, temperature=0)  # e.g. "openai:gpt-6-luna"
clarity_model = llm.with_structured_output(ClarityDecision)


def clarity_node(state: State) -> dict[str, Any]:
    try:
        decision: ClarityDecision = clarity_model.invoke(state["messages"])
    except (OutputParserException, ValidationError):
        # Parse-failure fallback: bias toward asking, never crash the graph
        return {"clarity_status": "needs_clarification"}
    return {"clarity_status": decision.status}
```

Why this matters: structured output makes routing deterministic and testable; you can mock the model to return a known Pydantic instance. `init_chat_model` keeps the provider and model ID in config, so swapping vendors is a settings change.

## 11. Tool Layer: Provider Interface + Runtime Context

Define an interface so swapping providers (mock for tests, Tavily for real) is one line, and inject it through **runtime context**, not state (clients are not serializable and must not be checkpointed).

```python
from dataclasses import dataclass
from typing import Protocol


class SearchProvider(Protocol):
    def search(self, query: str) -> dict[str, Any]: ...


class MockSearchProvider:
    def __init__(self, data: dict[str, dict[str, Any]]):
        self._data = data

    def search(self, query: str) -> dict[str, Any]:
        for name, blob in self._data.items():
            if name.lower() in query.lower():
                return {"source": "mock", "company": name, **blob}
        return {"source": "mock", "company": None, "note": "no match"}


class TavilySearchProvider:
    def __init__(self, api_key: str):
        from tavily import TavilyClient

        self._client = TavilyClient(api_key=api_key)

    def search(self, query: str) -> dict[str, Any]:
        resp = self._client.search(query=query, max_results=5)
        return {"source": "tavily", "results": resp.get("results", [])}


@dataclass(frozen=True)
class Context:
    search: SearchProvider
    user_id: str


CONTEXT = Context(search=MockSearchProvider(MOCK_DATA), user_id="demo")
```

Nodes declare `runtime: Runtime[Context]` (section 6) and the graph is built with `StateGraph(State, context_schema=Context)`. Callers pass `context=` to `invoke` / `stream`.

Why this matters: tests pass a `MockSearchProvider`; production passes `TavilySearchProvider`. The graph code is identical, and nothing unserializable ends up in a checkpoint.

## 12. MCP Server Wiring

MCP support ships in LangChain as `langchain.mcp` (beta; `pip install "langchain[mcp]>=1.4"`; it replaces the standalone `langchain-mcp-adapters` package and its `MultiServerMCPClient`). Treat every MCP server as a privileged tool surface:

```python
from langchain.mcp import MCPAdapter

ALLOWED_TOOLS = {"search"}  # minimum-necessary


async def load_research_tools(mcp_url: str):
    async with MCPAdapter(mcp_url) as adapter:  # str must be an http(s) URL
        return [t for t in await adapter.list_tools() if t.name in ALLOWED_TOOLS]
```

Bind the filtered tools to your model (`llm.bind_tools(tools)`) or pass them to `create_agent` (section 13).

Security: pin the server origin and version; allowlist tools by name (as above); treat all tool output as untrusted data (indirect prompt injection), never as instructions; re-review tool descriptions when a server updates (tool poisoning); log tool calls with structured fields, not full payloads. Building or auditing a server: see the `mcp-server-builder` skill.

## 13. When Not to Hand-Roll: `create_agent`

For a standard tool-calling loop, use LangChain's `create_agent` (it runs on LangGraph). It replaces the deprecated `langgraph.prebuilt.create_react_agent`; `prompt=` became `system_prompt=`, and pre/post model hooks became middleware.

```python
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware

agent = create_agent(
    model=SETTINGS.llm_model,
    tools=[search_web, send_report],
    system_prompt="You are a careful research assistant. Cite sources.",
    response_format=ResearchSummary,  # Pydantic model -> result["structured_response"]
    middleware=[HumanInTheLoopMiddleware(interrupt_on={"send_report": True})],
    checkpointer=InMemorySaver(),
)
```

Hand-roll a `StateGraph` (sections 1-9) when you need custom topology: validator loops, explicit clarity gates, parallel research, or subgraphs. A compiled `create_agent` graph can itself be added as a node in a larger graph.

## 14. Subgraphs

Extract a sub-pipeline into a subgraph when it has its own bounded loop, is reused across graphs, or would make the parent state unwieldy. If parent and subgraph share state keys, add the compiled subgraph directly as a node (`g.add_node("research", RESEARCH_SUB)`); otherwise invoke it from a wrapper node that maps state in and out. Full recipe: [references/advanced-patterns.md](references/advanced-patterns.md#subgraphs).

## 15. Parallel Branches (Fan-Out / Fan-In)

LangGraph runs nodes in parallel when an edge fans out. Any field that more than one parallel node writes **must** have a reducer.

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    news: Annotated[list[dict[str, Any]], operator.add]  # merged across branches
    financials: Annotated[list[dict[str, Any]], operator.add]


g.add_edge("dispatch", "news")
g.add_edge("dispatch", "financials")
g.add_edge(["news", "financials"], "merge")  # waits for both branches
```

Without a reducer, two branches writing the same key in one step raise `InvalidUpdateError` ("Can receive only one value per step").

For a dynamic number of branches (map-reduce), return `Send` objects from a router:

```python
def fan_out(state: State) -> list[Send]:
    return [Send("research_one", {"query": q}) for q in state["subqueries"]]


g.add_conditional_edges("planner", fan_out, ["research_one"])
g.add_edge("research_one", "merge")
```

## 16. Async and Streaming

Stream tokens with `graph.astream(..., stream_mode="messages", version="v2")`. Every v2 part is a dict with `type`, `ns`, and `data`; for `"messages"`, `data` is `(chunk, metadata)`. Use `"updates"` for per-node progress, `"values"` for full state, and `"custom"` for anything a node writes via `get_stream_writer()`. Prefer all-async nodes when streaming. Full recipe and notes: [references/advanced-patterns.md](references/advanced-patterns.md#async-and-streaming).

## 17. Retries and Failure Handling

Attach `retry_policy=RetryPolicy(max_attempts=3)` to nodes that call flaky I/O (section 4); retries apply per node, so keep side effects idempotent. Catch and convert expected failures inside the node (return a state flag the router can read); let unexpected ones raise so the checkpoint marks the step failed and the run can be resumed. Keep the **business** retry loop (validator -> research, section 6) separate from **transport** retries (RetryPolicy): they solve different problems and should not share a counter.

## 18. LangSmith Tracing

Env-var-gated, off by default in CI: set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT=myapp-dev` (`{service}-{env}`) in `.env`. The `LANGSMITH_*` names replaced the legacy `LANGCHAIN_*` ones. Tag runs at invoke time:

```python
config = {
    "configurable": {"thread_id": thread_id},
    "tags": ["myapp", "demo"],
    "metadata": {"user_id": hashed_user_id, "model": SETTINGS.llm_model, "prompt_version": "v3"},
    "run_name": f"chat-{thread_id}",
}
result = graph.invoke(input_state, config, context=CONTEXT, version="v2")
```

Why this matters: tagged runs are searchable in the LangSmith UI; per-run metadata makes debugging tractable. For vendor-neutral OpenTelemetry tracing, cost accounting, and trace-driven debugging, see the `agent-observability` skill.

## 19. Common Mistakes and Testing

The full catalog with broken/fixed code lives in [references/common-mistakes.md](references/common-mistakes.md); testing patterns (mocking the provider, replaying threads, interrupt round-trips, markers) are in [references/testing.md](references/testing.md). The short list:

1. Messages overwritten because the `add_messages` reducer is missing.
2. Counter incremented in the router (never advances).
3. Side effect before `interrupt()` replays on resume.
4. Router returns a literal that isn't a node name.
5. Per-request compile.
6. Parallel branches writing one key without a reducer (`InvalidUpdateError`).
7. `from_conn_string(...)` checkpointer used without `with`.
8. Clients or other unserializable objects stored in state instead of runtime context.
9. Still importing `create_react_agent` from `langgraph.prebuilt`.
10. Reading `result["__interrupt__"]` from a v2 `GraphOutput`.

## References

- LangGraph docs: <https://docs.langchain.com/oss/python/langgraph/overview>
- LangGraph v1 migration guide: <https://docs.langchain.com/oss/python/migrate/langgraph-v1>
- LangChain v1 migration guide: <https://docs.langchain.com/oss/python/migrate/langchain-v1>
- Streaming (v2 format): <https://docs.langchain.com/oss/python/langgraph/streaming>
- MCP in LangChain: <https://docs.langchain.com/oss/python/langchain/mcp>
- LangGraph releases: <https://github.com/langchain-ai/langgraph/releases>
- Pydantic v2: <https://docs.pydantic.dev/latest/>
- Pin `langgraph` minor version in `pyproject.toml` and commit the lockfile; release cadence is fast (roughly every two weeks) and import paths occasionally move.
