# Handoff — `support-triage`

**As of:** 2026-11-03
**Read first:** [`docs/NEXT.md`](docs/NEXT.md) for the build history and the measured numbers. This
file is the current handoff: what changed, what is verified, and what to do next. The repo is not
greenfield — M0–M7 are built, the demo runs, and the working tree is clean.

## Current state

Three readiness-review gaps closed and committed on 2026-11-03:

1. **Caller auth and a durable audit trail** (`ab2dd7d`). Every `/api` route requires a bearer
   token; approvals are written through an `ApprovalStore` — SQLite locally, S3 when
   `APPROVALS_BUCKET` is set. The CDK stack creates the bucket and a generated Secrets Manager app
   token, the health check sends the token, and deployments fail fast with a circuit breaker.
2. **CI and an eval gate** (`3ad9418`, plus the action-pin fix). `.github/workflows/test.yml` runs
   the 39-test offline suite on every push and pull request. A manual-dispatch `eval` job runs one
   real routed eval (~$0.30) and gates it with `python -m support_triage.eval_gate` against
   `results/baseline.json`.
3. **The per-policy cache** (`89e8f84`). The corpus is parsed once per process per file version,
   indexed once, and each section's terms and rendered block are computed once and reused, so the
   classify prompt is assembled rather than rebuilt six times per case. `retrieve(k=…)` can no
   longer cut below `POL-PLAN-1`/`POL-SCOPE-1`. **The token half of gap #3 was measured and
   refused** — see "Not verified / known limits".

`src/support_triage/traces.py` carries the **pre-existing 2026-10 edit**: optional OTLP export to
the shared collector when `OTEL_EXPORTER_OTLP_ENDPOINT` is set, on top of the file-tree export. It
is documented, self-contained, and was kept deliberately — the file tree remains the artifact.

`main` is pushed and in sync with `origin` (`epequeno/support-triage`, public). No CI existed
before today; it does now and it is green.

## What changed on 2026-11-03

| area | change |
|---|---|
| `src/support_triage/auth.py` | Bearer-token gate. Reads `APP_TOKEN`; constant-time compare; fails closed with 503 when the token is unset. |
| `src/support_triage/approvals.py` | `ApprovalStore` protocol with `SqliteApprovalStore` and `S3ApprovalStore`. `store_for(conn)` selects S3 when `APPROVALS_BUCKET` is set, SQLite otherwise. S3 writes one immutable JSON object per approval under `approvals/<case_id>/`. |
| `src/support_triage/tools.py` | `execute(store, proposal, approved_by)` now takes the store; the named-human gate is unchanged. |
| `src/support_triage/cli.py` | `--approve-as` writes through the store and reads the trail back from it. |
| `src/support_triage/web.py` | `/api` routes moved onto an authenticated router; the page prompts for the token and sends it as a bearer header. Approve and trail endpoints use the store. |
| `infra/support_triage_stack.py` | S3 bucket (private, encrypted, destroy-with-stack), generated `support-triage/app-token` secret, task role read+put on the bucket, execution role read on the secret, health check sends the token, deployment circuit breaker. Fixed the null execution-role bug found during synth. |
| `src/support_triage/eval_gate.py` | Judges a run against the baseline: per-axis floors as rates snapped up to whole cases, plus a cost ceiling. Exit 0 pass, 1 regression, 2 unusable input; names the axis and the case. |
| `results/baseline.json` | The floors — severity 1.0, in scope/sla/action/citations 0.9, cost ceiling $0.36 — with the four reference runs they came from. |
| `.github/workflows/test.yml` | `offline` job on push/PR; `eval` job on manual dispatch only, in the `eval` environment, refusing to start without `OPENROUTER_API_KEY`, uploading `results/eval.json`. |
| `src/support_triage/policy.py` | Per-policy-id caches: parse keyed by file identity (mtime, size), terms and rendered blocks keyed by the section's own text, `index()` derived once and returned read-only. `retrieve` keeps the mandatory sections at any `k`. |
| `src/support_triage/agent.py` | `_prompt` resolves the corpus index once instead of twice per candidate. Prompt bytes unchanged. |
| tests | `tests/test_web.py` (page open, API 401, fail-closed 503, approve through the store), `tests/test_eval_gate.py` (recorded run passes, one noise miss passes, two misses fail and are named, severity miss fails, cost blowup fails, partial and inconsistent runs refused, CLI exit codes), and three cache tests (mandatory sections survive any query and any `k`; the cache renders byte-for-byte what a fresh parse renders; a corpus edit is never served stale). **39 tests pass.** |
| docs | README run instructions, auth/approval file table, deploy caveat, CI/eval-gate section; `docs/NEXT.md` records the gate and its thresholds. |

## Verified

| check | command | result |
|---|---|---|
| Offline suite | `uv sync --frozen && uv run pytest -q` | 39 passed in ~0.3 s |
| CI on push | `git push origin main` → run 41656259069 | **green**; `offline suite` success, `eval gate` correctly skipped |
| Eval job wiring | `gh workflow run test.yml` → run 41656367230 | actions resolve, then `OPENROUTER_API_KEY is not set; the eval spends real credit and will not run blind` — **fails before any spend** |
| Gate, recorded runs | `just eval-gate` over `eval.json` and `eval-dist-1..3` | all four PASS, including the run that missed `CASE-1046` on four axes |
| Gate, regression | synthetic run with two citations misses + `$0.52` | exit 1; prints `citations: 8/10 below the floor of 9/10`, the two cases, and the cost breach |
| Gate, bad input | missing file | exit 2 with the OS error |
| SQLite durability | two separate Python processes opening the same DB | one approval row survived; `approved_by` intact |
| HTTP auth gate | `APP_TOKEN=smoke uv run uvicorn support_triage.web:app --port 8011` then `curl` | page 200 without token; `/api/cases` 401 without token; 200 with token; page contains the token bar |
| S3 adapter | `uv run --with 'moto[s3]' python …` | `record` + `list` round-trip for `CASE-1041`; unknown case returns `[]` |
| Cache equivalence | `_prompt` for all ten cases, pre-change commit (worktree) vs now | **10/10 byte-identical**; prompt assembly 390 µs → 12 µs per case |
| Cache invalidation | rewrite a temp corpus, then `load`/`index`/`retrieve` again | new text visible immediately |
| Cache guarantee | `retrieve(q, k=1)` over four queries, incl. an empty one | `POL-PLAN-1` and `POL-SCOPE-1` always in the set |
| CDK synthesis | `OPENROUTER_API_KEY=dummy CDK_DEFAULT_ACCOUNT=… CDK_DEFAULT_REGION=us-east-1 uv run cdk synth --quiet` | stack synthesizes; one `minHealthyPercent` annotation remains |

## Not verified / known limits

- **Real AWS.** `moto` proves the S3 adapter's code path, not IAM, secret injection, bucket policy,
  or the Fargate task's access to the bucket. The stack has not been deployed since the auth fix. A
  deploy creates billable resources; do not run it without explicit approval.
- **The eval job has never run end to end.** There is no repository secret and no `eval`
  environment yet, so only the fail-fast path is proven. Set `OPENROUTER_API_KEY` as a **repository
  secret**, and add required reviewers or a spending limit to the `eval` environment before
  dispatching for real.
- **Gap #3's token half is refused, on measurement.** Policy text is 76% of the classify prompt
  (3,013 of ~3,917 chars). The only real cut is sending `POL-SEC-1` only when the router's lexical
  security signal fires: that saves 638 chars on eight of ten cases — 18.5% of policy text, 14.3%
  of the prompts — and it hides the section that sets a security report's severity from every case
  the lexical signal misses. The cache therefore changes compute, not tokens.
- **The gate is a judgment at n=10.** Its floors tolerate exactly one miss on the four 0.9 axes and
  none on severity, because that is what the forty recorded case-draws support. It is not a
  statistical test; it is a tripwire that also prices the run.
- **`minHealthyPercent`.** CDK warns that the default 50% is used. That is acceptable for the
  single-task demo. Setting `min_healthy_percent=100` would start a second task during rollout and
  can complicate the "task public IP is the URL" workflow; decide deliberately.
- **CI triggers on `main` pushes and pull requests, but this repo commits straight to `main`.** The
  blocking-merge property only bites once changes go through PRs; the same job runs otherwise.
- **A red `workflow_dispatch` run (41656367230) sits in the Actions list by design** — it is the
  no-key guard firing. It disappears from the default view after a while; delete it if it bothers
  anyone.
- **OpenRouter key still rides as a plain task environment variable.** The app token moved to
  Secrets Manager; the key did not. This remains acceptable only for an hour-long demo and is called
  out in the README.
- **Local MinIO is not required.** Docker Hub denied `minio/minio` and Quay returned 401 in this
  environment. The S3 adapter is covered by the `moto` smoke; use `moto` for tests or a real S3
  endpoint for local end-to-end.
- **Triage records are still in memory.** Only approvals are durable. A task restart loses the
  server-side records the approve endpoint reads.
- **The readiness review's dimension count is stale.** Re-score after the auth and CI fixes; the
  verdict remains "not production ready" until the cache, capacity, and operability gaps close.

## Next steps, ranked

### 1. Measure concurrency and a p99 (review gap #4, ~half day)

- Add a small load script against `just web` or the ASGI app; record p50/p99 for concurrent requests.
- **Done when:** README or `results/` states a throughput and p99, even a small one. Optimizing is
  optional; replacing "unknown" with a number is the point.

### 2. Write the one-page runbook (review gap #5, ~2 h)

- `docs/RUNBOOK.md`: who is paged, what they look at, and the three likely incidents (spend cap hit,
  approval store unavailable, task unhealthy/port closed).
- **Done when:** the page exists and is linked from the README; no code change required.

### 3. Optional: verify the deployed shape against real AWS

- Requires explicit approval; it creates billable resources. Use the deploy auth in `docs/NEXT.md`
  (`AWS_PROFILE=arch`, SSO session `main`, account `496830984285`, `AdministratorAccess`).
- From `infra/`: set a real `OPENROUTER_API_KEY`, then `uv run cdk deploy --require-approval never`.
- Retrieve the token: `aws secretsmanager get-secret-value --secret-id support-triage/app-token
  --query SecretString --output text`.
- Triage and approve a case through the UI or API; confirm the approval object exists under
  `s3://<bucket>/approvals/CASE-1041/`; then `cdk destroy`.
- **Done when:** the S3 object survives a task restart (or is visible after the approval), the API
  rejects a request without the token, and `cdk destroy` leaves no stack resources. Note the bucket
  and secret are destroyed with the stack; the audit trail is not preserved after teardown unless the
  removal policy is changed.

### 4. Re-score the readiness review

- Re-run the five-vector/four-question rubric against this state. Dimensions 11 (security and
  permissions) and the evaluation dimension should move; the remaining gaps are cache, capacity,
  operability, and the unproven eval job.
- **Done when:** the review note and registry entry carry a current dimension count and a next gap.
- Note for the re-score: gap #3 is closed as a *compute* fix, with the token saving refused rather
  than shipped. If a reviewer wants the 14%, the honest route is a facts-conditioned candidate set
  plus a routed eval showing no axis loss — not a lexical shortcut.

## Commands and access

```bash
cd ~/code/support-triage
just sync && just seed          # seed the mock org if supportorg.db is missing
just test                       # 39 offline tests, no key
just triage CASE-1041           # one case end to end
just triage CASE-1041 --approve-as "Your Name"
APP_TOKEN=dev just web          # enter "dev" in the token bar
just eval                       # ten cases, real spend (~$0.30); needs OPENROUTER_API_KEY
just eval-gate                  # judge results/eval.json against results/baseline.json; no spend
gh workflow run test.yml        # the CI eval job; needs the repo secret, spends ~$0.30
```

- `OPENROUTER_API_KEY` is needed for real triage/eval only; never commit it.
- `APP_TOKEN` is needed for the web API; with it unset every `/api` request returns 503. The page
  stays open and prompts for the token.
- `APPROVALS_BUCKET` selects S3; unset means SQLite at `supportorg.db`.
- The CDK stack generates the app token; retrieve it from Secrets Manager after deploy. Do not put it
  in the task definition.
- `cdk synth` without deploying: from `infra/`, set `OPENROUTER_API_KEY=dummy` and the CDK account/
  region, then `uv run cdk synth --quiet`.
- `uv` is pinned in CI at `astral-sh/setup-uv@v10.3.3` — the action publishes no moving `v10` tag.

## First 30 minutes

1. `just test`; confirm 39 pass.
2. `just eval-gate`; confirm the recorded run clears the baseline.
3. Read `docs/NEXT.md` for the measured results and the bugs already found.
4. Pick step 1 (p99) or step 2 (runbook); both are small and unblock the re-score.
