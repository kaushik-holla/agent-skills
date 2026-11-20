---
name: python-quality
description: Standard Python project setup and quality gates using uv (or conda + pip), src-layout, ruff (lint + format), mypy strict, pytest 9 + pytest-asyncio, pre-commit, and GitHub Actions with setup-uv. Ships pyproject.toml, pre-commit, CI, .env, and environment.yml templates, a one-command quality gate script, and LLM-friendly test patterns. Use when scaffolding a new Python project, upgrading older tooling (pip/poetry/black/flake8/isort) to the uv + ruff stack, adding CI, or setting up tests for an agentic or LLM project.
license: MIT
metadata:
  author: kaushik-holla
  version: "2.0"
---

# Python Quality

A consistent, low-friction baseline for new Python projects. Optimised for fast iteration plus principal-level quality signals (typed strictness on `src/`, ruff for everything else, tests that don't lie).

> Throughout this skill, `myapp` is a placeholder for your package name. Replace it (and the `myapp` CLI / env / project names) with your own.

## When To Use

- Scaffolding a new Python project (sections 1-4).
- Upgrading an older project's tooling (sections 2, 4, 8). Typical migration: `pip`/`poetry` to `uv`, `black` + `isort` + `flake8` to `ruff`, `setup.py` to `pyproject.toml`.
- Adding CI (section 6).
- Setting up tests for an agentic / LLM project (section 5 and [references/testing-patterns.md](references/testing-patterns.md)).

## Bundled files

| File | Copy to | Purpose |
| --- | --- | --- |
| [assets/pyproject.toml](assets/pyproject.toml) | `pyproject.toml` | Project metadata, dependency groups, ruff, mypy, pytest, coverage config |
| [assets/pre-commit-config.yaml](assets/pre-commit-config.yaml) | `.pre-commit-config.yaml` | Same checks locally that CI runs remotely |
| [assets/ci.yml](assets/ci.yml) | `.github/workflows/ci.yml` | Lint + type + test + audit gate on every PR |
| [assets/env.example](assets/env.example) | `.env.example` | Placeholder-only env file |
| [assets/environment.yml](assets/environment.yml) | `environment.yml` | Conda option for mixed-binary stacks |
| [scripts/quality_gate.sh](scripts/quality_gate.sh) | run in place | Runs every gate in section 7, stops at first failure |

## 1. Bootstrap the environment

Pick one environment manager and stay consistent across the team and CI. Both options keep the dependency declaration in one place (`pyproject.toml`) and install the project itself in editable mode.

- **uv**: fastest resolver/installer, manages Python versions, lockfile, and tools; the modern default for pure-Python and most agentic/LLM projects.
- **conda + pip**: preferred when you need mixed-binary scientific stacks (CUDA, MKL, GDAL, etc.) or a non-pip-managed interpreter.

### Option A: uv (recommended default)

```bash
# New project: src-layout package, pinned Python, git repo
uv init --package --python 3.13 myapp && cd myapp

# Runtime and dev dependencies (dev goes into a PEP 735 dependency group)
uv add langgraph langchain langchain-openai pydantic pydantic-settings typer rich
uv add --dev pytest pytest-asyncio pytest-cov ruff mypy pre-commit

# Existing project with a pyproject.toml: create .venv, install project (editable) + dev group
uv sync

# Run anything inside the project env, no activation needed
uv run pytest
uv run ruff check .
uv run mypy
```

`uv sync` writes `uv.lock`; commit it. `uv sync --locked` in CI fails if the lock is stale. One-off tools run without polluting the project: `uvx pip-audit`, `uvx ruff@latest check`.

### Option B: conda + pip

Conda manages the Python interpreter and isolated environment; pip installs the project itself (editable) and all Python deps from `pyproject.toml`. This pairs well with mixed-binary scientific stacks while keeping the dependency declaration in one place.

```bash
# Create an isolated conda env (one-time, from the committed env file)
conda env create -f environment.yml
conda activate myapp

# Install the project in editable mode, plus the dev dependency group (pip >= 25.1)
pip install -e . --group dev

# Run anything inside the activated env (no prefix needed)
pytest
ruff check .
mypy
```

[assets/environment.yml](assets/environment.yml) installs Python from conda-forge and defers every Python package to `pip` against `pyproject.toml`, so the dependency declaration still lives in one place.

Whichever manager you choose, the `src/<package>/` layout (section 3) is important for avoiding import shadowing; `uv init --package` creates it, or run `mkdir -p src/<package> tests` and make sure `[tool.hatch.build.targets.wheel].packages` matches.

## 2. `pyproject.toml`

Start from [assets/pyproject.toml](assets/pyproject.toml). Key decisions it encodes:

- **Dev tools in `[dependency-groups]`, not `[project.optional-dependencies]`.** Optional extras are for users of your package (`myapp[tavily]`); dev tooling is not part of the published package.
- **Floors, not pins, in `pyproject.toml`.** The lockfile (`uv.lock`) pins exact versions.
- `ruff` covers formatting, linting, isort, security (`S`), bugbear, pyupgrade, pytest style: one tool, one config, fast.
- `mypy --strict` on the whole project catches signature drift and silent `Any`. Test files get a lighter regime via the `tool.mypy.overrides` block; the `pydantic.mypy` plugin types your models correctly.
- `asyncio_mode = "auto"` means you don't have to decorate every async test with `@pytest.mark.asyncio`.
- Markers `slow`, `live_llm`, `eval` are skipped by default; opt in with `uv run pytest -m live_llm`.
- Your own `DeprecationWarning`s fail the test run, so deprecated paths can't linger.

Type checker choice: mypy strict is the default here because it is the most mature for strict CI gating. `pyright` (or `basedpyright`) is a solid alternative with better editor integration; Astral's `ty` is very fast but still in beta (0.0.x), so treat it as an extra signal, not the gate.

## 3. `src/` Layout (and why)

```
project_root/
  pyproject.toml
  uv.lock
  .python-version
  src/
    myapp/
      __init__.py
      py.typed
      cli.py
      config.py
      graph/
      agents/
      state/
      tools/
  tests/
    conftest.py
    test_<unit>.py
```

Why `src/` and not a flat package at the root: when tests are run, Python's import system can pick up the package from the current directory instead of the installed venv copy, masking missing dependencies, broken `pyproject.toml`, or missing `__init__.py`. The `src/` layout forces tests to import the **installed** package.

## 4. Configuration and secrets

Copy [assets/env.example](assets/env.example) to `.env.example` (placeholders only; never commit real keys) and load it through one typed settings object.

`src/myapp/config.py`:

```python
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    llm_model: str = Field(default="openai:gpt-6-luna", alias="LLM_MODEL")
    tavily_api_key: SecretStr = Field(default=SecretStr(""), alias="TAVILY_API_KEY")
    search_provider: Literal["mock", "tavily"] = Field(default="mock", alias="SEARCH_PROVIDER")
    persist_checkpoints: bool = Field(default=False, alias="PERSIST_CHECKPOINTS")
    checkpoint_db_path: str = Field(default="./checkpoints.sqlite", alias="CHECKPOINT_DB_PATH")


SETTINGS = Settings()
```

Why this matters: a typed `Settings` singleton is the single source of truth, with no scattered `os.getenv` calls and no surprises. `SecretStr` keeps keys out of reprs, logs, and tracebacks (`settings.openai_api_key.get_secret_value()` when you actually need it). Model IDs live in config because they churn every few months.

Install hooks once: copy [assets/pre-commit-config.yaml](assets/pre-commit-config.yaml) to `.pre-commit-config.yaml`, then `uv run pre-commit install`.

## 5. Testing patterns

Full patterns with code: [references/testing-patterns.md](references/testing-patterns.md). The rules:

- Mock the LLM at the **provider boundary** (the chat-model object), never your own node functions.
- Use deterministic fixtures; pin `temperature=0` only where you compare structured fields.
- Gate real-provider tests behind `@pytest.mark.live_llm`; gate rubric/judge tests behind `@pytest.mark.eval` (see the `llm-evals` skill for building those harnesses).
- Build `Settings` explicitly in tests; never let a real `.env` leak in.
- No `time.sleep`; inject a clock.

## 6. CI: GitHub Actions

Copy [assets/ci.yml](assets/ci.yml) to `.github/workflows/ci.yml`. It uses `astral-sh/setup-uv` (installs uv + the Python from `.python-version`, with caching), `uv sync --locked`, then ruff, mypy, pytest with coverage, and `pip-audit`.

Why this matters: CI runs in 1-2 minutes for a small project, gates merges on lint + type + test, and never depends on real API keys. If the team uses conda locally, CI can still install straight from `pyproject.toml` with uv because the dependency declaration is identical; it avoids recreating the full conda env on every run.

## 7. Quality Gates (definition of "done")

Run them all with `bash scripts/quality_gate.sh` (add `--fix` to autofix first). Before committing or merging:

1. `uv run ruff check .` clean (no errors, no warnings).
2. `uv run ruff format --check .` clean.
3. `uv run mypy` clean.
4. `uv run pytest -q` green.
5. New behavior has at least one test.
6. README updated if surface area changed.
7. No secrets in code; the gate script's `git grep` secret scan returns no results.

These gates are a reasonable baseline for a prototype or small service; production projects add a coverage threshold (`fail_under` in the template), `pip-audit` in CI, secret scanning (`gitleaks` or GitHub push protection), and mutation testing for critical modules.

## 8. Common Mistakes

- Flat layout instead of `src/`. Tests pick up the source tree directly and mask "missing in install" bugs.
- `mypy` non-strict. Defeats the purpose; turn on `strict = true`.
- Pytest auto-mode without `--strict-markers`. Typos in `@pytest.mark.slowww` silently match nothing.
- Tests that call `dotenv.load_dotenv()` at module scope. Real `.env` values leak into tests, breaking determinism.
- Dev tools listed as a `[project.optional-dependencies]` extra. They ship in your package metadata; use `[dependency-groups]`.
- Skipping reproducibility entirely. Commit `uv.lock` (or a `conda-lock` file for conda envs). Without a lock, fresh installs drift.
- Mixing `pip install` into a uv-managed env. Use `uv add` / `uv sync` so `pyproject.toml` and `uv.lock` stay the source of truth.
- Running `python setup.py install` or editing `site-packages`. Use `uv sync` / `pip install -e .`.
- Keeping `black`, `isort`, and `flake8` alongside ruff. Pick ruff and delete the others; duplicate tools disagree.

## 9. References

- `uv`: <https://docs.astral.sh/uv/>
- Dependency groups (PEP 735): <https://packaging.python.org/en/latest/specifications/dependency-groups/>
- `pyproject.toml` guide: <https://packaging.python.org/en/latest/guides/writing-pyproject-toml/>
- `conda`: <https://docs.conda.io/projects/conda/en/stable/>
- `ruff`: <https://docs.astral.sh/ruff/>
- `mypy`: <https://mypy.readthedocs.io/>
- `pytest`: <https://docs.pytest.org/>
- `pytest-asyncio`: <https://pytest-asyncio.readthedocs.io/>
- `pre-commit`: <https://pre-commit.com/>
- `setup-uv`: <https://github.com/astral-sh/setup-uv>
- `pydantic-settings`: <https://docs.pydantic.dev/latest/concepts/pydantic_settings/>
