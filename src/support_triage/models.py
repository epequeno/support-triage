"""The vocabulary of the domain, typed.

Severity, plans and SLA targets are the fictional provider's, not any real vendor's — but they are
shaped like the real thing, because the point of the exercise is a realistic business problem.
"""

from __future__ import annotations

import re

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class NextAction(StrEnum):
    RESPOND = "respond"
    ESCALATE = "escalate"
    LINK_INCIDENT = "link_incident"
    REQUEST_INFO = "request_info"
    OUT_OF_SCOPE = "out_of_scope"


class Severity(StrEnum):
    SEV1 = "Sev1"
    SEV2 = "Sev2"
    SEV3 = "Sev3"
    SEV4 = "Sev4"


SEVERITY_LABEL = {
    Severity.SEV1: "Production System Down",
    Severity.SEV2: "Production System Impaired",
    Severity.SEV3: "System Impaired",
    Severity.SEV4: "General Guidance",
}


class Plan(StrEnum):
    BASIC = "Basic"
    DEVELOPER = "Developer"
    BUSINESS = "Business"
    ENTERPRISE = "Enterprise"


class Account(BaseModel):
    account_id: str
    name: str
    plan: Plan
    region: str
    tam: str | None = None
    production_critical: bool = False
    notes: str = ""


class Entitlement(BaseModel):
    account_id: str
    technical_support: bool
    intake: str          # "business-hours" or "24x7"
    workloads: str       # "non-production", "production", "mission-critical"
    named_tam: bool = False


class Incident(BaseModel):
    incident_id: str
    service: str
    region: str
    summary: str
    status: str          # "investigating", "identified", "monitoring", "resolved"
    started: str


class CaseRow(BaseModel):
    case_id: str
    account_id: str
    subject: str
    body: str
    declared_severity: str | None = None   # what the CUSTOMER says. Never binding.
    created: str
    opened_by: str = "customer"


class PolicyLine(BaseModel):
    """A citable line of policy. Citations resolve to these ids, or they are not citations."""

    policy_id: str
    section: str
    text: str


class RoutingDecision(BaseModel):
    tier: str                    # "cheap" or "strong"
    reasons: list[str] = Field(default_factory=list)


class Classification(BaseModel):
    """The model's verdict. Every field is checked against the corpus or the seed before it ships."""

    severity: Severity
    in_scope: bool
    sla_response_hours: float | None
    next_action: NextAction
    citations: list[str]
    account_facts: list[str] = Field(default_factory=list)
    rationale: str = ""

    @field_validator("sla_response_hours", mode="before")
    @classmethod
    def _parse_target(cls, v):
        """Models write '1 hour', '15 minutes', '72h' and occasionally a number. A response target is
        a number of hours or nothing at all; parse the shapes the interface actually receives rather
        than rejecting the run over a unit."""
        if v is None or isinstance(v, (int, float)):
            return v
        text = str(v).strip().lower()
        if text in {"none", "n/a", "no sla", "not applicable", ""}:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)", text)
        if not m:
            return None
        value = float(m.group(1))
        if "min" in text:
            return value / 60
        if "day" in text:
            return value * 24
        return value


    @field_validator("citations", mode="before")
    @classmethod
    def _normalise_citation_ids(cls, v):
        """Models wrap ids in brackets or quotes ('[POL-SEV-1]', '\"POL-SLA-1\"') often enough that
        the repair loop exists to absorb it. Normalise instead: an id is 'POL-' plus a bare token,
        and the citation gate does the real validation."""
        if not isinstance(v, list):
            return v
        return [re.sub(r"[^A-Z0-9-]", "", str(c).upper()) if str(c).upper().startswith(("[", '"', "POL"))
                else c for c in v]

class Proposal(BaseModel):
    """A state change the agent wants to make. It cannot make it. A human does, explicitly."""

    case_id: str
    action: str                  # set_severity | escalate | close_case | link_incident
    payload: dict
    rationale: str
    approved_by: str | None = None
    executed_at: str | None = None


class TriageRecord(BaseModel):
    case_id: str
    account_id: str
    plan: Plan
    severity: Severity
    severity_label: str
    in_scope: bool
    sla_response_hours: float | None
    next_action: str             # respond | escalate | link_incident | request_info | out_of_scope
    citations: list[str]         # policy ids, each of which must exist
    account_facts: list[str]     # human-readable grounding
    draft_reply: str | None
    proposals: list[Proposal]
    routing: RoutingDecision
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    notes: list[str] = Field(default_factory=list)
