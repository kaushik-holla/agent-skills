#!/usr/bin/env python3
"""Pre-flight checks before opening or updating a pull request (git-pr-workflow skill).

Compares your branch (including uncommitted changes) with its merge-base on the target
branch and reports: PR size, likely secrets and conflict markers in added lines, large
or binary files, debug leftovers, notebooks with outputs, source changes without tests,
and commit messages that break Conventional Commits or need squashing. Optionally runs
your own checks (tests, lint) and includes their results.

Usage
  python pr_preflight.py                          # auto-detect origin/main, main, or master
  python pr_preflight.py --base origin/develop
  python pr_preflight.py --run "uv run pytest -q" --run "uvx ruff check ."
  python pr_preflight.py --strict                 # warnings also fail (exit 1)

Exit 1 on errors (secrets, conflict markers, failing --run commands). Stdlib only; Python 3.10+.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAX_LINES = 400
LARGE_FILE_BYTES = 1_000_000
GENERATED = re.compile(r"(\.lock$|lock\.json$|\.min\.(js|css)$|^dist/|^build/|_pb2\.py$|\.snap$)")
SECRETS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Databricks token", re.compile(r"\bdapi[A-Za-z0-9]{32,}(?:-\d)?\b")),
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"),
    ),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    (
        "LLM API key (OpenAI/Claude/OpenRouter)",
        re.compile(
            r"\bsk-(?:ant-(?:api\d{2}-|oat\d{2}-)?|proj-|svcacct-|or-v1-)?[A-Za-z0-9_-]{20,}"
        ),
    ),
    ("Google/Gemini API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("Groq API key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("xAI API key", re.compile(r"\bxai-[A-Za-z0-9]{20,}\b")),
    ("Perplexity API key", re.compile(r"\bpplx-[A-Za-z0-9]{20,}\b")),
    ("Replicate token", re.compile(r"\br8_[A-Za-z0-9]{20,}\b")),
    ("LangSmith API key", re.compile(r"\blsv2_(?:pt|sk)_[0-9a-f]{32}_[0-9a-f]{10}\b")),
    ("npm/PyPI token", re.compile(r"\b(?:npm_[A-Za-z0-9]{36}|pypi-AgEI[A-Za-z0-9_-]{20,})\b")),
    (
        "hardcoded credential",
        re.compile(
            r"""(?i)\b\w*(api[_-]?key|secret|password|passwd|token)\w*\s*[:=]\s*['"][^'"\s]{8,}['"]"""
        ),
    ),
]
DEBUG = [
    ("breakpoint()", re.compile(r"^\s*(breakpoint\(\)|import i?pdb|i?pdb\.set_trace\(\))")),
    ("console.log", re.compile(r"\bconsole\.log\(")),
    ("debugger statement", re.compile(r"^\s*debugger;?\s*$")),
    ("focused test (.only)", re.compile(r"\b(it|describe|test)\.only\(")),
    ("skipped test", re.compile(r"@pytest\.mark\.skip\b(?!if)|\b(it|describe|test)\.skip\(")),
]
CONFLICT = re.compile(r"^(<{7}|>{7}|={7})( |$)")
CONVENTIONAL = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([\w./-]+\))?!?: \S"
)
TEST_PATH = re.compile(
    r"(^|/)(tests?/|test_[^/]+\.py$|[^/]+_test\.(py|go)$|[^/]+\.(test|spec)\.[jt]sx?$)"
)
SOURCE_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)


def git(*args: str, check: bool = True) -> str:
    res = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


def detect_base() -> str:
    for ref in ("origin/main", "origin/master", "main", "master"):
        if (
            subprocess.run(
                ["git", "rev-parse", "--verify", "-q", ref], capture_output=True
            ).returncode
            == 0
        ):
            return ref
    raise SystemExit("could not find a base branch; pass --base")


def added_lines(merge_base: str) -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    current, line_no = None, 0
    for line in git("diff", "-U0", "--no-color", merge_base).splitlines():
        if line.startswith("+++ "):
            current = None if line.endswith("/dev/null") else line[6:]
            out.setdefault(current or "", [])
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 0
        elif line.startswith("+") and current:
            out[current].append((line_no, line[1:]))
            line_no += 1
    return out


def untracked() -> list[str]:
    return [p for p in git("ls-files", "--others", "--exclude-standard").splitlines() if p]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base", help="target branch (default: origin/main, main, or master)")
    p.add_argument(
        "--run", action="append", default=[], help="command to run, e.g. 'uv run pytest -q'"
    )
    p.add_argument("--max-lines", type=int, default=MAX_LINES)
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = p.parse_args(argv)

    base = args.base or detect_base()
    mb = git("merge-base", base, "HEAD").strip()
    r = Report()

    numstat = [ln.split("\t") for ln in git("diff", "--numstat", mb).splitlines() if ln]
    new_files = untracked()
    files = [f for _, _, f in numstat] + new_files
    counted = [(a, d, f) for a, d, f in numstat if a != "-" and not GENERATED.search(f)]
    total = sum(int(a) + int(d) for a, d, _ in counted)
    r.info.append(
        f"base {base} @ {mb[:10]}; {len(files)} files, {total} changed lines (excl. generated)"
    )
    if total > args.max_lines:
        r.warnings.append(
            f"{total} changed lines > {args.max_lines}: consider splitting into stacked PRs"
        )
    if git("status", "--porcelain").strip():
        r.warnings.append("uncommitted changes present (included in this check, not in the PR yet)")
    if new_files:
        r.warnings.append(f"untracked files not in the PR: {new_files[:5]}")

    for a, _, f in numstat:
        if a == "-":
            r.warnings.append(f"binary file changed: {f}")
    for f in files:
        path = Path(f)
        if path.is_file() and path.stat().st_size > LARGE_FILE_BYTES:
            r.warnings.append(
                f"large file ({path.stat().st_size / 1e6:.1f} MB): {f}; use LFS or object storage"
            )
        if (
            f.endswith(".ipynb")
            and path.is_file()
            and re.search(r'"outputs":\s*\[\s*\{', path.read_text(errors="ignore"))
        ):
            r.warnings.append(f"notebook with outputs: {f} (clear outputs or use nbstripout)")
        if re.search(r"(^|/)\.env($|\.)", f) and not f.endswith(
            (".example", ".sample", ".template")
        ):
            r.errors.append(f"environment file in the diff: {f}")

    for f, lines in added_lines(mb).items():
        for n, text in lines:
            for label, rx in SECRETS:
                if rx.search(text) and "example" not in f.lower():
                    r.errors.append(f"possible {label}: {f}:{n}")
            if CONFLICT.match(text):
                r.errors.append(f"conflict marker: {f}:{n}")
            for label, rx in DEBUG:
                if rx.search(text):
                    r.warnings.append(f"{label}: {f}:{n}")
        todos = sum(1 for _, t in lines if re.search(r"\b(TODO|FIXME|XXX)\b", t))
        if todos:
            r.info.append(f"{todos} new TODO/FIXME in {f}")

    src = [f for f in files if f.endswith(SOURCE_EXT) and not TEST_PATH.search(f)]
    tests = [f for f in files if TEST_PATH.search(f)]
    if src and not tests:
        r.warnings.append(f"{len(src)} source files changed but no test files: {src[:3]}")

    commits = [c for c in git("log", "--format=%h%x00%s", f"{mb}..HEAD").splitlines() if c]
    for line in commits:
        sha, subject = line.split("\0", 1)
        if subject.startswith(("fixup!", "squash!", "amend!")):
            r.warnings.append(f"{sha} '{subject}': run git rebase -i --autosquash {base}")
        elif re.match(r"(?i)^(wip|tmp|temp|asdf|fix|update|changes)\b\s*$", subject):
            r.warnings.append(f"{sha} '{subject}': uninformative message")
        elif not CONVENTIONAL.match(subject):
            r.info.append(f"{sha} '{subject}': not Conventional Commits (type(scope): summary)")
        if len(subject) > 72:
            r.info.append(f"{sha} subject is {len(subject)} chars (> 72)")
    r.info.append(f"{len(commits)} commits on branch")

    for cmd in args.run:
        res = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
        if res.returncode == 0:
            r.info.append(f"PASS `{cmd}`")
        else:
            tail = (res.stdout + res.stderr).strip().splitlines()[-5:]
            r.errors.append(f"FAIL `{cmd}` (exit {res.returncode}): " + " | ".join(tail))

    for title, items in (("Errors", r.errors), ("Warnings", r.warnings), ("Info", r.info)):
        print(f"## {title} ({len(items)})")
        for item in items:
            print(f"- {item}")
        print()
    failed = bool(r.errors) or (args.strict and bool(r.warnings))
    print("PRE-FLIGHT FAILED" if failed else "PRE-FLIGHT OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
