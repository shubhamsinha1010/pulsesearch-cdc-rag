#!/usr/bin/env python3
"""Latency benchmark for the retrieval paths: BM25 vs vector vs hybrid vs RAG.

Measures single-client, sequential *latency* (not throughput) against a running
stack, and reports the cost each retrieval strategy adds over plain lexical
search. Client-side wall time is reported alongside the API's own ``took_ms``
so transport overhead is visible rather than hidden.

Usage (stack must be up):
    python scripts/bench_retrieval.py
    make bench

Override the target with PULSESEARCH_API (e.g. when port-forwarding from
Kubernetes):
    PULSESEARCH_API=http://localhost:8001 python scripts/bench_retrieval.py
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API = os.environ.get("PULSESEARCH_API", "http://localhost:8000")

# Sequential single-client runs. Enough samples for a stable p95 without making
# `make bench` a coffee break; RAG is deliberately fewer because each call is a
# hosted LLM round trip.
SEARCH_REPEATS = 20
RAG_REPEATS = 5
WARMUP = 3

# Per-request ceilings. Without these a wedged API makes the benchmark hang
# indefinitely instead of reporting that the stack is unhealthy.
READY_TIMEOUT = 15.0
SEARCH_TIMEOUT = 30.0
RAG_TIMEOUT = 120.0

# Mixed query shapes: short lexical, multi-word, and conceptual. The hybrid
# ranker weights BM25 vs kNN by query shape, so a single shape would flatter it.
QUERIES = [
    "climate",
    "Berlin",
    "world war two treaty",
    "how do neural networks learn representations",
    "python programming language history",
]

RAG_QUESTIONS = [
    "What changed recently in physics articles?",
    "Summarise the latest edits about Germany.",
]

# Laptop-scale guard rails, not production SLOs: they exist so a large
# regression fails `make bench` instead of scrolling past unnoticed.
P95_BUDGET_MS = {
    "bm25": 150.0,
    "vector": 400.0,
    "hybrid": 500.0,
    "rag": 8000.0,
}


@dataclass
class Result:
    """Timing samples for one retrieval path."""

    name: str
    client_ms: list[float] = field(default_factory=list)
    server_ms: list[float] = field(default_factory=list)
    errors: int = 0

    @property
    def ok(self) -> bool:
        budget = P95_BUDGET_MS.get(self.name)
        if self.errors or not self.client_ms or budget is None:
            return False
        return _percentile(self.client_ms, 95) <= budget


def _percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile.

    statistics.quantiles interpolates and needs n > 1, which is a poor fit for
    the small sample counts here; nearest-rank never invents a value.
    """

    if not samples:
        return float("nan")
    ordered = sorted(samples)
    rank = max(1, min(len(ordered), math.ceil(pct / 100.0 * len(ordered))))
    return ordered[rank - 1]


def _get(path: str, timeout: float):
    with urllib.request.urlopen(f"{API}{path}", timeout=timeout) as resp:
        return json.load(resp)


def _timed_search(query: str, mode: str) -> tuple[float, float, int]:
    """Return (client_ms, server_ms, hit_count) for one search call."""

    params = urllib.parse.urlencode({"q": query, "mode": mode, "size": 10, "wiki": "enwiki"})
    started = time.perf_counter()
    payload = _get(f"/search?{params}", SEARCH_TIMEOUT)
    client_ms = (time.perf_counter() - started) * 1000
    return client_ms, float(payload.get("took_ms") or 0.0), payload.get("count", 0)


def _timed_rag(question: str) -> tuple[float, bool]:
    req = urllib.request.Request(
        f"{API}/rag",
        data=json.dumps({"question": question, "wiki": "enwiki"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=RAG_TIMEOUT) as resp:
        payload = json.load(resp)
    return (time.perf_counter() - started) * 1000, bool(payload.get("grounded"))


def _bench_search(mode: str) -> Result:
    result = Result(name=mode)

    # Warm up ES caches and the query-embedding model so the first sample does
    # not dominate the tail.
    for query in QUERIES[:WARMUP]:
        with contextlib.suppress(urllib.error.URLError, OSError, ValueError):
            _timed_search(query, mode)

    for i in range(SEARCH_REPEATS):
        query = QUERIES[i % len(QUERIES)]
        try:
            client_ms, server_ms, _ = _timed_search(query, mode)
            result.client_ms.append(client_ms)
            result.server_ms.append(server_ms)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            result.errors += 1
            print(f"  ! {mode} q={query!r} failed: {exc}")
    return result


def _bench_rag() -> Result:
    result = Result(name="rag")
    grounded = 0
    for i in range(RAG_REPEATS):
        question = RAG_QUESTIONS[i % len(RAG_QUESTIONS)]
        try:
            client_ms, is_grounded = _timed_rag(question)
            result.client_ms.append(client_ms)
            grounded += int(is_grounded)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            result.errors += 1
            print(f"  ! rag q={question!r} failed: {exc}")
    if result.client_ms:
        print(f"  rag grounded answers: {grounded}/{len(result.client_ms)}")
    return result


def _print_table(results: list[Result]) -> None:
    header = (
        f"{'path':<8}{'n':>4}{'mean':>9}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}{'server p50':>12}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        if not r.client_ms:
            print(f"{r.name:<8}{0:>4}{'  no samples':>48}")
            continue
        server_p50 = f"{_percentile(r.server_ms, 50):.1f}" if r.server_ms else "-"
        print(
            f"{r.name:<8}{len(r.client_ms):>4}"
            f"{statistics.fmean(r.client_ms):>9.1f}"
            f"{_percentile(r.client_ms, 50):>9.1f}"
            f"{_percentile(r.client_ms, 95):>9.1f}"
            f"{_percentile(r.client_ms, 99):>9.1f}"
            f"{max(r.client_ms):>9.1f}"
            f"{server_p50:>12}"
        )
    print("\nAll figures in milliseconds. 'server p50' is the API's own took_ms;")
    print("the gap to client p50 is HTTP + JSON overhead.")


def _print_deltas(results: dict[str, Result]) -> None:
    baseline = results.get("bm25")
    if not baseline or not baseline.client_ms:
        return
    base_p50 = _percentile(baseline.client_ms, 50)
    print(f"\nCost over BM25 (p50 = {base_p50:.1f}ms):")
    for name in ("vector", "hybrid", "rag"):
        r = results.get(name)
        if not r or not r.client_ms:
            continue
        p50 = _percentile(r.client_ms, 50)
        print(f"  {name:<7} +{p50 - base_p50:>8.1f}ms  ({p50 / base_p50:.1f}x)")
    hybrid = results.get("hybrid")
    if hybrid and hybrid.client_ms:
        print(
            "\nHybrid runs BM25 and query embedding concurrently, so its cost is "
            "roughly\nmax(BM25, embed) + kNN + fusion rather than the sum."
        )


def main() -> int:
    try:
        ready = _get("/health/ready", READY_TIMEOUT)
    except (urllib.error.URLError, OSError) as exc:
        print(f"API unreachable at {API}: {exc}")
        return 2

    if not ready.get("elasticsearch") or ready.get("documents", 0) < 10:
        print("Index not ready:", ready)
        return 2

    print(f"API: {API}")
    print(f"Index documents: {ready['documents']}")
    print(
        f"Samples: {SEARCH_REPEATS} per search mode, {RAG_REPEATS} for RAG "
        f"({WARMUP} warmup calls discarded)\n"
    )

    results: dict[str, Result] = {}
    for mode in ("bm25", "vector", "hybrid"):
        results[mode] = _bench_search(mode)
    if ready.get("llm"):
        results["rag"] = _bench_rag()
    else:
        print("  (skipping RAG: LLM reported unhealthy by /health/ready)")

    print()
    _print_table(list(results.values()))
    _print_deltas(results)

    print("\nBudget check (p95):")
    failed = 0
    for r in results.values():
        budget = P95_BUDGET_MS.get(r.name)
        p95 = _percentile(r.client_ms, 95) if r.client_ms else float("nan")
        status = "OK" if r.ok else "SLOW"
        if not r.ok:
            failed += 1
        detail = f"p95={p95:.1f}ms budget={budget:.0f}ms"
        if r.errors:
            detail += f" errors={r.errors}"
        print(f"[{status}] {r.name}: {detail}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
