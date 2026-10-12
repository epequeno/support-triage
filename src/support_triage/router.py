"""Which cases deserve the expensive model, and why.

Cheap by default, escalate on signal. The point is not to save pennies on ten cases — it is that a
support org triaging thousands of cases a day cannot send every one to a frontier model, so routing is
part of the product rather than an optimisation bolted on afterwards.

Every route carries its reasons, because "why did this one cost 40x more" is the question an
operations lead actually asks.
"""

from __future__ import annotations

from .models import RoutingDecision

SECURITY_TERMS = {"credential", "access key", "unauthorised", "unauthorized", "breach", "leaked",
                  "leak", "compromised", "abuse", "exfiltration", "ransomware", "phishing"}
AMBIGUITY_TERMS = {"intermittent", "intermittently", "sometimes", "occasionally", "sporadic",
                   "flaky", "not sure", "unsure", "maybe", "roughly", "appears"}

LONG_BODY_CHARS = 600


def route(case: dict, account: dict, incidents: list[dict], force: str | None = None) -> RoutingDecision:
    if force in {"cheap", "strong"}:
        return RoutingDecision(tier=force, reasons=[f"forced by configuration: {force}"])

    body = f"{case.get('subject', '')} {case.get('body', '')}".lower()
    reasons: list[str] = []

    if any(t in body for t in SECURITY_TERMS):
        reasons.append("security language: the security path has its own rules and its own clock")
    if any(t in body for t in AMBIGUITY_TERMS):
        reasons.append("hedged or uncertain symptom description")
    if case.get("declared_severity") and account.get("production_critical"):
        reasons.append("a declared severity on a production-critical account must be checked, not trusted")
    if incidents:
        reasons.append("an open incident may supersede the case clock")
    if len(case.get("body", "")) > LONG_BODY_CHARS:
        reasons.append("long case body")
    if account.get("plan") == "Enterprise":
        reasons.append("Enterprise account")
    if account.get("production_critical") and any(
            t in body for t in ("production", "prod ", "customers", "revenue", "outage", "down")):
        reasons.append("production impact language on a production-critical account")

    tier = "strong" if reasons else "cheap"
    if not reasons:
        reasons = ["no escalation signal: routine classification"]
    return RoutingDecision(tier=tier, reasons=reasons)
