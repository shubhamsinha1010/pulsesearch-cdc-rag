"""Shared kernel for PulseSearch services.

This package centralises cross-cutting concerns (configuration, logging,
domain models, embeddings, Elasticsearch access and metrics) so that the
ingest, worker and api services depend on a single, well-tested abstraction
layer instead of duplicating infrastructure code (DRY).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
