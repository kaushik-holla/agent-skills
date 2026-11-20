## What and why

<!-- One or two sentences: the problem and the outcome. Link the issue: Closes #123 -->

## How

<!-- The approach and any alternatives you rejected. Point reviewers at the file to start with. -->

## Risk and rollout

- [ ] Backward compatible (API, schema, config, prompts, model outputs)
- [ ] Feature-flagged or safe to roll back by reverting
- [ ] Migrations / re-indexing / backfills described below, if any

## Testing

<!-- What you ran and what you saw. Include eval deltas for prompt or model changes. -->

- [ ] Unit / integration tests added or updated
- [ ] `uv run pytest` passes locally
- [ ] Lint and type checks pass (`uvx ruff check .`, `uv run mypy src`)
- [ ] For LLM changes: eval set run, no regression on critical slices (attach comparison)

## Reviewer notes

<!-- Anything you want a second opinion on. Screenshots or trace links for UI / agent behavior. -->

## AI assistance

<!-- If an AI agent wrote significant parts: which parts, and confirm you reviewed and understand every line. -->
