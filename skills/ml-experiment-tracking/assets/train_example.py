# /// script
# requires-python = ">=3.10"
# dependencies = ["mlflow>=3.4", "scikit-learn>=1.5", "pandas>=2.2"]
# ///
"""Reproducible, tracked training run with MLflow 3 (ml-experiment-tracking skill).

    uv run assets/train_example.py                   # logs to ./mlflow.db
    uv run mlflow ui --backend-store-uri sqlite:///mlflow.db

Shows: one config object, global seeding, a run manifest, params/metrics/dataset lineage,
a logged model with signature, registry versioning, and promotion by alias (not stages).
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from mlflow import MlflowClient
from mlflow.models import infer_signature
from sklearn.datasets import load_breast_cancer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
MODEL_NAME = "breast-cancer-clf"


@dataclass(frozen=True)
class Config:
    seed: int = 42
    test_size: float = 0.2
    C: float = 1.0
    max_iter: int = 500


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)  # affects subprocesses; set before launch for this one


def main() -> None:
    cfg = Config()
    seed_everything(cfg.seed)
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
    mlflow.set_experiment("breast-cancer-baseline")

    data = load_breast_cancer(as_frame=True)
    df: pd.DataFrame = data.frame
    X, y = df.drop(columns="target"), df["target"]
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=cfg.test_size, random_state=cfg.seed, stratify=y
    )

    with mlflow.start_run(run_name=f"logreg-C{cfg.C}") as run:
        manifest = HERE.parent / "scripts" / "run_manifest.py"
        subprocess.run(
            [
                sys.executable,
                str(manifest),
                "capture",
                "--seed",
                str(cfg.seed),
                "--out",
                "run_manifest.json",
            ],
            check=True,
        )
        mlflow.log_dict(json.loads(Path("run_manifest.json").read_text()), "run_manifest.json")
        mlflow.log_params(asdict(cfg))
        mlflow.set_tags({"owner": "my-team", "purpose": "baseline"})
        mlflow.log_input(mlflow.data.from_pandas(df, name="breast_cancer_v1"), context="training")

        model = make_pipeline(StandardScaler(), LogisticRegression(C=cfg.C, max_iter=cfg.max_iter))
        model.fit(X_tr, y_tr)
        proba = model.predict_proba(X_te)[:, 1]
        metrics = {
            "test_roc_auc": roc_auc_score(y_te, proba),
            "test_f1": f1_score(y_te, proba > 0.5),
        }
        mlflow.log_metrics(metrics)

        info = mlflow.sklearn.log_model(
            model,
            name="model",
            signature=infer_signature(X_tr, model.predict(X_tr)),
            input_example=X_te.head(3),
            registered_model_name=MODEL_NAME,
        )

    client = MlflowClient()
    version = info.registered_model_version
    client.set_registered_model_alias(MODEL_NAME, "challenger", version)
    print(f"run={run.info.run_id} metrics={ {k: round(v, 4) for k, v in metrics.items()} }")
    print(f"registered {MODEL_NAME} v{version} as @challenger; promote with:")
    print(f"  MlflowClient().set_registered_model_alias('{MODEL_NAME}', 'champion', {version})")

    loaded = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@challenger")
    if not (loaded.predict(X_te.head(5)) == model.predict(X_te.head(5))).all():
        raise RuntimeError("registered model predictions differ from the in-memory model")


if __name__ == "__main__":
    main()
