---
name: agent-observability
description: Instrument, trace, monitor, and debug LLM apps and multi-step agents in production with OpenTelemetry GenAI semantic conventions, LangSmith, Langfuse, or Arize Phoenix, covering span design, token and cost tracking, prompt-version metadata, PII-safe content capture, sampling, dashboards and alerts, and a trace-driven debugging playbook. Includes a stdlib trace report script (latency percentiles, errors, cost, tool-loop detection) and a runnable OTel example. Use when adding tracing to an agent, investigating slow, expensive, or looping agent runs, setting up LLM monitoring and alerts, or turning production traces into eval datasets.
license: MIT
metadata:
  author: kaushik-holla
  version: "1.0"
---

# Agent Observability

You cannot debug what you cannot see. An agent run is a tree of model calls, tool calls, and decisions; logs alone flatten that tree and lose the causality. Trace every run, attach the metadata you will need to slice by, and close the loop by turning bad traces into eval cases.

## When To Use

- Adding tracing to a LangGraph / LangChain / OpenAI SDK / custom agent before or after launch.
- "The agent is slow / expensive / sometimes loops / sometimes gives up." Find out which step.
- Setting up dashboards and alerts for an LLM feature.
- Building an eval dataset from real production failures (pairs with `llm-evals`).

## 1. Span design

One trace per user request. Root span is the agent invocation; children are model calls, tool calls, retrieval, and your own business steps. Use the OpenTelemetry GenAI semantic conventions so any backend understands the data (the conventions are still marked Development, so pin your instrumentation versions).

| Span | Name | Key attributes |
| --- | --- | --- |
| Agent run (root) | `invoke_agent {agent}` | `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `app.prompt_version`, `app.git_sha`, hashed `enduser.id` / tenant |
| Model call | `chat {model}` | `gen_ai.operation.name=chat`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`, temperature / reasoning effort |
| Tool call | `execute_tool {tool}` | `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, arguments (opt-in, redacted) |
| Retrieval | `retrieve {index}` | index name, top_k, result count, top score, filter summary |
| Failure | any span | status `ERROR` with message, `error.type`, recorded exception |

Rules:

- **Metadata you will slice by goes on the root span**: prompt version, model, feature flag, tenant tier, experiment ID. You cannot add it retroactively.
- **Record usage on every model span**, including cached-token counts if the provider reports them; cost is derived from these.
- **Mark errors on the span that failed**, not only on the root, so you can see whether the tool or the model broke.
- **Propagate context across services** (W3C `traceparent`) so an agent calling an MCP server or another service stays one trace.

## 2. Pick an integration

Start with auto-instrumentation and add manual spans for business steps only.

**LangSmith** (zero-code for LangChain / LangGraph):

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=...            # from your secret manager, never committed
export LANGSMITH_PROJECT=myapp-prod
```

Attach metadata per run: `graph.invoke(inputs, config={"metadata": {"prompt_version": "researcher-v3"}, "tags": ["prod"]})`. LangSmith also ingests OpenTelemetry.

**Langfuse** (open source, self-hostable, OTel-based) and **Arize Phoenix** (open source, OpenInference instrumentors) both offer LangChain/LangGraph and OpenAI integrations; follow their current quickstarts linked below.

**Vendor-neutral OpenTelemetry**: instrument once, export via OTLP to any backend (Datadog, Grafana Tempo, Honeycomb, Langfuse, Phoenix, LangSmith). [assets/otel_tracing.py](assets/otel_tracing.py) is a runnable example with the span shape above, a JSONL exporter for local analysis, and OTLP export when `OTEL_EXPORTER_OTLP_ENDPOINT` is set:

```bash
uv run assets/otel_tracing.py --out /tmp/spans.jsonl
python scripts/trace_report.py /tmp/spans.jsonl
```

## 3. Privacy, cost, and sampling

- **Content capture is opt-in.** Prompts and completions are the most useful and most sensitive data you have. Most OTel GenAI instrumentations capture content only when explicitly enabled (for example `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`); decide per environment.
- **Redact before export**: emails, phone numbers, keys, and account numbers via a span processor or the backend's masking hook. Hash user IDs.
- **Retention**: set it deliberately (for example 30 days for content, longer for metrics) and document it for privacy reviews.
- **Sampling**: keep 100% of errors, slow traces, and thumbs-down traces; sample routine successes (tail sampling in the OTel Collector does this). Never sample away the data your evals need.

## 4. Dashboards and alerts

Track per agent / route, sliced by model and prompt version:

| Signal | Why | Alert example |
| --- | --- | --- |
| Root latency p50 / p95 | User experience | p95 > 2x 7-day baseline for 15 min |
| Error rate (root and per tool) | Reliability | > 2% for 10 min |
| Tokens and cost per request | Budget, regressions from prompt bloat | Cost per request +30% day over day |
| Steps per run (LLM + tool calls) | Loops, over-planning | p95 steps above the recursion budget |
| Repeated identical tool calls | Stuck agents | Any trace with >= 3 identical calls |
| Finish reason `length` | Truncation bugs | > 1% of model calls |
| User feedback / online judge scores | Quality | Thumbs-down rate +50% vs baseline |

[scripts/trace_report.py](scripts/trace_report.py) computes most of these from a span export (flat JSONL or OTLP JSON from a collector `file` exporter) and can gate CI or a canary:

```bash
python scripts/trace_report.py spans.jsonl --prices prices.json --fail-p95-ms 8000 --fail-error-rate 0.05
python scripts/trace_report.py assets/example_spans.jsonl --prices assets/example_prices.json   # demo
```

## 5. Debugging playbook

Open the slowest, most expensive, or failed traces first, then read the tree top-down.

| Symptom | Trace pattern | Likely fix |
| --- | --- | --- |
| Slow runs | One long `chat` span with huge input tokens | Trim history (`trim_messages`), smaller context, cache static prompt prefix |
| Slow runs | Many sequential tool spans | Parallelize independent tools (fan-out), cache tool results |
| Expensive runs | Frontier model on every step | Route simple steps (classification, routing) to a cheaper model |
| Agent loops / recursion limit | Same tool + same args repeated | Bound the loop in state, return better tool errors, add a "give up" path |
| Wrong answers | Retrieval span returns 0 or off-topic results | Fix retrieval first (`rag-pipelines`) |
| Truncated output | `finish_reasons=["length"]` | Raise max tokens or shrink the output schema |
| Intermittent failures | 429 / timeout errors on model or tool spans | Retry with backoff (`RetryPolicy`), circuit breaker, fallback model |
| Regression after deploy | Compare by `app.prompt_version` or `app.git_sha` | Roll back, then reproduce in the eval set |

## 6. Close the loop

1. Flag traces automatically: errors, thumbs-down, low online-judge scores, loops, and long runs.
2. Review a sample weekly; label the failure mode (open coding, see `llm-evals`).
3. Add representative failures to the eval dataset with the expected behavior.
4. Fix, run the eval gate, deploy, and watch the same dashboard slice.

## Checklist

- [ ] Every request produces one trace with model, tool, and retrieval child spans
- [ ] GenAI semantic-convention attributes, including token usage on every model span
- [ ] Root span carries prompt version, git SHA, model, and hashed user / tenant ID
- [ ] Content capture decision documented per environment; redaction in place
- [ ] Errors marked on the failing span with type and message
- [ ] Tail sampling keeps all errors and slow traces
- [ ] Dashboard for latency, errors, cost per request, steps per run; alerts with baselines
- [ ] Weekly trace review feeds the eval dataset

## Common Mistakes

- Logging prompts with `print()` instead of tracing, so there is no tree, no timing, and no correlation.
- Capturing full prompts with PII in production without a retention or redaction decision.
- Missing prompt-version metadata, making "which change caused this?" unanswerable.
- Sampling 1% of everything, which throws away most of the rare failures you care about.
- Dashboards with averages only; agent latency and cost are long-tailed, so watch p95 and the max.

## References

- OpenTelemetry GenAI semantic conventions: <https://opentelemetry.io/docs/specs/semconv/gen-ai/>
- LangSmith observability: <https://docs.langchain.com/langsmith/observability>
- Langfuse docs: <https://langfuse.com/docs>
- Arize Phoenix docs: <https://arize.com/docs/phoenix>
- OpenTelemetry Collector tail sampling: <https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/tailsamplingprocessor>
