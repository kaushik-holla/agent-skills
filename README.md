# Agent Skills

**Production-grade skills and subagents for AI/ML engineering: agentic Python, LangGraph, LLM evals, RAG, MCP, observability, and reproducible data science.**

A coordinated set of [Agent Skills](https://agentskills.io) and specialist review agents that encode the workflows an engineer uses to take an AI system from design through evaluation, review, and ship. Every skill is a folder with a `SKILL.md` (YAML frontmatter + Markdown), plus helper scripts, templates, and references where they genuinely help. They work in **Cursor, Claude Code, Codex, OpenCode, Gemini CLI**, and any other skills-compatible agent.

- **10 skills**: each has a precise "use when" trigger, so your agent loads it only when relevant.
- **5 subagents**: read-mostly reviewers and specialists that cross-reference each other and the skills.
- **8 tested helper scripts**: eval harness with bootstrap CIs, retrieval metrics, schema linter, trace analyzer, run manifests, data profiler, PR pre-flight, and a quality gate. Most are standard-library only.
- **Validated in CI**: frontmatter, links, Python syntax, ruff, and installer output are checked on every push.

## Skills

| Skill | What it does | Use when |
| --- | --- | --- |
| [python-quality](skills/python-quality/SKILL.md) | Modern Python baseline: uv (or conda), src layout, ruff, mypy/pyright, pytest, pre-commit, GitHub Actions, and LLM-friendly test patterns. Ships `pyproject.toml`, CI, and pre-commit templates plus a quality-gate script. | Scaffolding a Python project or upgrading pip/poetry/black/flake8-era tooling. |
| [langgraph-patterns](skills/langgraph-patterns/SKILL.md) | Copy-paste LangGraph 1.x recipes: typed state, routers, bounded loops, interrupt/resume (v2 output), checkpointers, runtime context, structured outputs, MCP, `create_agent`, subgraphs, fan-out with reducers, streaming, retries, tracing. | Designing or implementing any LangGraph node, router, or graph. |
| [llm-evals](skills/llm-evals/SKILL.md) | Eval-driven LLM development: error analysis, datasets, code graders, calibrated LLM-as-judge, bootstrap CIs, paired regression checks, and CI gates. Includes a stdlib eval harness. | Measuring or regression-testing prompts, models, RAG, or agents. |
| [rag-pipelines](skills/rag-pipelines/SKILL.md) | RAG design defaults (structure-aware chunking, hybrid BM25 + vector with RRF, reranking, ACL filters), a stage-by-stage debugging playbook, and a retrieval metrics script (recall@k, MRR, nDCG). | Building RAG or semantic search, or debugging ungrounded or missing answers. |
| [prompt-engineering](skills/prompt-engineering/SKILL.md) | Production prompts and structured outputs: prompt structure, delimiting untrusted input, Pydantic schemas, validation and repair, tool design, versioning, caching, and model migration. Includes a schema linter. | Writing or reviewing prompts, schemas, or tool definitions; moving to a new model. |
| [mcp-server-builder](skills/mcp-server-builder/SKILL.md) | Build MCP servers with the Python SDK v2 (`MCPServer`): tool design, typed args and structured output, `ToolError`, in-memory tests, transports, client setup for Claude Code / Cursor / Codex, and a security checklist. Ships a tested template. | Exposing an API or dataset to agents, or migrating a FastMCP v1 server. |
| [agent-observability](skills/agent-observability/SKILL.md) | Tracing and monitoring for agents with OpenTelemetry GenAI conventions, LangSmith, Langfuse, or Phoenix: span design, cost, privacy, sampling, alerts, and a debugging playbook. Includes a trace report script with loop detection. | Adding tracing, or investigating slow, expensive, or looping agent runs. |
| [ml-experiment-tracking](skills/ml-experiment-tracking/SKILL.md) | Reproducible ML with MLflow 3 or W&B: run manifests, seeding, data versioning, honest evaluation, and registry promotion with aliases. Includes a run-manifest script and a runnable MLflow example. | Setting up tracking, comparing models, or chasing a result you cannot reproduce. |
| [data-quality](skills/data-quality/SKILL.md) | Polars-first data profiling and validation: placeholder and type fixes, join checks, Pandera contracts, leakage checks, and PSI drift. Includes a profiler that flags issues as Markdown. | Starting on a new dataset, before training, or when numbers look wrong. |
| [git-pr-workflow](skills/git-pr-workflow/SKILL.md) | Small, reviewable PRs: Conventional Commits, stacked PRs, self-review, reviewer comment labels, safe history rewriting, recovery, and AI-assisted PR etiquette. Includes a PR pre-flight script and template. | Preparing, splitting, describing, or reviewing a pull request. |

## Agents

Specialist personas for targeted design and review passes. They cross-reference each other (for example `code-reviewer` defers LangGraph design to `langgraph-architect`, security to `security-auditor`, and test design to `test-engineer`) and point to the skills above for detail.

| Agent | Role | Perspective |
| --- | --- | --- |
| [langgraph-architect](agents/langgraph-architect.md) | LangGraph architect (read-only) | State schema, topology, routing, interrupt/resume, checkpointers, runtime context, and tool/MCP correctness before implementation cost is sunk. |
| [code-reviewer](agents/code-reviewer.md) | Senior code reviewer (read-only) | Correctness, design, testability, observability, and idiomatic LangGraph / LangChain v1 usage for agentic Python. |
| [security-auditor](agents/security-auditor.md) | Security engineer (read-only) | Web/API/infra plus ML and LLM/agent/RAG/MCP threats, mapped to OWASP Top 10:2025, OWASP LLM Top 10 2025, and the OWASP Agentic Top 10. |
| [test-engineer](agents/test-engineer.md) | QA / ML and LLM test engineer | Test strategy, coverage, the Prove-It pattern, and eval harnesses for prompts, agent trajectories, retrieval, and serving. |
| [docs-writer](agents/docs-writer.md) | Documentation specialist | READMEs, ADRs, assumptions logs, and mermaid diagrams, verification-gated with no aspirational claims. |

Each agent is one Markdown file with portable frontmatter: `name`, `description`, `tools`, and `model` for Claude Code and Cursor, `readonly` for Cursor, and an `opencode:` block with OpenCode permissions. The installer converts them to Codex TOML and OpenCode format.

## Install

### Option 1: `npx skills` (any agent)

```bash
npx skills add kaushik-holla/agent-skills                                # interactive
npx skills add kaushik-holla/agent-skills -a cursor -a claude-code -a codex
```

Uses the open [skills CLI](https://github.com/vercel-labs/skills). Installs skills only; use Option 3 or 4 for agents.

### Option 2: Claude Code plugin

```text
/plugin marketplace add kaushik-holla/agent-skills
/plugin install agent-skills@kaushik-holla
```

Installs all skills and subagents. Plugin skills are namespaced, for example `/agent-skills:llm-evals`.

### Option 3: Installer script (skills + agents in native formats)

```bash
git clone https://github.com/kaushik-holla/agent-skills.git
cd agent-skills

python3 scripts/install.py --target cursor --target claude --target codex           # user scope (~)
python3 scripts/install.py --target cursor --scope project --project-dir ../my-repo  # one repo
python3 scripts/install.py --target opencode --skill llm-evals --dry-run             # preview a subset
```

| Target | Skills go to | Agents go to |
| --- | --- | --- |
| `cursor` | `~/.cursor/skills` or `.cursor/skills` | `~/.cursor/agents` or `.cursor/agents` (Markdown) |
| `claude` | `~/.claude/skills` or `.claude/skills` | `~/.claude/agents` or `.claude/agents` (Markdown) |
| `codex` | `~/.agents/skills` or `.agents/skills` | `~/.codex/agents` or `.codex/agents` (TOML) |
| `opencode` | `~/.config/opencode/skills` or `.opencode/skills` | `~/.config/opencode/agents` or `.opencode/agents` |
| `agents` | `~/.agents/skills` or `.agents/skills` (Gemini CLI, Amp, and other skills-compatible tools) | none |

Existing installs are skipped unless you pass `--force`.

### Option 4: Manual copy

```bash
# Cursor (project): skills and subagents
mkdir -p .cursor/skills .cursor/agents
cp -R skills/* .cursor/skills/ && cp agents/*.md .cursor/agents/

# Claude Code (user)
mkdir -p ~/.claude/skills ~/.claude/agents
cp -R skills/* ~/.claude/skills/ && cp agents/*.md ~/.claude/agents/

# Codex and other agents that read the shared .agents/skills location
mkdir -p ~/.agents/skills && cp -R skills/* ~/.agents/skills/
```

Cursor also discovers skills in `.agents/skills`, `.claude/skills`, and `.codex/skills`, so one copy can serve several tools. For Codex subagents use the installer, which writes the TOML format Codex expects.

### Using them

Skills load automatically when your request matches their description ("set up evals for this prompt", "why is retrieval missing SKUs?"). You can also invoke one explicitly (`/llm-evals` in Cursor or Claude Code, or "use the rag-pipelines skill"). Agents are invoked by name: "review this diff with the security-auditor" or "have the langgraph-architect check this design."

## How they work

Every skill and agent is a structured workflow, not prose: scope, step-by-step process, checklists, severity and output conventions, explicit rules, and "when to use" triggers. `SKILL.md` files stay lean (under 500 lines) and push detail into `references/`, runnable helpers into `scripts/`, and templates into `assets/`, so agents load only what a task needs.

Throughout, `myapp` is a placeholder package name; replace it with your own. Model IDs come from settings (`LLM_MODEL=provider:model`), never hardcoded.

## Project structure

```text
.
├── .claude-plugin/           # Claude Code plugin + marketplace manifests
├── .github/workflows/        # CI: validation, ruff, installer smoke test
├── agents/                   # 5 subagents (portable Markdown frontmatter)
├── scripts/
│   ├── install.py            # install skills + agents into Cursor, Claude Code, Codex, OpenCode
│   └── validate.py           # frontmatter, links, py_compile, manifests, README coverage
└── skills/
    └── <skill-name>/
        ├── SKILL.md          # required: frontmatter (name, description) + instructions
        ├── scripts/          # optional: executable helpers
        ├── references/       # optional: detail loaded on demand
        └── assets/           # optional: templates, examples, fixtures
```

## Validation

```bash
uv run scripts/validate.py              # frontmatter, links, py_compile, JSON, README coverage
uvx ruff check . && uvx ruff format --check .
```

`validate.py` checks every `SKILL.md` against the Agent Skills spec (name format and folder match, description length and "use when" trigger, allowed keys, body length), every agent's frontmatter, every relative link and anchor, Python syntax, shell syntax, and the plugin manifests.

## Contributing

Skills should be **specific** (actionable steps, not vague advice), **verifiable** (clear exit criteria), **battle-tested** (based on real workflows), and **minimal** (only what's needed to guide the agent). New skills need a folder name that matches `name`, a description that says what the skill does and when to use it, and a passing `uv run scripts/validate.py`.

## Changelog

### 2026-11-20: v2.0

- **8 new skills**: `llm-evals`, `rag-pipelines`, `prompt-engineering`, `mcp-server-builder`, `agent-observability`, `ml-experiment-tracking`, `data-quality`, `git-pr-workflow`, each with checklists and tested helper scripts or templates.
- **Agent Skills spec compliance**: every skill has validated `name` / `description` frontmatter with explicit "use when" triggers, `license`, and `metadata`, plus `scripts/`, `references/`, and `assets/` folders.
- **Cross-runtime install**: Claude Code plugin + marketplace manifests, `npx skills` support, and `scripts/install.py`, which writes native agent formats for Cursor, Claude Code, Codex (TOML), and OpenCode.
- **python-quality**: uv-first workflow, PEP 735 dependency groups, ruff 0.17 / mypy 2.x / pytest 9, current GitHub Actions, `uv-lock` and `pip-audit`, a quality-gate script, and ready-to-copy templates.
- **langgraph-patterns**: updated for LangGraph 1.2 and LangChain v1 (v2 invoke and stream output, `InMemorySaver`, runtime context instead of injected clients, `create_agent` + middleware, `langchain.mcp.MCPAdapter`, reducers for parallel branches, `RetryPolicy`, `LANGSMITH_*` env vars). Fixed recipes that could not run as written.
- **Agents**: portable frontmatter for Claude Code, Cursor, Codex, and OpenCode; OWASP LLM Top 10 2025 and Agentic Top 10 coverage; MCP security; uv-based commands; agent trajectory and model-migration testing.
- **Repo tooling**: `scripts/validate.py`, ruff config, and a CI workflow.
- **Portable by default**: examples use neutral placeholders (`myapp`, Acme, `example.com`) and model IDs come from settings, so nothing assumes a particular org, provider, or workspace.

### 2026-06-02: v1.0

- Initial release: `langgraph-patterns` and `python-quality` skills; `langgraph-architect`, `code-reviewer`, `security-auditor`, `test-engineer`, and `docs-writer` agents.

## License and attribution

Licensed under the [MIT License](LICENSE): free to use, modify, and redistribute, including commercially. The only requirement is keeping the copyright notice.

If you build on these skills or agents, a credit or link to this repository is appreciated.
