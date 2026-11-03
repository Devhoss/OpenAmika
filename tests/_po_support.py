"""Shared helpers for the PromptOptimized contract tests (CONTRACT §7).

Not a test module (the name does not match test*.py). Provides:
  - paths (payload / prompts / replay corpus) and the corpus loader
  - the text normalisation used by the parity tests
  - seeded random chunking of a raw model output
  - `pending_on(agent)`: marks a test (or class) as waiting for another agent's work;
    replay/run_all.py reads the mark to group failures
  - `extract_webui()`: the AST extraction technique of replay/rebuild_prompt_buildf.py,
    generalised so it does not depend on a fixed function list (the brain agent is
    renaming helpers): every undecorated top-level function, every import that succeeds
    offline, and every top-level assignment whose value only calls a small allow-list of
    side-effect-free constructors.
"""
from __future__ import annotations

import ast
import json
import random
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "payload"
REPLAY = ROOT / "replay"
CORPUS = REPLAY / "corpus"

if PAYLOAD.is_dir():
    # legacy build-workspace layout: one payload/ dir with brain + speech files merged
    PROMPTS = ROOT / "prompts"
    if str(PAYLOAD) not in sys.path:
        sys.path.insert(0, str(PAYLOAD))
else:
    # repo layout: brain/ (webui + templates) and speech/ (api + parsers); po_contract
    # is installed identically in both, so the duplicate module name is harmless.
    PROMPTS = ROOT / "brain"
    for _p in (ROOT / "speech", ROOT / "brain"):
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
    PAYLOAD = ROOT / "brain"

# ------------------------------------------------------------------ pending marks
# Agent names as used in CONTRACT §9.
AGENTS = ("templates", "brain", "speech", "integrator")


def pending_on(*agents):
    """Decorator for a TestCase class or method: the assertion depends on work owned by
    `agents` (CONTRACT §9). Purely informational; it never skips or weakens a test."""
    for a in agents:
        if a not in AGENTS:
            raise ValueError(f"unknown agent {a!r}")

    def mark(obj):
        obj.pending_on = tuple(agents)
        return obj
    return mark


# ------------------------------------------------------------------------ corpus
def load_corpus(phase: str) -> list:
    path = CORPUS / f"{phase}.jsonl"
    if not path.exists():
        import unittest
        raise unittest.SkipTest(f"replay corpus not shipped in this repo ({path})")
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------- normalisation
# Any 【...】 tag (the nine sound effects, copied placeholders, and the control markers).
SFX_ANY_RE = re.compile(r"【\s*[^】\n]{1,24}\s*】")
_LATIN = r"A-Za-z0-9À-ɏ"
_INSIGNIFICANT_SPACE_RE = re.compile(rf"(?<=[^{_LATIN}]) | (?=[^{_LATIN}])")


def norm_speech(text: str) -> str:
    """Comparison form of spoken text: 【...】 tags removed, whitespace collapsed, and a
    space kept only between two Latin letters/digits (where it separates words). Spaces
    next to CJK text or punctuation carry no meaning for TTS, and the two sides legitimately
    differ there (strip_sfx() leaves a space where a tag was; a streaming extractor
    that drops a placeholder leaves none)."""
    t = SFX_ANY_RE.sub("", text or "")
    t = " ".join(t.split())
    return _INSIGNIFICANT_SPACE_RE.sub("", t)


def display_speech(text: str) -> str:
    """Readable form for assertion messages: tags removed, whitespace collapsed."""
    return " ".join(SFX_ANY_RE.sub(" ", text or "").split())


def collapse_runs(seq):
    out = []
    for x in seq:
        if not out or out[-1] != x:
            out.append(x)
    return out


def random_chunks(raw: str, seed: int, lo: int = 1, hi: int = 40) -> list:
    rng = random.Random(seed)
    out, i = [], 0
    while i < len(raw):
        n = rng.randint(lo, hi)
        out.append(raw[i:i + n])
        i += n
    return out


def short(raw: str, limit: int = 600) -> str:
    raw = raw or ""
    return raw if len(raw) <= limit else raw[:limit] + f"…(+{len(raw) - limit} chars)"


# --------------------------------------------------------------- webui extraction
_ALLOWED_CALL_NAMES = {"ZoneInfo", "Path", "frozenset", "set", "dict", "list", "tuple", "str", "int",
                       "float", "bool", "len", "sorted", "timedelta", "deque", "defaultdict", "OrderedDict"}
_ALLOWED_CALL_ATTRS = {("re", "compile"), ("threading", "Lock"), ("threading", "RLock"),
                       ("threading", "Event"), ("threading", "Condition"), ("os", "getenv"),
                       ("_time", "monotonic"), ("time", "monotonic"), ("time", "time"),
                       ("str", "maketrans"), ("dict", "fromkeys"), ("re", "escape")}


def _call_allowed(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id in _ALLOWED_CALL_NAMES
    if isinstance(f, ast.Attribute):
        if isinstance(f.value, ast.Constant):          # "".join(...) / "x".format(...)
            return True
        if isinstance(f.value, ast.Name) and (f.value.id, f.attr) in _ALLOWED_CALL_ATTRS:
            return True
        if (isinstance(f.value, ast.Attribute) and isinstance(f.value.value, ast.Name)
                and f.value.value.id == "os" and f.value.attr in ("environ", "path")
                and f.attr in ("get", "join", "dirname", "abspath", "basename")):
            return True
    return False


def _safe_value(node) -> bool:
    if node is None:
        return True
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and not _call_allowed(sub):
            return False
        if isinstance(sub, (ast.Lambda, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr)):
            return False
    return True


def _try_block_is_harmless(node: ast.Try) -> bool:
    for sub in node.body + node.orelse + node.finalbody + [s for h in node.handlers for s in h.body]:
        if isinstance(sub, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        if isinstance(sub, (ast.Assign, ast.AnnAssign)) and _safe_value(sub.value):
            continue
        return False
    return True


def safe_zoneinfo(key):
    """ZoneInfo, or a fixed UTC offset when the tz database is missing (Windows without
    the tzdata package). Only Asia/Tokyo (the brain's LOCAL_TZ, no DST) is needed."""
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        if key == "Asia/Tokyo":
            return timezone(timedelta(hours=9), "JST")
        return timezone.utc


class FrozenDatetime(datetime):
    """datetime with a fixed now(): Friday 2026-09-25 21:30 Asia/Tokyo (a weekday evening)."""
    FIXED = datetime(2026, 9, 25, 21, 30, tzinfo=safe_zoneinfo("Asia/Tokyo"))

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED.astimezone(tz) if tz else cls.FIXED.replace(tzinfo=None)


def extract_webui(path: Path = PAYLOAD / "webui.py") -> dict:
    """Namespace with webui.py's prompt-building code, no Flask/network/filesystem side
    effects. Returns the namespace plus `__skipped__` (names not defined)."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    ns: dict = {"__name__": "webui_extract", "__file__": str(path), "__builtins__": __builtins__}
    skipped = []
    for node in tree.body:
        seg = ast.get_source_segment(src, node)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            try:
                exec(compile(seg, "webui_extract", "exec"), ns)
            except Exception:
                skipped.append(seg.split("\n")[0])
            if "ZoneInfo" in ns:
                ns["ZoneInfo"] = safe_zoneinfo
        elif isinstance(node, ast.Try):
            if _try_block_is_harmless(node):
                try:
                    exec(compile(seg, "webui_extract", "exec"), ns)
                except Exception:
                    skipped.append("try-block@%d" % node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.decorator_list:
                skipped.append(node.name)
                continue
            try:
                exec(compile(seg, "webui_extract", "exec"), ns)
            except Exception:            # e.g. an annotation naming an absent SDK type
                skipped.append(node.name)
        elif isinstance(node, ast.ClassDef):
            skipped.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if _safe_value(node.value):
                try:
                    exec(compile(seg, "webui_extract", "exec"), ns)
                except Exception:
                    skipped.append("assign@%d" % node.lineno)
            else:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                skipped.extend(getattr(t, "id", "?") for t in targets)
    ns["__skipped__"] = skipped
    return ns


def prepare_prompt_namespace(ns: dict) -> dict:
    """Common offline overrides: absolute template/scene paths, empty caches, frozen clock,
    fresh locks and state, silent print."""
    ns["print"] = lambda *a, **k: None
    ns["datetime"] = FrozenDatetime
    # the brain's clock zone, pinned to the frozen clock's: webui loads LOCAL_TZ from build_f.json
    # (a call, which the extractor skips), and the tests must not follow the owner's configured zone
    ns["LOCAL_TZ"] = FrozenDatetime.FIXED.tzinfo
    for key, name in (("TEMPLATE_FILE", "例子.txt"), ("SURFACE_TEMPLATE_FILE", "表层.txt"),
                      ("THINKING_TEMPLATE_FILE", "思考.txt"), ("SCENES_FILE", "scenes.json")):
        ns[key] = str(PROMPTS / name)
    for cache in ("_template_cache", "_surface_template_cache", "_thinking_template_cache",
                  "_scenes_cache", "_memory_template_cache"):
        ns[cache] = None
    ns["_think_state_lock"] = threading.Lock()
    ns["_think_lock"] = threading.RLock()
    ns["_hist_lock"] = threading.RLock()
    ns["_pending_think"] = None
    ns["THINKING_LOG"] = []
    ns["CONVERSATION_HISTORY"] = []
    ns.setdefault("CONSOLIDATED_MEMORIES", [])
    ns.setdefault("_COMPLETED_CPS", set())
    return ns
