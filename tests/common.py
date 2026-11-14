"""Shared test helpers. The tests use only the standard library (unittest); run them with
    python -m unittest discover -s tests -v          (from the repository root)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pm_adapter  # noqa: E402
from pm_adapter import Adapter, load_schema  # noqa: E402


def demo(**kw):
    """An adapter on the bundled v2 demo schema; 'test-runner' is a configured measurement source."""
    kw.setdefault("measurement_sources", {"test-runner"})
    return Adapter(load_schema("vault_demo"), **kw)


def schema_with_keys(keys, **extra):
    s = dict(load_schema("vault_demo"), keys=list(keys))
    s.update(extra)
    return s
