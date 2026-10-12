"""The tool surface.

Read tools are ordinary functions. **Write tools cannot execute anything**: they return a `Proposal`,
and the only code path that changes state is `execute()`, which requires a named human approver. That
is the invariant the tests assert and the eval counts violations of.

A support org will not let a model close cases or escalate on its own, and pretending otherwise would
be the least realistic thing in this repository.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from . import backend, policy
from .models import Proposal

WRITE_ACTIONS = {"set_severity", "escalate", "close_case", "link_incident"}


@dataclass(frozen=True)
class ToolCall:
    """What the agent did, recorded so the UI and the eval can show it."""

    name: str
    args: dict
    result_summary: str


# ── reads ────────────────────────────────────────────────────────────────────────────────────────

def get_account(conn, account_id: str) -> dict:
    acct = backend.account(conn, account_id) or {}
    ent = backend.entitlement(conn, account_id) or {}
    merged = {**acct, "entitlement": ent}
    return merged


def get_case_context(conn, case_id: str) -> dict:
    case = backend.get_case(conn, case_id)
    if case is None:
        raise KeyError(f"unknown case {case_id}")
    acct = get_account(conn, case["account_id"])
    region = acct.get("region")
    return {
        "case": case,
        "account": acct,
        "open_incidents": backend.open_incidents(conn, region),
        "all_open_incidents": backend.open_incidents(conn),
        "service_status": backend.service_status(conn, region),
        "prior_cases": backend.prior_cases(conn, case["account_id"], case_id),
    }


def search_policy(query: str, k: int = 6) -> list[dict]:
    """The one tool the model is allowed to call for itself (M0)."""
    return [{"policy_id": line.policy_id, "section": line.section, "text": line.text}
            for line in policy.retrieve(query, k=k)]


# ── writes: proposed here, executed only by a human ──────────────────────────────────────────────

def propose(case_id: str, action: str, payload: dict, rationale: str) -> Proposal:
    if action not in WRITE_ACTIONS:
        raise ValueError(f"{action!r} is not a state-changing action")
    return Proposal(case_id=case_id, action=action, payload=payload, rationale=rationale)


def execute(conn, proposal: Proposal, approved_by: str) -> Proposal:
    """The only path from a proposal to a state change, and it takes a name."""
    if not approved_by or not approved_by.strip():
        raise PermissionError("a state change requires a named approver; the agent cannot approve itself")
    if proposal.action not in WRITE_ACTIONS:
        raise ValueError(f"{proposal.action!r} is not a state-changing action")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    backend.record_approval(conn, proposal.case_id, proposal.action, json.dumps(proposal.payload),
                            proposal.rationale, approved_by.strip(), stamp)
    return proposal.model_copy(update={"approved_by": approved_by.strip(), "executed_at": stamp})
