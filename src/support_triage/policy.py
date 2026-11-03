"""The policy corpus, and the rule that makes citations mean something.

A policy id that does not exist in the corpus is not a citation. That single rule is what separates
"the agent said Severn 1" from "the agent said Sev1 and here is the line that says so" — and in a
support org it is the difference between a defensible commitment and one that costs money.

Retrieval is lexical on purpose: the corpus is eight sections, embeddings would be theatre, and a
lexical retriever is deterministic — which means the eval measures the agent, not the retriever.

Everything the corpus is made of is cached per policy id, because a single triage asks for the same
sections repeatedly — the classify prompt looks every candidate up, the citation check and the SLA
table each re-read it, and a repair attempt does it again. The cache is keyed by file identity
(mtime and size) for the parse, and by the section's own text for its tokens and its rendered block,
so editing `corpus/policy.md` is visible on the next call and a stale entry can never be served.

The cache is a cache, not a filter: every section still reaches the model for every case. That is
deliberate and pinned by a test — the one time retrieval was allowed to select (CASE-1042) the two
rules that decide entitlement and scope never made the cut, and no tier can cite a rule it cannot
see.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .models import PolicyLine

CORPUS = Path(__file__).resolve().parents[2] / "corpus"

_HEADING = re.compile(r"^##\s+([A-Z][A-Z0-9-]+)\s+—\s+(.+)$", re.M)
_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is", "are", "be", "with",
         "that", "this", "it", "as", "at", "by", "from", "not", "no", "any", "must", "may"}

# path -> (file identity, parsed sections) / (file identity, id -> section)
_PARSE: dict[Path, tuple[tuple[int, int], list[PolicyLine]]] = {}
_INDEX: dict[Path, tuple[tuple[int, int], Mapping[str, PolicyLine]]] = {}
# policy id -> ((the section text it was derived from, its heading), the derived value). Keyed by
# its source so an edited section is recomputed the next time it is used: the cache cannot outlive
# the text it was built from.
_TERMS: dict[str, tuple[tuple[str, str], frozenset[str]]] = {}
_BLOCKS: dict[str, tuple[tuple[str, str], str]] = {}


def _signature(line: PolicyLine) -> tuple[str, str]:
    return (line.text, line.section)


def _identity(path: Path) -> tuple[int, int]:
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


def _parse(path: Path) -> list[PolicyLine]:
    text = path.read_text(encoding="utf-8")
    marks = list(_HEADING.finditer(text))
    lines: list[PolicyLine] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        lines.append(PolicyLine(policy_id=m.group(1), section=m.group(2).strip(), text=body))
    return lines


def load(corpus: Path | None = None) -> list[PolicyLine]:
    """Split the corpus into citable sections. One heading, one id, one section.

    Parsed once per process per corpus file; re-parsed when the file's mtime or size changes.
    The list is shared — treat it as read-only. (Concurrent first use may parse twice; the result
    is identical either way, so no lock is worth taking on the hot path.)"""
    path = Path(corpus) if corpus else CORPUS / "policy.md"
    ident = _identity(path)
    hit = _PARSE.get(path)
    if hit is not None and hit[0] == ident:
        return hit[1]
    lines = _parse(path)
    _PARSE[path] = (ident, lines)
    return lines


def index(corpus: Path | None = None) -> Mapping[str, PolicyLine]:
    """The id -> section map, cached with the parse and returned read-only."""
    path = Path(corpus) if corpus else CORPUS / "policy.md"
    lines = load(path)  # refreshes the parse if the file changed
    ident = _PARSE[path][0]
    hit = _INDEX.get(path)
    if hit is not None and hit[0] == ident:
        return hit[1]
    mapping = MappingProxyType({line.policy_id: line for line in lines})
    _INDEX[path] = (ident, mapping)
    return mapping


def terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def _line_terms(line: PolicyLine) -> frozenset[str]:
    """A section's search terms, computed once per version of that section."""
    signature = _signature(line)
    hit = _TERMS.get(line.policy_id)
    if hit is not None and hit[0] == signature:
        return hit[1]
    keys = frozenset(terms(line.text + " " + line.section))
    _TERMS[line.policy_id] = (signature, keys)
    return keys


# Never cut by k. These two sections decide whether a commitment may exist at all, and they are the
# ones lexical ranking loses: CASE-1042 failed in every tier because "Basic plan support" did not
# overlap "POL-PLAN-1"/"POL-SCOPE-1" enough to make the top 6.
MANDATORY = ("POL-PLAN-1", "POL-SCOPE-1")


def retrieve(query: str, k: int | None = None, corpus: Path | None = None) -> list[PolicyLine]:
    """Sections ranked by term overlap, top-k (all when k is unset). Zero-overlap sections rank
    last instead of vanishing: a section the query never mentions can still be the rule that
    decides the case. Deterministic; ties broken by id so runs are reproducible.

    k can truncate, but never below the mandatory sections (MANDATORY); the returned order is the
    relevance order either way. No caller truncates today — this is the guard that keeps a future
    one from re-learning CASE-1042."""
    q = terms(query)
    ranked = sorted(load(corpus),
                    key=lambda line: (-len(q & _line_terms(line)), line.policy_id))
    if k is None or k >= len(ranked):
        return ranked
    keep = {line.policy_id for line in ranked[:k]} | set(MANDATORY)
    return [line for line in ranked if line.policy_id in keep]


def unknown_ids(cited: list[str], corpus: Path | None = None) -> list[str]:
    known = index(corpus)
    return [c for c in cited if c not in known]


# Targets that override the matrix, with the section that grants the override. The numbers live here
# and not in the agent, so the corpus and the code cannot drift apart silently. An override is also
# earned, not cited into existence: expected_sla honours one only if its precondition holds in the
# gathered facts (see expected_sla).
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
                 facts: dict | None = None, corpus: Path | None = None) -> float | None:
    """What the first-response target should be, given the plan, the severity and the cited policy.

    Without `facts`, a cited override is honoured — the corpus alone is the witness, which is what
    the seeded-truth derivation needs. With `facts` from the gathered context, an override applies
    only if its precondition holds: POL-INC-1 needs an open incident AND the verdict linking the
    case to it (its own wording — symptom match is the model's call to make, and a mere citation
    is not that call), POL-SEC-1 a security report, POL-SCOPE-1 an out-of-scope verdict. A
    citation the facts do not support falls back to the matrix, where the usual correction logic
    catches it.
    """
    gates = {"POL-INC-1": facts is None or (bool(facts.get("open_incidents"))
                                            and bool(facts.get("case_linked_to_incident"))),
             "POL-SEC-1": facts is None or bool(facts.get("security_report")),
             "POL-SCOPE-1": facts is None or bool(facts.get("out_of_scope"))}
    for cited in citations:
        if cited in SLA_OVERRIDES and gates[cited]:
            return SLA_OVERRIDES[cited]
    return sla_matrix(corpus).get(plan, {}).get(severity)


def _block(line: PolicyLine) -> str:
    """One section as the model sees it, composed once per version of that section."""
    signature = _signature(line)
    hit = _BLOCKS.get(line.policy_id)
    if hit is not None and hit[0] == signature:
        return hit[1]
    block = f"[{line.policy_id}] {line.section}\n{line.text}"
    _BLOCKS[line.policy_id] = (signature, block)
    return block


def render(lines: list[PolicyLine]) -> str:
    """The candidate policy set handed to the model: ids it may cite, and nothing else."""
    return "\n\n".join(_block(line) for line in lines)
