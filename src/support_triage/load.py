"""Measure the web API under concurrency: p50/p95/p99 and throughput, not a vibe.

Two shapes, one script. Without ``--url`` it drives the ASGI app in-process — no server, no port,
no network, so the numbers are the app's request path and nothing else. With ``--url`` it drives a
live ``just web`` (or the deployed task) through real sockets, which is what an operator actually
gets.

The triage endpoint is excluded on purpose: it calls a real model, so its latency is the model's,
not the app's, and measuring it would cost money per request. What is measured is everything a
dashboard or an approver hits — the page, the case list, the ledger, the approval trail.

Each path is measured on its own, sequentially, after a warm-up request; the concurrency is in
flight requests per path. This is a small honest number, not a benchmark suite: a single uvicorn
worker, sync endpoints in a threadpool, one SQLite connection per request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import time
from pathlib import Path

import httpx

RESULTS = Path(__file__).resolve().parents[2] / "results"
PATHS = ("/", "/api/cases", "/api/ledger", "/api/approvals/CASE-1041")


def percentile(sorted_latencies: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value that at least q of the samples do not exceed."""
    if not sorted_latencies:
        return 0.0
    rank = max(1, math.ceil(q * len(sorted_latencies)))
    return sorted_latencies[rank - 1]


async def _one(client: httpx.AsyncClient, path: str, headers: dict[str, str],
               semaphore: asyncio.Semaphore) -> float:
    async with semaphore:
        started = time.perf_counter()
        response = await client.get(path, headers=headers)
        response.raise_for_status()
        return time.perf_counter() - started


async def measure(client: httpx.AsyncClient, path: str, headers: dict[str, str],
                  requests: int, concurrency: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    await _one(client, path, headers, semaphore)  # warm: first request pays connection setup
    started = time.perf_counter()
    latencies = await asyncio.gather(*(_one(client, path, headers, semaphore)
                                       for _ in range(requests)))
    wall = time.perf_counter() - started
    ordered = sorted(latencies)
    return {"path": path, "n": requests,
            "p50_ms": round(percentile(ordered, 0.50) * 1000, 2),
            "p95_ms": round(percentile(ordered, 0.95) * 1000, 2),
            "p99_ms": round(percentile(ordered, 0.99) * 1000, 2),
            "max_ms": round(ordered[-1] * 1000, 2),
            "rps": round(requests / wall, 1)}


async def run(args: argparse.Namespace) -> dict:
    token = args.token
    headers = {"Authorization": f"Bearer {token}"}
    if args.url:
        transport, base = None, args.url.rstrip("/")
    else:
        from .web import app  # imported late so a live run does not need the app's dependencies
        os.environ["APP_TOKEN"] = token  # the in-process app reads its gate from the environment
        transport, base = httpx.ASGITransport(app=app), "http://in-process"

    async with httpx.AsyncClient(transport=transport, base_url=base, timeout=30) as client:
        rows = [await measure(client, path, headers, args.requests, args.concurrency)
                for path in args.paths]
    return {"target": args.url or "in-process ASGI app",
            "requests_per_path": args.requests, "concurrency": args.concurrency,
            "python": platform.python_version(), "cpus": os.cpu_count(),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="load", description="p50/p95/p99 for the web API")
    ap.add_argument("--url", default=None, help="live base URL (default: in-process ASGI app)")
    ap.add_argument("--path", dest="paths", action="append", default=None,
                    help=f"path to measure; repeatable (default: {' '.join(PATHS)})")
    ap.add_argument("--requests", type=int, default=300, help="requests per path")
    ap.add_argument("--concurrency", type=int, default=10, help="requests in flight per path")
    ap.add_argument("--token", default=os.getenv("APP_TOKEN") or "load-test")
    ap.add_argument("--out", default=str(RESULTS / "load.json"))
    args = ap.parse_args(argv)
    args.paths = args.paths or list(PATHS)

    result = asyncio.run(run(args))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"{result['target']} · {args.requests} requests/path · concurrency {args.concurrency}"
          f" · py{result['python']} · {result['cpus']} cpus")
    print(f"{'path':<28}{'n':>5}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}{'req/s':>8}")
    for row in result["rows"]:
        print(f"{row['path']:<28}{row['n']:>5}{row['p50_ms']:>8.1f}m{row['p95_ms']:>8.1f}m"
              f"{row['p99_ms']:>8.1f}m{row['max_ms']:>8.1f}m{row['rps']:>8.1f}")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
