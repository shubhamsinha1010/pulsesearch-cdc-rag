"""Ensures the service root is importable so tests can ``import app`` when
pytest is run from within ``services/ingest``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
