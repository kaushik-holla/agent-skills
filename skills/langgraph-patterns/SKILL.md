---
name: "langgraph-patterns"
description: "Canonical LangGraph patterns for typed State, conditional routers, interrupt/resume, checkpointers, structured outputs, tool/MCP wiring, subgraphs, parallel branches, async streaming, and LangSmith tracing. Use when designing or implementing any LangGraph node, router, or graph. Reference; not a runbook."
---

# LangGraph Patterns

Canonical, copy-paste recipes for LangGraph >= 1.0 (verified against 1.0.x and 1.1.x). Every snippet here is a *minimal working pattern* you can extend; do not paste them into production verbatim without adapting names and types to the project.

> The snippets use a recurring **example** graph — a research assistant with clarity → research → validator → synthesis nodes — purely to make the patterns concrete. Swap in your own domain. `myapp` is a placeholder for your package name.

## When To Use

- Designing a new graph (consult sections 1-7 first).
- Implementing a node, router, or interrupt (sections 4-8, 11).
- Wiring tools, MCP servers, or providers (sections 12-13).
- Adding subgraphs, fan-out parallelism, async streaming, or tracing (sections 14-17).
- Debugging a graph that "feels off" (section 18 — common mistakes catalog with fixes).
- Writing tests against a graph (section 19).

## 0. Install and Imports

Pin minimum versions in `pyproject.toml`:

```toml
[project]
dependencies = [
  "langgraph>=1.0,<2",
  "langchain-core>=0.3",
  "langchain-openai>=0.2",
  "pydantic>=2.7",
]

[project.optional-dependencies]
sqlite = ["langgraph-checkpoint-sqlite>=2.0"]  # only if you persist via SqliteSaver
```

Imports cheat-sheet (all from the **public** API):

```python
from typing import Annotated, Literal, TypedDict, Any
from pydantic import BaseModel, Field
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver  # optional
```

If an import path is not in the list above, double-check it — internal modules move between minor versions.

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

Use Pydantic State when you want validation on every update (catches mistyped fields early, at runtime, not silently). Slightly more boilerplate but excellent for catch-typos-fast.

```python
from pydantic import BaseModel, Field

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
def clarity_node(state: State) -> dict:
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
    g = StateGraph(State)
    g.add_node("clarity", clarity_node)
    g.add_node("research", research_node)
    g.add_node("validator", validator_node)
    g.add_node("synthesis", synthesis_node)

    g.add_edge(START, "clarity")
    g.add_conditional_edges("clarity", route_after_clarity,
                            {"research": "research", "interrupt": "clarity"})
    g.add_conditional_edges("research", route_after_research,
                            {"validator": "validator", "synthesis": "synthesis"})
    g.add_conditional_edges("validator", route_after_validator,
                            {"research": "research", "synthesis": "synthesis"})
    g.add_edge("synthesis", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())

GRAPH = build_graph()  # compiled once at import
```

Why this matters: compile is non-trivial; doing it per-request adds latency and risks divergent graph instances.

## 5. Conditional Edges with `Literal`

Router signature uses `Literal[...]` of exact node names. Mismatches between router output and `path_map` keys are silent runtime errors.

```python
def route_after_clarity(state: State) -> Literal["research", "interrupt"]:
    return "research" if state["clarity_status"] == "clear" else "interrupt"

def route_after_research(state: State) -> Literal["validator", "synthesis"]:
    return "synthesis" if state["confidence_score"] >= 6 else "validator"

def route_after_validator(state: State) -> Literal["research", "synthesis"]:
    if state["validation_result"] == "sufficient" or state["attempts"] >= 3:
        return "synthesis"
    return "research"
```

Why this matters: `Literal[...]` makes mypy verify the router only returns valid keys.

## 6. Bounded-Loop Pattern (Validator -> Research)

The attempt counter lives in `State`. Increment in the **worker** node (here, `research_node`), check in the **router**. Doing it the other way around is the #1 reason loops don't terminate.

```python
def research_node(state: State) -> dict:
    attempts = state.get("attempts", 0) + 1
    findings = run_research(state["messages"])
    return {
        "attempts": attempts,
        "research_findings": findings,
        "confidence_score": score_confidence(findings),
        "messages": [AIMessage(content=f"Research attempt {attempts} complete.")],
    }

def route_after_validator(state: State) -> Literal["research", "synthesis"]:
    if state["validation_result"] == "sufficient":
        return "synthesis"
    if state["attempts"] >= 3:
        return "synthesis"
    return "research"
```

Why this matters: state mutations from router functions are discarded by LangGraph; counters incremented there never advance.

## 7. `interrupt()` and `Command(resume=...)`

`interrupt()` pauses the graph and surfaces a payload to the caller. The caller collects human input out-of-band and resumes with `Command(resume=<value>)`.

```python
from langgraph.types import interrupt, Command

def clarity_node(state: State) -> dict:
    last = state["messages"][-1].content
    if not contains_company_name(last):
        clarification = interrupt({
            "question": "Which company are you asking about?",
            "context": last,
        })
        # On resume, `clarification` holds the value passed to Command(resume=...)
        return {
            "messages": [HumanMessage(content=clarification)],
            "clarity_status": "clear",
        }
    return {"clarity_status": "clear"}
```

Invoking and resuming:

```python
config = {"configurable": {"thread_id": "session-42"}}
result = GRAPH.invoke({"messages": [HumanMessage(content="Tell me about stocks")]}, config)
# result will contain an `__interrupt__` payload; the caller sees the question.
# After collecting human input:
final = GRAPH.invoke(Command(resume="Apple Inc."), config)
```

Caveats:

- The node that called `interrupt()` re-executes from its start on resume. Any side effect before the `interrupt()` call runs twice. Move side effects after, or guard with a state flag.
- `thread_id` must match between the original invoke and the resume invoke.
- `interrupt()` requires a checkpointer to be attached to the compiled graph.

## 8. Checkpointers: `MemorySaver` vs `SqliteSaver`

> Packaging note: as of LangGraph 0.2 (and unchanged in 1.x), `SqliteSaver` lives in a separate package. Install with `pip install langgraph-checkpoint-sqlite`, or `pip install -e ".[sqlite]"` if you wired the extra in your pyproject. `MemorySaver` ships with the base `langgraph` package and needs no extra install.

| Use case | Checkpointer |
| --- | --- |
| Unit tests, demos, single-process dev | `MemorySaver()` |
| Local CLI with persistence across restarts | `SqliteSaver.from_conn_string("./checkpoints.sqlite")` |
| Multi-process / production | A real backend (Postgres, Redis); use `AsyncPostgresSaver` from `langgraph.checkpoint.postgres` |

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = (
    SqliteSaver.from_conn_string("./checkpoints.sqlite")
    if PERSIST_TO_DISK
    else MemorySaver()
)
graph = build_graph(checkpointer=checkpointer)
```

Why this matters: `MemorySaver` evaporates between processes — multi-worker setups will lose state across requests.

## 9. Multi-Turn Conversation with `thread_id`

Each conversation is a `thread_id`. Reusing the same thread_id replays the conversation context.

```python
def chat_turn(graph, user_text: str, thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke({"messages": [HumanMessage(content=user_text)]}, config)

reply1 = chat_turn(GRAPH, "What's Apple's recent news?", "user-123")
reply2 = chat_turn(GRAPH, "And their competitors?", "user-123")  # has context
reply3 = chat_turn(GRAPH, "Tell me about the CEO", "user-123")
```

Why this matters: random per-call thread IDs lose context; deterministic per-user/session IDs preserve it.

### History Pruning

Long conversations exceed the model's context window. Prune in a "pruner" node or before invoking:

```python
def prune_messages(messages: list[AnyMessage], keep_last: int = 20) -> list[AnyMessage]:
    if len(messages) <= keep_last:
        return messages
    # Preserve the first system message if present, then the most recent `keep_last - 1`.
    head = messages[:1] if messages and messages[0].type == "system" else []
    tail = messages[-(keep_last - len(head)):]
    return head + tail
```

## 10. Structured Output with `with_structured_output`

For any LLM response a router will consume, use a Pydantic schema. Free-form text + regex is the #1 source of intermittent routing bugs.

```python
class ClarityDecision(BaseModel):
    status: Literal["clear", "needs_clarification"]
    reason: str = Field(..., description="One sentence justification.")

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
clarity_model = llm.with_structured_output(ClarityDecision)

def clarity_node(state: State) -> dict:
    try:
        decision: ClarityDecision = clarity_model.invoke(state["messages"])
    except Exception:
        # Parse-failure fallback: bias toward asking, never crash the graph
        return {"clarity_status": "needs_clarification"}
    return {"clarity_status": decision.status}
```

Why this matters: structured output makes routing deterministic and testable; you can mock the model to return a known Pydantic instance.

## 11. Tool Layer: Provider Interface (Mock + Tavily)

Define an interface so swapping providers (mock for tests, Tavily for real) is one line.

```python
from typing import Protocol

class SearchProvider(Protocol):
    def search(self, query: str) -> dict[str, Any]: ...

class MockSearchProvider:
    def __init__(self, data: dict[str, dict]):
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
```

Wire into the node:

```python
def research_node(state: State, provider: SearchProvider) -> dict:
    findings = provider.search(state["messages"][-1].content)
    return {"research_findings": findings, "attempts": state.get("attempts", 0) + 1}
```

Why this matters: tests use `MockSearchProvider`; production uses `TavilySearchProvider`. The graph code is identical.

## 12. MCP Server Wiring (Tavily MCP)

If using Tavily via an MCP server rather than the SDK, treat it as a privileged tool surface:

```python
# Pseudocode — exact API depends on your MCP client library
from mcp_client import MCPClient

mcp = MCPClient(
    server="tavily",
    url="https://mcp.tavily.com",
    api_key=os.environ["TAVILY_API_KEY"],
    allowed_tools=["search"],  # minimum-necessary
)

def research_node(state: State) -> dict:
    raw = mcp.call("search", {"query": state["messages"][-1].content})
    # Treat raw output as untrusted; do not interpolate raw text into prompt
    # instruction sections. Quote it as data.
    return {"research_findings": sanitize(raw)}
```

Security checklist for MCP:

- Pin the server origin and version; do not auto-update.
- Scope tools to the minimum needed (`allowed_tools=[...]`).
- Treat all output as untrusted (indirect prompt-injection risk).
- Log tool calls with structured fields, no full payloads.

## 13. Subgraphs

Extract a self-contained sub-pipeline into a subgraph when:

- It has its own bounded loop (e.g., a multi-attempt scraper).
- It's reusable across graphs.
- The parent state would otherwise become unwieldy.

```python
def build_research_subgraph():
    class SubState(TypedDict):
        query: str
        attempts: int
        result: dict
    g = StateGraph(SubState)
    g.add_node("search", lambda s: {"result": MOCK_DATA.get(s["query"], {})})
    g.add_edge(START, "search")
    g.add_edge("search", END)
    return g.compile()

RESEARCH_SUB = build_research_subgraph()

def research_node(state: State) -> dict:
    sub_result = RESEARCH_SUB.invoke({"query": state["messages"][-1].content, "attempts": 0, "result": {}})
    return {"research_findings": sub_result["result"]}
```

## 14. Parallel Branches (Fan-Out / Fan-In)

LangGraph runs nodes in parallel automatically when an edge fans out. Use a reducer on the merging field.

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    news: list[dict]      # reducer below
    financials: list[dict]

def news_node(state: State) -> dict:
    return {"news": [fetch_news(state["messages"][-1].content)]}

def financials_node(state: State) -> dict:
    return {"financials": [fetch_financials(state["messages"][-1].content)]}

g.add_edge("dispatch", "news")
g.add_edge("dispatch", "financials")
g.add_edge("news", "merge")
g.add_edge("financials", "merge")
```

If both nodes write to the same field, declare a reducer (e.g., `operator.add` for lists). Without a reducer, the later writer wins and you lose data.

## 15. Async and Streaming

For streaming the model's tokens back to the user as they're generated:

```python
async def run_streaming(graph, user_text: str, thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    async for event in graph.astream_events(
        {"messages": [HumanMessage(content=user_text)]},
        config=config,
        version="v2",
    ):
        if event["event"] == "on_chat_model_stream":
            yield event["data"]["chunk"].content
```

Notes:

- `astream` yields state updates; `astream_events` yields fine-grained events including token deltas.
- Mix async nodes with sync nodes is supported but adds friction; prefer all-async for clarity.
- Cancellation: wrapping the async iteration in `asyncio.TaskGroup` and cancelling cleanly leaves the checkpointer consistent.
- LangGraph 1.1 added an opt-in `version="v2"` streaming format that returns a typed `GraphOutput` (`.value` for state, `.interrupts` for paused interrupts). v1 remains the default and is backward-compatible; this skill's snippets use v1.

## 16. LangSmith Tracing

Env-var-gated, off by default in CI:

```bash
# .env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=<your-key>
LANGCHAIN_PROJECT=myapp
```

Tag runs at invoke time:

```python
config = {
    "configurable": {"thread_id": thread_id},
    "tags": ["myapp", "demo"],
    "metadata": {"user_id": user_id, "model": "gpt-4o-mini"},
    "run_name": f"chat-{thread_id}",
}
result = graph.invoke(input_state, config)
```

Why this matters: tagged runs are searchable in the LangSmith UI; per-run metadata makes debugging tractable.

## 17. Common Mistakes (with fix code)

### 17.1 Messages overwritten because reducer is missing

Broken:

```python
class State(TypedDict):
    messages: list[AnyMessage]  # no reducer!
```

Fixed:

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

### 17.2 Counter incremented in the router (never advances)

Broken:

```python
def route_after_validator(state: State) -> Literal[...]:
    state["attempts"] += 1   # discarded; LangGraph ignores router-side state mutations
    if state["attempts"] >= 3: return "synthesis"
    return "research"
```

Fixed: increment in the worker node and return it; let the router read it.

```python
def research_node(state: State) -> dict:
    return {"attempts": state.get("attempts", 0) + 1, ...}
```

### 17.3 Side effect replays on resume

Broken:

```python
def clarity_node(state: State) -> dict:
    db.save_event("clarity_entered")        # runs again on resume
    clarification = interrupt({"question": "..."})
    return {...}
```

Fixed: guard with a state flag, or perform side effects only after the interrupt.

```python
def clarity_node(state: State) -> dict:
    clarification = interrupt({"question": "..."})
    db.save_event("clarification_received")  # runs once
    return {...}
```

### 17.4 Routing returns a literal that isn't a node name

Broken:

```python
def route_after_clarity(state) -> Literal["research", "interrupt"]:
    return "synthesise"   # typo; not in path_map
```

Fixed: define `Literal[...]` with the exact strings, let mypy catch typos.

```python
def route_after_clarity(state) -> Literal["research", "interrupt"]:
    return "research" if state["clarity_status"] == "clear" else "interrupt"
```

### 17.5 Per-request compile

Broken:

```python
def handle(request):
    graph = build_graph()         # expensive, every request
    return graph.invoke(...)
```

Fixed: compile once.

```python
GRAPH = build_graph()
def handle(request):
    return GRAPH.invoke(...)
```

## 18. Testing Patterns

### 18.1 Mock the LLM at the boundary

Mock `ChatOpenAI` (the provider client), not your own node functions. Tests stay decoupled from your code.

```python
from unittest.mock import MagicMock

def make_fake_clarity_model(decision: ClarityDecision):
    m = MagicMock()
    m.invoke.return_value = decision
    return m

def test_clarity_node_routes_to_research_when_clear(monkeypatch):
    fake = make_fake_clarity_model(ClarityDecision(status="clear", reason="..."))
    monkeypatch.setattr("myapp.agents.clarity.clarity_model", fake)
    out = clarity_node({"messages": [HumanMessage(content="Tell me about Apple")]})
    assert out["clarity_status"] == "clear"
```

### 18.2 Replay a checkpointed thread

For multi-turn tests, exercise the same thread_id twice:

```python
def test_followup_question_uses_previous_context():
    g = build_graph()
    cfg = {"configurable": {"thread_id": "test-1"}}
    g.invoke({"messages": [HumanMessage("Apple recent news?")]}, cfg)
    out = g.invoke({"messages": [HumanMessage("What about competitors?")]}, cfg)
    # Assert the latest agent response references prior context
    assert "Apple" in out["messages"][-1].content or len(out["messages"]) > 2
```

### 18.3 Test interrupt + resume

```python
def test_interrupt_resume_round_trip():
    g = build_graph()
    cfg = {"configurable": {"thread_id": "test-2"}}
    out = g.invoke({"messages": [HumanMessage("Tell me about stocks")]}, cfg)
    assert "__interrupt__" in out  # graph paused
    final = g.invoke(Command(resume="Apple Inc."), cfg)
    assert final["clarity_status"] == "clear"
```

### 18.4 Determinism

- Pin LLM `temperature=0` for tests that compare structured fields.
- For tests asserting text content, mock the LLM. Don't depend on real model output.
- Fix seeds for any RNG in your own code (`random`, `numpy.random`).

### 18.5 Markers

Use pytest markers to separate fast/deterministic tests from slow/live ones:

```python
@pytest.mark.live_llm
def test_real_research_returns_a_finding():
    ...

@pytest.mark.eval
def test_synthesis_quality_meets_rubric():
    ...
```

Default `pytest` skips these unless `-m live_llm` or `-m eval` is passed.

## References

- LangGraph docs: <https://langchain-ai.github.io/langgraph/>
- LangChain core: <https://python.langchain.com/docs/concepts/>
- Pydantic v2: <https://docs.pydantic.dev/latest/>
- Pin `langgraph` minor version in `pyproject.toml`; release cadence is fast and import paths occasionally move.
