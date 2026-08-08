"""Ensures the service root is importable so tests can ``import app`` when
pytest is run from within ``services/worker``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
