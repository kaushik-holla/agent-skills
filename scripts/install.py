#!/usr/bin/env python3
"""Install the skills and agents from this repo into one or more agent runtimes.

Skills are copied as-is (they follow the Agent Skills spec, so every runtime reads them).
Agents are written in each runtime's native format:

  cursor    Markdown subagents  -> .cursor/agents/*.md     (skills -> .cursor/skills)
  claude    Markdown subagents  -> .claude/agents/*.md     (skills -> .claude/skills)
  codex     TOML custom agents  -> .codex/agents/*.toml    (skills -> .agents/skills)
  opencode  Markdown + permission blocks -> .opencode/agents (skills -> .opencode/skills)
  agents    skills only         -> .agents/skills          (Gemini CLI, Amp, Cline, ...)

Examples:
  python3 scripts/install.py --target claude --target cursor          # user scope (~)
  python3 scripts/install.py --target codex --scope project --project-dir ../my-repo
  python3 scripts/install.py --target cursor --skill llm-evals --skill rag-pipelines
  python3 scripts/install.py --target opencode --dry-run

Standard library only; Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
AGENTS_DIR = ROOT / "agents"


@dataclass(frozen=True)
class Runtime:
    skills_user: str
    skills_project: str
    agents_user: str | None
    agents_project: str | None
    agent_format: str | None  # "md", "toml", "opencode"


RUNTIMES: dict[str, Runtime] = {
    "cursor": Runtime(
        "~/.cursor/skills", ".cursor/skills", "~/.cursor/agents", ".cursor/agents", "md"
    ),
    "claude": Runtime(
        "~/.claude/skills", ".claude/skills", "~/.claude/agents", ".claude/agents", "md"
    ),
    "codex": Runtime(
        "~/.agents/skills", ".agents/skills", "~/.codex/agents", ".codex/agents", "toml"
    ),
    "opencode": Runtime(
        "~/.config/opencode/skills",
        ".opencode/skills",
        "~/.config/opencode/agents",
        ".opencode/agents",
        "opencode",
    ),
    "agents": Runtime("~/.agents/skills", ".agents/skills", None, None, None),
}


# ---------------------------------------------------------------------------
# Minimal frontmatter handling. The agent files in this repo use a deliberately
# simple shape: top-level scalars plus one nested `opencode:` block.
# scripts/validate.py checks the same files with a real YAML parser.
# ---------------------------------------------------------------------------


@dataclass
class AgentDoc:
    name: str
    scalars: dict[str, str]  # raw top-level `key: value` lines (value unparsed)
    opencode_block: list[str]  # indented lines under `opencode:`, dedented by 2
    body: str


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return json.loads(value)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_agent(path: Path) -> AgentDoc:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing frontmatter")
    end = text.index("\n---\n", 4)
    lines, body = text[4:end].splitlines(), text[end + 5 :]
    scalars: dict[str, str] = {}
    block: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        nested = []
        while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
            nested.append(lines[i])
            i += 1
        if key == "opencode":
            block = [n[2:] for n in nested]
        elif value.strip() in {">", ">-", "|", "|-"}:
            joiner = " " if value.strip().startswith(">") else "\n"
            scalars[key] = json.dumps(joiner.join(n.strip() for n in nested))
        else:
            scalars[key] = value.strip()
    return AgentDoc(_unquote(scalars["name"]), scalars, block, body)


def render_markdown(doc: AgentDoc) -> str:
    head = "\n".join(f"{k}: {v}" for k, v in doc.scalars.items())
    return f"---\n{head}\n---\n{doc.body}"


def render_opencode(doc: AgentDoc) -> str:
    keep = {k: v for k, v in doc.scalars.items() if k in {"name", "description"}}
    head = "\n".join(f"{k}: {v}" for k, v in keep.items())
    extra = "\n".join(doc.opencode_block).rstrip()
    return f"---\n{head}\n{extra}\n---\n{doc.body}"


def render_codex(doc: AgentDoc, source: Path) -> str:
    desc = _unquote(doc.scalars.get("description", ""))
    body = doc.body.strip("\n")
    instructions = f"'''\n{body}\n'''" if "'''" not in body else json.dumps(body)
    return (
        textwrap.dedent(
            f"""\
        # Generated from {source.relative_to(ROOT)} by scripts/install.py. Edit the source, not this file.
        name = {json.dumps(doc.name)}
        description = {json.dumps(desc, ensure_ascii=False)}
        """
        )
        + f"developer_instructions = {instructions}\n"
    )


# ---------------------------------------------------------------------------


def resolve(path_str: str, scope: str, project_dir: Path) -> Path:
    if scope == "user":
        return Path(os.path.expanduser(path_str))
    return project_dir / path_str


def place(src_text_or_dir: str | Path, dest: Path, *, force: bool, dry_run: bool) -> str:
    if dest.exists() or dest.is_symlink():
        if not force:
            return f"skip   {dest} (exists; use --force to replace)"
        if not dry_run:
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src_text_or_dir, Path):
            shutil.copytree(src_text_or_dir, dest, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            dest.write_text(src_text_or_dir, encoding="utf-8")
    return f"write  {dest}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target", action="append", choices=sorted(RUNTIMES), required=True)
    parser.add_argument("--scope", choices=["user", "project"], default="user")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--skill", action="append", help="install only these skills (repeatable)")
    parser.add_argument("--agent", action="append", help="install only these agents (repeatable)")
    parser.add_argument("--skills-only", action="store_true")
    parser.add_argument("--agents-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="replace existing installs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    skills = sorted(p for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").exists())
    agents = sorted(AGENTS_DIR.glob("*.md"))
    if args.skill:
        unknown = set(args.skill) - {p.name for p in skills}
        if unknown:
            parser.error(f"unknown skill(s): {', '.join(sorted(unknown))}")
        skills = [p for p in skills if p.name in args.skill]
    if args.agent:
        unknown = set(args.agent) - {p.stem for p in agents}
        if unknown:
            parser.error(f"unknown agent(s): {', '.join(sorted(unknown))}")
        agents = [p for p in agents if p.stem in args.agent]

    project_dir = args.project_dir.resolve()
    done_skill_dirs: set[Path] = set()
    for target in dict.fromkeys(args.target):
        rt = RUNTIMES[target]
        print(f"[{target}] scope={args.scope}")
        if not args.agents_only:
            base = resolve(
                rt.skills_user if args.scope == "user" else rt.skills_project,
                args.scope,
                project_dir,
            )
            if base in done_skill_dirs:
                print(f"  skills already installed to {base}")
            else:
                done_skill_dirs.add(base)
                for skill in skills:
                    print(
                        "  "
                        + place(skill, base / skill.name, force=args.force, dry_run=args.dry_run)
                    )
        if args.skills_only or rt.agent_format is None:
            continue
        agents_base = resolve(
            rt.agents_user if args.scope == "user" else rt.agents_project,  # type: ignore[arg-type]
            args.scope,
            project_dir,
        )
        for agent_path in agents:
            doc = parse_agent(agent_path)
            if rt.agent_format == "toml":
                text, suffix = render_codex(doc, agent_path), ".toml"
            elif rt.agent_format == "opencode":
                text, suffix = render_opencode(doc), ".md"
            else:
                text, suffix = render_markdown(doc), ".md"
            dest = agents_base / f"{doc.name}{suffix}"
            print("  " + place(text, dest, force=args.force, dry_run=args.dry_run))
    if args.dry_run:
        print("\n(dry run: nothing was written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
