"""`triage CASE-1041` — one case in, one grounded triage record out.

The CLI shows the whole record, including the proposals it is *not* allowed to execute. `--approve-as
NAME` is the human's side of the gate: it is the only thing in the program that changes state, and it
takes a name so the audit trail says who.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import backend, tools, traces
from .models import TriageRecord


def render(record: TriageRecord) -> str:
    out = [
        f"case {record.case_id}   account {record.account_id}   plan {record.plan}",
        f"  classification   {record.severity} ({record.severity_label})",
        f"  in scope         {record.in_scope}",
        f"  first response   {record.sla_response_hours}h" if record.sla_response_hours is not None
        else "  first response   none (not entitled)",
        f"  next action      {record.next_action}",
        f"  cites            {', '.join(record.citations)}",
        f"  routed to        {record.routing.tier} model — {record.routing.reasons[0]}",
        f"  cost             ${record.cost_usd:.6f} · {record.model_calls} call(s) · "
        f"{record.input_tokens} in / {record.output_tokens} out",
    ]
    if record.account_facts:
        out.append("  grounded in")
        out.extend(f"    - {f}" for f in record.account_facts)
    if record.draft_reply:
        out.append("  draft reply")
        out.extend(f"    | {line}" for line in record.draft_reply.splitlines())
    if record.proposals:
        out.append("  proposed state changes (NOT executed — a human approves these)")
        for p in record.proposals:
            out.append(f"    - {p.action} {p.payload} — {p.rationale}")
    else:
        out.append("  proposed state changes: none")
    for note in record.notes:
        out.append(f"  note: {note}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="triage", description="triage one support case")
    ap.add_argument("case_id")
    ap.add_argument("--json", action="store_true", help="emit the record as JSON")
    ap.add_argument("--cap", type=float, default=None, help="per-case spend cap in USD")
    ap.add_argument("--tier", choices=["cheap", "strong"], default=None, help="force a model tier")
    ap.add_argument("--approve-as", default=None,
                    help="execute this case's proposals as the named human (the only way state changes)")
    args = ap.parse_args(argv)

    from .agent import build_agent  # imported late so --help works without a model key

    conn = backend.connect()
    cap = args.cap
    agent = build_agent(conn, cap_usd=cap, force_tier=args.tier) if cap is not None else build_agent(
        conn, force_tier=args.tier)
    try:
        record = agent.triage(args.case_id)
    except Exception as exc:  # noqa: BLE001 — a failed triage is a result worth printing plainly
        print(f"triage failed for {args.case_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(record.model_dump(), indent=2) if args.json else render(record))
    trace_path = traces.last_path()
    if trace_path is not None:
        print(f"trace: {trace_path}")

    if args.approve_as:
        for proposal in record.proposals:
            tools.execute(conn, proposal, args.approve_as)
        print(f"\napproved {len(record.proposals)} state change(s) as {args.approve_as}")
        for row in backend.approvals(conn, record.case_id):
            print(f"  {row['action']} {row['payload']} by {row['approved_by']} at {row['executed_at']}")
    elif record.proposals:
        print(f"\n{len(record.proposals)} proposal(s) awaiting approval — rerun with "
              f"--approve-as <your name> to execute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
