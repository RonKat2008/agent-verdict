# Empty on purpose: pytest inserts this file's own directory (the scenario
# repo root, where mathlib.py lives) onto sys.path when it discovers this
# conftest.py, so `tests/test_math.py`'s `from mathlib import add` resolves
# without a package layout or an installed distribution.
