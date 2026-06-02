# Agent Skills

**Production-grade skills and subagents for building LangGraph / agentic-Python systems.**

A coordinated set of reference skills and specialist review agents that encode the
workflows a engineer uses to take an agentic Python app from design through
review and ship. Written as plain Markdown so they port across agent runtimes
(OpenCode, Claude Code, Cursor, Codex/Gemini).

## Skills

Reference skills are structured workflows an agent can follow or pull in on demand.

| Skill | What it does | Use when |
| --- | --- | --- |
| [langgraph-patterns](skills/langgraph-patterns/SKILL.md) | Canonical, copy-paste LangGraph recipes: typed state, conditional routers, interrupt/resume, checkpointers, structured outputs, tool/MCP wiring, subgraphs, parallel branches, async streaming, tracing, plus a common-mistakes catalog and testing patterns. | Designing or implementing any LangGraph node, router, or graph. |
| [python-quality](skills/python-quality/SKILL.md) | A low-friction Python baseline: uv or conda env setup, src-layout, ruff (lint + format), mypy strict, pytest, pre-commit, a CI snippet, and LLM-friendly test patterns. | Scaffolding a new Python project or upgrading existing tooling. |

## Agents

Specialist read-mostly personas for targeted design and review passes. They cross-reference
each other (e.g. `code-reviewer` defers LangGraph design to `langgraph-architect`, security to
`security-auditor`, and test design to `test-engineer`).

| Agent | Role | Perspective |
| --- | --- | --- |
| [langgraph-architect](agents/langgraph-architect.md) | LangGraph architect (read-only) | State-schema, topology, routing, interrupt/resume, checkpointer, and tool/MCP correctness before implementation cost is sunk. |
| [code-reviewer](agents/code-reviewer.md) | Senior code reviewer (read-only) | Correctness, design, testability, observability, and idiomatic LangGraph usage for agentic Python. |
| [security-auditor](agents/security-auditor.md) | Security engineer (read-only) | Web/API/infra plus ML and LLM/agent/RAG threats: prompt injection, insecure tool design, model supply chain (OWASP + OWASP LLM Top 10). |
| [test-engineer](agents/test-engineer.md) | QA / ML & LLM test engineer | Test strategy, coverage analysis, the Prove-It pattern, and eval harnesses for prompts, agents, retrieval, and serving. |
| [docs-writer](agents/docs-writer.md) | Documentation specialist | READMEs, ADRs, assumptions logs, and mermaid diagrams, verification-gated with no aspirational claims. |

## How they work

Every skill and agent is a structured workflow, not prose: scope, step-by-step process,
severity/output conventions, explicit rules, and "when to use" triggers. The `SKILL.md`
files keep the entry point lean and push detail into examples; the agent files carry their
own operating rules and output format so a review reads consistently every time.

Throughout, `myapp` is a placeholder package name; replace it with your own. The recurring
research-assistant graph in `langgraph-patterns` is an illustrative example to swap out.

## Project structure

```
.
├── skills/
│   ├── langgraph-patterns/SKILL.md
│   └── python-quality/SKILL.md
└── agents/
    ├── langgraph-architect.md
    ├── code-reviewer.md
    ├── security-auditor.md
    ├── test-engineer.md
    └── docs-writer.md
```

## Installation

Clone the repo, then copy the skills and agents into your agent runtime's config
directory. Use the global path (applies everywhere) or a project-local path (scoped to
one repo) depending on your need.

```bash
git clone https://github.com/kaushik-holla/agent-skills.git
cd agent-skills
```

The agent files use OpenCode subagent frontmatter (`mode: subagent`, `permission:`
blocks). They run natively in OpenCode and translate to the other runtimes below.

### OpenCode (native)

```bash
# Global (all projects)
mkdir -p ~/.config/opencode/skills ~/.config/opencode/agents
cp -R skills/* ~/.config/opencode/skills/
cp agents/*.md ~/.config/opencode/agents/

# Or project-local: copy into ./.opencode/skills and ./.opencode/agents
```

### Claude Code

```bash
# Skills work as-is
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/

# Agents: place under ~/.claude/agents/ and adapt the frontmatter to Claude's
# subagent format (name + description; drop the OpenCode-specific permission block).
mkdir -p ~/.claude/agents
cp agents/*.md ~/.claude/agents/
```

### Cursor

```bash
# Project-local skills
mkdir -p .cursor/skills
cp -R skills/* .cursor/skills/
```

Alternatively, reference a `SKILL.md` directly from `.cursor/rules/`, or point the agent
at the `skills/` directory. Use the agent files as personas/rules for targeted reviews.

### Codex / Gemini / other agents

Skills and agents are plain Markdown; include the relevant `SKILL.md` or agent file as an
instruction file or system-prompt context (e.g. under `~/.codex/skills/`, or referenced
from `AGENTS.md` / `GEMINI.md`).

After installing, ask your agent to use a skill by name (e.g. "use the langgraph-patterns
skill") or invoke an agent persona (e.g. "review this with the security-auditor").

## Contributing

Skills should be **specific** (actionable steps, not vague advice), **verifiable** (clear
exit criteria), **battle-tested** (based on real workflows), and **minimal** (only what's
needed to guide the agent).

## License & attribution

Licensed under the [MIT License](LICENSE), free to use, modify, and redistribute,
including commercially. The only requirement is keeping the copyright notice.

If you build on these skills or agents, a credit/link to this repository is appreciated.
