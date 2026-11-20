# LangGraph Common Mistakes (with fix code)

Loaded on demand from the `langgraph-patterns` skill. Each entry: what breaks, the broken code, the fix.

## 1. Messages overwritten because reducer is missing

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

## 2. Counter incremented in the router (never advances)

Broken:

```python
def route_after_validator(state: State) -> Literal["research", "synthesis"]:
    state["attempts"] += 1  # discarded; LangGraph ignores router-side state mutations
    if state["attempts"] >= 3:
        return "synthesis"
    return "research"
```

Fixed: increment in the worker node and return it; let the router read it.

```python
def research_node(state: State) -> dict[str, Any]:
    return {"attempts": state.get("attempts", 0) + 1, ...}
```

## 3. Side effect replays on resume

Broken:

```python
def clarity_node(state: State) -> dict[str, Any]:
    db.save_event("clarity_entered")  # runs again on resume
    clarification = interrupt({"question": "..."})
    return {...}
```

Fixed: guard with a state flag, or perform side effects only after the interrupt.

```python
def clarity_node(state: State) -> dict[str, Any]:
    clarification = interrupt({"question": "..."})
    db.save_event("clarification_received")  # runs once
    return {...}
```

## 4. Routing returns a literal that isn't a node name

Broken:

```python
def route_after_clarity(state: State) -> Literal["research", "interrupt"]:
    return "synthesise"  # typo; not in path_map
```

Fixed: define `Literal[...]` with the exact strings and let mypy catch typos.

```python
def route_after_clarity(state: State) -> Literal["research", "interrupt"]:
    return "research" if state["clarity_status"] == "clear" else "interrupt"
```

## 5. Per-request compile

Broken:

```python
def handle(request):
    graph = build_graph()  # expensive, every request
    return graph.invoke(...)
```

Fixed: compile once.

```python
GRAPH = build_graph()


def handle(request):
    return GRAPH.invoke(...)
```

## 6. Parallel branches write one key without a reducer

Broken: two fan-out nodes both return `{"findings": [...]}` and `findings: list[dict]` has no reducer. The superstep raises `InvalidUpdateError: At key 'findings': Can receive only one value per step`.

Fixed:

```python
class State(TypedDict):
    findings: Annotated[list[dict[str, Any]], operator.add]
```

## 7. `from_conn_string` used as if it returned a saver

Broken:

```python
checkpointer = SqliteSaver.from_conn_string(
    "./checkpoints.sqlite"
)  # a context manager, not a saver
graph = build_graph(checkpointer)
```

Fixed: use it as a context manager, or construct the saver from a connection you own.

```python
with SqliteSaver.from_conn_string("./checkpoints.sqlite") as checkpointer:
    graph = build_graph(checkpointer)
    graph.invoke(...)

# or, for a long-lived process:
checkpointer = SqliteSaver(sqlite3.connect("./checkpoints.sqlite", check_same_thread=False))
```

## 8. Clients stored in state

Broken: `state["search_client"] = TavilyClient(...)`. The checkpointer tries to serialize it on every step (fails, or silently persists credentials).

Fixed: pass dependencies through runtime context.

```python
g = StateGraph(State, context_schema=Context)


def research_node(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    return {"research_findings": runtime.context.search.search(...)}


GRAPH.invoke(inputs, config, context=Context(search=TavilySearchProvider(key), user_id="u1"))
```

## 9. Deprecated prebuilt agent

Broken:

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(model, tools, prompt="You are helpful.")
```

Fixed:

```python
from langchain.agents import create_agent

agent = create_agent(model, tools, system_prompt="You are helpful.")
```

Pre/post model hooks (`pre_model_hook`, `post_model_hook`, `state_modifier`) become middleware; when filtering streamed events by node name, the model node is now `"model"` instead of `"agent"`.

## 10. Dict-style access on a v2 `GraphOutput`

Broken (emits `LangGraphDeprecatedSinceV11`, removed in v3):

```python
result = graph.invoke(inputs, config, version="v2")
if "__interrupt__" in result:
    ...
```

Fixed:

```python
result = graph.invoke(inputs, config, version="v2")
if result.interrupts:
    ...
state = result.value
```

## 11. Relying on the default recursion limit

A validator loop with a bug runs until the default recursion limit trips, burning tokens on every step. Set `recursion_limit` in the invoke config to slightly above the worst legitimate path, and keep the explicit `attempts` counter as the primary stop condition.

## 12. Unbounded history

No pruning means a context-window error (or a silent cost explosion) after enough long turns. Trim with `trim_messages` before the model call, or remove old messages with `RemoveMessage`.

## 13. Router does work

A router that makes an LLM call makes routing non-deterministic and expensive, and its output isn't checkpointed. Move the call into an upstream node that writes a typed field; the router reads that field.
