"""CI runs `pytest tests -k "not live"`. pytest's -k matches substrings of test, class and file names, so an offline
test whose name merely contains those four letters ("delivery", "lives_in") would silently never run in CI. Only a
test that needs a running stack may carry them, and then on purpose: test_live_<what>, a Live<What> class or a
test_live_<what>.py file. Offline."""
import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
WORD = "li" + "ve"                       # spelled in two parts so this file never matches its own rule


def names(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            yield node.name, node.name.startswith(f"test_{WORD}_")
        elif isinstance(node, ast.ClassDef):
            yield node.name, node.name.startswith(WORD.capitalize())


def test_no_offline_test_is_hidden_by_the_ci_filter():
    hidden = []
    for path in sorted(TESTS.glob("test_*.py")):
        if WORD in path.stem.lower() and not path.stem.startswith(f"test_{WORD}_"):
            hidden.append(path.name)
        for name, on_purpose in names(path):
            if WORD in name.lower() and not on_purpose:
                hidden.append(f"{path.name}::{name}")
    assert not hidden, (f"CI's -k \"not {WORD}\" would skip these offline tests: rename them so their names do not "
                        f"contain {WORD!r} (or name a test that needs a running stack test_{WORD}_*): {hidden}")
