---
name: git-pr-workflow
description: Ship small, reviewable pull requests and review others' PRs well, covering branch and commit hygiene with Conventional Commits, stacked PRs, writing PR descriptions, self-review, reviewer comment taxonomy, safe history rewriting (rebase, autosquash, force-with-lease, range-diff), recovery with reflog and bisect, and etiquette for AI-generated code. Includes a pre-flight script that catches secrets, conflict markers, debug leftovers, missing tests, oversized diffs, and messy commits before you push. Use when preparing, splitting, describing, or reviewing a pull request, cleaning up commit history, recovering from a git mistake, or setting team PR conventions.
license: MIT
metadata:
  author: kaushik-holla
  version: "1.0"
---

# Git and Pull Request Workflow

A pull request is a request for someone else's attention. Make it small, explain why it exists, prove it works, and make the reviewer's first five minutes productive. The same rules apply when an AI agent wrote the code: you are the author of record.

## When To Use

- Before opening or updating a PR (yours or one an agent produced).
- Splitting a large change into reviewable pieces.
- Reviewing someone else's PR.
- Cleaning up history, resolving a bad rebase, or finding the commit that broke something.
- Setting or documenting team conventions.

## Author Workflow

### 1. Scope the change

- One logical change per PR. Refactors, formatting, and dependency bumps go in separate PRs from behavior changes.
- Aim for under ~400 changed lines (excluding lockfiles and generated files). Review quality drops sharply on larger diffs.
- For big features, **stack** PRs: `feat/1-schema` -> `feat/2-api` -> `feat/3-ui`, each mergeable on its own (feature flags help). Tools like Graphite, `git-branchless`, or `ghstack` make this easier; plain branches with `git rebase --update-refs` work too.

### 2. Commit with intent

Conventional Commits make history searchable and enable automated changelogs and versioning:

```text
feat(retrieval): add hybrid BM25 + vector search with RRF

Dense-only retrieval missed exact product codes (recall@10 0.71 on the
SKU slice). Hybrid with reciprocal rank fusion raises it to 0.93 with no
change on paraphrase queries. Reranker unchanged.

Refs: #482
```

- Subject: `type(scope): imperative summary`, at most ~72 chars. Types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`; `!` marks a breaking change.
- Body: **why**, and what you measured. The diff already shows what.
- While iterating, commit freely with `git commit --fixup <sha>`; clean up before review with `git rebase -i --autosquash origin/main`.

### 3. Self-review before anyone else

```bash
git fetch origin && git rebase origin/main
python scripts/pr_preflight.py --run "uv run pytest -q" --run "uvx ruff check ."
git diff origin/main...HEAD          # read your own diff top to bottom, as a reviewer would
```

[scripts/pr_preflight.py](scripts/pr_preflight.py) reports PR size, possible secrets and `.env` files, conflict markers, `breakpoint()` / `console.log` / `.only` leftovers, notebooks with outputs, large binaries, source changes without tests, and `wip` / `fixup!` commits, then runs your checks. Fix every error; consciously accept or fix each warning.

### 4. Write the description

Use [assets/pull_request_template.md](assets/pull_request_template.md) (copy to `.github/pull_request_template.md`). A good description has:

- **What and why** in two sentences, with the issue link.
- **How**, including rejected alternatives and where the reviewer should start reading.
- **Risk and rollout**: compatibility, flags, migrations, rollback plan.
- **Evidence**: tests run, screenshots, trace links, eval deltas for prompt or model changes.

Open as a draft early if you want feedback on direction; mark ready only when CI is green and the self-review is done.

### 5. Respond to review

- Reply to every comment: fixed (with the commit), disagree (with a reason), or follow-up (with an issue link).
- Push review fixes as new commits so reviewers can see what changed; squash at merge or autosquash before the final approval, per team convention.
- After a rebase, help reviewers with `git range-diff origin/main@{1} @{1} @` (or GitHub's "compare" link).

## Reviewer Workflow

1. **Read the description first**, then decide whether the approach is right before nitpicking lines. A wrong approach is cheaper to flag now than after 30 line comments.
2. **Check out and run it** when behavior matters: `gh pr checkout 123`, run tests, try the feature.
3. **Review in order of cost**: correctness and security, then design and API surface, then tests, then readability. Use the `code-reviewer` and `security-auditor` agents for a structured pass.
4. **Label every comment** so the author knows what blocks merge:

| Label | Meaning |
| --- | --- |
| `blocking:` | Must change before merge (bug, security, data loss, broken contract) |
| `suggestion:` | Better option; author decides |
| `question:` | I don't understand; may reveal a bug or a missing comment |
| `nit:` | Style or taste; never blocks |
| `praise:` | Call out good work; it teaches as much as criticism |

5. **Be specific and kind**: quote the line, explain the risk, propose a fix ("`blocking:` this retries non-idempotent POSTs; a timeout after success double-charges. Could we pass an idempotency key?").
6. **Approve when it is better than main and safe**, not when it is how you would have written it.

## Git Safety Toolkit

| Situation | Command |
| --- | --- |
| Update branch onto latest main | `git fetch origin && git rebase origin/main` |
| Squash fixups into their targets | `git rebase -i --autosquash origin/main` |
| Push rewritten history safely | `git push --force-with-lease --force-if-includes` (never plain `--force` on shared branches) |
| Compare a branch before/after rebase | `git range-diff @{u} @` or `git range-diff main old-branch new-branch` |
| Undo the last commit, keep changes | `git reset --soft HEAD~1` |
| Recover "lost" commits after a bad reset/rebase | `git reflog`, then `git branch rescue <sha>` |
| Find the commit that broke a test | `git bisect start HEAD <good-sha> && git bisect run uv run pytest tests/test_x.py -q` |
| Revert a merged PR | `git revert -m 1 <merge-sha>` |
| Remove a committed secret | **Rotate the secret first**, then purge with `git filter-repo`; deleting it in a new commit is not enough |
| Resolve repeated conflicts once | `git config rerere.enabled true` |

## AI-Assisted PRs

- The human opening the PR owns it: read every line, run it, and be able to explain it.
- Keep agent PRs as small as human ones; ask the agent to split work (one concern per PR) rather than reviewing a 3,000-line drop.
- Disclose meaningful AI assistance in the description when your team or project asks for it.
- Watch for agent-specific failure modes: invented APIs, tests that assert the implementation rather than behavior, silently weakened checks (skipped tests, loosened types, `# noqa`), and unrelated "drive-by" edits.
- Keep repo instructions (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`) current so agents follow the same conventions as humans.

## Checklist

- [ ] One logical change, under ~400 lines, or split / stacked
- [ ] Rebased on the latest target branch; history clean (no `wip` / `fixup!`)
- [ ] Conventional Commit subjects with "why" in the body
- [ ] `pr_preflight.py` clean (or warnings consciously accepted)
- [ ] Tests added or updated; CI green
- [ ] Description covers what, why, how, risk, and evidence
- [ ] Every review comment answered

## Common Mistakes

- "Misc fixes" PRs mixing refactors, formatting, and behavior changes.
- Force-pushing over a teammate's commits with `--force` instead of `--force-with-lease`.
- Deleting a leaked key in a follow-up commit without rotating it.
- Reviewing line by line before checking whether the approach is right.
- Unlabeled review comments, leaving the author guessing what blocks merge.
- Merging an agent's PR because CI is green without reading the diff.

## References

- Conventional Commits: <https://www.conventionalcommits.org/en/v1.0.0/>
- Google engineering practices, code review: <https://google.github.io/eng-practices/review/>
- Conventional Comments: <https://conventionalcomments.org/>
- Git docs, `git rebase --autosquash` and `range-diff`: <https://git-scm.com/docs/git-rebase>, <https://git-scm.com/docs/git-range-diff>
- git-filter-repo: <https://github.com/newren/git-filter-repo>
