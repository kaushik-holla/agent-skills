---
name: llm-evals
description: Design and run evaluation harnesses for LLM features, prompts, agents, and RAG systems, covering error analysis, golden datasets with slices, deterministic graders, LLM-as-a-judge rubrics calibrated against human labels, pairwise comparisons, bootstrap confidence intervals, and regression gates in CI. Includes a dependency-free harness script (run / compare / calibrate). Use when building or reviewing an eval suite, adding an LLM judge, comparing prompts or models, deciding whether a change is a real improvement, or gating releases on quality.
license: MIT
metadata:
  author: kaushik-holla
  version: "1.0"
---

# LLM Evals

Evals are the test suite for stochastic systems. The goal is not a leaderboard number; it is a fast, trustworthy answer to "did this change make the product better or worse, and where?"

## When To Use

- Standing up evals for a new LLM feature, agent, or RAG pipeline.
- Adding or auditing an LLM-as-a-judge.
- Comparing two prompts, models, retrievers, or agent designs.
- Migrating to a new model snapshot (always re-eval; behavior shifts silently).
- Turning production failures into regression tests.

## Principles

1. **Error analysis before metrics.** Read 30-100 real traces first. Metrics you invent before looking at data measure the wrong thing.
2. **Binary over scales.** Pass/fail per criterion is easier to label, calibrate, and act on than 1-10 scores.
3. **Cheapest grader that works.** Deterministic checks first, LLM judges only for what code can't check, humans to calibrate the judges.
4. **A judge is a model, so test it.** An uncalibrated judge is an opinion with a confidence interval of unknown width.
5. **Report uncertainty.** A 3-point gain on 60 cases is usually noise. Always show confidence intervals and per-slice results.
6. **Version everything together.** Dataset, prompts, model snapshots, judge rubrics, and judge model ID belong in the same commit or results header.

## Workflow

### 1. Define the contract and failure taxonomy

- Write down what "good" means for this feature in 3-6 bullet criteria (e.g., grounded, answers the question, correct format, refuses out-of-scope, under 200 words).
- Pull 30-100 real or realistic traces. For each failure, write a one-line note (open coding), then group notes into 4-8 failure categories (axial coding). These categories become your graders and dataset slices.

### 2. Build the dataset

- JSONL, one case per line: `{"id", "input", "expected"?, "slice", "metadata"?}`. See [assets/example_dataset.jsonl](assets/example_dataset.jsonl).
- Start with 50-200 cases. Cover every failure category, plus happy paths, edge cases, adversarial inputs (prompt injection, out-of-scope), and every production bug you've fixed.
- Split **dev** (iterate on it freely) and **holdout** (run only before release; never tune on it).
- Store it in git next to the prompts. Never generate the whole set synthetically; synthetic cases are fine for coverage once seeded from real data, but label a sample by hand.

### 3. Choose graders, cheapest first

| Grader | Use for | Cost |
| --- | --- | --- |
| Exact / regex / contains | IDs, numbers, classifications, required phrases | Free, deterministic |
| Schema validation | Structured outputs, tool-call arguments | Free, deterministic |
| Programmatic | Code executes, citations resolve, SQL returns expected rows, links valid | Cheap |
| Trajectory check | Agent called the right tools in a valid order, no loops | Cheap |
| LLM judge | Faithfulness, relevance, tone, policy adherence | Paid, needs calibration |
| Human review | Calibrating judges, high-stakes launches | Expensive |

### 4. Write judge rubrics

Use [references/judge-rubrics.md](references/judge-rubrics.md): one criterion per judge, binary verdict, reasoning before verdict, few-shot examples from your error analysis, structured output, pinned judge model and temperature. Prefer a judge from a different model family than the system under test.

### 5. Calibrate every judge

1. Hand-label 50-100 outputs pass/fail (two labelers on a subset; resolve disagreements to sharpen the rubric).
2. Run the judge on the same outputs.
3. `python scripts/eval_harness.py calibrate --results results.jsonl --labels human.jsonl --grader <judge>`
4. Targets: Cohen's kappa >= 0.6 (>= 0.8 for release gates) and TNR >= 0.8, meaning the judge catches real failures. A lenient judge that passes everything has high accuracy on a mostly-good dataset and is useless.
5. Iterate on the rubric using the disagreement list, not on the threshold.

### 6. Run and read results

```bash
python scripts/eval_harness.py run \
  --dataset evals/dev.jsonl --pythonpath src \
  --target myapp.eval_entry:answer \
  --grader contains --grader myapp.judges:faithfulness \
  --concurrency 8 --out results/candidate.jsonl
```

The harness prints pass rate with a bootstrap 95% CI per grader and per slice, plus latency p50/p95 and target errors. Read failures by slice, not just the headline. Track cost and latency next to quality; a quality win that triples cost is a product decision, not an automatic merge.

### 7. Compare against a baseline

```bash
python scripts/eval_harness.py compare \
  --baseline results/main.jsonl --candidate results/candidate.jsonl --grader myapp.judges:faithfulness
```

Paired comparison on the same case IDs with a paired-bootstrap CI on the delta. The verdict is "improvement" only if the whole CI is above zero. Always inspect the listed regressions: a net-positive change that breaks a critical slice is still a regression.

### 8. Gate in CI

- **Per PR**: deterministic graders plus a small, fast judge subset, marked `@pytest.mark.eval` (see the `python-quality` skill's markers) or run via `--min-pass` in a CI step.
- **Nightly / pre-release**: full dev set plus holdout, all judges, compared to the last release.
- Gate on **regression vs baseline**, not on an absolute number that someone will quietly lower.

### 9. Close the loop with production

Sample production traces (see the `agent-observability` skill), run the same judges online on a sample, and promote confirmed failures into the dataset as regression cases.

## Bundled files

| File | Purpose |
| --- | --- |
| [scripts/eval_harness.py](scripts/eval_harness.py) | Stdlib-only harness: `run`, `compare`, `calibrate` |
| [references/judge-rubrics.md](references/judge-rubrics.md) | Rubric templates (faithfulness, relevance, format, safety, pairwise) and judge-bias mitigations |
| [assets/example_dataset.jsonl](assets/example_dataset.jsonl) | Dataset format example with slices and out-of-scope cases |
| [assets/example_target.py](assets/example_target.py) | Toy target and judge for an offline smoke test |
| [assets/example_labels.jsonl](assets/example_labels.jsonl) | Human-label format for `calibrate` |

Smoke test, run from `scripts/`:

```bash
python eval_harness.py run --dataset ../assets/example_dataset.jsonl --pythonpath ../assets \
  --target example_target:answer --grader contains --grader example_target:judge --out /tmp/r.jsonl
python eval_harness.py calibrate --results /tmp/r.jsonl --labels ../assets/example_labels.jsonl \
  --grader example_target:judge
```

## Tooling landscape

The bundled harness is intentionally small. For larger suites, the same workflow maps onto: `promptfoo` (config-driven, CI-friendly), `DeepEval` (pytest-style metrics), `Ragas` (RAG metrics), `inspect_ai` (agentic and safety evals), LangSmith or Braintrust or Arize Phoenix (datasets + experiments + online evals tied to traces). Pick one platform per team; the methodology above matters more than the tool.

## Checklist

- [ ] Failure taxonomy written from real traces before any metric was chosen
- [ ] Dataset versioned in git with slices, adversarial cases, and a holdout split
- [ ] Deterministic graders cover everything code can check
- [ ] Each judge: one criterion, binary, reasoning-first, structured output, pinned model
- [ ] Each judge calibrated (kappa and TNR reported) against human labels
- [ ] Results report CIs and per-slice numbers, plus cost and latency
- [ ] Comparisons are paired against a baseline on identical case IDs
- [ ] CI gates on regressions; holdout untouched during iteration

## Common Mistakes

- Picking metrics off a framework's menu (BLEU, generic "helpfulness") instead of from your own failure analysis.
- 1-10 judge scales averaged into a single number that moves 0.2 and gets celebrated.
- Tuning prompts on the holdout set until it stops being a holdout.
- Judge and system under test from the same model, with the judge never checked against humans.
- Comparing unpaired runs (different case sets or sampling seeds) and calling the difference real.
- Dropping cases that "always fail" instead of fixing or documenting them.
- Re-generating `expected` answers with the new model, which turns the eval into a self-agreement test.

## References

- Hamel Husain, "Your AI Product Needs Evals": <https://hamel.dev/blog/posts/evals/>
- Eugene Yan, "Evaluating the Effectiveness of LLM-Evaluators": <https://eugeneyan.com/writing/llm-evaluators/>
- Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena" (position/verbosity bias): <https://arxiv.org/abs/2306.05685>
- promptfoo: <https://www.promptfoo.dev/docs/intro/>
- Inspect AI: <https://inspect.aisi.org.uk/>
- Ragas: <https://docs.ragas.io/>
