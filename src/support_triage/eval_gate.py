"""The eval gate: a run that falls below the recorded floors fails the build.

`eval.py` measures; this module decides whether the measurement is acceptable. The floors live in
`results/baseline.json`, derived from the recorded distribution over four routed runs of the
post-fix code (forty case-draws): severity was perfect in all four, the other four axes 39/40 each.
At n=10 with a non-deterministic model, one axis point is noise, so the floors are rates — 1.0 for
severity, 0.9 for the rest — snapped up to whole cases at the size of the run. A cost ceiling
catches the other way a run can regress: a router that stops routing is more accurate and much
more expensive, and the distributed eval already measured forced-strong at $0.51 against $0.30
routed.

Exit status is the interface: 0 the run clears the gate, 1 it does not, 2 the input is unusable.
The printout names the axis and the case, because a gate that says "worse" without saying where
is not a gate.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "results"
AXES = ("severity", "in_scope", "sla", "action", "citations")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def review(run: dict, baseline: dict, allow_partial: bool = False) -> dict:
    """Judge one eval run against the baseline.

    Returns ``ok``, the per-axis ``axes`` as ``(got, need, cases)``, ``problems`` (what fails the
    gate), ``notes`` (misses that are inside the noise floor, and errored cases), and ``fatal``
    when the input cannot be judged at all.
    """
    rows = run.get("rows") or []
    if not rows:
        return {"ok": False, "fatal": "the run has no rows — nothing was measured"}
    cases = len(rows)
    declared = run.get("cases", cases)
    if declared != cases:
        return {"ok": False,
                "fatal": f"the run says {declared} cases but carries {cases} rows — "
                         "refusing to score an inconsistent file"}
    want = baseline.get("cases", cases)
    if cases != want and not allow_partial:
        return {"ok": False,
                "fatal": f"the run covered {cases} cases; the baseline was recorded on {want} — "
                         "a partial run is not a gate (pass --allow-partial to compare it anyway)"}

    totals = {axis: 0 for axis in AXES}
    for row in rows:
        for axis in AXES:
            totals[axis] += int(bool(row.get("score", {}).get(axis)))

    floors = baseline["floors"]
    axes, problems = {}, []
    for axis in AXES:
        floor = float(floors.get(axis, 1.0))
        need = math.ceil(floor * cases)
        axes[axis] = (totals[axis], need, cases)
        if totals[axis] < need:
            problems.append(f"{axis}: {totals[axis]}/{cases} below the floor of {need}/{cases} "
                            f"(the recorded rate is {floor})")

    spent = float(run.get("cost_usd") or 0.0)
    ceiling = float(baseline["cost_ceiling_usd"])
    if spent > ceiling:
        problems.append(f"cost: ${spent:.4f} over the ceiling of ${ceiling:.2f} — "
                        "a run this expensive is not the routed shape")

    notes = []
    for row in rows:
        if "error" in row:
            notes.append(f"{row.get('case', '?')} errored: {row['error']}")
            continue
        missed = [axis for axis in AXES if not row.get("score", {}).get(axis)]
        if missed:
            notes.append(f"{row.get('case', '?')} missed {', '.join(missed)}")

    return {"ok": not problems, "problems": problems, "notes": notes, "axes": axes, "cases": cases,
            "cost": (spent, ceiling), "tiers": run.get("tiers") or {}}


def _report(path: Path, result: dict) -> None:
    spent, ceiling = result["cost"]
    print(f"eval gate · {path} · {result['cases']} cases · ${spent:.5f} "
          f"(ceiling ${ceiling:.2f}) · tiers {result['tiers'] or '-'}")
    print(f"{'axis':<11}{'got':>7}  {'floor':>5}  verdict")
    for axis, (got, need, cases) in result["axes"].items():
        print(f"{axis:<11}{f'{got}/{cases}':>7}  {need:>5}  {'ok' if got >= need else 'FAIL'}")
    for note in result["notes"]:
        print(f"  {note}")
    for problem in result["problems"]:
        print(f"  FAIL {problem}")
    print(f"gate: {'PASS' if result['ok'] else 'FAIL'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="eval-gate", description="fail when an eval run falls below the recorded baseline")
    ap.add_argument("run", nargs="?", default=str(RESULTS / "eval.json"),
                    help="the eval output to judge (default: results/eval.json)")
    ap.add_argument("--baseline", default=str(RESULTS / "baseline.json"))
    ap.add_argument("--allow-partial", action="store_true",
                    help="judge a run that covers fewer cases than the baseline")
    args = ap.parse_args(argv)

    try:
        run, baseline = _load(Path(args.run)), _load(Path(args.baseline))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"eval gate: cannot read input: {exc}", file=sys.stderr)
        return 2
    try:
        result = review(run, baseline, allow_partial=args.allow_partial)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"eval gate: the baseline is unusable: {exc!r}", file=sys.stderr)
        return 2
    if result.get("fatal"):
        print(f"eval gate: {result['fatal']}", file=sys.stderr)
        return 2
    _report(Path(args.run), result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
