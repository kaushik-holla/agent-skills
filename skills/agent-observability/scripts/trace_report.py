#!/usr/bin/env python3
"""Summarize agent / LLM traces from span JSONL (agent-observability skill).

Input: one span per line, OpenTelemetry-shaped, using GenAI semantic-convention attributes:
  {"trace_id": "t1", "span_id": "s2", "parent_span_id": "s1", "name": "chat gpt-6-luna",
   "start_time": 1763600000.120, "end_time": 1763600001.020, "status": "OK",
   "attributes": {"gen_ai.operation.name": "chat", "gen_ai.request.model": "gpt-6-luna",
                  "gen_ai.usage.input_tokens": 812, "gen_ai.usage.output_tokens": 95}}

Times may be epoch seconds, epoch nanoseconds, or ISO-8601 strings; `duration_ms` also works.
`status` may be "OK"/"ERROR"/"UNSET" or {"code": "ERROR", "message": "..."}.
OTLP/JSON lines (OpenTelemetry Collector `file` exporter, {"resourceSpans": [...]}) are
flattened automatically.

Report: per-operation count, error rate, p50/p95 latency, tokens, optional cost; per-trace
totals; slowest traces; errors; and loop detection (same tool + same arguments repeated).

Examples
  python trace_report.py spans.jsonl
  python trace_report.py spans.jsonl --prices prices.json --top 5
  python trace_report.py spans.jsonl --fail-p95-ms 8000 --fail-error-rate 0.05    # CI / alerting

prices.json maps model -> USD per 1M tokens: {"gpt-6-luna": {"input": 0.3, "output": 1.2}}
Standard library only; Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

OP = "gen_ai.operation.name"
MODEL = "gen_ai.request.model"
TOOL = "gen_ai.tool.name"
TOOL_ARGS = "gen_ai.tool.call.arguments"
IN_TOK = "gen_ai.usage.input_tokens"
OUT_TOK = "gen_ai.usage.output_tokens"
LLM_OPS = {"chat", "text_completion", "generate_content"}


def to_seconds(value: Any) -> float:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    v = float(value)
    return v / 1e9 if v > 1e14 else v


def duration_ms(span: dict[str, Any]) -> float:
    if "duration_ms" in span:
        return float(span["duration_ms"])
    start = span.get("start_time", span.get("start_time_unix_nano"))
    end = span.get("end_time", span.get("end_time_unix_nano"))
    if start is None or end is None:
        return 0.0
    return (to_seconds(end) - to_seconds(start)) * 1000


def status_of(span: dict[str, Any]) -> tuple[bool, str]:
    s = span.get("status", "UNSET")
    if isinstance(s, dict):
        code, msg = str(s.get("code", "UNSET")), str(s.get("message", ""))
    else:
        code, msg = str(s), ""
    err = "ERROR" in code.upper()
    attrs = span.get("attributes", {})
    return err, msg or str(attrs.get("error.type", ""))


def op_key(span: dict[str, Any]) -> str:
    a = span.get("attributes", {})
    op = a.get(OP)
    if not op:
        return f"span {span.get('name', '?')}"
    target = a.get(TOOL) or a.get(MODEL) or a.get("gen_ai.agent.name") or ""
    return f"{op} {target}".strip()


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1]


def otlp_value(v: dict[str, Any]) -> Any:
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in v:
            return v[key]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [otlp_value(x) for x in v["arrayValue"].get("values", [])]
    return None


def flatten_otlp(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one OTLP/JSON export (collector `file` exporter line) to flat spans."""
    out = []
    for rs in doc.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for s in ss.get("spans", []):
                code = s.get("status", {}).get("code", 0)
                out.append(
                    {
                        "trace_id": s.get("traceId"),
                        "span_id": s.get("spanId"),
                        "parent_span_id": s.get("parentSpanId") or None,
                        "name": s.get("name"),
                        "start_time": int(s.get("startTimeUnixNano", 0)),
                        "end_time": int(s.get("endTimeUnixNano", 0)),
                        "status": {
                            "code": "ERROR" if code in (2, "STATUS_CODE_ERROR") else "OK",
                            "message": s.get("status", {}).get("message", ""),
                        },
                        "attributes": {
                            a["key"]: otlp_value(a.get("value", {}))
                            for a in s.get("attributes", [])
                        },
                    }
                )
    return out


def load(path: Path) -> list[dict[str, Any]]:
    spans = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                doc = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{n}: invalid JSON ({exc})") from exc
            spans.extend(flatten_otlp(doc) if "resourceSpans" in doc else [doc])
    return spans


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("spans", type=Path)
    p.add_argument("--prices", type=Path, help="JSON of model -> {input, output} USD per 1M tokens")
    p.add_argument("--top", type=int, default=3, help="slowest traces to show")
    p.add_argument(
        "--loop-threshold", type=int, default=3, help="identical tool calls per trace to flag"
    )
    p.add_argument("--fail-p95-ms", type=float, help="exit 1 if root-span p95 exceeds this")
    p.add_argument("--fail-error-rate", type=float, help="exit 1 if trace error rate exceeds this")
    args = p.parse_args(argv)

    spans = load(args.spans)
    prices = json.loads(args.prices.read_text()) if args.prices else {}

    by_op: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in spans:
        by_op[op_key(s)].append(s)
        by_trace[str(s.get("trace_id", "?"))].append(s)

    def cost(s: dict[str, Any]) -> float:
        a = s.get("attributes", {})
        price = prices.get(a.get(MODEL) or a.get("gen_ai.response.model") or "")
        if not price:
            return 0.0
        return (a.get(IN_TOK, 0) * price["input"] + a.get(OUT_TOK, 0) * price["output"]) / 1e6

    print(f"# Trace report: {args.spans}\n")
    print(f"spans={len(spans)} traces={len(by_trace)}\n")
    print("## By operation\n")
    print("| operation | count | error % | p50 ms | p95 ms | in tok | out tok | cost $ |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    rows = []
    for key, ss in by_op.items():
        lat = [duration_ms(s) for s in ss]
        errs = sum(status_of(s)[0] for s in ss)
        tin = sum(s.get("attributes", {}).get(IN_TOK, 0) for s in ss)
        tout = sum(s.get("attributes", {}).get(OUT_TOK, 0) for s in ss)
        rows.append(
            (
                key,
                len(ss),
                errs / len(ss),
                pct(lat, 50),
                pct(lat, 95),
                tin,
                tout,
                sum(map(cost, ss)),
            )
        )
    for r in sorted(rows, key=lambda r: -r[4]):
        print(
            f"| {r[0]} | {r[1]} | {r[2]:.0%} | {r[3]:.0f} | {r[4]:.0f} | {r[5]} | {r[6]} | {r[7]:.4f} |"
        )

    print("\n## By trace\n")
    summaries = []
    for tid, ss in by_trace.items():
        ids = {s.get("span_id") for s in ss}
        roots = [s for s in ss if not s.get("parent_span_id") or s.get("parent_span_id") not in ids]
        root = max(roots, key=duration_ms) if roots else ss[0]
        ops = Counter(s.get("attributes", {}).get(OP) for s in ss)
        summaries.append(
            {
                "trace_id": tid,
                "root": root.get("name", "?"),
                "ms": duration_ms(root),
                "llm_calls": sum(ops[o] for o in LLM_OPS),
                "tool_calls": ops["execute_tool"],
                "tokens": sum(
                    s.get("attributes", {}).get(IN_TOK, 0) + s.get("attributes", {}).get(OUT_TOK, 0)
                    for s in ss
                ),
                "cost": sum(map(cost, ss)),
                "error": any(status_of(s)[0] for s in ss),
            }
        )
    root_lat = [t["ms"] for t in summaries]
    err_rate = sum(t["error"] for t in summaries) / len(summaries) if summaries else 0.0
    print(
        f"root latency p50={pct(root_lat, 50):.0f} ms p95={pct(root_lat, 95):.0f} ms; "
        f"traces with errors {err_rate:.0%}"
    )
    if summaries:
        avg = lambda k: sum(t[k] for t in summaries) / len(summaries)  # noqa: E731
        print(
            f"avg per trace: llm_calls={avg('llm_calls'):.1f} tool_calls={avg('tool_calls'):.1f} "
            f"tokens={avg('tokens'):.0f} cost=${avg('cost'):.4f}"
        )
    print(f"\nslowest {args.top}:")
    for t in sorted(summaries, key=lambda t: -t["ms"])[: args.top]:
        print(
            f"- {t['trace_id']} {t['ms']:.0f} ms, {t['llm_calls']} LLM / {t['tool_calls']} tool calls, "
            f"{t['tokens']} tokens{' [ERROR]' if t['error'] else ''} ({t['root']})"
        )

    print("\n## Errors\n")
    errors = [(s, status_of(s)[1]) for s in spans if status_of(s)[0]]
    for s, msg in errors[:20]:
        print(f"- {s.get('trace_id')} {op_key(s)}: {msg or '(no message)'}")
    if not errors:
        print("- none")

    print("\n## Possible loops\n")
    loops = 0
    for tid, ss in by_trace.items():
        calls = Counter(
            (s["attributes"][TOOL], json.dumps(s["attributes"].get(TOOL_ARGS), sort_keys=True))
            for s in ss
            if s.get("attributes", {}).get(TOOL)
        )
        for (tool, targs), n in calls.items():
            if n >= args.loop_threshold:
                loops += 1
                shown = "" if targs == "null" else f" args={targs[:80]}"
                print(f"- {tid}: {tool} called {n}x with identical arguments{shown}")
    if not loops:
        print("- none")

    failed = False
    if args.fail_p95_ms is not None and pct(root_lat, 95) > args.fail_p95_ms:
        print(f"\nFAIL: root p95 {pct(root_lat, 95):.0f} ms > {args.fail_p95_ms:.0f} ms")
        failed = True
    if args.fail_error_rate is not None and err_rate > args.fail_error_rate:
        print(f"\nFAIL: trace error rate {err_rate:.1%} > {args.fail_error_rate:.1%}")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
