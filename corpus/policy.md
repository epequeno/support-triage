# Meridian Cloud — support policy

Every line below is citable by its id. An answer that cites an id which does not exist here is not
grounded, and the triage fails rather than shipping it.

## POL-PLAN-1 — Support plans and entitlements

- Basic: billing and account questions only. **No technical support cases.**
- Developer: technical support during business hours, for non-production workloads.
- Business: technical support with 24x7 case intake, for production workloads.
- Enterprise: technical support with 24x7 intake, for production and mission-critical workloads, with a named technical account manager.

## POL-SEV-1 — Severity definitions

- Sev1 (Production System Down): a production workload is fully unavailable, with no workaround, and business operations have stopped.
- Sev2 (Production System Impaired): a production workload is significantly degraded, or a workaround exists but is not sustainable.
- Sev3 (System Impaired): a non-production workload is impaired, or a production issue has a straightforward workaround.
- Sev4 (General Guidance): a question, a how-to, or a feature request. Nothing is broken.

## POL-SEV-2 — Declared severity is not binding

The severity a customer declares in a case is an assertion, not a fact. Classification follows
POL-SEV-1 and the account's environment. A case may be raised or lowered accordingly, and the
response must state the classification that applies rather than the one that was requested.

## POL-SLA-1 — First-response targets, in hours

| plan | Sev1 | Sev2 | Sev3 | Sev4 |
|---|---|---|---|---|
| Basic | none | none | none | 72 |
| Developer | 12 | 24 | 48 | 120 |
| Business | 1 | 4 | 12 | 24 |
| Enterprise | 0.25 | 1 | 4 | 12 |

These are first-response targets, not resolution targets. A commitment to a customer may only quote a
target from this table for the account's plan and the classified severity.

## POL-SEC-1 — Security reports take the security path

Suspected credential exposure, unauthorised access, data loss or abuse must be routed to the Security
Response team within 15 minutes, on every plan including Basic. The target in POL-SLA-1 does not
apply. A security report is classified **Sev2** while the security path is active, on every plan; it
is raised to Sev1 only if a production workload is also fully unavailable under POL-SEV-1. The
customer-facing response may acknowledge receipt and state that the security team is
handling it; it must not discuss investigation detail, confirm or deny a breach, or promise a
timeframe.

## POL-INC-1 — Open incidents supersede the case

If an open incident in the account's region matches the reported symptom, the case is linked to that
incident and no separate clock starts. The response target is acknowledgement within 1 hour.

## POL-ESC-1 — Escalation

A Sev1 on a Business or Enterprise account must be escalated to the on-call engineer; on Enterprise
the named technical account manager is also notified. Escalation is a state change and requires
approval.

## POL-SCOPE-1 — Out of scope

A technical support request from a Basic account is out of scope and is closed with a pointer to the
plan comparison. The same applies to feature requests, which are Sev4 and are not commitments.
