#!/usr/bin/env bash
# Run the python-quality "definition of done" gates in order and stop at the first failure.
#
# Usage: bash quality_gate.sh [--fix]
#   --fix   apply ruff autofixes and formatting before checking
#
# Uses `uv run` when uv and pyproject.toml are present; otherwise runs tools from the active env.
set -euo pipefail

FIX=0
[[ "${1:-}" == "--fix" ]] && FIX=1

if command -v uv >/dev/null 2>&1 && [[ -f pyproject.toml ]]; then
  RUN=(uv run)
else
  RUN=()
fi

step() {
  echo
  echo "==> $*"
  "$@"
}

if [[ $FIX -eq 1 ]]; then
  step ${RUN[@]+"${RUN[@]}"} ruff check --fix .
  step ${RUN[@]+"${RUN[@]}"} ruff format .
fi

step ${RUN[@]+"${RUN[@]}"} ruff check .
step ${RUN[@]+"${RUN[@]}"} ruff format --check .
step ${RUN[@]+"${RUN[@]}"} mypy
step ${RUN[@]+"${RUN[@]}"} pytest -q

echo
echo "==> secret scan (tracked files)"
SECRET_RE='(sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git grep -nIE "$SECRET_RE" -- . ':!*.lock' ':!*.example'; then
    echo "Possible secret found above. Remove it and rotate the key." >&2
    exit 1
  fi
fi

echo
echo "All quality gates passed."
