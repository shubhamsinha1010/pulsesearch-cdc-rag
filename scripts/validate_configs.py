#!/usr/bin/env python3
"""Well-formedness check for every checked-in YAML and JSON config.

This catches the boring failure mode that unit tests never see: a Grafana
dashboard, Debezium connector or Prometheus config that is syntactically broken
and only explodes when a container starts.

Scope is deliberately syntax, not semantics — `docker compose config` and
kubeconform (both wired into CI) cover schema validation for their own files.

Usage:
    python scripts/validate_configs.py
    make validate-configs
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

YAML_GLOBS = (
    "docker-compose.yml",
    ".github/dependabot.yml",
    ".github/workflows/*.yml",
    "infra/prometheus/*.yml",
    "infra/grafana/provisioning/**/*.yml",
    "deploy/k8s/**/*.yaml",
)

JSON_GLOBS = (
    "connectors/*.json",
    "infra/grafana/dashboards/*.json",
    "scripts/*.json",
    "web/package.json",
)


def _iter_paths(globs: tuple[str, ...]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in globs:
        paths.update(p for p in ROOT.glob(pattern) if p.is_file())
    return sorted(paths)


def _check(path: Path) -> str | None:
    """Return an error string, or None when the file parses."""

    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            json.loads(text)
        else:
            # Multi-document YAML is normal for Kubernetes manifests.
            docs = list(yaml.safe_load_all(text))
            if not any(d is not None for d in docs):
                return "no YAML documents found"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return str(exc).replace("\n", " ")
    return None


def main() -> int:
    paths = _iter_paths(YAML_GLOBS) + _iter_paths(JSON_GLOBS)
    if not paths:
        print("No config files matched — check the globs in this script.")
        return 2

    failed = 0
    for path in paths:
        error = _check(path)
        rel = path.relative_to(ROOT)
        if error:
            failed += 1
            print(f"[FAIL] {rel}: {error}")
        else:
            print(f"[ OK ] {rel}")

    print(f"\n{len(paths) - failed}/{len(paths)} config files parsed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
