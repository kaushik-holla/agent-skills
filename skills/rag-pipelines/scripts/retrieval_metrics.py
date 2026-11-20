#!/usr/bin/env python3
"""Retrieval metrics for RAG pipelines (rag-pipelines skill).

Input JSONL, one query per line:
  {"query_id": "q1", "retrieved": ["d3", "d7", "d1"], "relevant": ["d1", "d9"]}
  {"query_id": "q2", "retrieved": [...], "relevant": {"d4": 3, "d5": 1}}   # graded relevance

`retrieved` is in rank order (best first). `relevant` is a list (binary) or a
{doc_id: grade} map (graded, used by nDCG). Optional "slice" field groups results.

Metrics per k: hit@k, recall@k, precision@k, nDCG@k; plus MRR over the full list.
Also lists the queries with zero relevant docs in the top-k, which are the first
ones to debug: generation cannot fix a retrieval miss.

Examples
  python retrieval_metrics.py runs.jsonl --k 1 5 10
  python retrieval_metrics.py baseline.jsonl --compare hybrid.jsonl --k 5 10
  python retrieval_metrics.py runs.jsonl --json > metrics.json

Standard library only; Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def load(path: Path) -> list[dict[str, Any]]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        for key in ("query_id", "retrieved", "relevant"):
            if key not in row:
                raise SystemExit(f"{path}:{n}: missing '{key}'")
        rel = row["relevant"]
        row["relevant"] = (
            {d: 1.0 for d in rel}
            if isinstance(rel, list)
            else {d: float(g) for d, g in rel.items()}
        )
        rows.append(row)
    return rows


def dcg(gains: list[float]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def per_query(row: dict[str, Any], ks: list[int]) -> dict[str, float]:
    retrieved: list[str] = list(dict.fromkeys(row["retrieved"]))  # dedupe, keep rank order
    rel: dict[str, float] = row["relevant"]
    relevant_ids = {d for d, g in rel.items() if g > 0}
    out: dict[str, float] = {}
    rr = 0.0
    for rank, doc in enumerate(retrieved, 1):
        if doc in relevant_ids:
            rr = 1.0 / rank
            break
    out["mrr"] = rr
    ideal = sorted(rel.values(), reverse=True)
    for k in ks:
        top = retrieved[:k]
        hits = sum(1 for d in top if d in relevant_ids)
        out[f"hit@{k}"] = 1.0 if hits else 0.0
        out[f"recall@{k}"] = hits / len(relevant_ids) if relevant_ids else 0.0
        out[f"precision@{k}"] = hits / k
        idcg = dcg(ideal[:k])
        out[f"ndcg@{k}"] = dcg([rel.get(d, 0.0) for d in top]) / idcg if idcg else 0.0
    return out


def aggregate(rows: list[dict[str, Any]], ks: list[int]) -> dict[str, Any]:
    per = {r["query_id"]: per_query(r, ks) for r in rows}
    keys = next(iter(per.values())).keys() if per else []
    overall = {m: sum(p[m] for p in per.values()) / len(per) for m in keys}
    slices: dict[str, list[dict[str, float]]] = defaultdict(list)
    for r in rows:
        slices[r.get("slice", "all")].append(per[r["query_id"]])
    by_slice = {
        s: {m: sum(p[m] for p in ps) / len(ps) for m in keys} | {"n": len(ps)}
        for s, ps in slices.items()
    }
    kmax = max(ks)
    misses = [qid for qid, p in per.items() if p[f"hit@{kmax}"] == 0.0]
    return {
        "n": len(per),
        "overall": overall,
        "by_slice": by_slice,
        "misses": misses,
        "per_query": per,
    }


def print_report(name: str, agg: dict[str, Any], ks: list[int]) -> None:
    print(f"== {name}  (queries={agg['n']})")
    cols = ["mrr"] + [f"{m}@{k}" for k in ks for m in ("hit", "recall", "precision", "ndcg")]
    for c in cols:
        print(f"  {c:<14} {agg['overall'][c]:.3f}")
    if len(agg["by_slice"]) > 1:
        print("  by slice (recall@{0}, ndcg@{0}):".format(max(ks)))
        for s, m in sorted(agg["by_slice"].items()):
            print(
                f"    {s:<18} n={m['n']:<4} {m[f'recall@{max(ks)}']:.3f}  {m[f'ndcg@{max(ks)}']:.3f}"
            )
    print(f"  zero-hit queries @ {max(ks)} ({len(agg['misses'])}): {agg['misses'][:20]}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("runs", type=Path)
    p.add_argument("--k", type=int, nargs="+", default=[1, 5, 10])
    p.add_argument("--compare", type=Path, help="second run file to diff against the first")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = p.parse_args(argv)
    ks = sorted(set(args.k))

    base = aggregate(load(args.runs), ks)
    other = aggregate(load(args.compare), ks) if args.compare else None
    if args.json:
        payload = {"baseline": base} | ({"candidate": other} if other else {})
        for v in payload.values():
            v.pop("per_query", None)
        print(json.dumps(payload, indent=2))
        return 0
    print_report(str(args.runs), base, ks)
    if other:
        print()
        print_report(str(args.compare), other, ks)
        print("\n== delta (candidate - baseline)")
        for c in ["mrr"] + [f"recall@{k}" for k in ks] + [f"ndcg@{k}" for k in ks]:
            print(f"  {c:<14} {other['overall'][c] - base['overall'][c]:+.3f}")
        shared = set(base["per_query"]) & set(other["per_query"])
        k = max(ks)
        lost = sorted(
            q
            for q in shared
            if base["per_query"][q][f"hit@{k}"] > other["per_query"][q][f"hit@{k}"]
        )
        print(f"  queries that lost all hits @ {k}: {lost[:20]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
