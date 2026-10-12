# support-triage: a case in, a grounded triage record out
set shell := ["bash", "-uc"]

default:
    @just --list

sync:
    @uv sync --quiet

seed:
    @uv run python -m support_triage.seed --reset

triage case="CASE-1041":
    @uv run triage "{{case}}"

eval *ARGS:
    @uv run python -m support_triage.eval {{ARGS}}

test:
    @uv run pytest -q

check: test
    @uv run python -m support_triage.costs
