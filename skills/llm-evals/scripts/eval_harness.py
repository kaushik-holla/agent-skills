#!/usr/bin/env python3
"""Minimal, dependency-free eval harness for LLM features (llm-evals skill).

Subcommands
  run        Run a target over a JSONL dataset, grade every output, write results JSONL,
             and print pass rates with bootstrap 95% confidence intervals (overall + per slice).
  compare    Paired comparison of two results files (baseline vs candidate) for one grader:
             delta with a paired-bootstrap CI, plus the list of regressions.
  calibrate  Agreement between a grader (e.g. an LLM judge) and human labels:
             accuracy, TPR, TNR, Cohen's kappa, and the disagreements to review.

Dataset rows (JSONL):   {"id": "q1", "input": ..., "expected": ..., "slice": "pricing"}
Target:                 "pkg.module:function", called as fn(input) -> output
Custom grader:          "pkg.module:function", called as fn(row, output) -> bool | dict
                        (dict: {"pass": bool, "score": float?, "reason": str?})
Built-in graders:       exact, contains, regex (uses row["pattern"]), json_keys (row["expected"]
                        is a list of required keys), not_empty

Examples
  python eval_harness.py run --dataset ../assets/example_dataset.jsonl --pythonpath ../assets \
      --target example_target:answer --grader contains --grader example_target:judge \
      --out results.jsonl
  python eval_harness.py compare --baseline base.jsonl --candidate cand.jsonl --grader contains
  python eval_harness.py calibrate --results results.jsonl --labels human.jsonl \
      --grader example_target:judge

Standard library only; Python 3.10+.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import random
import re
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

Grade = dict[str, Any]
Grader = Callable[[dict[str, Any], Any], Any]


# ---------------------------------------------------------------- built-in graders


def _text(x: Any) -> str:
    return x if isinstance(x, str) else json.dumps(x, sort_keys=True)


def g_exact(row: dict[str, Any], out: Any) -> bool:
    return _text(out).strip() == _text(row.get("expected", "")).strip()


def g_contains(row: dict[str, Any], out: Any) -> bool:
    exp = row.get("expected", "")
    needles = exp if isinstance(exp, list) else [exp]
    return all(str(n).lower() in _text(out).lower() for n in needles)


def g_regex(row: dict[str, Any], out: Any) -> bool:
    return re.search(row["pattern"], _text(out)) is not None


def g_json_keys(row: dict[str, Any], out: Any) -> Grade:
    try:
        obj = out if isinstance(out, dict) else json.loads(out)
    except (TypeError, json.JSONDecodeError):
        return {"pass": False, "reason": "output is not JSON"}
    missing = [k for k in row.get("expected", []) if k not in obj]
    return {"pass": not missing, "reason": f"missing keys: {missing}" if missing else ""}


def g_not_empty(row: dict[str, Any], out: Any) -> bool:
    return bool(_text(out).strip())


BUILTINS: dict[str, Grader] = {
    "exact": g_exact,
    "contains": g_contains,
    "regex": g_regex,
    "json_keys": g_json_keys,
    "not_empty": g_not_empty,
}


# ---------------------------------------------------------------- helpers


def load_callable(spec: str) -> Callable[..., Any]:
    if spec in BUILTINS:
        return BUILTINS[spec]
    module, sep, attr = spec.partition(":")
    if not sep:
        raise SystemExit(f"callable spec must look like 'pkg.module:function', got {spec!r}")
    return getattr(importlib.import_module(module), attr)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{n}: invalid JSON ({exc})") from exc
    return rows


def normalize(result: Any) -> Grade:
    if isinstance(result, bool):
        return {"pass": result}
    if isinstance(result, dict) and "pass" in result:
        return {"pass": bool(result["pass"]), **{k: v for k, v in result.items() if k != "pass"}}
    raise TypeError(f"grader must return bool or dict with 'pass', got {result!r}")


def bootstrap_ci(values: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    k = len(values)
    means = sorted(sum(rng.choices(values, k=k)) / k for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def fmt_rate(values: list[float]) -> str:
    if not values:
        return "n/a"
    lo, hi = bootstrap_ci(values)
    return f"{sum(values) / len(values):6.1%}  [{lo:.1%}, {hi:.1%}]  n={len(values)}"


# ---------------------------------------------------------------- run


def run_one(
    row: dict[str, Any], target: Callable[..., Any], graders: dict[str, Grader]
) -> dict[str, Any]:
    t0 = time.perf_counter()
    rec: dict[str, Any] = {"id": row.get("id"), "slice": row.get("slice", "all"), "grades": {}}
    try:
        out = target(row["input"])
        rec["output"] = out
    except Exception as exc:  # a failing target is a result, not a crash
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        for name in graders:
            rec["grades"][name] = {"pass": False, "reason": "target raised"}
        return rec
    rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    for name, grader in graders.items():
        try:
            rec["grades"][name] = normalize(grader(row, out))
        except Exception as exc:  # a broken grader fails the case, not the run
            rec["grades"][name] = {"pass": False, "reason": f"grader error: {exc}"}
    return rec


def cmd_run(args: argparse.Namespace) -> int:
    rows = read_jsonl(args.dataset)
    if args.limit:
        rows = rows[: args.limit]
    target = load_callable(args.target)
    graders = {g: load_callable(g) for g in args.grader}
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda r: run_one(r, target, graders), rows))
    if args.out:
        with args.out.open("w", encoding="utf-8") as fh:
            for rec in results:
                fh.write(json.dumps(rec, default=str) + "\n")

    errors = sum("error" in r for r in results)
    lat = sorted(r["latency_ms"] for r in results)
    print(
        f"cases={len(results)} target_errors={errors} "
        f"p50={lat[len(lat) // 2]:.0f}ms p95={lat[int(0.95 * (len(lat) - 1))]:.0f}ms"
    )
    failed = False
    for name in graders:
        vals = [float(r["grades"][name]["pass"]) for r in results]
        print(f"\n{name}: {fmt_rate(vals)}")
        by_slice: dict[str, list[float]] = defaultdict(list)
        for r in results:
            by_slice[r["slice"]].append(float(r["grades"][name]["pass"]))
        if len(by_slice) > 1:
            for s, v in sorted(by_slice.items()):
                print(f"  {s:<20} {fmt_rate(v)}")
        if args.min_pass is not None and sum(vals) / len(vals) < args.min_pass:
            failed = True
            print(f"  FAIL: below --min-pass {args.min_pass:.0%}")
    return 1 if failed else 0


# ---------------------------------------------------------------- compare


def cmd_compare(args: argparse.Namespace) -> int:
    base = {r["id"]: r for r in read_jsonl(args.baseline)}
    cand = {r["id"]: r for r in read_jsonl(args.candidate)}
    ids = sorted(set(base) & set(cand), key=str)
    if not ids:
        raise SystemExit("no overlapping ids between baseline and candidate")
    b = [float(base[i]["grades"][args.grader]["pass"]) for i in ids]
    c = [float(cand[i]["grades"][args.grader]["pass"]) for i in ids]
    diffs = [ci - bi for bi, ci in zip(b, c, strict=True)]
    lo, hi = bootstrap_ci(diffs)
    delta = sum(diffs) / len(diffs)
    print(
        f"grader={args.grader} paired n={len(ids)} (dropped {len(set(base) ^ set(cand))} unpaired)"
    )
    print(f"baseline  {sum(b) / len(b):.1%}")
    print(f"candidate {sum(c) / len(c):.1%}")
    print(f"delta     {delta:+.1%}  95% CI [{lo:+.1%}, {hi:+.1%}]")
    verdict = "improvement" if lo > 0 else "regression" if hi < 0 else "no significant change"
    print(f"verdict   {verdict}")
    regressions = [i for i, bi, ci in zip(ids, b, c, strict=True) if bi == 1 and ci == 0]
    fixes = [i for i, bi, ci in zip(ids, b, c, strict=True) if bi == 0 and ci == 1]
    print(f"\nregressions ({len(regressions)}): {regressions[:25]}")
    print(f"fixes       ({len(fixes)}): {fixes[:25]}")
    return 1 if verdict == "regression" else 0


# ---------------------------------------------------------------- calibrate


def cmd_calibrate(args: argparse.Namespace) -> int:
    results = {r["id"]: r for r in read_jsonl(args.results)}
    labels = {r["id"]: bool(r["label"]) for r in read_jsonl(args.labels)}
    ids = sorted(set(results) & set(labels), key=str)
    if not ids:
        raise SystemExit("no overlapping ids between results and labels")
    tp = fp = tn = fn = 0
    disagreements = []
    for i in ids:
        judged = bool(results[i]["grades"][args.grader]["pass"])
        human = labels[i]
        tp += judged and human
        tn += (not judged) and (not human)
        fp += judged and not human
        fn += (not judged) and human
        if judged != human:
            disagreements.append((i, human, results[i]["grades"][args.grader].get("reason", "")))
    n = len(ids)
    acc = (tp + tn) / n
    p_yes = ((tp + fp) / n) * ((tp + fn) / n)
    p_no = ((tn + fn) / n) * ((tn + fp) / n)
    pe = p_yes + p_no
    kappa = (acc - pe) / (1 - pe) if pe < 1 else 1.0
    tpr = tp / (tp + fn) if tp + fn else float("nan")
    tnr = tn / (tn + fp) if tn + fp else float("nan")
    print(f"grader={args.grader} n={n}")
    print(f"accuracy {acc:.1%}  TPR {tpr:.1%}  TNR {tnr:.1%}  Cohen's kappa {kappa:.2f}")
    print(f"confusion: TP={tp} FP={fp} TN={tn} FN={fn}")
    print("\nDisagreements (id, human_label, judge_reason):")
    for i, human, reason in disagreements[:50]:
        print(f"  {i}\thuman={'pass' if human else 'fail'}\t{str(reason)[:120]}")
    ok = kappa >= args.min_kappa and (math.isnan(tnr) or tnr >= args.min_tnr)
    if not ok:
        print(f"\nFAIL: judge below thresholds (kappa >= {args.min_kappa}, TNR >= {args.min_tnr})")
    return 0 if ok else 1


# ---------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run target over dataset and grade")
    r.add_argument("--dataset", type=Path, required=True)
    r.add_argument("--target", required=True)
    r.add_argument("--grader", action="append", required=True)
    r.add_argument("--out", type=Path)
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--limit", type=int)
    r.add_argument("--min-pass", type=float, help="exit 1 if any grader's pass rate is below this")
    r.add_argument("--pythonpath", action="append", default=[], help="extra import roots")

    c = sub.add_parser("compare", help="paired baseline vs candidate comparison")
    c.add_argument("--baseline", type=Path, required=True)
    c.add_argument("--candidate", type=Path, required=True)
    c.add_argument("--grader", required=True)

    k = sub.add_parser("calibrate", help="judge vs human-label agreement")
    k.add_argument("--results", type=Path, required=True)
    k.add_argument("--labels", type=Path, required=True)
    k.add_argument("--grader", required=True)
    k.add_argument("--min-kappa", type=float, default=0.6)
    k.add_argument("--min-tnr", type=float, default=0.8, help="judges must catch real failures")

    args = p.parse_args(argv)
    for extra in [*getattr(args, "pythonpath", []), "."]:
        sys.path.insert(0, str(Path(extra).resolve()))
    return {"run": cmd_run, "compare": cmd_compare, "calibrate": cmd_calibrate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
