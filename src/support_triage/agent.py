"""The triage agent: a LangGraph state machine with a model in the loop.

The graph is explicit rather than a free-running agent loop, because a support org needs to be able to
say where a decision was made. Every node has one job, and the two places a model is called are
`classify` and `draft` — the cheap model by default, the expensive one when the router says so.

Nothing here writes to the support org. State changes come out as proposals (tools.py).
"""

from __future__ import annotations

import os
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph

from . import costs, policy, router, tools
from .models import Classification, Proposal, RoutingDecision, Severity, SEVERITY_LABEL, TriageRecord


class CitationError(RuntimeError):
    """A verdict citing a policy id that does not exist. It fails rather than shipping."""


class Usage(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    model: str


class Caller(Protocol):
    """The model boundary. Tests inject a fake; production injects an OpenRouter client."""

    tier: str

    def classify(self, context: dict, candidates: list[dict]) -> tuple[Classification, Usage]: ...

    def draft(self, context: dict, verdict: Classification) -> tuple[str, Usage]: ...


class TriageState(TypedDict, total=False):
    case_id: str
    context: dict
    routing: dict
    candidates: list[dict]
    verdict: dict
    draft: str
    notes: list[str]
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    record: dict


FACTS_HEADER = "ACCOUNT FACTS"
REQUIREMENTS_HEADER = "CANDIDATE POLICY LINES"


def _prompt(context: dict, candidates: list[dict]) -> str:
    case, account = context["case"], context["account"]
    ent = account.get("entitlement", {})
    incidents = context.get("open_incidents", [])
    status = context.get("service_status", [])
    lines = [
        f"CASE {case['case_id']}: {case['subject']}",
        f"body: {case['body']}",
        f"customer-declared severity: {case.get('declared_severity') or 'none declared'}",
        "",
        FACTS_HEADER,
        f"- account: {account.get('name')} ({account.get('account_id')}), plan {account.get('plan')}",
        f"- region: {account.get('region')}; production-critical: {bool(account.get('production_critical'))}",
        f"- entitlement: technical support {bool(ent.get('technical_support'))}, intake {ent.get('intake')},"
        f" workloads {ent.get('workloads')}, named TAM {bool(ent.get('named_tam'))}",
        f"- technical account manager: {account.get('tam') or 'none'}",
        "- open incidents in this region: " + (", ".join(
            f"{i['incident_id']} {i['service']} ({i['status']}: {i['summary']})" for i in incidents)
            or "none"),
        "- service status in this region: " + (", ".join(
            f"{s['service']}={s['status']}" for s in status) or "unknown"),
        f"- prior cases: {len(context.get('prior_cases', []))}",
        "",
        REQUIREMENTS_HEADER,
        policy.render([policy.index()[c["policy_id"]] for c in candidates if c["policy_id"] in policy.index()]),
        "",
        "Classify the case. Cite the policy ids that ground your classification and the response target,",
        "using only the ids above. The declared severity is an assertion, not evidence.",
    ]
    repair = context.get("repair")
    if repair:
        lines += ["", "CORRECTION REQUIRED"]
        if repair.get("validation_error"):
            lines.append(f"Your previous answer did not validate: {repair['validation_error']}")
        if repair.get("unknown_citations"):
            lines.append(f"These cited ids do not exist: {', '.join(repair['unknown_citations'])}")
        lines.append(f"Valid ids are exactly: {', '.join(repair['valid_ids'])}")
        lines.append("Answer again and cite only ids from that list.")
    return "\n".join(lines)


class TriageAgent:
    def __init__(self, conn, callers: dict[str, Caller], cap_usd: float = costs.DEFAULT_CAP_USD,
                 force_tier: str | None = None):
        self.conn = conn
        self.callers = callers
        self.ledger = costs.Ledger(cap_usd=cap_usd)
        self.force_tier = force_tier
        self.graph = self._build()

    # ── nodes ────────────────────────────────────────────────────────────────────────────────────
    def _gather(self, state: TriageState) -> TriageState:
        context = tools.get_case_context(self.conn, state["case_id"])
        return {"context": context, "notes": [], "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0}

    def _route(self, state: TriageState) -> TriageState:
        context = state["context"]
        decision = router.route(context["case"], context["account"], context.get("open_incidents", []),
                               force=self.force_tier)
        return {"routing": decision.model_dump()}

    def _retrieve(self, state: TriageState) -> TriageState:
        context = state["context"]
        query = f"{context['case']['subject']} {context['case']['body']} severity response target escalation"
        hits = tools.search_policy(query, k=6)
        return {"candidates": hits}

    def _classify(self, state: TriageState) -> TriageState:
        caller = self.callers[state["routing"]["tier"]]
        context, candidates = state["context"], state["candidates"]
        notes = list(state.get("notes", []))
        try:
            verdict, usage = caller.classify(context, candidates)
        except Exception as exc:  # noqa: BLE001 — malformed model output is a repairable failure
            verdict, usage = self._repair(caller, context, candidates, exc, notes, state)

        unknown = policy.unknown_ids(verdict.citations)
        if unknown:
            # One repair attempt, with the failure named and shown to the model. A second miss fails
            # the case rather than shipping an ungrounded commitment.
            verdict, usage2 = self._repair(caller, context, candidates,
                                           CitationError(f"cited ids do not exist: {unknown}"),
                                           notes, state)
            usage = _merge_usage(usage, usage2)
            still = policy.unknown_ids(verdict.citations)
            if still:
                raise CitationError(f"verdict cites policy ids that do not exist: {still}")

        # The SLA table in the corpus is the source of truth. A number that disagrees with it is
        # corrected and reported, never quietly accepted. An override also has to be earned: the
        # facts gathered for this case decide whether a cited override applies at all.
        body = f"{context['case'].get('subject', '')} {context['case'].get('body', '')}".lower()
        facts = {"open_incidents": bool(context.get("open_incidents")),
                 "case_linked_to_incident": verdict.next_action == "link_incident",
                 "security_report": any(t in body for t in router.SECURITY_TERMS),
                 "out_of_scope": not verdict.in_scope}
        expected = policy.expected_sla(context["account"]["plan"], str(verdict.severity),
                                       verdict.citations, facts=facts)
        if verdict.sla_response_hours != expected:
            notes.append(
                f"sla corrected: model said {verdict.sla_response_hours}, corpus says {expected}"
                f" for {context['account']['plan']}/{verdict.severity}")
            verdict = verdict.model_copy(update={"sla_response_hours": expected})

        cost = self._charge(state["case_id"], caller.tier, usage)
        return {"verdict": verdict.model_dump(), "notes": notes, **_usage_state(state, usage, cost)}

    def _repair(self, caller: Caller, context: dict, candidates: list[dict], exc: Exception,
                notes: list[str], state: TriageState) -> tuple[Classification, Usage]:
        """One bounded retry, told exactly what was wrong. Bounded because an unbounded repair loop is
        how a support agent spends a hundred dollars on one case."""
        valid = sorted(policy.index())
        detail = str(exc)
        repair = {"valid_ids": valid,
                  "unknown_citations": [i for i in policy.unknown_ids(
                      getattr(exc, "args", [""])[0].replace(",", " ").split()) if i.startswith("POL-")],
                  "validation_error": detail[:300]}
        notes.append(f"repair attempt: {type(exc).__name__}: {detail[:120]}")
        return caller.classify({**context, "repair": repair}, candidates)

    def _draft(self, state: TriageState) -> TriageState:
        caller = self.callers[state["routing"]["tier"]]
        verdict = Classification(**state["verdict"])
        text, usage = caller.draft(state["context"], verdict)
        cost = self._charge(state["case_id"], caller.tier, usage)
        return {"draft": text, **_usage_state(state, usage, cost)}

    def _propose(self, state: TriageState) -> TriageState:
        verdict = Classification(**state["verdict"])
        case_id = state["case_id"]
        proposals: list[Proposal] = []
        if verdict.next_action == "escalate":
            proposals.append(tools.propose(case_id, "escalate",
                                           {"team": "security" if "POL-SEC-1" in verdict.citations else "on-call"},
                                           verdict.rationale or "escalation required by policy"))
        if verdict.next_action == "link_incident":
            incidents = state["context"].get("open_incidents", [])
            if incidents:
                proposals.append(tools.propose(case_id, "link_incident",
                                               {"incident_id": incidents[0]["incident_id"]},
                                               "an open incident matches the reported symptom"))
        if verdict.next_action == "out_of_scope":
            proposals.append(tools.propose(case_id, "close_case", {"resolution": "out_of_scope"},
                                           "not entitled under POL-SCOPE-1"))
        declared = state["context"]["case"].get("declared_severity")
        if declared and declared != str(verdict.severity):
            proposals.append(tools.propose(case_id, "set_severity", {"from": declared,
                                                                    "to": str(verdict.severity)},
                                           "classification follows POL-SEV-1, not the declaration"))
        return {"record": {"proposals": [p.model_dump() for p in proposals]}}

    def _assemble(self, state: TriageState) -> TriageState:
        verdict = Classification(**state["verdict"])
        context = state["context"]
        record = TriageRecord(
            case_id=state["case_id"],
            account_id=context["account"]["account_id"],
            plan=context["account"]["plan"],
            severity=verdict.severity,
            severity_label=SEVERITY_LABEL[verdict.severity],
            in_scope=verdict.in_scope,
            sla_response_hours=verdict.sla_response_hours,
            next_action=verdict.next_action,
            citations=verdict.citations,
            account_facts=verdict.account_facts,
            draft_reply=state.get("draft"),
            proposals=[Proposal(**p) for p in state["record"]["proposals"]],
            routing=RoutingDecision(**state["routing"]),
            model_calls=state.get("calls", 0),
            input_tokens=state.get("input_tokens", 0),
            output_tokens=state.get("output_tokens", 0),
            cost_usd=round(state.get("cost_usd", 0.0), 6),
            notes=state.get("notes", []),
        )
        return {"record": record.model_dump()}

    # ── plumbing ─────────────────────────────────────────────────────────────────────────────────
    def _charge(self, case_id: str, tier: str, usage: Usage) -> float:
        """Charge the ledger and log it. The single place a call becomes money, so it cannot be
        double-counted by a node that also computes a price of its own."""
        cost = self.ledger.charge(tier, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        costs.log(self.conn, case_id, usage.get("model") or tier, tier, usage.get("input_tokens", 0),
                  usage.get("output_tokens", 0), cost)
        return cost

    def _build(self):
        g = StateGraph(TriageState)
        for name, fn in (("gather", self._gather), ("route", self._route), ("retrieve", self._retrieve),
                         ("classify", self._classify), ("draft", self._draft),
                         ("propose", self._propose), ("assemble", self._assemble)):
            g.add_node(name, fn)
        g.set_entry_point("gather")
        for a, b in (("gather", "route"), ("route", "retrieve"), ("retrieve", "classify"),
                     ("classify", "draft"), ("draft", "propose"), ("propose", "assemble")):
            g.add_edge(a, b)
        g.add_edge("assemble", END)
        return g.compile()

    def triage(self, case_id: str) -> TriageRecord:
        # A fresh ledger per case: the cap is a per-case budget, and a reused ledger would let one
        # case spend another case's headroom.
        self.ledger = costs.Ledger(cap_usd=self.ledger.cap_usd)
        final = self.graph.invoke({"case_id": case_id})
        record = TriageRecord(**final["record"])
        if abs(record.cost_usd - round(self.ledger.spent_usd, 6)) > 1e-9:
            raise AssertionError("logged cost and ledger disagree; the cost meter is lying")
        return record


def _merge_usage(a: Usage, b: Usage) -> Usage:
    return Usage(input_tokens=a.get("input_tokens", 0) + b.get("input_tokens", 0),
                 output_tokens=a.get("output_tokens", 0) + b.get("output_tokens", 0),
                 model=b.get("model", a.get("model", "")))


def _usage_state(state: TriageState, usage: Usage, cost: float) -> dict[str, Any]:
    return {"calls": state.get("calls", 0) + 1,
            "input_tokens": state.get("input_tokens", 0) + usage.get("input_tokens", 0),
            "output_tokens": state.get("output_tokens", 0) + usage.get("output_tokens", 0),
            "cost_usd": state.get("cost_usd", 0.0) + cost}


# ── the real model boundary ──────────────────────────────────────────────────────────────────────

# Verified against the provider catalogue on 2026-10-11. Override with TRIAGE_MODEL_CHEAP /
# TRIAGE_MODEL_STRONG; routing is by tier, never by name.
MODELS = {"cheap": os.environ.get("TRIAGE_MODEL_CHEAP", "google/gemini-2.5-flash-lite"),
          "strong": os.environ.get("TRIAGE_MODEL_STRONG", "openai/gpt-5")}


class OpenRouterCaller:
    """OpenRouter behind the same interface as the fake, so the tests exercise the real graph."""

    def __init__(self, tier: str, model: str | None = None):
        from langchain_openai import ChatOpenAI

        self.tier = tier
        self.model = model or MODELS[tier]
        self.llm = ChatOpenAI(model=self.model, base_url="https://openrouter.ai/api/v1",
                              api_key=os.environ["OPENROUTER_API_KEY"], temperature=0)

    def classify(self, context: dict, candidates: list[dict]) -> tuple[Classification, Usage]:
        structured = self.llm.with_structured_output(Classification, include_raw=True)
        out = structured.invoke([{"role": "user", "content": _prompt(context, candidates)}])
        usage = _usage_of(out, self.model)
        return out["parsed"], usage

    def draft(self, context: dict, verdict: Classification) -> tuple[str, Usage]:
        case, account = context["case"], context["account"]
        system = ("You are a support engineer writing a first response. State the classification you "
                  "have applied and the first-response target from policy. Do not promise resolution "
                  "times. Do not confirm or deny anything about security investigations.")
        user = (f"Case: {case['subject']}\n{case['body']}\n\nAccount: {account['name']}, plan "
                f"{account['plan']}\nApplied classification: {verdict.severity} "
                f"({SEVERITY_LABEL[verdict.severity]}), first-response target "
                f"{verdict.sla_response_hours}h\nNext action: {verdict.next_action}")
        msg = self.llm.invoke([{"role": "system", "content": system}, {"role": "user", "content": user}])
        usage: Usage = {"model": self.model}
        meta = getattr(msg, "usage_metadata", None) or {}
        usage["input_tokens"] = int(meta.get("input_tokens", 0))
        usage["output_tokens"] = int(meta.get("output_tokens", 0))
        return str(msg.content), usage


def _usage_of(out: dict, model: str) -> Usage:
    raw = out.get("raw")
    meta = getattr(raw, "usage_metadata", None) or {}
    return {"input_tokens": int(meta.get("input_tokens", 0)),
            "output_tokens": int(meta.get("output_tokens", 0)), "model": model}


def build_agent(conn, cap_usd: float = costs.DEFAULT_CAP_USD, force_tier: str | None = None) -> TriageAgent:
    return TriageAgent(conn, callers={t: OpenRouterCaller(t) for t in ("cheap", "strong")},
                       cap_usd=cap_usd, force_tier=force_tier)
