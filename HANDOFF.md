# Handoff — `support-triage`

**As of:** 2026-11-03
**Read first:** [`docs/NEXT.md`](docs/NEXT.md) for the build history and the measured numbers. This
file is the current handoff: what changed, what is uncommitted, what is verified, and what to do
next. The repo is not greenfield — M0–M7 are built and the demo runs.

## Current state

The 2026-11-03 session closed the production-readiness review's **gap #1: caller auth and a durable
audit trail**. Every `/api` route now requires a bearer token, and approvals are written through an
`ApprovalStore` that uses SQLite locally and S3 when `APPROVALS_BUCKET` is set. The CDK stack now
creates the S3 bucket and a generated Secrets Manager app token; the task health check sends the
token; deployments fail fast with a circuit breaker.

**The changes are uncommitted.** `git status` shows modified `README.md`,
`infra/support_triage_stack.py`, `pyproject.toml`, `src/support_triage/cli.py`,
`src/support_triage/tools.py`, `src/support_triage/traces.py`, `src/support_triage/web.py`,
`tests/test_triage.py`, `uv.lock`, plus untracked `HANDOFF.md`,
`src/support_triage/approvals.py`, `src/support_triage/auth.py`, and `tests/test_web.py`.
`src/support_triage/traces.py` carries a
**pre-existing** edit from before this session; inspect it and decide whether to keep or drop it
before committing. `pyproject.toml` and `uv.lock` also contain the pre-existing edits plus the new
`boto3` and `httpx` additions.

No CI exists. The S3 path has **not** been run against real AWS. The readiness review's "5 of 12
dimensions at L2" count predates the auth fix and should be re-scored; the other ranked gaps stand.

## What changed on 2026-11-03

| area | change |
|---|---|
| `src/support_triage/auth.py` | Bearer-token gate. Reads `APP_TOKEN`; constant-time compare; fails closed with 503 when the token is unset. |
| `src/support_triage/approvals.py` | `ApprovalStore` protocol with `SqliteApprovalStore` and `S3ApprovalStore`. `store_for(conn)` selects S3 when `APPROVALS_BUCKET` is set, SQLite otherwise. S3 writes one immutable JSON object per approval under `approvals/<case_id>/`. |
| `src/support_triage/tools.py` | `execute(store, proposal, approved_by)` now takes the store; the named-human gate is unchanged. |
| `src/support_triage/cli.py` | `--approve-as` writes through the store and reads the trail back from it. |
| `src/support_triage/web.py` | `/api` routes moved onto an authenticated router; the page prompts for the token and sends it as a bearer header. Approve and trail endpoints use the store. |
| `infra/support_triage_stack.py` | S3 bucket (private, encrypted, destroy-with-stack), generated `support-triage/app-token` secret, task role read+put on the bucket, execution role read on the secret, health check sends the token, deployment circuit breaker. Fixed the null execution-role bug found during synth. |
| tests | New `tests/test_web.py`: page open/API 401, fail-closed 503, and an approved proposal writing through the store. The gate test now uses the SQLite store. **27 tests pass.** |
| docs | README run instructions, auth/approval file table, deploy caveat, and gap-closure note updated. |

## Verified

| check | command | result |
|---|---|---|
| Offline suite | `uv sync --quiet && uv run pytest -q` | 27 passed in ~0.3 s |
| SQLite durability | two separate Python processes opening the same DB | one approval row survived; `approved_by` intact |
| HTTP auth gate | `APP_TOKEN=smoke uv run uvicorn support_triage.web:app --port 8011` then `curl` | page 200 without token; `/api/cases` 401 without token; 200 with token; page contains the token bar |
| S3 adapter | `uv run --with 'moto[s3]' python …` | `record` + `list` round-trip for `CASE-1041`; unknown case returns `[]` |
| CDK synthesis | `OPENROUTER_API_KEY=dummy CDK_DEFAULT_ACCOUNT=… CDK_DEFAULT_REGION=us-east-1 uv run cdk synth --quiet` | stack synthesizes; one `minHealthyPercent` annotation remains |

## Not verified / known limits

- **Real AWS.** `moto` proves the S3 adapter's code path, not IAM, secret injection, bucket policy,
  or the Fargate task's access to the bucket. The stack has not been deployed since the fix. A deploy
  creates billable resources; do not run it without explicit approval.
- **`minHealthyPercent`.** CDK warns that the default 50% is used. That is acceptable for the
  single-task demo. Setting `min_healthy_percent=100` would start a second task during rollout and
  can complicate the "task public IP is the URL" workflow; decide deliberately.
- **No CI, no eval gate.** The 27 tests are manual. The review's next gap is a CI job running the
  tests and comparing an eval run to the recorded 39–40/40 baseline.
- **OpenRouter key still rides as a plain task environment variable.** The app token moved to
  Secrets Manager; the key did not. This remains acceptable only for an hour-long demo and is called
  out in the README.
- **Local MinIO is not required.** Docker Hub denied `minio/minio` and Quay returned 401 in this
  environment. The S3 adapter is covered by the `moto` smoke; use `moto` for tests or a real S3
  endpoint for local end-to-end.
- **Triage records are still in memory.** Only approvals are durable. A task restart loses the
  server-side records the approve endpoint reads.
- **The readiness review's dimension count is stale.** Re-score after the auth fix; the verdict
  remains "not production ready" until the CI, capacity, and operability gaps close.

## Next steps, ranked

### 1. Commit and clean the working tree (do this first)

- Review `git diff`, especially the pre-existing `src/support_triage/traces.py` edit. Keep it only if
  it is intentional; otherwise drop it.
- Commit the auth/durable work and this handoff with a message such as:
  `feat: gate the web API with a bearer token and persist approvals to S3`
- Include the new `HANDOFF.md`, `auth.py`, `approvals.py`, `tests/test_web.py`, and the README /
  `docs/NEXT.md` updates.
- **Done when:** `git status` is clean and `uv sync --frozen && uv run pytest -q` passes 27 tests.

### 2. Add CI and an eval gate (review gap #2, ~3 h)

- Add `.github/workflows/test.yml`: checkout, install `uv`, `uv sync --frozen`, `uv run pytest -q`.
  No API key needed; the suite injects a fake model.
- Decide the eval gate: `just eval` spends ~$0.30 per ten-case run and needs `OPENROUTER_API_KEY`.
  Either run it on a schedule/manual dispatch with a repository secret and a budget, or keep it
  manual and add a script that compares `results/eval.json` to the recorded 39–40/40 baseline.
- **Done when:** a pull request runs the offline suite and a failing test blocks merge; if the eval
  gate is enabled, a regression on any of the five axes is visible in the check output.

### 3. Add the per-policy cache (review gap #3, ~half day)

- Current `policy.retrieve` renders all eight sections on every call. Add a per-policy-id cache while
  guaranteeing the scope and entitlement sections are always present; keep the `CASE-1042` fix.
- **Done when:** the offline suite and a routed eval keep the same axes, and token/cost per case drops
  without hiding a rule. Add a test that the cache cannot omit `POL-PLAN-1`/`POL-SCOPE-1`.

### 4. Measure concurrency and a p99 (review gap #4, ~half day)

- Add a small load script against `just web` or the ASGI app; record p50/p99 for concurrent requests.
- **Done when:** README or `results/` states a throughput and p99, even a small one. Optimizing is
  optional; replacing "unknown" with a number is the point.

### 5. Write the one-page runbook (review gap #5, ~2 h)

- `docs/RUNBOOK.md`: who is paged, what they look at, and the three likely incidents (spend cap hit,
  approval store unavailable, task unhealthy/port closed).
- **Done when:** the page exists and is linked from the README; no code change required.

### 6. Optional: verify the deployed shape against real AWS

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

### 7. Re-score the readiness review

- Re-run the five-vector/four-question rubric against this state. Dimension 11 (security and
  permissions) should move; the remaining gaps are CI, cache, capacity, and operability.
- **Done when:** the review note and registry entry carry a current dimension count and a next gap.

## Commands and access

```bash
cd ~/code/support-triage
just sync && just seed          # seed the mock org if supportorg.db is missing
just test                       # 27 offline tests, no key
just triage CASE-1041           # one case end to end
just triage CASE-1041 --approve-as "Your Name"
APP_TOKEN=dev just web          # enter "dev" in the token bar
just eval                       # ten cases, real spend (~$0.30); needs OPENROUTER_API_KEY
```

- `OPENROUTER_API_KEY` is needed for real triage/eval only; never commit it.
- `APP_TOKEN` is needed for the web API; with it unset every `/api` request returns 503. The page
  stays open and prompts for the token.
- `APPROVALS_BUCKET` selects S3; unset means SQLite at `supportorg.db`.
- The CDK stack generates the app token; retrieve it from Secrets Manager after deploy. Do not put it
  in the task definition.
- `cdk synth` without deploying: from `infra/`, set `OPENROUTER_API_KEY=dummy` and the CDK account/
  region, then `uv run cdk synth --quiet`.

## First 30 minutes

1. `git status` and `git diff`; decide the `traces.py` edit; commit.
2. `uv sync --frozen && uv run pytest -q`; confirm 27 pass.
3. Read `docs/NEXT.md` for the measured results and the bugs already found.
4. Pick step 2 (CI) unless the owner asks for the AWS verification first.
