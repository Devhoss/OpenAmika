"""Pytest bootstrap for the OpenAmika repo layout.

The suite was written against the original build workspace (one merged `payload/` dir).
In this repo the brain lives in brain/ and the speech layer in speech/ — po_contract.py
is installed identically in both, so the duplicate module name is harmless. Individual
tests still insert the legacy `payload` path themselves; that path simply doesn't exist
here and these entries win instead.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "speech", ROOT / "brain"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
