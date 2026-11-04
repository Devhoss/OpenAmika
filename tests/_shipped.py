"""What this checkout ships: a tracked file as git's index holds it.

Onboarding and the pages change some tracked files on purpose: the first-run wizard writes the person's name, city and
her clock into config/build_f.json, Setup → Brain writes their model choice into config/llm_roles.json. The tests that
guard what ships read the shipped copy, so the suite passes on an install in use as it does in CI (where the two are
the same). Without git (a download without .git) the working tree's copy is all there is.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def shipped_bytes(rel: str) -> bytes:
    """The committed (staged) bytes of ROOT/rel, else the file in the working tree."""
    try:
        p = subprocess.run(["git", "-c", "core.quotepath=off", "show", f":./{rel}"], cwd=ROOT, capture_output=True,
                           timeout=60)
    except (OSError, subprocess.SubprocessError):
        p = None
    if p is not None and p.returncode == 0:
        return p.stdout
    return (ROOT / rel).read_bytes()
