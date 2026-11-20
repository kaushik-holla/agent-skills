---
name: data-quality
description: Profile, validate, and clean tabular data with Polars (and pandas) before analysis or modeling, covering a fast EDA workflow, type and placeholder fixes, key and join sanity checks, Pandera data contracts, target leakage checks, train/serve drift with PSI, and analysis hygiene. Includes a Polars profiling script that flags duplicates, sentinel values, numbers stored as strings, future dates, leakage, and drift as Markdown. Use when starting work on a new dataset, before training a model, when numbers look wrong after a join or aggregation, when adding data validation to a pipeline, or when checking for drift between datasets.
license: MIT
metadata:
  author: kaushik-holla
  version: "1.0"
---

# Data Quality and Analysis Hygiene

Most modeling bugs are data bugs: a join that fanned out, a sentinel `-999` averaged into revenue, a feature that is only known after the label. Profile first, write down what "valid" means as a contract, and check joins and leakage before you trust any metric.

## When To Use

- First contact with a dataset (new source, new extract, new quarter).
- Before training or evaluating a model.
- A KPI or row count changed unexpectedly after a pipeline or query change.
- Adding validation to an ETL / feature pipeline.
- Comparing training data with production or a newer extract (drift).

## Workflow

### 1. Profile before you analyze

```bash
uv run scripts/profile_data.py data/orders.parquet --key order_id --target churned
uv run scripts/profile_data.py assets/example_orders.csv --key order_id --target churned   # demo
```

[scripts/profile_data.py](scripts/profile_data.py) prints a Markdown profile (types, nulls, cardinality, ranges, top values) and an issues list: duplicated rows and keys, all-null or constant columns, placeholder strings and sentinel numbers, numbers stored as strings, whitespace and case variants, extreme outliers, future dates, ID-like columns, and features that look like target leakage. Paste the output into the PR or notebook so reviewers see what you saw.

The bundled [assets/example_orders.csv](assets/example_orders.csv) contains one of each common problem; run the profiler on it to see what each flag looks like.

### 2. Fix types and missing values explicitly

```python
import polars as pl

PLACEHOLDERS = ["", "N/A", "n/a", "NA", "null", "-", "?", "unknown"]

df = (
    pl.read_csv(
        "orders.csv", null_values=PLACEHOLDERS, infer_schema_length=10_000, try_parse_dates=True
    )
    .with_columns(
        pl.col("amount").str.replace_all(",", "").cast(pl.Float64),
        pl.col("region").str.strip_chars().str.to_uppercase(),
    )
    .with_columns(
        pl.when(pl.col("amount") == -999).then(None).otherwise(pl.col("amount")).alias("amount")
    )
    .unique(maintain_order=True)
)
```

- Decide per column whether missing means unknown, zero, or not applicable, and write that decision down. Never `fillna(0)` blindly.
- Keep a raw copy; cleaning is a reproducible script, not manual edits.
- Re-run the profiler after cleaning: fixing types reveals the next layer of issues (sentinels inside a string column only show up once it is numeric).

### 3. Check every join

Joins are the most common source of silently wrong numbers.

```python
before = orders.height
assert customers["customer_id"].is_unique().all(), "dimension key not unique"

joined = orders.join(customers, on="customer_id", how="left", validate="m:1")
assert joined.height == before, f"join changed row count {before} -> {joined.height}"

unmatched = orders.join(customers, on="customer_id", how="anti")
print(f"{unmatched.height} orders without a customer ({unmatched.height / before:.1%})")
```

pandas equivalent: `orders.merge(customers, on="customer_id", how="left", validate="many_to_one", indicator=True)`, then inspect `_merge`.

Also check: key dtypes match (int vs string IDs silently match nothing), keys are normalized (case, whitespace, leading zeros), and time-bounded joins use the right as-of date (`join_asof`).

### 4. Write a data contract

Turn what you learned into a schema that runs in the pipeline and in tests. Pandera supports both Polars and pandas:

```python
import pandera.polars as pa
import polars as pl


class Orders(pa.DataFrameModel):
    order_id: int = pa.Field(unique=True)
    customer_id: str = pa.Field(str_matches=r"^C\d+$")
    region: str = pa.Field(isin=["EU", "US", "APAC"], nullable=True)
    amount: float = pa.Field(ge=0, le=100_000)
    order_date: pl.Date

    class Config:
        strict = True  # unexpected columns fail
        coerce = True


Orders.validate(df, lazy=True)  # lazy=True reports all failures at once
```

Validate at boundaries: after ingestion, before feature computation, and before writing outputs. Alternatives: Great Expectations / GX, dbt tests, Soda, or SQL assertions in the warehouse.

### 5. Check for leakage before training

- **Temporal**: every feature must be computable at prediction time. Use point-in-time joins and a time-based split when predicting the future.
- **Target proxies**: refunds, cancellation reasons, "account_closed_date," or post-outcome statuses. The profiler flags features with very high correlation or a perfect threshold split against the target.
- **Entity overlap**: the same user, patient, or device in train and test inflates scores. Split by group.
- **Preprocessing leakage**: scalers, imputers, encoders, and feature selection fit on the full data. Fit inside a pipeline on train only.
- **Duplicates across splits**: near-duplicate rows (same text, same image) in train and test.
- If a model looks too good (AUC 0.99 on a hard problem), assume leakage until proven otherwise; check feature importances first.

### 6. Monitor drift

```bash
uv run scripts/profile_data.py data/prod_2026_11.parquet --baseline data/train.parquet --fail-on-issues
```

The drift section reports null-rate changes, mean shift in baseline standard deviations, PSI per numeric column (< 0.1 stable, 0.1-0.25 moderate, > 0.25 major), unseen categories, and added or missing columns. Investigate before retraining: drift is often an upstream bug, not a change in the world.

## Polars and pandas Quick Reference

| Task | Polars | pandas |
| --- | --- | --- |
| Lazy read + filter | `pl.scan_parquet(p).filter(pl.col("x") > 0).collect()` | `pd.read_parquet(p, filters=[("x", ">", 0)])` |
| Group + aggregate | `df.group_by("k").agg(pl.len(), pl.col("v").mean())` | `df.groupby("k").agg(n=("v", "size"), v=("v", "mean"))` |
| Null counts | `df.null_count()` | `df.isna().sum()` |
| Duplicates | `df.is_duplicated().sum()` | `df.duplicated(keep=False).sum()` |
| Validated join | `a.join(b, on="k", how="left", validate="m:1")` | `a.merge(b, on="k", how="left", validate="many_to_one")` |
| Window | `pl.col("v").sum().over("k")` | `df.groupby("k")["v"].transform("sum")` |
| As-of join | `a.join_asof(b, on="ts", by="k")` | `pd.merge_asof(a, b, on="ts", by="k")` |

Prefer Polars (lazy) for large files and pipelines; pandas remains fine for small data and library compatibility. Use DuckDB when SQL is the clearest expression.

## Checklist

- [ ] Profile run and reviewed; output saved with the analysis
- [ ] Placeholders and sentinels converted to null; types explicit
- [ ] Missing-value semantics decided per column
- [ ] Primary key unique and non-null; every join row-count checked with `validate`
- [ ] Data contract (Pandera or equivalent) runs in the pipeline and in tests
- [ ] Leakage review done (temporal, proxies, entity overlap, preprocessing)
- [ ] Drift checked against the training baseline before retraining or scoring
- [ ] Cleaning is a script under version control, not manual edits

## Common Mistakes

- Averaging a column that contains `-999` or `9999` sentinels.
- A many-to-many join that doubles revenue, discovered weeks later.
- Treating `""`, `"N/A"`, and null as different categories.
- Random splits on time-series or grouped data.
- Dropping rows with nulls without checking whether missingness correlates with the target.
- Validating data only in a notebook, never in the production pipeline.

## References

- Polars user guide: <https://docs.pola.rs/>
- Pandera (Polars integration): <https://pandera.readthedocs.io/en/stable/polars.html>
- scikit-learn, common pitfalls and data leakage: <https://scikit-learn.org/stable/common_pitfalls.html>
- Kaufman et al., "Leakage in Data Mining" (KDD 2011): <https://dl.acm.org/doi/10.1145/2020408.2020496>
