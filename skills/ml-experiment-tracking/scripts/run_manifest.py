#!/usr/bin/env python3
"""Capture and compare reproducibility manifests for ML runs (ml-experiment-tracking skill).

A manifest records what you need to reproduce or explain a result: git commit and dirty
state, Python and platform, GPU, installed packages, lockfile hash, data and config
hashes, seed, command, and selected environment variables.

Usage
  python run_manifest.py capture --data data/train.parquet data/val/ --config configs/exp.yaml \
      --seed 42 --out runs/2026-11-20/manifest.json -- python train.py --lr 3e-4
  python run_manifest.py capture --strict ...           # refuse to run from a dirty git tree
  python run_manifest.py check old/manifest.json new/manifest.json

Log the manifest with your tracker, for example:
  mlflow.log_dict(json.load(open("manifest.json")), "run_manifest.json")
  wandb.save("manifest.json")

`check` exits 1 when critical fields differ (code, data, config, lockfile, seed).
Standard library only; Python 3.10+.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOCKFILES = (
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "pixi.lock",
    "conda-lock.yml",
    "environment.yml",
    "requirements.txt",
    "requirements.lock",
)
ENV_VARS = (
    "CUDA_VISIBLE_DEVICES",
    "PYTHONHASHSEED",
    "CUBLAS_WORKSPACE_CONFIG",
    "OMP_NUM_THREADS",
    "TOKENIZERS_PARALLELISM",
    "HF_HUB_OFFLINE",
)
CRITICAL = ("git.commit", "git.dirty", "git.diff_sha256", "lockfile", "data", "config", "seed")
SKIP_DIRS = {".git", "__pycache__", ".ipynb_checkpoints", ".DS_Store"}


def sh(*cmd: str) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_path(path: Path) -> dict[str, Any]:
    if path.is_file():
        return {"sha256": sha256_file(path), "bytes": path.stat().st_size, "files": 1}
    if path.is_dir():
        h = hashlib.sha256()
        files = total = 0
        for f in sorted(p for p in path.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.parts)):
            h.update(f.relative_to(path).as_posix().encode())
            h.update(sha256_file(f).encode())
            files += 1
            total += f.stat().st_size
        return {"sha256": h.hexdigest(), "bytes": total, "files": files}
    return {"error": "missing"}


def git_info() -> dict[str, Any]:
    commit = sh("git", "rev-parse", "HEAD")
    if commit is None:
        return {"commit": None}
    diff = sh("git", "diff", "HEAD") or ""
    untracked = sh("git", "ls-files", "--others", "--exclude-standard") or ""
    return {
        "commit": commit,
        "branch": sh("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "remote": sh("git", "config", "--get", "remote.origin.url"),
        "dirty": bool(diff or untracked),
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff else None,
        "untracked_files": len(untracked.splitlines()),
    }


def gpu_info() -> list[str]:
    if not shutil.which("nvidia-smi"):
        return []
    out = sh("nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader")
    return out.splitlines() if out else []


def packages() -> dict[str, str]:
    pkgs: dict[str, str] = {}
    for dist in md.distributions():
        name = dist.metadata.get("Name")
        if name:
            pkgs[name.lower()] = dist.version
    return dict(sorted(pkgs.items()))


def capture(
    data: list[str], config: list[str], seed: int | None, command: list[str]
) -> dict[str, Any]:
    pkgs = packages()
    root = Path(sh("git", "rev-parse", "--show-toplevel") or ".")
    lock = next((root / name for name in LOCKFILES if (root / name).is_file()), None)
    return {
        "schema": "run-manifest/1",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": command or sys.argv,
        "seed": seed,
        "git": git_info(),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpus": os.cpu_count(),
        },
        "gpu": gpu_info(),
        "lockfile": {"path": lock.name, "sha256": sha256_file(lock)} if lock else None,
        "packages_sha256": hashlib.sha256(json.dumps(pkgs).encode()).hexdigest(),
        "packages": pkgs,
        "key_packages": {
            k: v
            for k, v in pkgs.items()
            if k
            in {
                "torch",
                "jax",
                "tensorflow",
                "numpy",
                "pandas",
                "polars",
                "scikit-learn",
                "xgboost",
                "lightgbm",
                "transformers",
                "datasets",
                "mlflow",
                "wandb",
                "langgraph",
                "langchain",
            }
        },
        "data": {p: hash_path(Path(p)) for p in data},
        "config": {p: hash_path(Path(p)) for p in config},
        "env": {k: os.environ[k] for k in ENV_VARS if k in os.environ},
    }


def flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and k != "packages":
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def check(a: dict[str, Any], b: dict[str, Any]) -> int:
    fa, fb = flatten(a), flatten(b)
    ignore = {"created_utc", "python.executable"}
    critical = []
    for key in sorted(set(fa) | set(fb)):
        if key in ignore or fa.get(key) == fb.get(key):
            continue
        if key == "packages":
            pa, pb = fa.get(key) or {}, fb.get(key) or {}
            changed = [
                f"{n}: {pa.get(n)} -> {pb.get(n)}"
                for n in sorted(set(pa) | set(pb))
                if pa.get(n) != pb.get(n)
            ]
            print(f"  packages: {len(changed)} changed")
            for line in changed[:15]:
                print(f"    {line}")
            continue
        is_crit = any(key == c or key.startswith(c + ".") for c in CRITICAL)
        critical += [key] if is_crit else []
        print(f"{'! ' if is_crit else '  '}{key}: {fa.get(key)!r} -> {fb.get(key)!r}")
    if critical:
        print(
            f"\nNOT REPRODUCIBLE AS-IS: {len(critical)} critical difference(s) (code, data, config, lock, seed)"
        )
        return 1
    print("\nCritical fields match (code, data, config, lockfile, seed).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="write a manifest for the current run")
    c.add_argument("--data", nargs="*", default=[], help="data files or directories to hash")
    c.add_argument("--config", nargs="*", default=[], help="config files to hash")
    c.add_argument("--seed", type=int)
    c.add_argument("--out", type=Path, default=Path("run_manifest.json"))
    c.add_argument("--strict", action="store_true", help="exit 2 if the git tree is dirty")
    c.add_argument("command", nargs=argparse.REMAINDER, help="training command after --")
    k = sub.add_parser("check", help="compare two manifests")
    k.add_argument("a", type=Path)
    k.add_argument("b", type=Path)
    args = p.parse_args(argv)

    if args.cmd == "check":
        return check(json.loads(args.a.read_text()), json.loads(args.b.read_text()))

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    manifest = capture(args.data, args.config, args.seed, command)
    if args.strict and manifest["git"].get("dirty"):
        print(
            "refusing to capture: git tree is dirty (commit or stash first, or drop --strict)",
            file=sys.stderr,
        )
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")
    g = manifest["git"]
    print(
        f"wrote {args.out}: commit={str(g.get('commit'))[:10]} dirty={g.get('dirty')} "
        f"data={len(args.data)} config={len(args.config)} packages={len(manifest['packages'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
