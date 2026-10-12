"""OTEL traces (M4): a triage read as a span tree, not a wall of text.

Real OpenTelemetry API and SDK — not a lookalike — but deliberately no collector and no network
exporter. Every root span (one triage) flushes to `results/traces/<case>-<stamp>.json` as a nested
tree: which nodes ran, how long each took, which model calls cost what, and what the verdict was.
That file is the artifact; it goes in the repo's ignored results, not git.

Spans are buffered per trace and written when the root ends, because SimpleSpanProcessor hands them
over one at a time and a trace is only legible as a whole.
"""

from __future__ import annotations

import functools
import json
import threading
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

TRACES = Path(__file__).resolve().parents[2] / "results" / "traces"

_ready = False
_lock = threading.Lock()
_last_path: Path | None = None


def setup() -> None:
    """Idempotent: safe to call from build_agent, the tests, and this module itself."""
    global _ready
    with _lock:
        if _ready:
            return
        provider = TracerProvider(resource=Resource.create({"service.name": "support-triage"}))
        provider.add_span_processor(SimpleSpanProcessor(_TreeExporter(TRACES)))
        trace.set_tracer_provider(provider)
        _ready = True


def tracer() -> trace.Tracer:
    setup()
    return trace.get_tracer("support_triage")


def last_path() -> Path | None:
    """Where the most recent finished trace landed — the CLI prints it after a run."""
    return _last_path


def span(name: str):
    """Decorate a graph node (or any function) so it records a child span. Boring on purpose:
    the alternative is a `with` block re-indenting every node body."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            with tracer().start_as_current_span(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def _set_last(path: Path) -> None:
    global _last_path
    _last_path = path


def _as_dict(span) -> dict:
    return {"name": span.name,
            "started": round(span.start_time / 1e6, 3),       # ms since epoch, three decimals
            "duration_ms": round((span.end_time - span.start_time) / 1e6, 3),
            "attributes": {k: v for k, v in (span.attributes or {}).items()},
            "children": []}


class _TreeExporter(SpanExporter):
    """Buffers spans per trace; on the root's end, writes the nested tree to one JSON file."""

    def __init__(self, out: Path):
        self._out = out
        self._pending: dict[int, list] = {}

    def export(self, spans) -> SpanExportResult:
        for span in spans:
            trace_id = span.context.trace_id
            self._pending.setdefault(trace_id, []).append(span)
            if span.parent is None:
                self._flush(trace_id)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> SpanExportResult:
        for trace_id in list(self._pending):          # a crashed root still leaves its children
            self._flush(trace_id)
        return SpanExportResult.SUCCESS

    def _flush(self, trace_id: int) -> None:
        spans = self._pending.pop(trace_id)
        by_id = {s.context.span_id: _as_dict(s) for s in spans}
        root = next(by_id[s.context.span_id] for s in spans if s.parent is None)
        for s in spans:                              # hang each span under its parent, in start order
            if s.parent is None:
                continue
            parent = by_id.get(s.parent.span_id)
            (parent or root)["children"].append(by_id[s.context.span_id])
        case = root["attributes"].get("case_id", f"trace-{trace_id:x}")
        stamp = root["started"]
        path = self._out / f"{case}-{stamp:.0f}.json"
        self._out.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(root, indent=2) + "\n", encoding="utf-8")
        _set_last(path)


setup()
