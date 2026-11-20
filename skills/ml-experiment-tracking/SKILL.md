---
name: ml-experiment-tracking
description: Make ML experiments reproducible, comparable, and promotable with MLflow 3 (or Weights & Biases), covering run structure, config management, seeding and determinism, data and code versioning, run manifests, honest evaluation protocols, model registry with aliases, and experiment hygiene. Includes a stdlib run-manifest script (git, packages, lockfile, data and config hashes) and a tested MLflow training example. Use when setting up experiment tracking, when a result cannot be reproduced, when comparing models or hyperparameters, when registering or promoting a model, or when reviewing an ML training pipeline for reproducibility.
license: MIT
metadata:
  author: kaushik-holla
  version: "1.0"
---

# ML Experiment Tracking and Reproducibility

A result you cannot reproduce is an anecdote. Every run should answer five questions without archaeology: **which code, which data, which config, which environment, which seed**, and what it scored on which evaluation protocol.

## When To Use

- Starting a modeling project or adding tracking to notebooks and scripts.
- "We got 0.91 last month and 0.87 now and nobody knows why."
- Hyperparameter sweeps, model comparisons, or ablations.
- Registering, promoting, or rolling back a model.
- Reviewing a training pipeline before it goes to production.

## The Reproducibility Contract

Every tracked run records:

| What | How |
| --- | --- |
| Code | Git commit SHA; refuse or flag dirty trees (`run_manifest.py capture --strict`) |
| Environment | Lockfile hash (`uv.lock`), Python version, key package versions, GPU / driver |
| Data | Content hash or immutable version (DVC, lakeFS, Delta / Iceberg snapshot, dated object path) |
| Config | Single config object logged as params, plus the config file hash |
| Randomness | Seed logged; deterministic settings documented |
| Evaluation | Split definition, metric code version, test set hash |

[scripts/run_manifest.py](scripts/run_manifest.py) captures all of this in one JSON file and diffs two runs:

```bash
python scripts/run_manifest.py capture --data data/train.parquet data/val/ --config configs/exp.yaml \
    --seed 42 --strict --out runs/exp-017/manifest.json -- python train.py --config configs/exp.yaml
python scripts/run_manifest.py check runs/exp-012/manifest.json runs/exp-017/manifest.json
```

`check` flags critical differences (code, data, config, lockfile, seed) and lists changed packages, which is usually the answer to "why did the number change?"

## Workflow

### 1. One config object, logged whole

Use a dataclass, Pydantic model, or Hydra config as the single source of truth. Log it with `mlflow.log_params(asdict(cfg))` and never read hyperparameters from scattered globals or notebook cells.

### 2. Seed and control determinism

```python
import random

import numpy as np


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)  # seeds CPU and all CUDA devices
        torch.backends.cudnn.benchmark = False
        torch.use_deterministic_algorithms(True, warn_only=True)
    except ImportError:
        pass


# Before the process starts: PYTHONHASHSEED=<seed> and, for deterministic cuBLAS,
# CUBLAS_WORKSPACE_CONFIG=:4096:8
```

Full GPU determinism costs speed and some ops do not support it. Choose per project: deterministic for debugging and papers, seeded-but-fast with **multiple seeds** for model selection.

### 3. Track runs with MLflow 3

[assets/train_example.py](assets/train_example.py) is a complete, runnable example (`uv run assets/train_example.py`). The core pattern:

```python
import mlflow
from mlflow.models import infer_signature

mlflow.set_experiment("churn-model")
with mlflow.start_run(run_name="xgb-depth6"):
    mlflow.log_params(asdict(cfg))
    mlflow.log_dict(manifest, "run_manifest.json")
    mlflow.log_input(mlflow.data.from_pandas(train_df, name="churn_2026_10"), context="training")
    for epoch, loss in train(...):
        mlflow.log_metric("val_loss", loss, step=epoch)
    mlflow.log_metrics({"test_auc": auc, "test_f1": f1})
    info = mlflow.sklearn.log_model(
        model,
        name="model",
        signature=infer_signature(X_train, model.predict(X_train)),
        input_example=X_train.head(3),
        registered_model_name="churn-model",
    )
```

- Use `name=` in `log_model` (MLflow 3; `artifact_path` is deprecated).
- `mlflow.autolog()` is a good start for sklearn, XGBoost, LightGBM, and PyTorch Lightning; still log the manifest and your own eval metrics.
- Use a real backend for teams (`MLFLOW_TRACKING_URI` pointing at a tracking server or `sqlite:///mlflow.db` locally); the plain file store does not support the registry.
- Name experiments by problem, runs by the change being tested, and tag runs (`purpose=ablation`, `owner=...`).

**Weights & Biases** equivalent: `wandb.init(project=..., config=asdict(cfg))`, `wandb.log({...}, step=epoch)`, `wandb.save("run_manifest.json")`, and Artifacts for datasets and models.

### 4. Evaluate honestly

- **Split before anything else**; fit preprocessing (scalers, encoders, imputers, target encoding) inside a pipeline on train only.
- **Group and time-aware splits** when rows are correlated (users, sessions, patients) or the model predicts the future (`GroupKFold`, `TimeSeriesSplit`, or a time cutoff).
- **Hold out a test set you look at rarely.** Model selection uses validation or cross-validation; the test set confirms.
- **Report variance**: mean and spread over 3-5 seeds or CV folds, plus a bootstrap CI on the test metric. A 0.3-point gain inside the noise is not a gain.
- **Compare against baselines**: a trivial baseline (majority class, last value) and the current production model on the same split.
- **Slice metrics** by segment (region, device, cohort) and check calibration if probabilities drive decisions.
- See the `data-quality` skill for leakage checks.

### 5. Register and promote with aliases

```python
from mlflow import MlflowClient

client = MlflowClient()
client.set_registered_model_alias("churn-model", "challenger", version)
# after validation / shadow / A-B test:
client.set_registered_model_alias("churn-model", "champion", version)

model = mlflow.pyfunc.load_model(
    "models:/churn-model@champion"
)  # serving code never hardcodes versions
```

Model stages (`Staging` / `Production`) are deprecated; use aliases and tags. Promotion should be a reviewed, logged action with the eval report attached, and rollback is re-pointing the alias.

### 6. Version data and pipelines

- Small/medium files: DVC (`dvc add data/train.parquet`, remote storage, `.dvc` files in git) or dated, immutable object-store paths.
- Tables: Delta Lake / Iceberg snapshots or time travel, and log the snapshot ID.
- Pipelines: make each stage a script with explicit inputs/outputs (DVC pipelines, Make, or an orchestrator) so a rerun recomputes only what changed.

## Checklist

- [ ] Every run logs code SHA, lockfile hash, data version, full config, and seed (manifest attached)
- [ ] Dirty-tree runs are blocked or clearly tagged
- [ ] Preprocessing fit on train only, inside a pipeline
- [ ] Split strategy matches the data (group / time aware where needed)
- [ ] Results reported as mean +/- spread over seeds or folds, with baselines
- [ ] Test set touched only for final confirmation
- [ ] Model logged with signature and input example; registered with an alias
- [ ] Serving loads by alias, never by hardcoded version or local path
- [ ] A teammate can reproduce a run from the tracker alone

## Common Mistakes

- Tracking metrics but not the data version, so reruns silently use new data.
- Tuning hyperparameters against the test set ("just one more look").
- Comparing single-seed runs and celebrating noise.
- Logging models without signatures, so schema drift breaks serving at runtime.
- Promoting by copying files around instead of moving a registry alias.
- Notebook state (out-of-order cells) as the only record of how a model was trained.

## References

- MLflow docs: <https://mlflow.org/docs/latest/>
- MLflow model registry and aliases: <https://mlflow.org/docs/latest/ml/model-registry/>
- PyTorch reproducibility notes: <https://docs.pytorch.org/docs/stable/notes/randomness.html>
- DVC: <https://dvc.org/doc>
- Weights & Biases docs: <https://docs.wandb.ai/>
- scikit-learn, common pitfalls (data leakage): <https://scikit-learn.org/stable/common_pitfalls.html>
