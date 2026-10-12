"""Seed the mock support organisation.

Ten cases with known-correct triage, written to include the traps that matter: a customer who
under-declares severity, one who over-declares, a Basic account filing a technical case, an incident
that supersedes the case clock, and a credential exposure that must take the security path.
"""

from __future__ import annotations

import json
import sys

from . import backend

ACCOUNTS = [
    ("ACC-1001", "Northwind Freight", "Business", "us-east-1", None, 1, "order processing is revenue-critical"),
    ("ACC-1002", "Kestrel Health", "Enterprise", "us-east-2", "Dana Whitfield", 1, "patient-facing, mission-critical"),
    ("ACC-1003", "Willow & Sons LLC", "Basic", "us-west-2", None, 0, "small business, no technical support"),
    ("ACC-1004", "Tidewater Analytics", "Developer", "eu-west-1", None, 0, "non-production workloads only"),
    ("ACC-1005", "Ridgeway Retail", "Business", "us-east-1", None, 1, "storefront is production"),
    ("ACC-1006", "Halcyon Media", "Enterprise", "us-east-2", "Priya Raman", 1, "streaming, mission-critical"),
    ("ACC-1007", "Bluewater Logistics", "Developer", "us-west-2", None, 0, "staging only"),
    ("ACC-1008", "Sable Interactive", "Basic", "eu-west-1", None, 0, "billing questions only"),
]

ENTITLEMENTS = {
    "Basic": (0, "business-hours", "none", 0),
    "Developer": (1, "business-hours", "non-production", 0),
    "Business": (1, "24x7", "production", 0),
    "Enterprise": (1, "24x7", "mission-critical", 1),
}

INCIDENTS = [
    ("INC-7001", "compute", "us-east-2", "Elevated API error rates on managed compute", "identified", "2026-10-11T08:40:00Z"),
    ("INC-7002", "storage", "us-east-1", "Increased latency on object reads", "investigating", "2026-10-11T07:15:00Z"),
    ("INC-7003", "networking", "eu-west-1", "Intermittent packet loss to edge endpoints", "monitoring", "2026-10-11T06:05:00Z"),
    ("INC-7004", "compute", "us-west-2", "Instance launch failures", "resolved", "2026-10-10T22:10:00Z"),
]

SERVICE_STATUS = [
    ("compute", "us-east-1", "operational"), ("compute", "us-east-2", "degraded"),
    ("compute", "us-west-2", "operational"), ("compute", "eu-west-1", "operational"),
    ("storage", "us-east-1", "degraded"), ("storage", "us-east-2", "operational"),
    ("storage", "us-west-2", "operational"), ("storage", "eu-west-1", "operational"),
    ("networking", "us-east-1", "operational"), ("networking", "us-east-2", "operational"),
    ("networking", "us-west-2", "operational"), ("networking", "eu-west-1", "degraded"),
]

CASES = [
    ("CASE-1041", "ACC-1001",
     "Production order processing is down - no orders since 09:10",
     "Our production order-processing service has been unavailable since 09:10 this morning. "
     "No orders are being accepted and there is no workaround. This is stopping the business.",
     "Sev1", "2026-10-11T09:22:00Z"),

    ("CASE-1042", "ACC-1003",
     "How do I resize an instance? Site also feels slow",
     "We want to increase the size of our instance. Also our website has been slow for a few days.",
     None, "2026-10-11T09:40:00Z"),

    ("CASE-1043", "ACC-1002",
     "Intermittent 500s from our production cluster",
     "Our production cluster in us-east-2 is returning intermittent 500s. Roughly one request in five "
     "fails; the rest succeed. Affecting patient portal logins.",
     "Sev2", "2026-10-11T09:05:00Z"),

    ("CASE-1044", "ACC-1004",
     "Dev environment cannot reach object storage endpoint",
     "Our development environment in eu-west-1 cannot reach the storage endpoint. Connections time out "
     "intermittently. Nothing production is affected.",
     None, "2026-10-11T07:30:00Z"),

    ("CASE-1045", "ACC-1006",
     "Possible credential exposure - access key committed to a public repo",
     "One of our engineers pushed a repo publicly that appears to contain a long-lived access key. "
     "We have rotated it, but we need to know if it was used. This is urgent.",
     "Sev1", "2026-10-11T10:02:00Z"),

    ("CASE-1046", "ACC-1005",
     "Feature request: scheduled scaling for managed compute",
     "We would like the ability to schedule scaling windows in advance rather than reacting to load. "
     "Is this on the roadmap?",
     None, "2026-10-11T10:15:00Z"),

    ("CASE-1047", "ACC-1007",
     "Staging database completely down - cannot test release",
     "Our staging database is fully unavailable and we cannot run our release tests. There is no "
     "workaround. We consider this critical because it blocks the sprint.",
     "Sev1", "2026-10-11T08:55:00Z"),

    ("CASE-1048", "ACC-1008",
     "Someone may have accessed our billing account",
     "We think an unauthorised person accessed our account console. We are on the Basic plan - can "
     "you investigate?",
     None, "2026-10-11T10:30:00Z"),

    ("CASE-1049", "ACC-1001",
     "Order processing unavailable again",
     "Order processing is fully down, customers cannot check out, no workaround available. Marking "
     "this as Sev2 since the API health check still returns 200.",
     "Sev2", "2026-10-11T11:05:00Z"),

    ("CASE-1050", "ACC-1002",
     "General question about resource tagging conventions",
     "Is there a recommended tagging convention for cost allocation across projects? Not blocking "
     "anything, just planning.",
     None, "2026-10-11T11:20:00Z"),
]

# Held out from the agent path: ground truth for the eval, never an input to a run.
TRUTH = [
    ("CASE-1041", "Sev1", 1, 1.0, "escalate", ["POL-SEV-1", "POL-SLA-1", "POL-ESC-1"],
     "Production down, no workaround, Business plan: Sev1, 1h first response, escalation mandatory."),
    ("CASE-1042", "Sev4", 0, None, "out_of_scope", ["POL-SCOPE-1", "POL-PLAN-1"],
     "Technical support request from a Basic account. Not entitled, whatever the severity."),
    ("CASE-1043", "Sev2", 1, 1.0, "link_incident", ["POL-INC-1", "POL-SEV-1"],
     "Matches open incident INC-7001 in us-east-2; the 1-hour acknowledgement supersedes the matrix."),
    ("CASE-1044", "Sev3", 1, 1.0, "link_incident", ["POL-INC-1", "POL-SEV-1"],
     "Non-production and intermittent, matching INC-7003: Sev3, and the incident acknowledgement applies."),
    ("CASE-1045", "Sev2", 1, 0.25, "escalate", ["POL-SEC-1"],
     "Credential exposure takes the security path: 15 minutes, every plan, no detail in the reply."),
    ("CASE-1046", "Sev4", 1, 24.0, "respond", ["POL-SEV-1", "POL-SLA-1"],
     "Feature request on a Business plan, nothing broken: Sev4 with a 24h first response."),
    ("CASE-1047", "Sev3", 1, 48.0, "respond", ["POL-SEV-1", "POL-SEV-2", "POL-SLA-1"],
     "Declared Sev1, but the workload is non-production: severity follows the environment, not the "
     "assertion. Developer Sev3 is 48h."),
    ("CASE-1048", "Sev2", 1, 0.25, "escalate", ["POL-SEC-1"],
     "An unauthorised-access claim on a Basic account still takes the security path: 15 minutes, and "
     "the plan does not restrict it."),
    ("CASE-1049", "Sev1", 1, 1.0, "escalate", ["POL-SEV-1", "POL-SEV-2", "POL-ESC-1"],
     "Declared Sev2, facts say production down with no workaround: raised to Sev1, escalation required."),
    ("CASE-1050", "Sev4", 1, 12.0, "respond", ["POL-SEV-1", "POL-SLA-1"],
     "Enterprise Sev4 first response is 12h. A question is Sev4 even for a mission-critical account."),
]


def seed(conn) -> None:
    conn.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?)", ACCOUNTS)
    conn.executemany(
        "INSERT INTO entitlements VALUES (?,?,?,?,?)",
        [(a[0], *ENTITLEMENTS[a[2]]) for a in ACCOUNTS])
    conn.executemany("INSERT INTO incidents VALUES (?,?,?,?,?,?)", INCIDENTS)
    conn.executemany("INSERT INTO service_status VALUES (?,?,?)", SERVICE_STATUS)
    conn.executemany("INSERT INTO cases VALUES (?,?,?,?,?,?,?)",
                     [(c[0], c[1], c[2], c[3], c[4], c[5], "customer") for c in CASES])
    conn.executemany("INSERT INTO case_truth VALUES (?,?,?,?,?,?,?)",
                     [(t[0], t[1], t[2], t[3], t[4], json.dumps(t[5]), t[6]) for t in TRUTH])
    conn.commit()


def main(argv: list[str]) -> int:
    conn = backend.reset()
    seed(conn)
    print(f"seeded {len(ACCOUNTS)} accounts · {len(INCIDENTS)} incidents · {len(CASES)} cases"
          f" · {len(TRUTH)} with held-out truth")
    print(f"db: {backend.DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
