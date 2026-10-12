# State and next steps

Written 2026-10-11 at a pause; updated 2026-10-12 after a working session that ran the routing
experiment, closed the security-severity corpus gap, fixed a real bug in the SLA gate, and found
the bug behind the one case every tier was failing: the retriever was hiding the scope rules from
the model. The routed eval is now a perfect 50/50. This project is **mid-build and runs**. Read
this before touching anything.

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
| `policy.py` | SLA overrides are **earned**: a cited override applies only when the gathered facts support it. Retrieval renders the whole corpus, ranked — no section is ever hidden. |
| `costs.py` | Per-case cap that stops a run; the record must reconcile with the ledger. |
| `cli.py` | `triage CASE-1041 [--tier strong|--approve-as NAME]`. |
| `eval.py` | `just eval` — all ten cases scored against held-out truth, with cost. |
| `web.py` | The thin web UI (M3, 2026-10-12): one page, no framework. The approve endpoint executes the server's own record — a browser cannot invent a state change. |
| tests | 21 offline tests, fake model, no key, no spend. |

## The routing experiment: measured (2026-10-12)

All ten cases, real API, one run per configuration. "Pre-fix" rows ran before the retrieval fix
(below); "post-fix" rows after:

| run | severity | in scope | sla | action | citations | cost |
|---|---|---|---|---|---|---|
| forced cheap (pre-fix) | 7/10 | 8/10 | 8/10 | 8/10 | 8/10 | **$0.0024** |
| forced strong (pre-fix) | 7/10 | 8/10 | 8/10 | 8/10 | 8/10 | $0.51 |
| forced cheap (post-fix) | 9/10 | 10/10 | 10/10 | 8/10 | 9/10 | **$0.0028** |
| routed (post-fix) | **10/10** | **10/10** | **10/10** | **10/10** | **10/10** | $0.30 |

**Read it honestly:**

- **The biggest miss was not a model at all — it was retrieval.** The lexical retriever's top-6 cut
  hid `POL-PLAN-1` and `POL-SCOPE-1` from every tier on `CASE-1042`, so "respond, in scope" was the
  only answer any model could give without citing an id it never saw. The corpus is eight sections;
  it now renders in full, ranked. Post-fix, forced cheap jumps two axes to 9/10/10/8/9 and routed is
  a perfect 50/50.
- Forced cheap and forced strong **tied on every axis at a 214× cost difference** (pre-fix, same
  blindfold on both). The residual, post-fix gap is what routing is actually for: forced cheap
  still fumbles security handling (`CASE-1045` severity/action, `CASE-1048` action) that the strong
  model gets right — and the router's security signal sends exactly those cases to strong.
- At n=10, one run per cell, non-deterministic model: **±1-2 axis points between runs is noise**.
  The 50/50 is a run, not a guarantee; the pre-fix routed runs scored 9/10 in_scope and 8/10 in
  scope on the same code with different draws.

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
4. **Retrieval no longer hides sections from the model.** `policy.retrieve` ranked only sections with
   term overlap and `tools.search_policy` truncated at k=6 — so for `CASE-1042` ("How do I resize an
   instance?") neither `POL-PLAN-1` nor `POL-SCOPE-1` ever reached the prompt, and no tier could
   cite a rule it could not see. An account-aware query was tried first and it traded the bug
   (gained scope sections, lost `POL-INC-1` on the incident cases) — with eight sections total, the
   boring fix is to render the corpus in full, ranked. Pinned by a test.
5. **The repair path no longer crashes on exceptions raised bare.** A live `CASE-1048` crashed with
   `IndexError` because `_repair` indexed `exc.args[0]` and the incoming exception had empty args —
   the repair died repairing. Now it uses `str(exc)`. Intermittent (model-output-shaped), so pinned
   by a deterministic test rather than a rerun.
6. **The incident-linking bug did not reproduce.** All 70+ case runs since the pause linked
   incidents correctly, including declining a region-only match on `CASE-1041`. No corpus change
   made; keep watching for it, do not fix what is not failing.

## Bugs already found by running it (all fixed, all worth remembering)

The offline tests did not catch any of these, because every one is in the interface between model
output and code: the repair prompt was never rendered into the prompt (so a repair repeated the
identical failure); `next_action` was a free string and a model wrote prose into it; an SLA of
`"1 hour"` crashed the run instead of being parsed; the cost-reconcile assertion was tighter than its
own rounding; a model citing the incident override without a matching incident had a correct target
"corrected" into a wrong one (2026-10-12); bracketed citation ids burned repair calls (2026-10-12);
the retriever's top-6 cut hid the scope rules from every tier — the one case no model could pass
(2026-10-12); and the repair itself crashed on an exception raised bare (2026-10-12).
**The lesson held: type the interface, make the gates check facts rather than citations — and never
hide the rulebook from the model and call the miss a model failure.**

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

Spend: **$1.02 to 2026-10-11, $1.77 for the 2026-10-12 sessions** (nine ten-case evals, one smoke
run, one duplicated-and-cancelled eval that still paid for its tokens) — **$2.79 total**. The
expensive part remains output tokens from drafts, and the routed run at $0.30/ten cases is the
operating cost of the current configuration.

## How to pick it up

```bash
cd ~/code/support-triage
just sync && just seed        # if supportorg.db is missing (it is gitignored)
just test                     # 21 offline tests, no key needed
just triage CASE-1047         # a declared-Severity-1 case on a non-production workload
just eval                     # the ten-case table, live, real spend (~$0.30)
just eval --tier cheap        # the same table for $0.0028 — useful for prompt work
just web                      # the thin UI: triage a case, approve a proposal as a named human
```

**Next, in order:** OTEL traces (M4) — spans for the two model calls and the nodes between them,
so a triage can be read as a trace rather than a wall of text. Then the CDK stack and one real
deploy-and-destroy (M6): container, one VPC, the app behind it, torn down after the screenshot.
The web UI (M3) landed 2026-10-12: `just web`, one page, no framework — triage a case, see the
whole record (routing reasons, cost, corrections, citations), approve a proposal as a named human,
see the audit trail. The approve endpoint executes the proposal out of the *server's* in-memory
record, not the request body, so the browser cannot invent a state change. With the routed eval at
50/50, the interesting open question is stability under variance: rerun `just eval` a few times and
see what a *distribution* over the ten cases looks like, before widening the case set.

**Local git only — the repo is not published.** Publishing is M7 and has not been decided.
