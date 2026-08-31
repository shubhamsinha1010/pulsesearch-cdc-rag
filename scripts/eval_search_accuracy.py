#!/usr/bin/env python3
"""Labeled live accuracy eval for PulseSearch modes + RAG grounding.

Usage (stack must be up):
    python scripts/eval_search_accuracy.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "http://localhost:8000"
FIXTURES = Path(__file__).with_name("eval_fixtures.json")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _get(path: str):
    with urllib.request.urlopen(f"{API}{path}") as resp:
        return json.load(resp)


def _search(q: str, mode: str, size: int = 5, namespace: int = 0):
    params = urllib.parse.urlencode(
        {"q": q, "mode": mode, "size": size, "wiki": "enwiki", "namespace": namespace}
    )
    return _get(f"/search?{params}")


def _rag(question: str):
    req = urllib.request.Request(
        f"{API}/rag",
        data=json.dumps({"question": question, "wiki": "enwiki"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def _sample_title() -> str | None:
    sample = _search("the", "bm25", size=30, namespace=0)
    for hit in sample["hits"]:
        title = hit["document"]["title"]
        if hit["document"].get("namespace", 0) == 0 and len(title.split()) >= 2:
            return title
    return None


def _run_search_case(case: dict, sample_title: str | None) -> list[Check]:
    checks: list[Check] = []
    case_id = case["id"]

    if case.get("query_from_index"):
        if not sample_title:
            return [Check(case_id, False, "no sample title available")]
        words = [w for w in sample_title.replace(":", " ").split() if len(w) > 2][:3]
        query = " ".join(words)
        for mode in case.get("modes", ["hybrid"]):
            hits = _search(query, mode, size=5)["hits"]
            top = hits[0]["document"]["title"] if hits else None
            ok = bool(hits) and (
                sample_title.lower() in (top or "").lower()
                or (top or "").lower() in sample_title.lower()
            )
            checks.append(
                Check(
                    f"{case_id}/{mode}",
                    ok,
                    f"q={query!r} expected~{sample_title!r} got={top!r}",
                )
            )
        return checks

    query = case["query"]
    mode = case.get("mode", "hybrid")
    res = _search(query, mode, size=5)
    hits = res["hits"]
    titles = [h["document"]["title"] for h in hits]

    if case.get("expect_empty"):
        bad = []
        for prefix in case.get("forbid_title_prefix", []):
            bad.extend([t for t in titles if t.startswith(prefix)])
        ok = res["count"] == 0 and not bad
        checks.append(Check(case_id, ok, f"count={res['count']} titles={titles[:3]}"))
        return checks

    if case.get("expect_namespace") is not None:
        namespaces = {h["document"].get("namespace", 0) for h in hits}
        ok_ns = bool(hits) and namespaces <= {case["expect_namespace"]}
    else:
        ok_ns = True

    patterns = [re.compile(p) for p in case.get("expect_any_title_regex", [])]
    ok_regex = any(p.search(t) for t in titles for p in patterns) if patterns else True

    min_hits = case.get("min_hits", 0)
    ok = ok_ns and ok_regex and len(hits) >= min_hits
    checks.append(
        Check(
            case_id,
            ok,
            f"titles={titles[:5]} namespaces="
            f"{sorted({h['document'].get('namespace', 0) for h in hits})}",
        )
    )
    return checks


def main() -> int:
    ready = _get("/health/ready")
    if not ready.get("elasticsearch") or ready.get("documents", 0) < 10:
        print("Index not ready:", ready)
        return 2

    fixtures = json.loads(FIXTURES.read_text())
    sample_title = _sample_title()
    checks: list[Check] = []

    for case in fixtures["cases"]:
        if case["type"] == "search":
            checks.extend(_run_search_case(case, sample_title))
        elif case["type"] == "rag":
            ans = _rag(case["question"])
            ok = ans.get("grounded") is case.get("expect_grounded")
            checks.append(
                Check(
                    case["id"],
                    ok,
                    f"grounded={ans.get('grounded')} answer={ans.get('answer', '')[:120]!r}",
                )
            )

    print(f"Index documents: {ready['documents']}")
    if sample_title:
        print(f"Lexical probe title: {sample_title!r}")
    failed = 0
    for check in checks:
        status = "PASS" if check.ok else "FAIL"
        if not check.ok:
            failed += 1
        print(f"[{status}] {check.name}: {check.detail}")

    total = len(checks)
    passed = total - failed
    pct = (100.0 * passed / total) if total else 0.0
    print(f"\n{passed}/{total} checks passed ({pct:.0f}%)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
