# Testing Patterns for Agentic / LLM Python Projects

Loaded on demand from the `python-quality` skill. `myapp` is a placeholder package name.

## Layout

```
tests/
  conftest.py          # shared fixtures
  unit/
    test_state.py
    test_routers.py
    test_clarity_node.py
  integration/
    test_graph_happy_path.py
    test_validator_loop.py
    test_interrupt_resume.py
  eval/
    test_synthesis_quality.py   # marked @pytest.mark.eval
```

## Mock the LLM at the boundary

Patch the **provider** (the chat-model object your node calls), not your own function.

```python
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_clarity_model() -> MagicMock:
    """A chat-model-shaped mock whose `.invoke` returns a Pydantic decision."""
    from myapp.agents.clarity import ClarityDecision

    m = MagicMock()
    m.invoke.return_value = ClarityDecision(status="clear", reason="company named")
    return m


def test_clarity_node_clear(monkeypatch: pytest.MonkeyPatch, fake_clarity_model: MagicMock) -> None:
    from myapp.agents import clarity

    monkeypatch.setattr(clarity, "clarity_model", fake_clarity_model)
    out = clarity.clarity_node({"messages": [...], "attempts": 0})
    assert out["clarity_status"] == "clear"
```

For free-text responses, LangChain ships a real fake model that behaves like a chat model
(streaming, callbacks, tracing) without network calls:

```python
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

fake = GenericFakeChatModel(messages=iter([AIMessage(content="Acme Corp is ...")]))
```

## Deterministic fixtures

```python
import pytest
from langchain_core.messages import HumanMessage


@pytest.fixture
def fixed_company_query() -> list[HumanMessage]:
    return [HumanMessage(content="Tell me about Acme Corp")]
```

## Marker-gated live LLM tests

```python
import pytest


@pytest.mark.live_llm
def test_real_clarity_call(real_settings) -> None:
    # Only runs with: uv run pytest -m live_llm
    ...
```

## Settings isolation

Never let a developer's real `.env` leak into tests. Build `Settings` explicitly in a fixture:

```python
import pytest

from myapp.config import Settings


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return Settings(_env_file=None, LLM_MODEL="openai:test-model", SEARCH_PROVIDER="mock")
```

## No `time.sleep` in tests

Use pytest-asyncio time control, or refactor the code to take a clock dependency, rather than
sleeping in tests. Sleeps make CI flaky.

## Async tests

With `asyncio_mode = "auto"` (see `assets/pyproject.toml`), plain `async def test_...` functions
run on an event loop without a decorator. Keep `asyncio_default_fixture_loop_scope = "function"`
so each test gets an isolated loop unless a fixture explicitly asks for a wider scope.

## Graph-level tests

For LangGraph-specific testing (checkpointed threads, interrupt/resume round-trips, router
coverage), see the `langgraph-patterns` skill's `references/testing.md`.
