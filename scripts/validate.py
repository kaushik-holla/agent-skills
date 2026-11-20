#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6"]
# ///
"""Validate this repository before committing or releasing.

Checks:
  * Every skills/<name>/SKILL.md has spec-valid YAML frontmatter (Agent Skills spec:
    https://agentskills.io/specification) and stays under the 500-line body budget.
  * Every agents/*.md has valid frontmatter (name, description, portable fields).
  * Every relative Markdown link (and #anchor) resolves.
  * Every Python file compiles; every shell script passes `bash -n`; every JSON file parses.
  * The README lists every skill and agent.

Run:  uv run scripts/validate.py          (uv installs PyYAML on the fly)
 or:  pip install pyyaml && python3 scripts/validate.py
"""

from __future__ import annotations

import json
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only without deps
    sys.exit("PyYAML is required: run `uv run scripts/validate.py` or `pip install pyyaml`.")

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache"}

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SPEC_SKILL_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
# Client-specific extensions that are safe to carry (ignored by other clients).
EXTENSION_SKILL_KEYS = {"paths", "disable-model-invocation"}
AGENT_KEYS = {"name", "description", "tools", "model", "readonly", "is_background", "opencode"}
MAX_SKILL_BODY_LINES = 500

LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

errors: list[str] = []
warnings: list[str] = []


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def err(path: Path, msg: str) -> None:
    errors.append(f"{rel(path)}: {msg}")


def warn(path: Path, msg: str) -> None:
    warnings.append(f"{rel(path)}: {msg}")


def iter_files(pattern: str) -> list[Path]:
    return sorted(p for p in ROOT.rglob(pattern) if not any(part in SKIP_DIRS for part in p.parts))


def split_frontmatter(path: Path) -> tuple[dict | None, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        err(path, "missing YAML frontmatter (file must start with '---')")
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        err(path, "unterminated YAML frontmatter")
        return None, text
    raw, body = text[4:end], text[end + 5 :]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        err(path, f"invalid YAML frontmatter: {exc}")
        return None, body
    if not isinstance(data, dict):
        err(path, "frontmatter must be a YAML mapping")
        return None, body
    return data, body


def check_description(path: Path, desc: object) -> None:
    if not isinstance(desc, str) or not desc.strip():
        err(path, "'description' must be a non-empty string")
        return
    if len(desc) > 1024:
        err(path, f"'description' is {len(desc)} chars (max 1024)")
    if "use when" not in desc.lower() and "use for" not in desc.lower():
        err(path, "'description' should say when to use it (include 'Use when ...')")


def check_skills() -> list[str]:
    names = []
    skill_dirs = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            err(skill_dir, "skill folder has no SKILL.md")
            continue
        data, body = split_frontmatter(skill_md)
        if data is None:
            continue
        name = data.get("name")
        names.append(skill_dir.name)
        if not isinstance(name, str) or not NAME_RE.fullmatch(name) or len(name) > 64:
            err(skill_md, f"invalid 'name' {name!r} (lowercase, digits, single hyphens, <=64)")
        elif name != skill_dir.name:
            err(skill_md, f"'name' {name!r} must match folder name {skill_dir.name!r}")
        check_description(skill_md, data.get("description"))
        compat = data.get("compatibility")
        if compat is not None and (not isinstance(compat, str) or not 1 <= len(compat) <= 500):
            err(skill_md, "'compatibility' must be a 1-500 char string")
        meta = data.get("metadata")
        if meta is not None and (
            not isinstance(meta, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in meta.items())
        ):
            err(skill_md, "'metadata' must map string keys to string values (quote versions)")
        unknown = set(data) - SPEC_SKILL_KEYS - EXTENSION_SKILL_KEYS
        if unknown:
            err(skill_md, f"unknown frontmatter keys: {sorted(unknown)}")
        body_lines = body.count("\n")
        if body_lines > MAX_SKILL_BODY_LINES:
            err(skill_md, f"body is {body_lines} lines (keep under {MAX_SKILL_BODY_LINES})")
    return names


def check_agents() -> list[str]:
    names = []
    for agent_md in sorted((ROOT / "agents").glob("*.md")):
        data, body = split_frontmatter(agent_md)
        if data is None:
            continue
        names.append(agent_md.stem)
        name = data.get("name")
        if name != agent_md.stem:
            err(agent_md, f"'name' {name!r} must match file name {agent_md.stem!r}")
        if isinstance(name, str) and not NAME_RE.fullmatch(name):
            err(agent_md, f"invalid 'name' {name!r}")
        check_description(agent_md, data.get("description"))
        if "tools" in data and not isinstance(data["tools"], str):
            err(agent_md, "'tools' must be a comma-separated string (Claude Code format)")
        if "readonly" in data and not isinstance(data["readonly"], bool):
            err(agent_md, "'readonly' must be a boolean (Cursor format)")
        oc = data.get("opencode")
        if oc is not None and (not isinstance(oc, dict) or oc.get("mode") != "subagent"):
            err(agent_md, "'opencode' must be a mapping with mode: subagent")
        unknown = set(data) - AGENT_KEYS
        if unknown:
            warn(agent_md, f"unrecognised frontmatter keys: {sorted(unknown)}")
        if not body.strip():
            err(agent_md, "agent has an empty prompt body")
    return names


def unfenced_lines(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, line) pairs outside fenced code blocks, honoring fence length."""
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
                continue
            if (
                marker[0] == fence[0]
                and len(marker) >= len(fence)
                and not line.strip()[len(marker) :]
            ):
                fence = None
                continue
        if fence is None:
            out.append((lineno, line))
    return out


def github_slug(heading: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors_for(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        seen: dict[str, int] = {}
        slugs: set[str] = set()
        for _, line in unfenced_lines(path):
            m = HEADING_RE.match(line)
            if m:
                slug = github_slug(m.group(2))
                n = seen.get(slug, 0)
                slugs.add(slug if n == 0 else f"{slug}-{n}")
                seen[slug] = n + 1
        cache[path] = slugs
    return cache[path]


def check_links() -> None:
    cache: dict[Path, set[str]] = {}
    for md in iter_files("*.md"):
        for lineno, line in unfenced_lines(md):
            line_wo_code = re.sub(r"`[^`]*`", "", line)
            for target in LINK_RE.findall(line_wo_code):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto:
                    continue
                file_part, _, anchor = target.partition("#")
                dest = md if not file_part else (md.parent / file_part).resolve()
                if not dest.exists():
                    err(md, f"line {lineno}: broken link -> {target}")
                    continue
                if anchor and dest.suffix == ".md" and anchor not in anchors_for(dest, cache):
                    err(md, f"line {lineno}: missing anchor -> {target}")


def check_code() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for py in iter_files("*.py"):
            try:
                py_compile.compile(str(py), cfile=str(Path(tmp) / "x.pyc"), doraise=True)
            except py_compile.PyCompileError as exc:
                err(py, f"py_compile failed: {exc.msg}")
    bash = shutil.which("bash")
    for sh in iter_files("*.sh"):
        if bash:
            proc = subprocess.run([bash, "-n", str(sh)], capture_output=True, text=True)
            if proc.returncode:
                err(sh, f"bash -n failed: {proc.stderr.strip()}")
    for js in iter_files("*.json"):
        try:
            json.loads(js.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            err(js, f"invalid JSON: {exc}")
    plugin = ROOT / ".claude-plugin" / "plugin.json"
    market = ROOT / ".claude-plugin" / "marketplace.json"
    if plugin.exists() and not json.loads(plugin.read_text()).get("name"):
        err(plugin, "plugin.json requires 'name'")
    if market.exists():
        data = json.loads(market.read_text())
        for key in ("name", "owner", "plugins"):
            if key not in data:
                err(market, f"marketplace.json requires '{key}'")
        for entry in data.get("plugins", []):
            if not {"name", "source"} <= set(entry):
                err(market, "each plugin entry needs 'name' and 'source'")


def check_readme(skills: list[str], agents: list[str]) -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    for s in skills:
        if f"(skills/{s}/SKILL.md)" not in text:
            err(readme, f"skill '{s}' is not linked in the README")
    for a in agents:
        if f"(agents/{a}.md)" not in text:
            err(readme, f"agent '{a}' is not linked in the README")


def main() -> int:
    skills = check_skills()
    agents = check_agents()
    check_links()
    check_code()
    check_readme(skills, agents)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    status = "FAILED" if errors else "OK"
    print(
        f"\n{status}: {len(skills)} skills, {len(agents)} agents, "
        f"{len(errors)} errors, {len(warnings)} warnings"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
