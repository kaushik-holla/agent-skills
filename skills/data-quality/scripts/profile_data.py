#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.20"]
# ///
"""Profile a dataset and flag data-quality issues, as Markdown (data-quality skill).

Reads CSV, TSV, Parquet, JSON, or NDJSON with Polars. Reports shape, per-column types,
nulls, cardinality, numeric stats, top values, and flags: all-null or constant columns,
high null rates, placeholder values ("N/A", "-", -999 ...), numbers stored as strings,
stray whitespace, ID-like columns, duplicate rows or keys, IQR outliers, future dates,
and features suspiciously correlated with the target. Optional drift vs a baseline file
(null-rate change, mean shift, PSI, unseen categories).

Usage
  uv run profile_data.py data.csv
  uv run profile_data.py data.parquet --key order_id --target churned
  uv run profile_data.py current.parquet --baseline train.parquet       # drift
  uv run profile_data.py data.csv --fail-on-issues > profile.md          # CI gate
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path

import polars as pl

PLACEHOLDER_STR = {
    "",
    "na",
    "n/a",
    "nan",
    "null",
    "none",
    "-",
    "--",
    "?",
    "unknown",
    "missing",
    "tbd",
    "#n/a",
}
PLACEHOLDER_NUM = {-1, -99, -999, -9999, 999, 9999, 99999}


def read(path: Path) -> pl.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pl.read_parquet(path)
    if suffix in {".ndjson", ".jsonl"}:
        return pl.read_ndjson(path)
    if suffix == ".json":
        return pl.read_json(path)
    sep = "\t" if suffix in {".tsv", ".tab"} else ","
    return pl.read_csv(path, separator=sep, infer_schema_length=10_000, try_parse_dates=True)


def fmt(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4g}"
    s = str(v)
    return s if len(s) <= 24 else s[:21] + "..."


def column_issues(df: pl.DataFrame, name: str, null_threshold: float) -> list[str]:
    s = df[name]
    n = len(s)
    issues: list[str] = []
    nulls = s.null_count()
    if n and nulls == n:
        return ["all values null"]
    if n and nulls / n > null_threshold:
        issues.append(f"{nulls / n:.0%} null")
    non_null = s.drop_nulls()
    n_unique = non_null.n_unique()
    if n_unique == 1:
        issues.append("constant (single distinct value)")
    if s.dtype == pl.String:
        lowered = non_null.str.strip_chars().str.to_lowercase()
        placeholders = int(lowered.is_in(list(PLACEHOLDER_STR)).sum())
        if placeholders:
            issues.append(
                f"{placeholders} placeholder strings (e.g. 'N/A', '-', '') that should be null"
            )
        padded = int((non_null != non_null.str.strip_chars()).sum())
        if padded:
            issues.append(f"{padded} values with leading/trailing whitespace")
        real = non_null.filter(~lowered.is_in(list(PLACEHOLDER_STR)))
        numeric_like = (
            real.str.strip_chars().str.replace_all(",", "").cast(pl.Float64, strict=False)
        )
        if len(real) and numeric_like.null_count() < 0.05 * len(real):
            issues.append(
                "numbers stored as strings (cast after nulling placeholders, then re-profile)"
            )
        case_variants = n_unique - lowered.n_unique()
        if case_variants > 0 and n_unique <= 1000:
            issues.append(f"{case_variants} categories differ only by case/whitespace")
    if s.dtype.is_numeric() and len(non_null):
        if s.dtype.is_float():
            bad = int(non_null.is_nan().sum() + non_null.is_infinite().sum())
            if bad:
                issues.append(f"{bad} NaN/inf values")
        hits = int(non_null.cast(pl.Float64).is_in([float(v) for v in PLACEHOLDER_NUM]).sum())
        if hits > 0.01 * len(non_null) and n_unique > 10:
            issues.append(f"{hits} sentinel-looking values ({sorted(PLACEHOLDER_NUM)})")
        q1, q3 = non_null.quantile(0.25), non_null.quantile(0.75)
        if q1 is not None and q3 is not None and q3 > q1:
            iqr = q3 - q1
            out = int(((non_null < q1 - 3 * iqr) | (non_null > q3 + 3 * iqr)).sum())
            if out:
                issues.append(f"{out} extreme outliers (beyond 3x IQR)")
    if s.dtype in (pl.Date, pl.Datetime) and len(non_null):
        today = date.today()
        mx = non_null.max()
        if isinstance(mx, datetime):
            mx = mx.date()
        if isinstance(mx, date) and mx > today:
            future = int((non_null.cast(pl.Date) > today).sum())
            issues.append(f"{future} dates in the future (max {mx})")
    if (
        n > 50
        and n_unique == len(non_null) == n
        and s.dtype in (pl.String, pl.Int64, pl.Int32, pl.UInt64)
    ):
        issues.append("unique per row (ID-like; exclude from features)")
    return issues


def profile(df: pl.DataFrame, args: argparse.Namespace) -> int:
    n = df.height
    print(f"# Data profile: {args.path}\n")
    print(f"- rows: {n:,}  columns: {df.width}  in-memory: {df.estimated_size('mb'):.1f} MB")
    dup_rows = int(df.is_duplicated().sum()) if n else 0
    print(f"- fully duplicated rows: {dup_rows:,}")
    issues: list[tuple[str, str]] = []
    if dup_rows:
        issues.append(("<table>", f"{dup_rows} duplicated rows"))
    if args.key:
        missing = [k for k in args.key if k not in df.columns]
        if missing:
            raise SystemExit(f"key column(s) not found: {missing}")
        dup_keys = int(df.select(args.key).is_duplicated().sum())
        null_keys = int(df.select(pl.any_horizontal(pl.col(args.key).is_null())).to_series().sum())
        print(f"- key {args.key}: {dup_keys:,} rows share a key, {null_keys:,} null keys")
        if dup_keys or null_keys:
            issues.append(
                (",".join(args.key), f"key not unique/non-null ({dup_keys} dup, {null_keys} null)")
            )

    print("\n## Columns\n")
    print("| column | dtype | null % | distinct | min | median | max | top values |")
    print("| --- | --- | ---: | ---: | --- | --- | --- | --- |")
    for name in df.columns:
        s = df[name]
        nn = s.drop_nulls()
        mn = md = mx = None
        if len(nn) and (s.dtype.is_numeric() or s.dtype.is_temporal()):
            mn, mx = nn.min(), nn.max()
            md = nn.median() if s.dtype.is_numeric() else None
        top = ""
        if s.dtype in (pl.String, pl.Categorical, pl.Boolean) or (
            s.dtype.is_integer() and nn.n_unique() <= 20
        ):
            vc = nn.value_counts(sort=True).head(3)
            top = ", ".join(f"{fmt(r[0])} ({r[1]})" for r in vc.iter_rows())
        null_pct = s.null_count() / n if n else 0.0
        print(
            f"| {name} | {s.dtype} | {null_pct:.1%} | {nn.n_unique():,} | {fmt(mn)} | {fmt(md)} | {fmt(mx)} | {top} |"
        )
        issues += [(name, i) for i in column_issues(df, name, args.null_threshold)]

    if args.target:
        if args.target not in df.columns:
            raise SystemExit(f"target column not found: {args.target}")
        t = df[args.target]
        print(f"\n## Target `{args.target}`\n")
        vc = t.value_counts(sort=True).head(10)
        print(
            "- distribution: "
            + ", ".join(f"{fmt(r[0])}: {r[1]} ({r[1] / n:.1%})" for r in vc.iter_rows())
        )
        if t.dtype.is_numeric() or t.dtype == pl.Boolean:
            tf = t.cast(pl.Float64)
            for name in df.columns:
                if name == args.target or not df[name].dtype.is_numeric():
                    continue
                pair = pl.DataFrame({"x": df[name].cast(pl.Float64), "y": tf}).drop_nulls()
                if pair.height < 3:
                    continue
                r = pair.select(pl.corr("x", "y")).item()
                if r is not None and not math.isnan(r) and abs(r) > args.leak_corr:
                    issues.append((name, f"|corr| with target = {abs(r):.3f}; check for leakage"))
                elif pair["y"].n_unique() == 2:
                    g = pair.group_by("y").agg(lo=pl.col("x").min(), hi=pl.col("x").max()).sort("y")
                    (lo0, hi0), (lo1, hi1) = g.select("lo", "hi").rows()
                    if lo1 > hi0 or lo0 > hi1:
                        issues.append(
                            (
                                name,
                                "a single threshold perfectly separates the target; "
                                "likely leakage (is it known at prediction time?)",
                            )
                        )

    if args.baseline:
        issues += drift(df, read(args.baseline), args.baseline)

    print("\n## Issues\n")
    for col, msg in issues:
        print(f"- **{col}**: {msg}")
    if not issues:
        print("- none found")
    return 1 if issues and args.fail_on_issues else 0


def psi(base: pl.Series, cur: pl.Series, bins: int = 10) -> float | None:
    base, cur = base.drop_nulls(), cur.drop_nulls()
    if base.dtype.is_float():
        base, cur = base.drop_nans(), cur.drop_nans()
    if len(base) < bins or len(cur) < bins:
        return None
    edges = sorted({base.quantile(i / bins) for i in range(1, bins)})
    bounds = [-math.inf, *edges, math.inf]
    total = 0.0
    for lo, hi in pairwise(bounds):
        b = max(int(((base > lo) & (base <= hi)).sum()) / len(base), 1e-4)
        c = max(int(((cur > lo) & (cur <= hi)).sum()) / len(cur), 1e-4)
        total += (c - b) * math.log(c / b)
    return total


def drift(cur: pl.DataFrame, base: pl.DataFrame, base_path: Path) -> list[tuple[str, str]]:
    print(f"\n## Drift vs `{base_path}`\n")
    print("| column | null % base -> cur | mean shift (std) | PSI | new categories |")
    print("| --- | --- | ---: | ---: | --- |")
    issues: list[tuple[str, str]] = []
    for name in [c for c in cur.columns if c in base.columns]:
        b, c = base[name], cur[name]
        bn = b.null_count() / max(len(b), 1)
        cn = c.null_count() / max(len(c), 1)
        shift = p = None
        new: list[str] = []
        if b.dtype.is_numeric() and c.dtype.is_numeric():
            std = b.std()
            if std and b.mean() is not None and c.mean() is not None:
                shift = (c.mean() - b.mean()) / std
            p = psi(b.cast(pl.Float64), c.cast(pl.Float64))
        elif b.dtype == pl.String and c.dtype == pl.String and b.n_unique() <= 1000:
            new = sorted(set(c.drop_nulls().unique()) - set(b.drop_nulls().unique()))
        print(
            f"| {name} | {bn:.1%} -> {cn:.1%} | {fmt(shift)} | {fmt(p)} | {', '.join(map(str, new[:5]))} |"
        )
        if abs(cn - bn) > 0.05:
            issues.append((name, f"null rate changed {bn:.1%} -> {cn:.1%}"))
        if p is not None and p > 0.25:
            issues.append((name, f"PSI {p:.2f} (> 0.25 = major distribution shift)"))
        if new:
            issues.append((name, f"{len(new)} categories unseen in baseline: {new[:5]}"))
    for name in sorted(set(base.columns) ^ set(cur.columns)):
        issues.append(
            (name, "column added" if name in cur.columns else "column missing vs baseline")
        )
    return issues


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("path", type=Path)
    p.add_argument("--key", nargs="+", help="column(s) that should uniquely identify a row")
    p.add_argument("--target", help="label column; enables leakage hints")
    p.add_argument("--baseline", type=Path, help="reference dataset for drift checks")
    p.add_argument(
        "--null-threshold", type=float, default=0.2, help="flag columns above this null rate"
    )
    p.add_argument(
        "--leak-corr", type=float, default=0.95, help="flag |corr| with target above this"
    )
    p.add_argument("--fail-on-issues", action="store_true", help="exit 1 if any issue is found")
    args = p.parse_args(argv)
    return profile(read(args.path), args)


if __name__ == "__main__":
    sys.exit(main())
