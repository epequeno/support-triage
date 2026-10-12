# support-triage

**A support case triage agent: a case in plain language in, a grounded triage record out.**

It reads the case, looks up the customer in a (mock) support organisation, checks reality against open
incidents and service status, retrieves the policy that applies, and produces a classification, a
first-response commitment, a drafted reply and — for anything that changes state — a proposal that a
human has to approve.

The interesting part is not the model call. It is that **a commitment may only be made if the policy
and the entitlement support it**: a policy id that does not exist in the corpus is not a citation, a
response target that disagrees with the published matrix is corrected and reported, and no state
change happens without a named human.

## Run it

```bash
just sync && just seed          # uv sync, then seed the mock support org (SQLite)
just triage CASE-1041           # one case, end to end, with the full record
just triage CASE-1047 --tier strong
just triage CASE-1047 --approve-as your-name   # the human half of the approval gate
just web                        # the thin web UI: triage a case, approve a proposal, see the trail
just test                       # offline and deterministic; no API key needed
uv run python -m support_triage.costs          # what the last runs cost, by tier
```

`OPENROUTER_API_KEY` is needed only for real triage runs. The tests inject a fake model, so they are
free and reproducible.

## How it is put together

```
case text ─▶ gather ─▶ route ─▶ retrieve ─▶ classify ─▶ draft ─▶ propose ─▶ record
              │         │        │           │                     │
              │         │        │           │                     └─ gated state changes
              │         │        │           └─ cites policy ids; unknown ids fail the case
              │         │        └─ lexical retrieval over corpus/policy.md
              │         └─ cheap model by default, strong model on signal
              └─ accounts, entitlements, incidents, service status, case history
```

| path | what it is |
|---|---|
| `corpus/policy.md` | Eight citable policy sections: plans, severity definitions, the SLA matrix, the security path, incidents, escalation, scope. **The matrix in this file is the source of truth** for response targets. |
| `src/support_triage/backend.py` · `seed.py` | The mock support org: 8 accounts across four plans, 4 incidents, 10 cases with held-out truth. |
| `src/support_triage/agent.py` | The LangGraph state machine. Two model calls at most per case: classify, draft. |
| `src/support_triage/router.py` | Model routing: cheap by default, escalate on signal, with the reasons recorded per case. |
| `src/support_triage/tools.py` | Reads are ordinary functions. **Writes return a proposal**; `execute()` needs a named approver. |
| `src/support_triage/costs.py` | Token and dollar accounting, and a per-case cap that stops a run rather than overspending it. |
| `src/support_triage/web.py` | The thin web UI (M3): one page, no framework. Triage a case, see the record, approve a proposal by name — the approve endpoint executes a proposal out of the server's own record, so a browser can never invent a state change. |
| `src/support_triage/traces.py` | OTEL traces (M4): every triage flushes a span tree to `results/traces/<case>-<stamp>.json` — nodes, both model calls (with tier and tokens), repairs, and durations. Real opentelemetry-api/sdk, no collector. |

## The cases worth looking at

| case | why it is in the set |
|---|---|
| `CASE-1041` | Production down on a Business plan: Sev1, 1h, escalation required. The straightforward one. |
| `CASE-1042` | A Basic account asking for technical support. Not entitled, whatever the severity. |
| `CASE-1045` | Credential exposure: the security path, 15 minutes, on every plan. |
| `CASE-1047` | The customer declares Sev1 on a **non-production** workload. Severity follows the environment, not the assertion. |
| `CASE-1049` | The customer declares Sev2 on a production outage. Raised, not accepted. |
| `CASE-1043` · `CASE-1044` | Open incidents that supersede the case clock. |

## Where this stands, and what is next

[`docs/NEXT.md`](docs/NEXT.md) is the handoff: what is built, the measured numbers with their
caveats, what the routing experiment showed, and how to pick the work back up.

## Status

**M0 done, the eval (M5) runs, routing is measured, the web UI (M3), OTEL traces (M4) and the
AWS deploy (M6) are built.** Built: the mock support org, the citable policy corpus, the
LangGraph graph, model routing, the approval gate, cost accounting with a per-case cap, the
CLI, the thin web UI (`just web`), span-tree traces on every run, 24 offline tests, and
`just eval` over all ten cases with held-out truth.

The routing experiment (real API, all ten cases, 2026-10-12). "Pre-fix" rows ran before the
retrieval fix below; "post-fix" rows after:

| run | severity | in scope | sla | action | citations | cost |
|---|---|---|---|---|---|---|
| forced cheap (pre-fix) | 7/10 | 8/10 | 8/10 | 8/10 | 8/10 | **$0.0024** |
| forced strong (pre-fix) | 7/10 | 8/10 | 8/10 | 8/10 | 8/10 | $0.51 |
| forced cheap (post-fix) | 9/10 | 10/10 | 10/10 | 8/10 | 9/10 | **$0.0028** |
| routed (post-fix) | **10/10** | **10/10** | **10/10** | **10/10** | **10/10** | $0.30 |

Two results, not one. First: **a large share of the early misses were retrieval, not models.** The
lexical retriever's top-6 cut hid the scope rules from every tier — `CASE-1042` failed everywhere
because `POL-PLAN-1` and `POL-SCOPE-1` never reached the model, and no tier can cite a rule it
cannot see. The corpus is eight sections; it now renders in full, ranked, and the routed run is a
perfect 50/50 at $0.30. Second: even with everything visible, forced cheap gives up one to two
points (security handling) at 1% of routed's cost — that residual gap is what the router is for.
One run per cell, non-deterministic model, n=10. Stability, measured over four routed runs of the
post-fix code: severity **40/40**; in scope, sla, action and citations **39/40** each, every
single miss the same case (`CASE-1046`, a known `POL-SCOPE-1` wording ambiguity — a corpus fix,
not a model problem); $0.29–$0.30 per run.

The `sla` axis remains partly self-fulfilling — the corpus corrects a wrong target — but the
correction is now earned: a cited override (incident, security, out of scope) applies only when the
gathered facts support it, after a model citing the incident override on an unrelated case had a
right 12h "corrected" into a wrong 1h. Security cases have a defined severity (`POL-SEC-1`:
Sev2), and the repair path survives an exception raised bare (an empty `args` tuple used to crash
the repair itself, mid-eval).

## The deploy (M6), for the record

`infra/` holds a one-stack CDK Python deploy — public-subnet VPC, one Fargate service, the task's
public IP as the URL, no ALB. It was **deployed once for real, photographed, and torn down**
([`docs/ui-in-the-cloud.png`](docs/ui-in-the-cloud.png)): the UI live on the task's public IP,
CASE-1041 triaged through it — routed to strong with the routing reasons recorded — the escalation
proposal gated as *not executed, a human approves these*, approved by a named human, and the audit
trail showing the executed change. Total cloud lifetime: under an hour.

Two things the deploy found that nothing local could: the ledger table existed only from its
first write (a freshly seeded container 500'd `/api/ledger`), and an Apple-Silicon Docker build
crashed Fargate with `exec format error` (the asset now pins `LINUX_AMD64`).

**One caveat worth copying correctly:** the demo stack passes the OpenRouter key as a plain
task-definition environment variable. That is acceptable only for a stack that is torn down
within the hour, and it is spelled out so nobody ships that shortcut. For anything that outlives
a screenshot: SSM Parameter Store or Secrets Manager, not the task definition.

**Not built yet:** a wider case set.

**Deliberately not built:** MCP, authentication, multi-tenancy, anything persistent, fine-tuning.
