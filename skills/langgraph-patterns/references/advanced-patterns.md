# Advanced LangGraph Patterns

Loaded on demand from the `langgraph-patterns` skill. Imports and the `State`, `CONTEXT`, and `MOCK_DATA` names follow the main `SKILL.md`.

## Subgraphs

Extract a self-contained sub-pipeline into a subgraph when:

- It has its own bounded loop (e.g., a multi-attempt scraper).
- It's reusable across graphs.
- The parent state would otherwise become unwieldy.

```python
class SubState(TypedDict):
    query: str
    attempts: int
    result: dict[str, Any]


def build_research_subgraph():
    g = StateGraph(SubState)
    g.add_node("search", lambda s: {"result": MOCK_DATA.get(s["query"], {})})
    g.add_edge(START, "search")
    g.add_edge("search", END)
    return g.compile()


RESEARCH_SUB = build_research_subgraph()


def research_node(state: State) -> dict[str, Any]:
    sub = RESEARCH_SUB.invoke({"query": state["messages"][-1].content, "attempts": 0, "result": {}})
    return {"research_findings": sub["result"]}
```

If parent and subgraph share state keys, add the compiled subgraph directly as a node (`g.add_node("research", RESEARCH_SUB)`) and LangGraph maps the shared keys for you.

## Async and Streaming

Stream tokens with `stream_mode="messages"` and the unified v2 `StreamPart` format:

```python
async def run_streaming(graph, user_text: str, thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    async for part in graph.astream(
        {"messages": [HumanMessage(content=user_text)]},
        config,
        context=CONTEXT,
        stream_mode="messages",
        version="v2",
    ):
        if part["type"] == "messages":
            chunk, meta = part["data"]  # meta["langgraph_node"] names the node
            if chunk.content:
                yield chunk.content
```

Notes:

- Every v2 part is a dict with `type`, `ns`, and `data`. Combine modes with `stream_mode=["updates", "messages", "custom"]` and branch on `part["type"]`.
- `"updates"` yields per-node state deltas (good for progress UIs); `"values"` yields full state after each step; `"custom"` carries anything a node writes via `get_stream_writer()`.
- `astream_events` still exists for fine-grained callback events, but `stream_mode="messages"` is the simpler, recommended path for tokens.
- Mixing async nodes with sync nodes is supported but adds friction; prefer all-async for clarity.
- Cancellation: cancelling the consuming task cleanly leaves the checkpointer at the last completed step.
