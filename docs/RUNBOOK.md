# Runbook — `support-triage`

**One page, for the person who owns the deploy.** The deployed shape is a single Fargate task
behind no load balancer: the task's public IP on port 8000 *is* the URL. There is exactly one owner
and no pager integration — this is a demo stack, and the honest runbook says so rather than
pretending an on-call rotation exists.

## What is running, and where to look

| thing | where |
|---|---|
| The app | `http://<task public IP>:8000` — the IP changes when the task is replaced |
| Task and service events | ECS console → cluster from the CDK stack → service `support-triage` |
| Application logs | CloudWatch log group from the stack (stream prefix `triage`), **1-day retention**, deleted with the stack |
| The app token | Secrets Manager `support-triage/app-token` |
| The audit trail | S3 bucket from the stack, `approvals/<case_id>/<timestamp>-<uuid>.json` |
| Spend so far | `http://<ip>:8000/api/ledger` (with the bearer token), or `just check` locally |
| The last measured behaviour | `results/eval.json` + `results/baseline.json`; `just eval-gate` judges a run |

The health check is the app checking itself: the task runs `GET /api/cases` with the app token
every 30 s, 10 s timeout. ECS replaces an unhealthy task, and a deployment that never becomes
healthy is rolled back by the circuit breaker. **A task that is replaced every few minutes is a
wiring failure, not a load problem** — read the reason below before scaling anything.

## First five minutes of any report

```bash
IP=<task public ip>
TOKEN=$(aws secretsmanager get-secret-value --secret-id support-triage/app-token \
        --query SecretString --output text)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://$IP:8000/api/cases
curl -s -H "Authorization: Bearer $TOKEN" http://$IP:8000/api/ledger
aws logs tail <log group from the stack> --follow
```

- `200` from `/api/cases` → the app and its database are fine; the report is about something else.
- `401` → the token you sent is not the token the task has. Re-read the secret; do not edit the
  task definition.
- `503` → the task has no `APP_TOKEN` in its environment, so the API fails closed. That is the
  intended behaviour; it is a wiring bug, not an outage to be worked around.

## Incident 1 — the spend cap was hit

**Symptom.** `/api/triage/<case>` returns 500 and the log shows `CostCapExceeded`. The record is
not produced. `just check` or `/api/ledger` shows the run's cost near the cap.

**What is true.** The cap is **$0.25 per case** (`costs.DEFAULT_CAP_USD`), enforced by the ledger
inside one triage run: a call that would cross it raises rather than completing optimistically. It
is a per-case guardrail, not an account-level billing stop — nothing here can spend beyond the
provider's own balance, and nothing here paginates anyone.

**Do this.** Read the ledger first and write down the figure. If one case is above the cap, that is
the guardrail working. If every case is expensive, the router has stopped routing — compare
`tiers` in the ledger (or `/api/ledger`) against the recorded run: routed was roughly $0.30 for ten
cases with 8 strong calls; forced-strong was $0.51. A router stuck on strong is the expensive
failure, and `just eval-gate` fails a run that costs more than $0.36 for this reason.

**Do not** raise the cap to get a demo through. A cap hit is the one number in this repo that is
supposed to be loud.

## Incident 2 — the approval store is unavailable

**Symptom.** `/api/approve` returns 500 (S3: `AccessDenied`, `NoSuchBucket`, or a timeout; locally:
`database is locked` or a permissions error) and the audit trail does not appear. The CLI raises the
same error from `--approve-as`.

**What is true.** An approval is a state change with a name on it, and the write is the record. If
the write fails, the approval did not happen — the app returns an error rather than reporting
success, and there is no retry and no queue. That is deliberate: a silent approval is worse than a
failed one.

**Do this.**
1. Confirm the object store: `aws s3 ls s3://<bucket>/approvals/<case_id>/`.
2. If the bucket is gone, the stack is gone — the bucket is destroyed with it, and with it the
   trail. Check `cdk deploy` state before assuming a permissions bug.
3. If the bucket exists but writes fail, look at the **task role**'s `s3:PutObject` grant and the
   bucket policy; the task role reads and writes only this bucket.
4. Re-triage the case in the UI after the store recovers, then approve: the approved proposal is
   taken from the server's in-memory record, so a task restart in between means the case must be
   triaged again first (`no such proposal — triage the case first`).

**Do not** switch `APPROVALS_BUCKET` off to "get it working" on the deployed stack: that silently
moves the trail onto the container's filesystem, where it dies with the task.

## Incident 3 — the task is unhealthy or the port is closed

**Symptom.** The URL does not answer, the ECS service keeps replacing tasks, or a deploy rolled
back. Health-check failures show as the service bouncing; the log shows the health check's own
`401`/`503` (the check sends the token, so an auth failure means the secret did not reach the
container).

**Do this, in order.**
1. **Is the task running?** ECS console → service → events. `Task failed ELB health checks` here
   means the app itself; `CannotPullContainerError` means the image.
2. **Does the container have its environment?** The task must have `APPROVALS_BUCKET`,
   `AWS_REGION`, `OPENROUTER_API_KEY` (plain), and `APP_TOKEN` (from Secrets Manager). `503` from
   the health check points at `APP_TOKEN`; the execution role needs `secretsmanager:GetSecretValue`
   on that secret.
3. **Is the port right?** The container listens on 8000 and the security group opens 8000. The task
   public IP changes on every replacement — a bookmark to the old IP is not an outage.
4. **A rollback is the circuit breaker, not a crash loop quirk.** The previous task definition was
   restored; fix forward and redeploy, do not disable the circuit breaker.

## Escalation

| situation | who | first artefact to attach |
|---|---|---|
| Anything above | The repo owner (single owner, no rotation) | The log excerpt, `/api/ledger` output, and the ECS service events |
| Suspected security exposure (a real credential in a task) | Owner, immediately; treat the OpenRouter key as burned | The task definition revision ID |
| Cost | Owner, before changing anything | `results/eval.json` from the last gated run |

## Known gaps this runbook does not paper over

- **No alarms.** Nothing pages anyone; every check above is manual. That is the operability gap the
  readiness review flagged.
- **Retention.** Logs are kept for one day and the log group dies with the stack; the approvals bucket and the secret are destroyed with
  the stack. If an audit trail must outlive the deploy, change the bucket's removal policy first.
- **One task, no ALB.** There is a single point of failure and no rolling upgrade that keeps serving
  traffic; `minHealthyPercent` is the default 50%.
- **Triage records are in memory.** Only approvals are durable — a replacement loses the server-side
  records the approve endpoint reads.
