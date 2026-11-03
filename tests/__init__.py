"""Package marker so `python -m unittest discover -s tests -t .` works.

Several Build F test modules import each other by bare name (`import test_f_bridge`),
so the tests folder itself is put on sys.path as well.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
