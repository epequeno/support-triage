"""The eval gate: a real regression fails the build, model noise does not, and the blame is named."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from support_triage import eval_gate

RESULTS = eval_gate.RESULTS


@pytest.fixture()
def run() -> dict:
    return json.loads((RESULTS / "eval.json").read_text(encoding="utf-8"))


@pytest.fixture()
def baseline() -> dict:
    return json.loads((RESULTS / "baseline.json").read_text(encoding="utf-8"))


def _miss(run: dict, axis: str, *cases: str) -> dict:
    for row in run["rows"]:
        if row["case"] in cases:
            row["score"][axis] = False
    return run


def test_the_recorded_run_clears_the_recorded_baseline(run, baseline):
    result = eval_gate.review(run, baseline)
    assert result["ok"], result["problems"]
    assert result["axes"]["severity"] == (10, 10, 10)


def test_one_miss_on_a_ninety_percent_axis_is_noise_not_a_regression(run, baseline):
    result = eval_gate.review(_miss(run, "citations", "CASE-1041"), baseline)
    assert result["ok"], result["problems"]
    assert "CASE-1041 missed citations" in result["notes"]


def test_two_misses_on_one_axis_fail_and_name_the_axis(run, baseline):
    result = eval_gate.review(_miss(run, "citations", "CASE-1041", "CASE-1045"), baseline)
    assert not result["ok"]
    assert any(problem.startswith("citations: 8/10") for problem in result["problems"])


def test_a_single_severity_miss_fails_because_severity_has_never_been_wrong(run, baseline):
    result = eval_gate.review(_miss(run, "severity", "CASE-1047"), baseline)
    assert not result["ok"]
    assert any(problem.startswith("severity: 9/10") for problem in result["problems"])
    assert "CASE-1047 missed severity" in result["notes"]


def test_a_router_that_stops_routing_fails_on_cost_not_accuracy(run, baseline):
    run["cost_usd"] = 0.51
    result = eval_gate.review(run, baseline)
    assert not result["ok"]
    assert any(problem.startswith("cost:") for problem in result["problems"])


def test_a_partial_run_is_refused_unless_explicitly_allowed(run, baseline):
    run["rows"], run["cases"] = run["rows"][:5], 5
    assert "covered 5 cases" in eval_gate.review(run, baseline)["fatal"]
    assert eval_gate.review(deepcopy(run), baseline, allow_partial=True)["ok"]


def test_an_inconsistent_run_is_refused(run, baseline):
    run["cases"] = 11
    assert "inconsistent" in eval_gate.review(run, baseline)["fatal"]


def test_an_errored_case_is_reported_by_name(run, baseline):
    run["rows"][0] = {"case": "CASE-1041", "error": "KeyError: 'OPENROUTER_API_KEY'",
                      "score": {axis: False for axis in eval_gate.AXES}}
    result = eval_gate.review(run, baseline)
    assert not result["ok"]
    assert "CASE-1041 errored: KeyError: 'OPENROUTER_API_KEY'" in result["notes"]


def test_the_cli_exits_on_the_verdict(tmp_path, run, baseline):
    baseline_path = str(RESULTS / "baseline.json")
    assert eval_gate.main([str(RESULTS / "eval.json"), "--baseline", baseline_path]) == 0
    bad = tmp_path / "eval.json"
    bad.write_text(json.dumps(_miss(deepcopy(run), "in_scope", "CASE-1041", "CASE-1042")),
                   encoding="utf-8")
    assert eval_gate.main([str(bad), "--baseline", baseline_path]) == 1
    assert eval_gate.main([str(tmp_path / "missing.json"), "--baseline", baseline_path]) == 2
