"""The tests that make the interesting claims real.

Offline and deterministic: the model boundary is a fake, so these run without a key and without
spending anything, and a failure here is a failure of the pipeline rather than of a model's mood.
"""

from __future__ import annotations

import pytest

from support_triage import backend, costs, policy, router, seed, tools
from support_triage.agent import CitationError, TriageAgent
from support_triage.models import Classification, Severity


@pytest.fixture()
def conn(tmp_path):
    c = backend.reset(tmp_path / "t.db")
    seed.seed(c)
    yield c
    c.close()


class FakeCaller:
    """A model that says what the test needs it to say, and reports plausible usage."""

    def __init__(self, tier: str, verdict: Classification, draft: str = "We have classified this case.",
                 then: Classification | None = None):
        self.tier = tier
        self.verdict = verdict
        self.draft_text = draft
        self.then = then
        self.calls = 0

    def classify(self, context, candidates):
        self.calls += 1
        verdict = self.then if (self.calls > 1 and self.then is not None) else self.verdict
        return verdict, {"input_tokens": 800, "output_tokens": 60, "model": f"fake-{self.tier}"}

    def draft(self, context, verdict):
        return self.draft_text, {"input_tokens": 300, "output_tokens": 80, "model": f"fake-{self.tier}"}


def make_agent(tier: str, verdict: Classification, **kwargs) -> TriageAgent:
    callers = {t: FakeCaller(t, verdict, **kwargs) for t in ("cheap", "strong")}
    return TriageAgent(conn=None, callers=callers)  # type: ignore[arg-type]


# ── the corpus is the source of truth for response targets ───────────────────────────────────────

def test_the_sla_matrix_reproduces_every_seeded_target(conn):
    """Every held-out target is derivable from the corpus. If this fails, the corpus and the seeded
    truth have drifted apart and the eval would be measuring the drift."""
    mismatches = []
    for case in backend.all_cases(conn):
        plan = backend.account(conn, case["account_id"])["plan"]
        truth = backend.truth(conn, case["case_id"])
        citations = __import__("json").loads(truth["citations"])
        got = policy.expected_sla(plan, truth["severity"], citations)
        if got != truth["sla_response_hours"]:
            mismatches.append((case["case_id"], plan, truth["severity"], got, truth["sla_response_hours"]))
    assert not mismatches, f"corpus disagrees with seeded truth: {mismatches}"


def test_the_matrix_has_all_four_plans_and_basic_is_unentitled():
    matrix = policy.sla_matrix()
    assert set(matrix) == {"Basic", "Developer", "Business", "Enterprise"}
    assert matrix["Basic"]["Sev1"] is None
    assert matrix["Enterprise"]["Sev1"] == 0.25


# ── routing ──────────────────────────────────────────────────────────────────────────────────────

def test_security_language_routes_to_the_strong_model():
    decision = router.route({"subject": "key leaked", "body": "an access key was committed publicly"},
                            {"plan": "Business", "production_critical": True}, [])
    assert decision.tier == "strong"
    assert any("security" in r for r in decision.reasons)


def test_a_routine_question_stays_cheap():
    decision = router.route({"subject": "tagging convention", "body": "what is the recommended tag scheme?"},
                            {"plan": "Business", "production_critical": False}, [])
    assert decision.tier == "cheap"
    assert decision.reasons == ["no escalation signal: routine classification"]


def test_routing_can_be_forced_for_an_experiment():
    assert router.route({}, {}, [], force="strong").tier == "strong"


# ── the graph end to end, offline ────────────────────────────────────────────────────────────────

def test_a_production_outage_is_a_sev1_with_an_escalation_proposal(conn):
    verdict = Classification(severity=Severity.SEV1, in_scope=True, sla_response_hours=1.0,
                             next_action="escalate",
                             citations=["POL-SEV-1", "POL-SLA-1", "POL-ESC-1"],
                             account_facts=["Business plan, production workload"])
    agent = make_agent("strong", verdict)
    agent.conn = conn
    record = agent.triage("CASE-1041")
    assert record.severity is Severity.SEV1
    assert record.sla_response_hours == 1.0
    assert [p.action for p in record.proposals] == ["escalate"]
    assert record.proposals[0].payload["team"] == "on-call"
    assert record.cost_usd > 0 and record.model_calls == 2


def test_a_wrong_sla_is_corrected_by_the_corpus_and_reported(conn):
    verdict = Classification(severity=Severity.SEV1, in_scope=True, sla_response_hours=8.0,
                             next_action="respond", citations=["POL-SEV-1", "POL-SLA-1"])
    agent = make_agent("strong", verdict)
    agent.conn = conn
    record = agent.triage("CASE-1041")
    assert record.sla_response_hours == 1.0, "Business Sev1 is 1h; the model said 8h"
    assert any("sla corrected" in n for n in record.notes)


def test_an_override_cited_without_its_precondition_falls_back_to_the_matrix():
    """CASE-1050 happened: a model cited the incident override on a question without linking the
    case to it, and the gate 'corrected' a right 12h into a wrong 1h. Overrides are earned, not
    cited: the policy requires symptom match, and the verdict's own action is the record of it."""
    incident = {"incident_id": "INC-7001"}  # open in the region, but the case is not linked to it
    unlinked = policy.expected_sla("Enterprise", "Sev4", ["POL-INC-1"],
                                   facts={"open_incidents": [incident], "case_linked_to_incident": False})
    linked = policy.expected_sla("Enterprise", "Sev4", ["POL-INC-1"],
                                 facts={"open_incidents": [incident], "case_linked_to_incident": True})
    assert unlinked == 12.0, "Enterprise Sev4 is 12h; POL-INC-1 without a linked case must not shorten it"
    assert linked == 1.0, "a case actually linked to an open incident gets the 1h acknowledgement"


def test_an_unearned_incident_citation_is_corrected_end_to_end(conn):
    verdict = Classification(severity=Severity.SEV4, in_scope=True, sla_response_hours=1.0,
                             next_action="respond", citations=["POL-SEV-1", "POL-INC-1"])
    agent = make_agent("strong", verdict)
    agent.conn = conn
    record = agent.triage("CASE-1050")  # Enterprise Sev4 question; INC-7001 is open but not linked
    assert record.sla_response_hours == 12.0
    assert any("sla corrected" in n for n in record.notes)


def test_a_declared_severity_differing_from_the_classification_proposes_a_change(conn):
    verdict = Classification(severity=Severity.SEV3, in_scope=True, sla_response_hours=48.0,
                             next_action="respond", citations=["POL-SEV-1", "POL-SEV-2", "POL-SLA-1"])
    agent = make_agent("strong", verdict)
    agent.conn = conn
    record = agent.triage("CASE-1047")  # declared Sev1, non-production
    assert record.severity is Severity.SEV3
    assert "set_severity" in [p.action for p in record.proposals]


def test_a_security_case_escalates_to_the_security_team_with_the_15_minute_clock(conn):
    verdict = Classification(severity=Severity.SEV2, in_scope=True, sla_response_hours=0.25,
                             next_action="escalate", citations=["POL-SEC-1"],
                             account_facts=["Enterprise account"])
    agent = make_agent("strong", verdict)
    agent.conn = conn
    record = agent.triage("CASE-1045")
    assert record.sla_response_hours == 0.25
    assert record.proposals[0].payload["team"] == "security"


def test_an_out_of_scope_case_proposes_a_close_and_promises_nothing(conn):
    verdict = Classification(severity=Severity.SEV4, in_scope=False, sla_response_hours=None,
                             next_action="out_of_scope", citations=["POL-SCOPE-1", "POL-PLAN-1"])
    agent = make_agent("cheap", verdict)
    agent.conn = conn
    record = agent.triage("CASE-1042")
    assert record.in_scope is False
    assert record.sla_response_hours is None
    assert "close_case" in [p.action for p in record.proposals]


def test_a_citation_that_does_not_exist_fails_the_case(conn):
    """The one rule that makes the citations worth anything: ungrounded means unshipped."""
    verdict = Classification(severity=Severity.SEV1, in_scope=True, sla_response_hours=1.0,
                             next_action="respond", citations=["POL-SEV-1", "POL-MADE-UP"])
    agent = make_agent("strong", verdict)
    agent.conn = conn
    with pytest.raises(CitationError, match="POL-MADE-UP"):
        agent.triage("CASE-1041")



def test_bracketed_citation_ids_are_normalised_not_repaired(conn):
    """The strong model intermittently wraps ids in brackets or quotes. Normalising at the interface
    is free; a repair round-trip is a second model call."""
    verdict = Classification(severity=Severity.SEV4, in_scope=True, sla_response_hours=12.0,
                             next_action="respond", citations=["[POL-SEV-1]", '"POL-SLA-1"'])
    assert verdict.citations == ["POL-SEV-1", "POL-SLA-1"]
    agent = make_agent("strong", verdict)
    agent.conn = conn
    record = agent.triage("CASE-1050")
    assert record.sla_response_hours == 12.0
    assert not any("repair" in n for n in record.notes)

def test_the_repair_path_recovers_once_and_records_that_it_did(conn):
    bad = Classification(severity=Severity.SEV1, in_scope=True, sla_response_hours=1.0,
                         next_action="respond", citations=["POL-NOPE-1"])
    good = Classification(severity=Severity.SEV1, in_scope=True, sla_response_hours=1.0,
                          next_action="respond", citations=["POL-SEV-1", "POL-SLA-1"])
    agent = make_agent("strong", bad, then=good)
    agent.conn = conn
    record = agent.triage("CASE-1041")
    assert record.citations == ["POL-SEV-1", "POL-SLA-1"]
    assert any("repair attempt" in n for n in record.notes)


# ── the gate, and the meter ──────────────────────────────────────────────────────────────────────

def test_state_changes_require_a_named_human(conn):
    proposal = tools.propose("CASE-1041", "escalate", {"team": "on-call"}, "policy says so")
    with pytest.raises(PermissionError):
        tools.execute(conn, proposal, "")
    tools.execute(conn, proposal, "steven")
    rows = backend.approvals(conn, "CASE-1041")
    assert len(rows) == 1 and rows[0]["approved_by"] == "steven"


def test_proposing_a_non_state_change_is_rejected():
    with pytest.raises(ValueError):
        tools.propose("CASE-1041", "respond", {}, "not a state change")


def test_the_cost_cap_stops_a_run_rather_than_completing_it():
    ledger = costs.Ledger(cap_usd=0.0001)
    with pytest.raises(costs.CostCapExceeded):
        ledger.charge("strong", 5_000, 5_000)


def test_the_record_cost_reconciles_with_the_ledger(conn):
    """The meter is either right or loud. A silent disagreement would make 'routing saves money'
    an unfalsifiable claim."""
    verdict = Classification(severity=Severity.SEV1, in_scope=True, sla_response_hours=1.0,
                             next_action="respond", citations=["POL-SEV-1", "POL-SLA-1"])
    agent = make_agent("strong", verdict)
    agent.conn = conn
    record = agent.triage("CASE-1041")
    assert record.cost_usd == pytest.approx(agent.ledger.spent_usd)
