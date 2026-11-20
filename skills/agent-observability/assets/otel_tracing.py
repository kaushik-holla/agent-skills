# /// script
# requires-python = ">=3.10"
# dependencies = ["opentelemetry-sdk>=1.30", "opentelemetry-exporter-otlp-proto-http>=1.30"]
# ///
"""Manual OpenTelemetry GenAI instrumentation for an agent loop (agent-observability skill).

Shows the span shape the skill recommends: one `invoke_agent` root span per request with
`chat` and `execute_tool` children, GenAI semantic-convention attributes, token usage,
and error status. Spans go to an OTLP endpoint if OTEL_EXPORTER_OTLP_ENDPOINT is set,
and always to a local JSONL file that `scripts/trace_report.py` can summarize.

    uv run assets/otel_tracing.py --out /tmp/spans.jsonl
    python scripts/trace_report.py /tmp/spans.jsonl

In a real app, prefer auto-instrumentation (LangSmith, OpenInference, OpenLLMetry) and add
manual spans only for your own business steps. The fake LLM and tool below stand in for real calls.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections.abc import Sequence
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Status, StatusCode

MODEL = os.environ.get("LLM_MODEL", "openai:gpt-6-luna").split(":", 1)[-1]


class JsonlExporter(SpanExporter):
    """Writes spans in the flat JSONL shape that trace_report.py reads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.write_text("")

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self.path.open("a", encoding="utf-8") as fh:
            for s in spans:
                ctx, parent = s.get_span_context(), s.parent
                fh.write(
                    json.dumps(
                        {
                            "trace_id": f"{ctx.trace_id:032x}",
                            "span_id": f"{ctx.span_id:016x}",
                            "parent_span_id": f"{parent.span_id:016x}" if parent else None,
                            "name": s.name,
                            "start_time": s.start_time,
                            "end_time": s.end_time,
                            "status": {
                                "code": s.status.status_code.name,
                                "message": s.status.description or "",
                            },
                            "attributes": dict(s.attributes or {}),
                        }
                    )
                    + "\n"
                )
        return SpanExportResult.SUCCESS


def setup_tracing(out: Path) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": "myapp-agent"}))
    provider.add_span_processor(BatchSpanProcessor(JsonlExporter(out)))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


tracer = trace.get_tracer("myapp.agent")


def fake_llm(messages: list[str]) -> tuple[str, int, int]:
    time.sleep(random.uniform(0.02, 0.12))
    text = "search: acme revenue" if len(messages) == 1 else "Acme revenue was $12M [web_search]."
    return text, 40 * sum(len(m.split()) for m in messages) + 300, len(text.split()) * 2


def call_llm(messages: list[str]) -> str:
    with tracer.start_as_current_span(f"chat {MODEL}", kind=SpanKind.CLIENT) as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.provider.name", "openai")
        span.set_attribute("gen_ai.request.model", MODEL)
        span.set_attribute("gen_ai.request.temperature", 0.0)
        text, tokens_in, tokens_out = fake_llm(messages)
        span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
        span.set_attribute("gen_ai.usage.output_tokens", tokens_out)
        span.set_attribute("gen_ai.response.finish_reasons", ["stop"])
        return text


def run_tool(name: str, query: str) -> str:
    with tracer.start_as_current_span(f"execute_tool {name}", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.name", name)
        span.set_attribute("gen_ai.tool.call.arguments", json.dumps({"query": query}))
        try:
            time.sleep(random.uniform(0.05, 0.3))
            if random.random() < 0.2:
                raise TimeoutError("search backend timed out after 10s")
            return f"results for {query}"
        except TimeoutError as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.set_attribute("error.type", type(exc).__name__)
            return "tool error: search timed out"


def handle_request(question: str, conversation_id: str) -> str:
    with tracer.start_as_current_span("invoke_agent researcher", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.agent.name", "researcher")
        span.set_attribute("gen_ai.conversation.id", conversation_id)
        span.set_attribute("app.prompt_version", "researcher-v3")
        plan = call_llm([question])
        evidence = run_tool("web_search", plan.removeprefix("search: "))
        return call_llm([question, evidence])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("spans.jsonl"))
    parser.add_argument("--requests", type=int, default=5)
    args = parser.parse_args()
    provider = setup_tracing(args.out)
    random.seed(7)
    for i in range(args.requests):
        handle_request("What was Acme's revenue last quarter?", conversation_id=f"c-{i}")
    provider.shutdown()
    print(f"wrote spans to {args.out}")


if __name__ == "__main__":
    main()
