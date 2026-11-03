"""What produced a run: the model ids, the corpus digest and the prompt digest.

The readiness review's last open dimension was identity and provenance: a run could not be
reproduced from the repository alone because it named neither the model nor the corpus version.
This module is the single source for those facts. The agent writes them into each record and trace
root; `eval` writes them into the run payload; the recorded run's backfilled manifest lives beside
`results/eval.json`.

The corpus digest is the SHA-256 of `corpus/policy.md`. The prompt digest is the SHA-256 of the
classify prompt function's source, computed in `agent.py` because that is where the function lives;
it changes whenever the prompt code changes. Model ids are resolved at import time from the same
environment overrides the agent uses.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Mapping

from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus" / "policy.md"

# Verified against the provider catalogue on 2026-10-11. Override with TRIAGE_MODEL_CHEAP /
# TRIAGE_MODEL_STRONG; routing is by tier, never by name.
MODEL_IDS: Mapping[str, str] = MappingProxyType({
    "cheap": os.environ.get("TRIAGE_MODEL_CHEAP", "google/gemini-2.5-flash-lite"),
    "strong": os.environ.get("TRIAGE_MODEL_STRONG", "openai/gpt-5"),
})


def sha256_file(path: Path) -> str:
    """The digest of the bytes on disk, not of a decoded string."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def corpus_sha256() -> str:
    """The version of the policy corpus this process is reading."""
    return sha256_file(CORPUS)


def snapshot(prompt_sha256: str) -> dict:
    """The provenance block written into a trace root or an eval payload."""
    return {
        "models": dict(MODEL_IDS),
        "corpus_sha256": corpus_sha256(),
        "prompt_sha256": prompt_sha256,
    }
