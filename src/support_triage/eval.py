"""Run every seeded case and score the triage against held-out truth.

The point of this file is to make "how do you know it works?" answerable in one screen, and to make
the *cost* of being right visible next to the accuracy. Both numbers travel together, because routing
that halves the cost by making worse calls is not an improvement.

Scoring is objective wherever the truth allows it: severity, entitlement, response target, next action
and citation validity. The draft reply is not scored here — a model judging prose is a judge, not
ground truth, and it would flatter the table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import backend, costs
from .agent import build_agent

RESULTS = Path(__file__).resolve().parents[2] / "results"

AXES = ("severity", "in_scope", "sla", "action", "citations")


def score_case(record, truth) -> dict:
    cited = set(record.citations)
    expected = set(json.loads(truth["citations"]))
    return {
        "severity": str(record.severity) == truth["severity"],
        "in_scope": bool(record.in_scope) == bool(truth["in_scope"]),
        "sla": (record.sla_response_hours == truth["sla_response_hours"]),
        "action": record.next_action == truth["next_action"],
        # The verdict must include every policy line the correct answer rests on. Extra, valid
        # citations are not penalised: a case can legitimately rest on more than one rule.
        "citations": expected <= cited,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval", description="score triage against held-out truth")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tier", choices=["cheap", "strong"], default=None, help="force one tier")
    ap.add_argument("--out", default=str(RESULTS / "eval.json"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    conn = backend.connect()
    cases = backend.all_cases(conn)
    if args.limit:
        cases = cases[: args.limit]

    rows, totals = [], {axis: 0 for axis in AXES}
    spend = 0.0
    tiers: dict[str, int] = {}
    for case in cases:
        truth = backend.truth(conn, case["case_id"])
        agent = build_agent(conn, force_tier=args.tier)
        try:
            record = agent.triage(case["case_id"])
        except Exception as exc:  # noqa: BLE001 — a refused or failed case is a result, not a crash
            rows.append({"case": case["case_id"], "error": f"{type(exc).__name__}: {exc}",
                         "score": {axis: False for axis in AXES}, "cost_usd": 0.0,
                         "tier": args.tier or "?", "severity": "-", "expected": truth["severity"]})
            continue
        score = score_case(record, truth)
        for axis, ok in score.items():
            totals[axis] += int(ok)
        spend += record.cost_usd
        tiers[record.routing.tier] = tiers.get(record.routing.tier, 0) + 1
        rows.append({
            "case": case["case_id"], "account": record.account_id, "plan": str(record.plan),
            "severity": str(record.severity), "expected": truth["severity"],
            "sla": record.sla_response_hours, "expected_sla": truth["sla_response_hours"],
            "action": record.next_action, "expected_action": truth["next_action"],
            "tier": record.routing.tier, "cost_usd": record.cost_usd, "score": score,
            "notes": record.notes, "proposals": len(record.proposals),
        })

    n = len(rows)
    payload = {"cases": n, "tier": args.tier or "routed", "totals": totals,
               "accuracy": {a: (totals[a] / n if n else 0) for a in AXES},
               "cost_usd": round(spend, 6), "tiers": tiers, "rows": rows}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        print(f"{'case':<11}{'plan':<11}{'sev':<6}{'want':<6}{'sla':<7}{'want':<7}{'action':<15}"
              f"{'want':<15}{'tier':<8}{'cost':>9}  score")
        for r in rows:
            if "error" in r:
                print(f"{r['case']:<11}{'ERROR':<11}{r['error'][:60]}")
                continue
            marks = "".join("." if r["score"][a] else "X" for a in AXES)
            print(f"{r['case']:<11}{r['plan']:<11}{r['severity']:<6}{r['expected']:<6}"
                  f"{str(r['sla']):<7}{str(r['expected_sla']):<7}{r['action']:<15}"
                  f"{r['expected_action']:<15}{r['tier']:<8}${r['cost_usd']:>8.5f}  {marks}")
        print(f"\nscore key: {' '.join(AXES)}   (. = correct, X = missed)")
        print(f"accuracy: " + "  ".join(f"{a} {totals[a]}/{n}" for a in AXES))
        print(f"cost: ${spend:.5f} across {n} cases · routed {tiers}")
        print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
