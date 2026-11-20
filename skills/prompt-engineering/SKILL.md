---
name: prompt-engineering
description: Write, refactor, and harden production prompts and structured outputs for current LLMs, covering system prompt structure, few-shot examples, delimiting untrusted input, native structured outputs with Pydantic / JSON Schema, schema design rules, validation and repair, tool definitions, prompt versioning, prompt caching, and model migration. Includes a schema linter script for structured-output pitfalls. Use when writing or reviewing a prompt, when model output is unreliable or unparseable, when designing a schema or tool interface for an LLM, or when moving prompts to a new model.
license: MIT
metadata:
  author: kaushik-holla
  version: "1.0"
---

# Prompt Engineering and Structured Outputs

Prompts are code that runs on a probabilistic interpreter. Treat them like code: explicit contracts, typed outputs, version control, and tests (evals). Most "the model is bad at this" problems are underspecified prompts or schemas.

## When To Use

- Writing a new system prompt, classifier, extractor, router, or agent instruction set.
- Output is inconsistent, unparseable, or ignores instructions.
- Designing the Pydantic / JSON Schema for a structured response or a tool's arguments.
- Migrating prompts to a new model snapshot or provider.
- Reviewing a prompt for injection risk or hidden assumptions.

## Principles

1. **Be explicit about the goal, audience, and what "good" looks like.** Modern models follow instructions precisely; vague prompts get generic output.
2. **Give the reason behind constraints.** "Don't use markdown, because this is read aloud by a TTS engine" generalizes better than "NO MARKDOWN."
3. **Show, don't adjectivize.** Two or three diverse examples beat "be concise, professional, accurate."
4. **Separate instructions from data.** Wrap untrusted content (user text, documents, tool results) in clear delimiters (XML-style tags work well across providers) and say it is data.
5. **Say what to do, not only what to avoid.** Replace "don't hallucinate" with "if the answer isn't in the sources, reply 'unknown'."
6. **Structure long prompts.** Long reference material first, instructions and the question last.
7. **Let reasoning models reason.** Don't script step-by-step chains for models with built-in thinking; set the reasoning effort / budget instead and specify the output.
8. **Enforce structure with the API, not with pleading.** Use native structured outputs instead of "respond ONLY with valid JSON."

Templates for each pattern (system prompt, classification, extraction, tool-using agent, rewrites, model migration): [references/prompt-patterns.md](references/prompt-patterns.md).

## Workflow

### 1. Write the contract first

Before the prompt, write: inputs, output schema, 3-6 success criteria, known hard cases, and what should happen on out-of-scope input. This doubles as the eval spec for the `llm-evals` skill.

### 2. Draft the prompt

Use the skeleton in [references/prompt-patterns.md](references/prompt-patterns.md): Role, Context, Task, Constraints, Output, Examples. Keep static sections stable and at the top (prompt caching); put per-request content at the end.

### 3. Define the structured output

```python
from typing import Literal

from pydantic import BaseModel, Field


class TicketTriage(BaseModel):
    """Triage decision for one support ticket."""

    reasoning: str = Field(description="1-2 sentences quoting the words that justify the category.")
    category: Literal["billing", "bug", "feature_request", "account", "other"]
    urgency: Literal["low", "medium", "high"] = Field(
        description="high only if the customer is blocked or data is at risk."
    )
    product_area: str | None = Field(description="Product area if explicitly mentioned, else null.")
```

Call it through native structured outputs:

```python
# LangChain (any provider): returns a validated TicketTriage instance
from langchain.chat_models import init_chat_model

llm = init_chat_model(settings.llm_model, temperature=0)
triage = llm.with_structured_output(TicketTriage).invoke(messages)

# OpenAI SDK, Responses API
resp = client.responses.parse(model=settings.llm_model, input=messages, text_format=TicketTriage)
triage = resp.output_parsed
```

Anthropic, Google, and most hosted providers offer equivalent schema-constrained output or tool-use-based extraction; `with_structured_output` picks the provider's native method. Instructor and PydanticAI are good alternatives outside LangChain.

### 4. Lint the schema

```bash
python scripts/schema_lint.py --model myapp.schemas:TicketTriage --strict
python scripts/schema_lint.py schema.json --fail-on warn        # CI
python scripts/schema_lint.py assets/example_schema.json --strict   # demo: a deliberately flawed schema
```

[scripts/schema_lint.py](scripts/schema_lint.py) flags deep nesting, missing descriptions, free-form objects, non-required fields under strict mode, oversized enums and unions, unbounded arrays, and verdict-before-reasoning order. SDK helpers (`responses.parse`, `with_structured_output(..., strict=True)`) often patch `additionalProperties` and `required` for you; the linter reports what the raw schema says, so treat those two findings as "verify the SDK handles it."

Schema design rules:

- **Flat and small.** 3-7 fields per call. Split complex extractions into multiple calls or steps.
- **Every field has a description**, written as an instruction ("ISO 8601 date of the invoice, not the due date").
- **`Literal` / enums for anything a router or metric consumes.**
- **Nullable instead of optional** in strict modes (`str | None` and listed in `required`), with "null when absent" in the description.
- **Reasoning before verdict** in field order, so the model commits to evidence first.
- **Bound arrays** (`max_length`) and avoid free-form `dict[str, Any]` fields.
- **Version schemas** alongside prompts; a renamed field is a breaking change for downstream code.

### 5. Validate and repair

- Validate every response with Pydantic even when the provider "guarantees" the schema (refusals, truncation at max tokens, and provider bugs happen).
- On validation failure: retry once with the validation error appended, then fall back to a safe default and log the raw output. Never loop unbounded.
- Check `finish_reason` / stop reason: a truncated response is a bug in your token budget, not in the parser.

### 6. Design tools like APIs for a new hire

- Names are `verb_noun` (`search_orders`, `get_invoice`), descriptions say **when** to use the tool and when not to.
- Few tools with clear boundaries beat many overlapping ones; overlapping tools cause wrong-tool calls.
- Arguments are typed, described, and constrained (enums, ranges). Return concise, model-readable results with IDs the model can pass to the next call.
- Errors are returned as actionable text ("No order 1234. Order IDs look like ORD-123456.") so the model can recover.

### 7. Version and test

- Store prompts in files (or a prompt registry), not inline f-strings scattered through code. Include a `prompt_version` in trace metadata.
- Every prompt change runs the eval set (`llm-evals`) and is compared against the previous version.
- Render templates with explicit escaping; never interpolate untrusted text into the instruction section.

## Checklist

- [ ] Goal, audience, and success criteria stated in the prompt
- [ ] Constraints carry reasons; out-of-scope behavior is specified
- [ ] Untrusted input delimited and declared as data
- [ ] 2-3 diverse examples, including a hard or edge case
- [ ] Native structured output with a linted, flat, described schema
- [ ] Validation + single repair retry + safe fallback; truncation detected
- [ ] Static prefix stable for caching; per-request content last
- [ ] Prompt versioned, traced, and covered by evals

## Common Mistakes

- "Respond ONLY with JSON" instead of schema-enforced output.
- ALL-CAPS and repeated warnings carried over from older models; newer models over-apply them and become rigid.
- One giant prompt doing classification, extraction, and generation at once.
- Optional fields without "null when absent," which invites fabricated values.
- Putting the verdict field before the reasoning field.
- Asking a reasoning model to "think step by step" while also capping its reasoning budget at minimum.
- Changing a prompt without re-running evals because "it's just wording."

## References

- OpenAI, Structured Outputs guide: <https://platform.openai.com/docs/guides/structured-outputs>
- Anthropic, prompt engineering overview: <https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/overview>
- LangChain, structured output: <https://docs.langchain.com/oss/python/langchain/structured-output>
- Pydantic, JSON Schema: <https://docs.pydantic.dev/latest/concepts/json_schema/>
- OWASP LLM01:2025 Prompt Injection: <https://genai.owasp.org/llmrisk/llm01-prompt-injection/>
