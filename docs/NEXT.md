# State and next steps

Written 2026-10-11 at a pause; updated 2026-10-12 after a working session that ran the routing
experiment, closed the security-severity corpus gap, and fixed a real bug in the SLA gate. This
project is **mid-build and runs**. Read this before touching anything.

## Where it stands

**Goal, restated:** demonstrate applied AI engineering — a running system with a GenAI model doing
load-bearing work on a realistic business problem, with infra visible through a container and IaC and
cost kept honest. Not a research artifact; capability over defensibility. (Owner's words: *"it's more
important that we can do something than what the outcomes are"*, and the previous project's
epistemics-heavy habit was explicitly set aside.)

**Built and working**

| piece | state |
|---|---|
| `corpus/policy.md` | 8 citable sections. **The SLA matrix is parsed out of this file**, so the table and the code cannot drift apart. Security severity is defined (POL-SEC-1: Sev2). |
| `backend.py` · `seed.py` | Mock support org: 8 accounts across four plans, 4 incidents, 10 cases **with held-out truth**. |
| `agent.py` | LangGraph state machine, seven nodes, at most two model calls per case (classify, draft). |
| `router.py` | Cheap by default, strong on signal; reasons recorded per case. |
| `tools.py` | Reads are functions; **writes return a proposal and need a named approver**. |
| `policy.py` | SLA overrides are **earned**: a cited override applies only when the gathered facts support it. |
| `costs.py` | Per-case cap that stops a run; the record must reconcile with the ledger. |
| `cli.py` | `triage CASE-1041 [--tier strong|--approve-as NAME]`. |
| `eval.py` | `just eval` — all ten cases scored against held-out truth, with cost. |
| tests | 19 offline tests, fake model, no key, no spend. |

## The routing experiment: measured (2026-10-12)

All ten cases, real API, one run per configuration:

| run | severity | in scope | sla | action | citations | cost |
|---|---|---|---|---|---|---|
| forced cheap | 7/10 | 8/10 | 8/10 | 8/10 | 8/10 | **$0.0024** |
| forced strong | 7/10 | 8/10 | 8/10 | 8/10 | 8/10 | $0.51 |
| routed (after fixes) | **9/10** | **9/10** | **10/10** | **9/10** | **9/10** | $0.33 |

**Read it honestly:**

- Forced cheap and forced strong **tied on every axis at a 214× cost difference**. On this case set
  the strong model buys nothing aggregate. "We route models" is now a measured result: the value is
  not "better answers everywhere", it is sending the two signals that need it (security language,
  open incidents) to strong.
- The failures differ even when counts tie. Cheap's catastrophic miss was `CASE-1048` (security on a
  Basic plan: `out_of_scope`, the whole security path missed) — and the router's existing security
  signal sends exactly that case to strong. Strong's embarrassing miss was promising a 1-hour SLA to
  a Basic account (`CASE-1042`). Same score, different failure kinds.
- At n=10, one run, non-deterministic model: **±1-2 axis points between runs is noise**. The routed
  row is a run, not a claim. The 2026-10-11 routed run scored sla 10/10 *before* the gate fix and
  8/10 in_scope in another — same code path, different draws.
- `CASE-1042` (Basic account filing a technical case) fails in **every** configuration, always as
  "respond, in scope". No tier gets `POL-PLAN-1`/`POL-SCOPE-1` right on it. It is the one remaining
  systematic miss and it is a prompt problem, not a model-choice problem.

## What the 2026-10-12 session changed

1. **Security severity is grounded.** `POL-SEC-1` now states a security report is classified Sev2
   (Sev1 only if production is also fully down). Both security cases (`CASE-1045`, `CASE-1048`) now
   pass all five axes; severity went 7/10 → 9/10.
2. **The SLA override gate was earning overrides for citations, not facts — found by running it.**
   `CASE-1050`: the model said the right 12h, also cited `POL-INC-1` (an incident exists in its
   region, unrelated symptom), and the gate "corrected" the right 12h into a wrong 1h. The exact
   inverse of the repo's central claim. Fix: `policy.expected_sla` takes the gathered facts —
   `POL-INC-1` needs an open incident AND the verdict linking the case to it, `POL-SEC-1` a
   security report, `POL-SCOPE-1` an out-of-scope verdict. Two tests pin it.
3. **Bracketed citation ids are normalised, not repaired.** The strong model intermittently emitted
   `[POL-SEV-1]`-shaped ids, burning a repair round-trip (a second model call). The fourth interface
   failure of its class — the lesson from 2026-10-11 ("type the interface, not prompt harder")
   applied: `Classification` normalises ids at the boundary.
4. **The incident-linking bug did not reproduce.** All 50+ case runs since the pause linked
   incidents correctly, including declining a region-only match on `CASE-1041`. No corpus change
   made; keep watching for it, do not fix what is not failing.

## Bugs already found by running it (all fixed, all worth remembering)

The offline tests did not catch any of these, because every one is in the interface between model
output and code: the repair prompt was never rendered into the prompt (so a repair repeated the
identical failure); `next_action` was a free string and a model wrote prose into it; an SLA of
`"1 hour"` crashed the run instead of being parsed; the cost-reconcile assertion was tighter than its
own rounding; a model citing the incident override without a matching incident had a correct target
"corrected" into a wrong one (2026-10-12); bracketed citation ids burned repair calls (2026-10-12).
**The lesson held: type the interface, and make the gates check facts, not citations.**

## Decisions taken

LangGraph (the model is genuinely in the loop, so the state machine is real). CDK Python, one stack,
**not** over-productionised. Cheap model by default with an easy upgrade, and routing as a
first-class feature. **Deploy once for real, and no CI investment** — this is not a production
system. The routing experiment's answer (forced tiers tie, routed wins modestly) does not change the
router's design; it changes its story: routing is about the 214× cost floor and the two signals,
not blanket accuracy.

Model defaults are verified against the provider catalogue on 2026-10-11 (cheap
`google/gemini-2.5-flash-lite` at $0.10/$0.40 per 1M; strong `openai/gpt-5` at $1.25/$10.00).
Nothing was assumed: the names and prices were read from the catalogue, and `costs.PRICES` is the
single place to update them.

Spend: **$1.02 to 2026-10-11, $1.18 for the 2026-10-12 session** (five ten-case evals plus smoke
runs) — **$2.20 total**. The expensive part remains output tokens from drafts, and the routed run
at $0.33/ten cases is the operating cost of the current configuration.

## How to pick it up

```bash
cd ~/code/support-triage
just sync && just seed        # if supportorg.db is missing (it is gitignored)
just test                     # 19 offline tests, no key needed
just triage CASE-1047         # a declared-Severity-1 case on a non-production workload
just eval                     # the ten-case table, live, real spend (~$0.33)
just eval --tier cheap        # the same table for $0.0024 — useful for prompt work
```

**Next, in order:** fix `CASE-1042` (prompt work: the Basic-plan entitlement rule is stated in two
sections and neither is winning) — cheapest way to iterate is `just eval --tier cheap`. Then the
web UI (M3) — the screenshot/video artifact that makes the rest legible in three minutes,
deliberately thin. Then OTEL traces (M4), then the CDK stack and one real deploy-and-destroy (M6).

**Local git only — the repo is not published.** Publishing is M7 and has not been decided.
