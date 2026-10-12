"""The policy corpus, and the rule that makes citations mean something.

A policy id that does not exist in the corpus is not a citation. That single rule is what separates
"the agent said Severn 1" from "the agent said Sev1 and here is the line that says so" — and in a
support org it is the difference between a defensible commitment and one that costs money.

Retrieval is lexical on purpose: the corpus is eight sections, embeddings would be theatre, and a
lexical retriever is deterministic — which means the eval measures the agent, not the retriever.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import PolicyLine

CORPUS = Path(__file__).resolve().parents[2] / "corpus"

_HEADING = re.compile(r"^##\s+([A-Z][A-Z0-9-]+)\s+—\s+(.+)$", re.M)
_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is", "are", "be", "with",
         "that", "this", "it", "as", "at", "by", "from", "not", "no", "any", "must", "may"}


def load(corpus: Path | None = None) -> list[PolicyLine]:
    """Split the corpus into citable sections. One heading, one id, one section."""
    text = (corpus or CORPUS / "policy.md").read_text(encoding="utf-8")
    marks = list(_HEADING.finditer(text))
    lines: list[PolicyLine] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        lines.append(PolicyLine(policy_id=m.group(1), section=m.group(2).strip(), text=body))
    return lines


def index(corpus: Path | None = None) -> dict[str, PolicyLine]:
    return {line.policy_id: line for line in load(corpus)}


def terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def retrieve(query: str, k: int = 6, corpus: Path | None = None) -> list[PolicyLine]:
    """Top-k sections by term overlap. Deterministic; ties broken by id so runs are reproducible."""
    scored: list[tuple[int, str, PolicyLine]] = []
    q = terms(query)
    for line in load(corpus):
        overlap = len(q & terms(line.text + " " + line.section))
        if overlap:
            scored.append((-overlap, line.policy_id, line))
    scored.sort()
    return [line for _, _, line in scored[:k]]


def unknown_ids(cited: list[str], corpus: Path | None = None) -> list[str]:
    known = index(corpus)
    return [c for c in cited if c not in known]


# Targets that override the matrix, with the section that grants the override. The numbers live here
# and not in the agent, so the corpus and the code cannot drift apart silently.
# None means the matrix does not apply at all: an out-of-scope case is closed, not answered late.
SLA_OVERRIDES: dict[str, float | None] = {"POL-INC-1": 1.0, "POL-SEC-1": 0.25, "POL-SCOPE-1": None}


def sla_matrix(corpus: Path | None = None) -> dict[str, dict[str, float | None]]:
    """The first-response targets, parsed out of POL-SLA-1 so the table IS the source of truth."""
    section = index(corpus).get("POL-SLA-1")
    if section is None:
        raise LookupError("POL-SLA-1 is missing from the corpus; the SLA table cannot be derived")
    table: dict[str, dict[str, float | None]] = {}
    for row in section.text.splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] in {"plan", ""} or set(cells[0]) <= {"-", " "}:
            continue
        values: list[float | None] = []
        for cell in cells[1:]:
            values.append(None if cell.lower() in {"none", "n/a", "-", ""} else float(cell))
        table[cells[0]] = dict(zip(("Sev1", "Sev2", "Sev3", "Sev4"), values))
    return table


def expected_sla(plan: str, severity: str, citations: list[str],
                 corpus: Path | None = None) -> float | None:
    """What the first-response target should be, given the plan, the severity and the cited policy."""
    for cited in citations:
        if cited in SLA_OVERRIDES:
            return SLA_OVERRIDES[cited]
    return sla_matrix(corpus).get(plan, {}).get(severity)


def render(lines: list[PolicyLine]) -> str:
    """The candidate policy set handed to the model: ids it may cite, and nothing else."""
    return "\n\n".join(f"[{line.policy_id}] {line.section}\n{line.text}" for line in lines)
