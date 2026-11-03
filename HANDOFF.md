# Handoff — `support-triage`

**As of:** 2026-11-03
**Read first:** [`docs/NEXT.md`](docs/NEXT.md) for the build history and the measured numbers. This
file is the current handoff: what changed, what is verified, and what to do next. The repo is not
greenfield — M0–M7 are built, the demo runs, and the working tree is clean.

## Current state

Six readiness-review gaps closed, the deployed shape verified against real AWS, and the review re-scored, on 2026-11-03:

1. **Caller auth and a durable audit trail** (`ab2dd7d`). Every `/api` route requires a bearer
   token; approvals are written through an `ApprovalStore` — SQLite locally, S3 when
   `APPROVALS_BUCKET` is set. The CDK stack creates the bucket and a generated Secrets Manager app
   token, the health check sends the token, and deployments fail fast with a circuit breaker.
2. **CI and an eval gate** (`3ad9418`, plus the action-pin fix). A workflow ran the 40-test offline
   suite on every push and pull request, and a manual-dispatch `eval` job ran one real routed eval
   (~$0.30) gated by `python -m support_triage.eval_gate` against `results/baseline.json`. **The
   workflow was then disabled at the owner's request** — it is kept, working and documented, at
   `ci/github-actions-test.yml`, outside `.github/workflows/`, so GitHub does not read it. The gate
   itself is unaffected: `just eval-gate` is the check, the workflow was only its trigger.
3. **The per-policy cache** (`89e8f84`). The corpus is parsed once per process per file version,
   indexed once, and each section's terms and rendered block are computed once and reused, so the
   classify prompt is assembled rather than rebuilt six times per case. `retrieve(k=…)` can no
   longer cut below `POL-PLAN-1`/`POL-SCOPE-1`. **The token half of gap #3 was measured and
   refused** — see "Not verified / known limits".
4. **Concurrency and p99** (`load.py`, `results/load.json`). `just load` drives the ASGI app
   in-process or a live `just web` and records p50/p95/p99 and throughput per path.
5. **The runbook** (`docs/RUNBOOK.md`). What is running, where to look, and the three incidents this
   deploy can have: spend cap, approval store unavailable, task unhealthy. Linked from the README.
6. **The deployed shape verified against real AWS** (deployed, exercised, destroyed). The gate held
   (200/401/401/200 on page / no token / wrong token / token), CASE-1041 was triaged and approved in
   the cloud, the approval object landed in S3 and survived a task replacement, and teardown left
   nothing behind — after one fix: the container's log group was retained by every `cdk destroy`,
   leaking a group per deploy (two from October were still there). The stack now sets
   `RemovalPolicy.DESTROY` on it and a second cycle left zero.
7. **The readiness review re-scored** (`notes/2026-11-03-production-readiness-support-triage.md` §8,
   outside the repo). **11 of 12 dimensions at L2**, one short: dimension 1, identity and provenance,
   stays L1 because a run records neither the model version nor a corpus digest. The bar needs L2 on
   dimension 1, so the verdict is still "not production ready" — by one dimension, not six.

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
| `infra/support_triage_stack.py` | S3 bucket (private, encrypted, destroy-with-stack), generated `support-triage/app-token` secret, task role read+put on the bucket, execution role read on the secret, health check sends the token, deployment circuit breaker. Fixed the null execution-role bug found during synth, and — found live during the 2026-11-03 teardown — the log group now dies with the stack. |
| `src/support_triage/eval_gate.py` | Judges a run against the baseline: per-axis floors as rates snapped up to whole cases, plus a cost ceiling. Exit 0 pass, 1 regression, 2 unusable input; names the axis and the case. |
| `results/baseline.json` | The floors — severity 1.0, in scope/sla/action/citations 0.9, cost ceiling $0.36 — with the four reference runs they came from. |
| `ci/github-actions-test.yml` · `ci/README.md` | The pipeline as an **artifact, not wired**: `offline` job on push/PR; `eval` job on manual dispatch only, in the `eval` environment, refusing to start without `OPENROUTER_API_KEY`. `ci/README.md` says how to enable it and what does not run meanwhile. |
| `src/support_triage/policy.py` | Per-policy-id caches: parse keyed by file identity (mtime, size), terms and rendered blocks keyed by the section's own text, `index()` derived once and returned read-only. `retrieve` keeps the mandatory sections at any `k`. |
| `src/support_triage/agent.py` | `_prompt` resolves the corpus index once instead of twice per candidate. Prompt bytes unchanged. |
| `src/support_triage/load.py` · `results/load.json` | p50/p95/p99 and throughput per path, in-process or against a live URL. Recorded run: 300 requests/path at concurrency 10. |
| `docs/RUNBOOK.md` | One page: first five minutes, the three incidents, escalation, and the gaps it does not paper over. |
| tests | `tests/test_web.py` (page open, API 401, fail-closed 503, approve through the store), `tests/test_eval_gate.py` (recorded run passes, one noise miss passes, two misses fail and are named, severity miss fails, cost blowup fails, partial and inconsistent runs refused, CLI exit codes), a failed audit write case, and three cache tests (mandatory sections survive any query and any `k`; the cache renders byte-for-byte what a fresh parse renders; a corpus edit is never served stale). **40 tests pass.** |
| docs | README run instructions, auth/approval file table, deploy caveat, CI/eval-gate section; `docs/NEXT.md` records the gate and its thresholds. |

## Verified

| check | command | result |
|---|---|---|
| Offline suite | `uv sync --frozen && uv run pytest -q` | 39 passed in ~0.3 s |
| CI on push (before it was disabled) | `git push origin main` → run 41656259069 | **green**; `offline suite` success, `eval gate` correctly skipped |
| Eval job wiring (before it was disabled) | `gh workflow run test.yml` → run 41656367230 | actions resolve, then `OPENROUTER_API_KEY is not set; the eval spends real credit and will not run blind` — **fails before any spend** |
| Gate, recorded runs | `just eval-gate` over `eval.json` and `eval-dist-1..3` | all four PASS, including the run that missed `CASE-1046` on four axes |
| Gate, regression | synthetic run with two citations misses + `$0.52` | exit 1; prints `citations: 8/10 below the floor of 9/10`, the two cases, and the cost breach |
| Gate, bad input | missing file | exit 2 with the OS error |
| SQLite durability | two separate Python processes opening the same DB | one approval row survived; `approved_by` intact |
| HTTP auth gate | `APP_TOKEN=smoke uv run uvicorn support_triage.web:app --port 8011` then `curl` | page 200 without token; `/api/cases` 401 without token; 200 with token; page contains the token bar |
| S3 adapter | `uv run --with 'moto[s3]' python …` | `record` + `list` round-trip for `CASE-1041`; unknown case returns `[]` |
| Cache equivalence | `_prompt` for all ten cases, pre-change commit (worktree) vs now | **10/10 byte-identical**; prompt assembly 390 µs → 12 µs per case |
| Cache invalidation | rewrite a temp corpus, then `load`/`index`/`retrieve` again | new text visible immediately |
| Cache guarantee | `retrieve(q, k=1)` over four queries, incl. an empty one | `POL-PLAN-1` and `POL-SCOPE-1` always in the set |
| Load, in-process | `just load` (300/path, concurrency 10) | `/` p50 5.1 ms / p99 17.1 ms · `/api/cases` p50 32.0 ms / p99 64.7 ms |
| Load, live uvicorn | `just load --url http://127.0.0.1:8012` | `/` p50 3.8 ms / p99 50.6 ms · `/api/cases` p50 16.2 ms / p99 54.5 ms, 480 req/s; `/api/cases` p50 57 ms, 291 req/s at concurrency 16 |
| Failed audit write | store that raises on `record`, through `TestClient` | HTTP 500 — a failed approval is never reported as an approval (now test-pinned) |
| Real deploy | `cdk deploy` (167 s), then curl the task public IP | task HEALTHY; page 200, `/api/cases` 401 without a token, 401 with a wrong one, 200 with the Secrets Manager token |
| Real triage | `POST /api/triage/CASE-1041` on the deployed task | Sev1, 1 h, escalate, routed strong, $0.0347 |
| Real approval | `POST /api/approve` by name, then `aws s3 ls` | object at `approvals/CASE-1041/2026-11-03T15:01:35Z-f08736fade…json`; empty approver → 400 |
| Durability across a restart | `update-service --force-new-deployment`, new public IP | trail still listed from S3; approving the lost in-memory record → 404 |
| Real teardown | `cdk destroy` twice, around the fix | first left 3 log groups → `RemovalPolicy.DESTROY` added, orphans deleted; second left 0 log groups, no bucket, no secret, no cluster |
| CDK synthesis | `OPENROUTER_API_KEY=dummy CDK_DEFAULT_ACCOUNT=… CDK_DEFAULT_REGION=us-east-1 uv run cdk synth --quiet` | stack synthesizes; one `minHealthyPercent` annotation remains |

## Not verified / known limits

- **Real AWS is verified for the paths exercised on 2026-11-03** — deploy, health, gate, triage,
  approval to S3, restart, teardown, and a second cycle after the log-group fix. Not exercised: a
  redeploy over a live stack (the circuit-breaker path), secret rotation, a bucket-policy change, and
  anything under load. A deploy still creates billable resources; do not run it without approval, and
  `cdk destroy` after.
- **The eval job has never run end to end.** There is no repository secret and no `eval`
  environment yet, so only the fail-fast path is proven. Set `OPENROUTER_API_KEY` as a **repository
  secret**, and add required reviewers or a spending limit to the `eval` environment before
  dispatching for real.
- **Gap #3's token half is refused, on measurement.** Policy text is 76% of the classify prompt
  (3,013 of ~3,917 chars). The only real cut is sending `POL-SEC-1` only when the router's lexical
  security signal fires: that saves 638 chars on eight of ten cases — 18.5% of policy text, 14.3%
  of the prompts — and it hides the section that sets a security report's severity from every case
  the lexical signal misses. The cache therefore changes compute, not tokens.
- **The load numbers are one box.** One uvicorn worker, one SQLite connection per request, a
  32-core dev machine, no network between client and app in the in-process mode. `/api/cases`
  degrades 70× from concurrency 1 to 16 — the per-request `backend.connect()`, not the query (the
  handler body is 0.1 ms). A thread-local connection pool is the fix if this ever serves more than a
  demo; it was deliberately not built.
- **CI is disabled by choice.** The workflow is complete and one `git mv` from running
  (`ci/README.md`), but nothing runs on push, no repository secret is set, and `main` is unprotected —
  so the green suite and the eval gate are discipline, not enforcement. Dimension 10's L2 rests on
  the gate existing and being runnable (`just eval-gate`), not on CI; its L3 ("gate blocks
  promotion") needs the workflow enabled plus required checks.
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

### 1. Record provenance in the run manifest (the only dimension below L2, ~2 h)

- `results/eval.json` rows carry `tier` but not the model; no run records the corpus digest or a
  prompt hash; traces carry the model per call but are gitignored and not tied to a commit.
- Put the model id, a digest of `corpus/policy.md`, and a hash of the prompt template in the trace
  root span and in each eval row (or a run manifest beside it). Then a run is reproducible from the
  repo alone.
- **Done when:** `results/eval.json` names the model and the corpus version, and dimension 1 can be
  scored L2 against its own evidence — after which the project passes the rubric's bar.

### 2. Alarms, required checks, and the token economics

- **Alarms (~half day, dimension 9's L3):** task health, 5xx rate, approval-store errors, spend —
  plus the decision on who is paged, which today is "nobody".
- **Required checks (~1–2 h, dimension 10's L3):** re-enable the workflow from `ci/` (one `git mv`),
  protect `main`, require the offline check, and schedule the eval run for drift. Today nothing is
  automatic and nothing blocks.
- **Token economics (a day, ~$0.30 of eval, dimension 4's L3):** a facts-conditioned candidate set
  with a routed eval proving no axis loss. The 14% saving is refused today on measurement, not
  unimplemented by accident.

## Commands and access

```bash
cd ~/code/support-triage
just sync && just seed          # seed the mock org if supportorg.db is missing
just test                       # 40 offline tests, no key
just load --url http://127.0.0.1:8000   # p50/p99 for the API against a live `just web`
just triage CASE-1041           # one case end to end
just triage CASE-1041 --approve-as "Your Name"
APP_TOKEN=dev just web          # enter "dev" in the token bar
just eval                       # ten cases, real spend (~$0.30); needs OPENROUTER_API_KEY
just eval-gate                  # judge results/eval.json against results/baseline.json; no spend
gh workflow run test.yml        # the CI eval job; needs the repo secret, spends ~$0.30
(cd infra && uv run cdk deploy --require-approval never)   # billable; cdk destroy after
```

- `OPENROUTER_API_KEY` is needed for real triage/eval only; never commit it.
- `APP_TOKEN` is needed for the web API; with it unset every `/api` request returns 503. The page
  stays open and prompts for the token.
- `APPROVALS_BUCKET` selects S3; unset means SQLite at `supportorg.db`.
- The CDK stack generates the app token; retrieve it from Secrets Manager after deploy. Do not put it
  in the task definition.
- `cdk synth` without deploying: from `infra/`, set `OPENROUTER_API_KEY=dummy` and the CDK account/
  region, then `uv run cdk synth --quiet`.
- `uv` is pinned in the workflow artifact at `astral-sh/setup-uv@v10.3.3` — the action publishes no moving `v10` tag.

## First 30 minutes

1. `just test`; confirm 40 pass.
2. `just eval-gate`; confirm the recorded run clears the baseline.
3. Read `docs/NEXT.md` for the measured results and the bugs already found.
4. Pick step 1 (provenance) — it is the last dimension between this and the rubric's bar.
