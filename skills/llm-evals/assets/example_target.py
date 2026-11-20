"""Toy target + judge so eval_harness.py can be smoke-tested offline.

Replace `answer` with a call into your real system (graph.invoke, chain.invoke, an HTTP call)
and `judge` with an LLM-as-judge built from references/judge-rubrics.md.
"""

from __future__ import annotations

from typing import Any

_FAQ = {
    "refund": "Refunds are issued within 14 days of purchase to the original payment method.",
    "shipping": "Standard shipping takes 3-5 business days.",
    "warranty": "All devices include a 2-year limited warranty.",
}


def answer(question: str) -> str:
    """Stand-in for the system under test."""
    q = question.lower()
    for key, text in _FAQ.items():
        if key in q:
            return text
    return "I don't know."


def judge(row: dict[str, Any], output: str) -> dict[str, Any]:
    """Stand-in for an LLM judge: binary verdict + reason, same contract as a real judge.

    A real implementation would call a pinned judge model with structured output, e.g.

        verdict = judge_model.invoke(render(FAITHFULNESS_RUBRIC, row=row, output=output))
        return {"pass": verdict.verdict == "pass", "reason": verdict.reasoning}
    """
    should_abstain = row.get("expected") == "I don't know."
    abstained = "don't know" in output.lower()
    if should_abstain:
        return {
            "pass": abstained,
            "reason": "abstained" if abstained else "answered without evidence",
        }
    return {
        "pass": not abstained,
        "reason": "answered" if not abstained else "unnecessary abstention",
    }
