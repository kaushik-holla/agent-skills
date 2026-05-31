---
name: "python-quality"
description: "Standard Python project setup using uv or conda + pip, src-layout, ruff (lint + format), mypy strict, pytest + pytest-asyncio, and pre-commit. Includes pyproject.toml, uv, and environment.yml templates, a GitHub Actions CI snippet, and LLM-friendly test patterns. Use when scaffolding a new project or upgrading existing tooling."
---

# Python Quality

A consistent, low-friction baseline for new Python projects. Optimised for fast iteration plus principal-level quality signals (typed strictness on `src/`, ruff for everything else, tests that don't lie).

> Throughout this skill, `myapp` is a placeholder for your package name. Replace it (and the `myapp` CLI / env / project names) with your own.

## When To Use

- Scaffolding a new Python project (sections 1-4).
- Upgrading an older project's tooling (sections 2, 5-6).
- Adding CI (section 7).
- Setting up tests for an agentic / LLM project (section 6 has LLM-specific patterns).

## 1. Bootstrap the environment

Pick one environment manager and stay consistent across the team and CI. Both options below keep the dependency declaration in one place (`pyproject.toml`) and install the project itself in editable mode.

- **uv** — fastest resolver/installer; the modern default for pure-Python and most agentic/LLM projects.
- **conda + pip** — preferred when you need mixed-binary scientific stacks (CUDA, MKL, GDAL, etc.) or a non-pip-managed interpreter.

### Option A: uv (recommended default)

```bash
# Create and use a project-local virtual env, pinned to a Python version
uv venv --python 3.11
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install the project in editable mode with dev extras
uv pip install -e ".[dev]"

# Run anything inside the activated env
python -V
pytest
ruff check .
mypy src
```

uv reads the same `pyproject.toml` as everything else, resolves fast, and produces a reproducible `uv.lock` you can commit (`uv lock`).

### Option B: conda + pip

Conda manages the Python interpreter and isolated environment; pip installs the project itself (editable) and all Python deps from `pyproject.toml`. This pairs well with mixed-binary scientific stacks while keeping the dependency declaration in one place.

```bash
# Create an isolated conda env (one-time, from the committed env file)
conda env create -f environment.yml

# Or, if you prefer to bootstrap before committing environment.yml:
#   conda create -y -n myapp python=3.11 pip

conda activate myapp

# Install the project in editable mode with dev extras
pip install -e ".[dev]"

# Run anything inside the activated env (no prefix needed)
python -V
pytest
ruff check .
mypy src
```

#### `environment.yml` template

```yaml
name: myapp
channels:
  - conda-forge
dependencies:
  - python=3.11
  - pip
  - pip:
      - -e .[dev]
```

This installs Python 3.11 from conda-forge and then defers all Python package installs to `pip` against the project's `pyproject.toml`. Reproducible (`conda env create -f environment.yml`) and pip-friendly — every Python dep (including the project itself, in editable mode) lives in one place: `pyproject.toml`.

Whichever manager you choose, the `src/<package>/` layout below (section 3) is important for avoiding import shadowing; create the directory tree by hand or with `mkdir -p src/<package> tests`, then ensure `[tool.hatch.build.targets.wheel].packages` in `pyproject.toml` matches.

## 2. `pyproject.toml` Template

```toml
[project]
name = "myapp"
version = "0.1.0"
description = "An example agentic application."
readme = "README.md"
requires-python = ">=3.11"
authors = [{ name = "Your Name" }]
license = { text = "MIT" }
dependencies = [
  "langgraph>=1.0,<2",
  "langchain-core>=0.3",
  "langchain-openai>=0.2",
  "pydantic>=2.7",
  "pydantic-settings>=2.4",
  "typer>=0.12",
  "rich>=13",
  "python-dotenv>=1",
]

[project.optional-dependencies]
tavily = ["tavily-python>=0.5"]
tracing = ["langsmith>=0.1"]
sqlite = ["langgraph-checkpoint-sqlite>=2.0"]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.23",
  "pytest-cov>=5",
  "ruff>=0.6",
  "mypy>=1.10",
  "pre-commit>=3.7",
]

[project.scripts]
myapp = "myapp.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/myapp"]

# ---- ruff ----
[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]
extend-exclude = ["docs", "checkpoints"]

[tool.ruff.lint]
select = [
  "E",   # pycodestyle errors
  "F",   # pyflakes
  "I",   # isort
  "B",   # bugbear
  "UP",  # pyupgrade
  "RUF", # ruff-native
  "SIM", # flake8-simplify
  "PIE", # flake8-pie
  "PL",  # pylint subset
  "S",   # flake8-bandit (security)
  "ASYNC",
]
ignore = [
  "E501",      # line length handled by formatter
  "PLR0913",   # too-many-arguments (false-positive heavy)
  "S101",      # assert (we use it in tests)
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S311", "PLR2004"]  # asserts, random, magic numbers

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

# ---- mypy ----
[tool.mypy]
python_version = "3.11"
files = ["src", "tests"]
strict = true
warn_return_any = true
warn_unused_ignores = true
show_error_codes = true

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_decorators = false   # pytest decorators are not typed well

# ---- pytest ----
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = [
  "-ra",
  "--strict-markers",
  "--strict-config",
  "-m", "not live_llm and not eval and not slow",
]
markers = [
  "slow: tests that take more than a few seconds",
  "live_llm: tests that hit a real LLM provider",
  "eval: LLM-as-judge or rubric-based evals",
]

[tool.coverage.run]
source = ["src/myapp"]
omit = ["tests/*", "**/__init__.py"]

[tool.coverage.report]
exclude_lines = [
  "pragma: no cover",
  "raise NotImplementedError",
  "if __name__ == .__main__.:",
  "if TYPE_CHECKING:",
]
```

Why this matters:

- `ruff` covers formatting, linting, isort, security (`S`), bugbear, pyupgrade — one tool, one config, fast.
- `mypy --strict` on the whole project catches signature drift and silent `Any`. Test files get a lighter regime via the `tool.mypy.overrides` block.
- `asyncio_mode = "auto"` means you don't have to decorate every async test with `@pytest.mark.asyncio`.
- Markers `slow`, `live_llm`, `eval` are skipped by default; opt in with `pytest -m live_llm`.

## 3. `src/` Layout (and why)

```
project_root/
  pyproject.toml
  src/
    myapp/
      __init__.py
      cli.py
      graph/
      agents/
      state/
      tools/
  tests/
    conftest.py
    test_<unit>.py
```

Why `src/` and not a flat package at the root: when tests are run, Python's import system can pick up the package from the current directory instead of the installed venv copy — masking missing dependencies, broken `pyproject.toml`, or missing `__init__.py`. The `src/` layout forces tests to import the **installed** package.

## 4. `.pre-commit-config.yaml`

Runs the same checks locally that CI runs remotely, on every commit.

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-added-large-files
        args: ["--maxkb=500"]
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: ["--fix"]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.11.2
    hooks:
      - id: mypy
        additional_dependencies:
          - "pydantic>=2.7"
          - "types-requests"
        args: ["--config-file=pyproject.toml"]
```

Install once (inside your activated env): `pre-commit install`.

## 5. `.env.example`

Only placeholders. Never commit real keys.

```bash
# LLM provider
OPENAI_API_KEY=
LLM_MODEL=gpt-4o-mini

# Optional: real web search via Tavily
TAVILY_API_KEY=
SEARCH_PROVIDER=mock   # one of: mock | tavily

# Optional: LangSmith tracing
LANGCHAIN_TRACING_V2=
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=myapp

# Optional: SQLite persistence for the LangGraph checkpointer
PERSIST_CHECKPOINTS=false
CHECKPOINT_DB_PATH=./checkpoints.sqlite
```

In `src/myapp/config.py`:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    search_provider: str = Field(default="mock", alias="SEARCH_PROVIDER")
    persist_checkpoints: bool = Field(default=False, alias="PERSIST_CHECKPOINTS")
    checkpoint_db_path: str = Field(default="./checkpoints.sqlite", alias="CHECKPOINT_DB_PATH")

SETTINGS = Settings()
```

Why this matters: a typed `Settings` singleton is the single source of truth — no scattered `os.getenv` calls, no surprises.

## 6. Testing Patterns

### 6.1 Layout

```
tests/
  conftest.py          # shared fixtures
  unit/
    test_state.py
    test_routers.py
    test_clarity_node.py
  integration/
    test_graph_happy_path.py
    test_validator_loop.py
    test_interrupt_resume.py
  eval/
    test_synthesis_quality.py   # marked @pytest.mark.eval
```

### 6.2 Mock the LLM at the boundary

```python
import pytest
from unittest.mock import MagicMock

@pytest.fixture
def fake_clarity_model():
    """A ChatOpenAI-shaped mock with a stub `.invoke` returning a Pydantic decision."""
    from myapp.agents.clarity import ClarityDecision
    m = MagicMock()
    m.invoke.return_value = ClarityDecision(status="clear", reason="company named")
    return m
```

Patch the **provider** (not your own function) in the test:

```python
def test_clarity_node_clear(monkeypatch, fake_clarity_model):
    from myapp.agents import clarity
    monkeypatch.setattr(clarity, "clarity_model", fake_clarity_model)
    out = clarity.clarity_node({"messages": [...], "attempts": 0, ...})
    assert out["clarity_status"] == "clear"
```

### 6.3 Deterministic fixtures

```python
@pytest.fixture
def fixed_company_query():
    from langchain_core.messages import HumanMessage
    return [HumanMessage(content="Tell me about Apple Inc.")]
```

### 6.4 Marker-gated live LLM tests

```python
import pytest

@pytest.mark.live_llm
def test_real_clarity_call(real_openai_settings):
    # Only runs with: pytest -m live_llm
    ...
```

### 6.5 No `time.sleep` in tests

Use `pytest-asyncio` time fixtures, or refactor the code to take a clock dependency, rather than sleeping in tests. Sleeps make CI flaky.

## 7. CI: GitHub Actions

`.github/workflows/ci.yml`:

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install
        run: pip install -e ".[dev]"

      - name: Ruff (lint)
        run: ruff check .

      - name: Ruff (format check)
        run: ruff format --check .

      - name: Mypy
        run: mypy src

      - name: Pytest
        run: pytest -q
        env:
          # No real keys in CI; tests must mock or be marked live_llm and skipped.
          OPENAI_API_KEY: dummy
```

Why this matters: the CI runs in 1-2 minutes for a small project, gates merges on lint + type + test, and never depends on real API keys. Whatever environment manager you use locally (uv or conda), CI installs straight from `pyproject.toml` via `actions/setup-python` + `pip install -e ".[dev]"` (or `uv pip install --system -e ".[dev]"`) because the dependency resolution is identical and it avoids the cost of recreating the full local env on every run.

## 8. Quality Gates (definition of "done")

Before committing or merging (with your env activated):

1. `ruff check .` clean (no errors, no warnings).
2. `ruff format --check .` clean.
3. `mypy src` clean.
4. `pytest -q` green.
5. New behavior has at least one test.
6. README updated if surface area changed.
7. No secrets in code; `rg -n "sk-[A-Za-z0-9]{20,}"` returns no results.

These gates are a reasonable baseline for a take-home; production projects add coverage thresholds, mutation testing, and security scanners (`pip-audit`, `bandit`).

## 9. Common Mistakes

- Flat layout instead of `src/`. Tests pick up the source tree directly and mask "missing in install" bugs.
- `mypy` non-strict. Defeats the purpose; turn on `strict = true`.
- Pytest auto-mode without `--strict-markers`. Typos in `@pytest.mark.slowww` silently match nothing.
- Tests that import `dotenv.load_dotenv()` at module scope. Real `.env` values leak into tests, breaking determinism.
- Skipping reproducibility entirely. Lock dependencies with `uv lock` (commit `uv.lock`), `pip-compile` (`pip install pip-tools` -> `pip-compile pyproject.toml -o requirements.lock`), or a `conda env export --from-history > environment.lock.yml` snapshot. Without a lock, fresh installs drift.
- Manually editing `site-packages` or running `python setup.py install`. Use `pip install -e .` to keep the install editable and reproducible.

## 10. References

- `uv`: <https://docs.astral.sh/uv/>
- `conda`: <https://conda.io/projects/conda/en/latest/>
- `pip`: <https://pip.pypa.io/en/stable/>
- `pyproject.toml` (PEP 621): <https://packaging.python.org/en/latest/guides/writing-pyproject-toml/>
- `ruff`: <https://docs.astral.sh/ruff/>
- `mypy`: <https://mypy.readthedocs.io/>
- `pytest`: <https://docs.pytest.org/>
- `pre-commit`: <https://pre-commit.com/>
- `pydantic-settings`: <https://docs.pydantic.dev/latest/concepts/pydantic_settings/>
