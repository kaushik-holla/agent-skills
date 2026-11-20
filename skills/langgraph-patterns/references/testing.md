# Testing LangGraph Graphs

Loaded on demand from the `langgraph-patterns` skill. Pair with the `python-quality` skill's
`references/testing-patterns.md` for general pytest setup.

## 1. Mock the LLM at the boundary

Mock the chat model (the provider client), not your own node functions. Tests stay decoupled from your code.

```python
from unittest.mock import MagicMock


def make_fake_clarity_model(decision: ClarityDecision) -> MagicMock:
    m = MagicMock()
    m.invoke.return_value = decision
    return m


def test_clarity_node_routes_to_research_when_clear(monkeypatch):
    fake = make_fake_clarity_model(ClarityDecision(status="clear", reason="..."))
    monkeypatch.setattr("myapp.agents.clarity.clarity_model", fake)
    out = clarity_node({"messages": [HumanMessage(content="Tell me about Acme")]})
    assert out["clarity_status"] == "clear"
```

For nodes that call a model directly (not structured output), `GenericFakeChatModel` from
`langchain_core.language_models.fake_chat_models` gives you a real chat model that replays
scripted messages, including streaming.

## 2. Inject fakes through runtime context

Because providers come in via `context=` (see SKILL.md section 11), tests need no monkeypatching:

```python
TEST_CONTEXT = Context(search=MockSearchProvider({"Acme": {"ceo": "Jane Doe"}}), user_id="test")


def test_research_node_uses_injected_provider():
    g = build_graph()
    cfg = {"configurable": {"thread_id": "t-ctx"}}
    out = g.invoke(
        {"messages": [HumanMessage("Acme CEO?")]}, cfg, context=TEST_CONTEXT, version="v2"
    )
    assert out.value["research_findings"]["company"] == "Acme"
```

## 3. Replay a checkpointed thread

For multi-turn tests, exercise the same thread_id twice:

```python
def test_followup_question_uses_previous_context():
    g = build_graph()
    cfg = {"configurable": {"thread_id": "test-1"}}
    g.invoke({"messages": [HumanMessage("Acme recent news?")]}, cfg, context=TEST_CONTEXT)
    out = g.invoke(
        {"messages": [HumanMessage("What about competitors?")]},
        cfg,
        context=TEST_CONTEXT,
        version="v2",
    )
    # The second turn sees the first turn's messages
    assert len(out.value["messages"]) > 2
```

## 4. Test interrupt + resume

```python
def test_interrupt_resume_round_trip():
    g = build_graph()
    cfg = {"configurable": {"thread_id": "test-2"}}
    out = g.invoke(
        {"messages": [HumanMessage("Tell me about stocks")]},
        cfg,
        context=TEST_CONTEXT,
        version="v2",
    )
    assert out.interrupts, "graph should pause for clarification"
    assert "question" in out.interrupts[0].value
    final = g.invoke(Command(resume="Acme Corp"), cfg, context=TEST_CONTEXT, version="v2")
    assert final.value["clarity_status"] == "clear"
```

## 5. Test routers as pure functions

Routers are the cheapest, highest-value tests in a graph:

```python
import pytest


@pytest.mark.parametrize(
    ("validation", "attempts", "expected"),
    [
        ("sufficient", 1, "synthesis"),
        ("insufficient", 1, "research"),
        ("insufficient", 3, "synthesis"),
    ],
)
def test_route_after_validator(validation, attempts, expected):
    state = {"validation_result": validation, "attempts": attempts}
    assert route_after_validator(state) == expected
```

## 6. Assert the loop is bounded

```python
def test_validator_loop_stops_at_max_attempts(monkeypatch):
    monkeypatch.setattr("myapp.agents.validator.judge", lambda *_: "insufficient")
    g = build_graph()
    out = g.invoke(
        {"messages": [HumanMessage("Acme?")]},
        {"configurable": {"thread_id": "t-loop"}, "recursion_limit": 25},
        context=TEST_CONTEXT,
        version="v2",
    )
    assert out.value["attempts"] == MAX_ATTEMPTS
```

## 7. Determinism

- Pin LLM `temperature=0` for tests that compare structured fields.
- For tests asserting text content, mock the LLM. Don't depend on real model output.
- Fix seeds for any RNG in your own code (`random`, `numpy.random`).

## 8. Markers

Use pytest markers to separate fast/deterministic tests from slow/live ones:

```python
@pytest.mark.live_llm
def test_real_research_returns_a_finding(): ...


@pytest.mark.eval
def test_synthesis_quality_meets_rubric(): ...
```

Default `pytest` skips these unless `-m live_llm` or `-m eval` is passed. Build the eval side with the `llm-evals` skill.
