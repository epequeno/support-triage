# State and next steps

Written 2026-10-11 at a pause, not a closure. This project is **mid-build and runs**: M0 is done and an
early version of the eval works. Read this before touching anything.

## Where it stands

**Goal, restated:** demonstrate applied AI engineering — a running system with a GenAI model doing
load-bearing work on a realistic business problem, with infra visible through a container and IaC and
cost kept honest. Not a research artifact; capability over defensibility. (Owner's words: *"it's more
important that we can do something than what the outcomes are"*, and the previous project's
epistemics-heavy habit was explicitly set aside.)

**Built and working**

| piece | state |
|---|---|
| `corpus/policy.md` | 8 citable sections. **The SLA matrix is parsed out of this file**, so the table and the code cannot drift apart. |
| `backend.py` · `seed.py` | Mock support org: 8 accounts across four plans, 4 incidents, 10 cases **with held-out truth**. |
| `agent.py` | LangGraph state machine, seven nodes, at most two model calls per case (classify, draft). |
| `router.py` | Cheap by default, strong on signal; reasons recorded per case. |
| `tools.py` | Reads are functions; **writes return a proposal and need a named approver**. |
| `costs.py` | Per-case cap that stops a run; the record must reconcile with the ledger. |
| `cli.py` | `just triage CASE-1041 [--tier strong|--approve-as NAME]`. |
| `eval.py` | `just eval` — all ten cases scored against held-out truth, with cost. |
| tests | 16 offline tests, fake model, no key, no spend. |

**First measured run** (routed, real API): sla **10/10** (partly self-fulfilling — see below),
in_scope 9/10, action 8/10, citations 9/10, severity 7/10. **$0.33 per ten-case run.**
Total project spend to date: **$1.02**, of which $0.98 is output tokens — the drafted reply is the
expensive part, not the classification.

## The three findings that matter, in the order I would act on them

1. **Routing has no evidence yet.** 8 of 10 cases routed to the strong model, so the router is not
   earning its place; and the one case that routed *cheap* and failed (`CASE-1042`, a Basic account
   filing a technical case) is evidence it under-escalated. **The experiment: force cheap on all ten,
   force strong on all ten, and report accuracy × cost for both** (~$0.40). That turns "we route models"
   from a claim into a measured result, and it is the most interview-ready thing this repo can produce.
2. **Two corpus gaps, which are my bugs and not the model's.** (a) The corpus never defines a severity
   for a security report, so two of the three severity misses have a *contestable label* — the agent
   got the behaviour right (escalate to security, 15-minute clock, exception to the plan rule) and the
   label wrong. (b) Incident linking is over-eager: `CASE-1041` matched an incident by **region** when
   the policy requires the **symptom** to match, so a storage-latency incident got linked to a dead
   order pipeline.
3. **The `sla` axis is partly self-fulfilling.** The corpus corrects a wrong first-response target, so
   10/10 measures "never promises a wrong number" rather than "the model knows the matrix". Stated in
   the README next to the number; keep it there.

**Then:** the web UI (M3) — the screenshot/video artifact that makes the rest legible in three minutes,
deliberately thin. Then OTEL traces (M4), then the CDK stack and one real deploy-and-destroy (M6).

## Bugs already found by running it (all fixed, all worth remembering)

The offline tests did not catch any of these, because every one is in the interface between model
output and code: the repair prompt was never rendered into the prompt (so a repair repeated the
identical failure); `next_action` was a free string and a model wrote prose into it; an SLA of
`"1 hour"` crashed the run instead of being parsed; and the cost-reconcile assertion was tighter than
its own rounding. **If a fourth of these appears, the lesson is to type the interface, not to prompt
harder.**

## Decisions taken

LangGraph (the model is genuinely in the loop, so the state machine is real). CDK Python, one stack,
**not** over-productionised. Cheap model by default with an easy upgrade, and routing as a first-class
feature. **Deploy once for real, and no CI investment** — this is not a production system.

Model defaults are verified against the provider catalogue on 2026-10-11 (cheap
`google/gemini-2.5-flash-lite` at $0.10/$0.40 per 1M; strong `openai/gpt-5` at $1.25/$10.00). Nothing
was assumed: the names and prices were read from the catalogue, and `costs.PRICES` is the single place
to update them.

## How to pick it up

```bash
cd ~/code/support-triage
just sync && just seed        # if supportorg.db is missing (it is gitignored)
just test                     # 16 offline tests, no key needed
just triage CASE-1047         # a declared-Severity-1 case on a non-production workload
just eval                     # the ten-case table, live, real spend
```

**Local git only — the repo is not published.** `git init` and one commit (`d21b4d8`) exist; publishing
is M7 and has not been decided.
