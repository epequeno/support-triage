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

## The cases worth looking at

| case | why it is in the set |
|---|---|
| `CASE-1041` | Production down on a Business plan: Sev1, 1h, escalation required. The straightforward one. |
| `CASE-1042` | A Basic account asking for technical support. Not entitled, whatever the severity. |
| `CASE-1045` | Credential exposure: the security path, 15 minutes, on every plan. |
| `CASE-1047` | The customer declares Sev1 on a **non-production** workload. Severity follows the environment, not the assertion. |
| `CASE-1049` | The customer declares Sev2 on a production outage. Raised, not accepted. |
| `CASE-1043` · `CASE-1044` | Open incidents that supersede the case clock. |

## Status

**M0 done, and an early version of the eval (M5) already runs.** Built: the mock support org, the
citable policy corpus, the LangGraph graph, model routing, the approval gate, cost accounting with a
per-case cap, the CLI, 16 offline tests, and `just eval` over all ten cases with held-out truth.

First measured run (routed, real API, 2026-10-11):

| axis | score | read it as |
|---|---|---|
| first-response target | **10/10** | **partly self-fulfilling** — the corpus corrects a wrong target, so this measures "never promises a wrong number", not "the model knows the matrix" |
| in scope | 9/10 | the miss is a Basic account filing a technical case |
| next action | 8/10 | one over-eager incident link, one missed entitlement |
| citations | 9/10 | one verdict cited a policy id that does not exist, caught by the gate |
| severity | 7/10 | two of the three misses are **security cases, where the corpus never defines a severity at all** — the label is contestable, not just the model |

Cost: **$0.33 for ten cases**, 8 of which routed to the strong model. That split is the next thing to
fix: with 8/10 escalated the router is not yet earning its place, and the one case that routed cheap
and failed (entitlement on a Basic account) is evidence it under-escalated.

**Not built yet:** the web UI (M3), OpenTelemetry traces (M4), a wider case set, the CDK stack (M6).

**Deliberately not built:** MCP, authentication, multi-tenancy, anything persistent, fine-tuning.
