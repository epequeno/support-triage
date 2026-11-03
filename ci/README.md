# `ci/` — the pipeline definition, kept as an artifact

`github-actions-test.yml` is a complete, working GitHub Actions pipeline. It is **not wired**: this
directory is not `.github/workflows/`, so GitHub never reads it and nothing runs on push or pull
request. That is deliberate — the file is here as evidence that the pipeline exists and as a
copy-paste to turn on, not as an active service.

## What it does when enabled

Two jobs, both `ubuntu-latest`:

| job | trigger | what it runs |
|---|---|---|
| `offline suite` | push to `main`, any pull request, manual | `uv sync --frozen`, then `uv run pytest -q` — 40 tests, a fake model, no API key, no network, ~0.3 s |
| `eval gate (real spend)` | manual dispatch only | seeds the mock org, runs `python -m support_triage.eval` (one real routed eval, ~$0.30), then `python -m support_triage.eval_gate results/eval.json`, and uploads the run as an artifact |

The eval job runs in the `eval` environment and **exits before spending** if the repository secret
`OPENROUTER_API_KEY` is not set.

## Turning it on

```bash
git mv ci/github-actions-test.yml ci/.github-workflows-test.yml   # optional: keep a copy
mkdir -p .github/workflows
git mv ci/github-actions-test.yml .github/workflows/test.yml
git commit -m "ci: re-enable the offline suite and the eval gate"
```

Then, on GitHub:

1. `gh secret set OPENROUTER_API_KEY` — needed only for the eval job; the offline suite needs nothing.
2. Settings → Environments → `eval`: add required reviewers or a spending limit before dispatching
   the eval job. One run costs about $0.30.
3. Settings → Branches → protect `main` and require the `offline suite` check, if a red suite should
   block a merge. Today `main` is unprotected and history is direct commits, so enabling the
   workflow alone would report, not block.

## What runs without it

Everything the gate protects, just by hand:

```bash
just test        # the same 40 tests the offline job runs
just eval        # one real routed eval, ~$0.30, needs OPENROUTER_API_KEY
just eval-gate   # judge results/eval.json against results/baseline.json — no spend
```

`eval_gate.py` and `results/baseline.json` are the gate; the workflow was only the thing that ran
them on a schedule. Disabling it does not weaken the gate — it removes the automatic trigger.
