#!/usr/bin/env python3
# Copyright (c) 2026 MekaHime Inc.
# SPDX-License-Identifier: MIT
"""Install Amika's voice (the OpenAmika voice server, a MekaHime build of GPT-SoVITS) from public sources.

    python scripts/setup_tts.py                      # NVIDIA GPU on Windows / Linux (CUDA 12.8 torch)
    python scripts/setup_tts.py --cpu                # no GPU: CPU torch (slow, for testing)
    python scripts/setup_tts.py --accept-voice-license   # unattended: accept Amika's voice license up front
    python scripts/setup_tts.py --skip-voice         # everything except the voice download
    python scripts/setup_tts.py --skip-avatar        # everything except her Live2D model
    python scripts/setup_tts.py --self-test          # also start the server once and synthesize a line

What it builds in vendor/tts-engine/ (everything but main.py and the docs is git-ignored):

    .venv/                  Python 3.11 venv (3.12 works, untested): torch + torchaudio 2.9.1 (cu128 / cpu) +
                            requirements-tts.txt
    GPT-SoVITS/             upstream RVC-Boss/GPT-SoVITS at a pinned commit (MIT), with its pretrained models
                            (HuBERT, RoBERTa, ERes2NetV2 speaker model, G2PW, fastText language ID) and the
                            Japanese user dictionary its front-end builds on first use
    nltk_data/              English POS tagger + CMUdict for the English front-end
    ffmpeg/                 ffmpeg + ffprobe for Opus output (optional: WAV works without it)
    Speakers/MekaHime_Amika/
                            Amika's voice from the Hugging Face repo MekaHime/MekaHime_Amika (personal-use
                            license, accepted when the script asks; HF_TOKEN is read if set, for a private copy)

It also puts Amika's Live2D model (live2d/Amika in the same repository, same license) into avatar/models/Amika/,
where the Use page finds it (--skip-avatar leaves the stage empty: "No Live2D model yet"). At the end it checks
that the voice has a clip for every tag OpenAmika's voice adapter (speech/voice_adapter.py) can send, and that
settings.json starts this server (services.tts) with that voice (tts_character).

Needs Python 3.11 or 3.12 (scipy, onnxruntime and matplotlib at the pinned versions have no wheels for 3.10, and
upstream's numpy<2 has none for 3.13); Linux with glibc 2.28 or newer; macOS 14 or newer on Apple Silicon.

Integrity: every model and data file, the voice weights (at a pinned revision of the voice repository) and the
Windows ffmpeg build are checked against a size and SHA-256 pinned below; a download that does not match fails at
once instead of being retried. GPT-SoVITS is pinned by commit: git checks the objects it fetches. Without git, the
zip GitHub serves for that commit is checked before unpacking against a pinned SHA-256 over all 282 of its files
(see tree_digest), and the unpacked GPT_SoVITS/TTS_infer_pack/TTS.py against its own pinned SHA-256, again on
every re-run (for a git checkout too). The Linux ffmpeg build comes from a rolling release and is checked against
the SHA-256 GitHub lists for that file, and Python packages come from PyPI / the PyTorch index at pinned versions.
Re-running skips what is already present and verified. A failed step is reported and the steps that do not depend
on it still run; the summary at the end lists every step, and the exit code is non-zero if any of them failed.
Only the standard library is used here; packages are installed into the venv this script creates.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
SOURCE_ENGINE = ROOT / "vendor" / "tts-engine"
ENGINE_FILES = ("main.py", "requirements-tts.txt", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
UA = {"User-Agent": "OpenAmika-voice-setup/1.0"}

# ---- pins -------------------------------------------------------------------------------------
GPT_SOVITS_GIT = "https://github.com/RVC-Boss/GPT-SoVITS.git"
GPT_SOVITS_ZIP = "https://codeload.github.com/RVC-Boss/GPT-SoVITS/zip/{commit}"
GPT_SOVITS_COMMIT = "b4570b876f5c086819ba84e3215472733c6a7b70"   # main @ 2026-10-19, v2ProPlus + TTS_infer_pack
# Checks for the zip fallback (no git). Both values were computed from GitHub's zip of the commit and from a git
# checkout of it (core.autocrlf=false), which agree:
#   the SHA-256 of the lines "<sha256 of file>  <path>\n" (sorted by path) over the 282 files (see tree_digest), and
#   the SHA-256 of GPT_SoVITS/TTS_infer_pack/TTS.py, the module main.py imports (raw.githubusercontent.com agrees).
GPT_SOVITS_TREE_SHA256 = "6f924a174f64f4437aa4e3e754657bce6c8e7634ea492e68459536c77ae2ec34"
GPT_SOVITS_TTS_PY = ("GPT_SoVITS/TTS_infer_pack/TTS.py", "6a80acef239829a5af42916bb1f38598d9fd3ffc49d939601d3153a1f1484fd0")
# tracked files upstream rewrites by itself while it runs (caches and its default config); not local edits
GPT_SOVITS_SELF_WRITTEN = {"GPT_SoVITS/text/g2pw/polyphonic.md5", "GPT_SoVITS/text/g2pw/polyphonic.pickle",
                           "GPT_SoVITS/configs/tts_infer.yaml"}

TORCH_VERSION = "2.9.1"           # same torch/torchaudio pair the previous engine ran on an RTX 5070 (CUDA 12.8)
TORCH_INDEX = {"cuda": "https://download.pytorch.org/whl/cu128", "cpu": "https://download.pytorch.org/whl/cpu"}
TORCH_LOCAL = {"cuda": "+cu128", "cpu": "+cpu"}
# scipy 1.17.1, onnxruntime 1.31.0 and matplotlib 3.11.2 need 3.11+; numpy<2 (upstream pin) has no wheels for 3.13
PY_MIN, PY_MAX, PY_TESTED = (3, 11), (3, 12), (3, 11)
GLIBC_MIN = (2, 28)               # torch 2.9.1, onnxruntime 1.31.0, soundfile 0.14.0: manylinux_2_28 wheels only
MACOS_MIN = (14, 0)               # onnxruntime 1.31.0: one macOS wheel, macosx_14_0_arm64, and no source package

HF = "https://huggingface.co"
PRETRAINED_REPO, PRETRAINED_REV = "lj1995/GPT-SoVITS", "8f8857563c040d542de108a2c18b0425abd6c9c5"
PRETRAINED = [  # path in lj1995/GPT-SoVITS == path under GPT_SoVITS/pretrained_models, size, sha256
    ("chinese-hubert-base/config.json", 1449, "c3e5060a1277e0f078cc6be9da4528a605dba6ece93018981fe2c820e5c7b103"),
    ("chinese-hubert-base/preprocessor_config.json", 212, "dcd684124d06722947939d41ea6ae58dbf10968c60a11a29f23ddc602c64a29b"),
    ("chinese-hubert-base/pytorch_model.bin", 188811417, "24164f129c66499d1346e2aa55f183250c223161ec2770c0da3d3b08cf432d3c"),
    ("chinese-roberta-wwm-ext-large/config.json", 963, "3d57de2fd7e80d0e5c8ff194f0bbb6baa10df7e43fc262a0cc71298a78b0a3e5"),
    ("chinese-roberta-wwm-ext-large/tokenizer.json", 268962, "173796956820ea27bd14f76bf28162607ff4254807e2948253eb5b46f5bb643b"),
    ("chinese-roberta-wwm-ext-large/pytorch_model.bin", 651225145, "e53a693acc59ace251d143d068096ae0d7b79e4b1b503fa84c9dcf576448c1d8"),
    ("sv/pretrained_eres2netv2w24s4ep4.ckpt", 107528697, "4f5a0bf73c61eb41b174e1bb54e7ee3c83233892be8e0af1f187024e8e581a35"),
]
G2PW_REPO, G2PW_REV = "XXXXRT/GPT-SoVITS-Pretrained", "0c47645e02a7bc3688d7b263b0042c81e3cd82cd"
G2PW_ZIP = ("G2PWModel.zip", 588856634, "46292be0374a49308069233cd5c147ae4c41806558e4781a2467a31a4d8099da")
G2PW_ONNX = ("g2pW.onnx", 635212732, "2eb3c71fd95117b2e1abef8d2d0cd78aae894bbe7f0fac105ddc9c32ce63cbd0")
LANGID = ("https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin", 131266198,
          "7e69ec5451bc261cc7844e49e4792a85d7f09c06789ec800fc4a44aec362764e")
NLTK_COMMIT = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
NLTK = [  # g2p_en checks the first and last zip at import; nltk>=3.9 pos_tag reads the *_eng tagger
    ("taggers/averaged_perceptron_tagger.zip", 2526731, "e1f13cf2532daadfd6f3bc481a49859f0b8ea6432ccdcd83e6a49a5f19008de9"),
    ("taggers/averaged_perceptron_tagger_eng.zip", 1539115, "6025f530624335c67d6547d44757b357b4e79bae030a0383e9887a92c1718f0b"),
    ("corpora/cmudict.zip", 896069, "d07cca47fd72ad32ea9d8ad1219f85301eeaf4568f8b6b73747506a71fb5afd6"),
]
FFMPEG_WIN = ("https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/ffmpeg-8.0.1-essentials_build.zip",
              106259850, "e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673")
# BtbN's static Linux builds are re-published daily under the release tag "latest"; its asset list (GitHub API) gives
# each file's size and SHA-256. The newest release branch is taken (ffmpeg-nX.Y-latest-linux64-gpl-X.Y.tar.xz).
FFMPEG_LINUX_RELEASE = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"
FFMPEG_LINUX_ASSET = re.compile(r"ffmpeg-n(\d+)\.(\d+)-latest-linux64-gpl-\1\.\2\.tar\.xz")

VOICE_REPO, VOICE_ID = "MekaHime/MekaHime_Amika", "MekaHime_Amika"
VOICE_REV = "a3934e5a83dcb0192901f69536c66b837414e822"    # 2026-11-03: v1.0, 69 emotion tags, each with its clip
VOICE_WEIGHTS = {  # at VOICE_REV: file -> (size, sha256)
    "MekaHime_Amika.ckpt": (155312594, "65534ce89de5c1d37e5731806ef0eccecb91aef0c7f5b05d8ee975700973edac"),
    "MekaHime_Amika.pth": (172764966, "651759b6c5f37004ad93159018edc658d1161b639de9c24463a8cdc8f491fe50"),
}
# OpenAmika's voice adapter: its V1_IDS are every tag it can send to the voice server (checked in check_voice)
VOICE_ADAPTER = ROOT / "speech" / "voice_adapter.py"
# Amika's Live2D model is in the same repository (same license) under live2d/Amika; it goes to avatar/models/Amika
AVATAR_ID, AVATAR_PATH = "Amika", "live2d/Amika"
AVATAR_FILES = {  # at VOICE_REV: file under live2d/Amika -> (size, sha256); banks.json is not pinned (Setup edits it)
    "AmikaV4.model3.json": (2978, "4e1783bd45f4dcb8f7a69980ad89f8574653f4e6b6673c2f0bdb6ab2c1495bee"),
    "AmikaV4.moc3": (5829824, "d398f37daed8a0d08080c769110f6b78d1ffd07a37fa97fd5ce527d47b3e6d9a"),
    "AmikaV4.8192/texture_00.png": (18229093, "3cab50e24a7c280da88ae57da86766311eb5460d1cb493508038946a8c48fd7f"),
    "AmikaV4.8192/texture_01.png": (2339604, "388f74b023d1a78be4f281b8f7afb35ef4a0c21647be01a121627cd4512d6034"),
}

ROWS: list[tuple[str, str, str]] = []   # (mark, name, detail) for the final summary


def log(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def row(mark: str, name: str, detail: str) -> None:
    ROWS.append((mark, name, detail))


class SetupError(RuntimeError):
    pass


class IntegrityError(SetupError):
    """A complete download does not match its pinned hash: downloading it again will not help."""


# ---- small helpers ------------------------------------------------------------------------------
def run(cmd: list[str], *, cwd: Optional[Path] = None, env: Optional[dict] = None, check: bool = True,
        capture: bool = False) -> subprocess.CompletedProcess:
    shown = " ".join(c if " " not in c else f'"{c}"' for c in cmd)
    if not capture:
        log(f"$ {shown}")
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, text=True, encoding="utf-8", errors="replace",
                       capture_output=capture)
    if check and p.returncode != 0:
        tail = (p.stdout or "")[-1500:] + (p.stderr or "")[-1500:] if capture else ""
        raise SetupError(f"command failed ({p.returncode}): {shown}\n{tail}".rstrip())
    return p


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Verified:
    """Remembers files already hashed (by size + mtime) so a re-run does not re-hash gigabytes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def ok(self, f: Path, size: int, sha: str) -> bool:
        if not f.is_file() or f.stat().st_size != size:
            return False
        return self.sha256(f) == sha

    def sha256(self, f: Path) -> str:
        """The file's SHA-256, hashed again only when its size or modification time changed."""
        st = f.stat()
        rec = self.data.get(str(f))
        if rec and rec.get("mtime_ns") == st.st_mtime_ns and rec.get("size") == st.st_size and rec.get("sha256"):
            return str(rec["sha256"])
        got = sha256_file(f)
        self.record(f, st.st_size, got)
        return got

    def record(self, f: Path, size: int, sha: str) -> None:
        self.data[str(f)] = {"sha256": sha, "size": size, "mtime_ns": f.stat().st_mtime_ns}
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")


RETRY_HTTP = (408, 425, 429, 500, 502, 503, 504)   # worth asking again; any other HTTP error fails at once


def remote_size(r: Any, have: int) -> Optional[int]:
    """Full size of the file behind a response: the total of a 206's Content-Range, else Content-Length."""
    total = (r.headers.get("Content-Range") or "").rpartition("/")[2].strip()
    if r.status == 206 and total.isdigit():
        return int(total)
    length = (r.headers.get("Content-Length") or "").strip()
    return int(length) + (have if r.status == 206 else 0) if length.isdigit() else None


def fetch(url: str, dest: Path, size: Optional[int], sha: Optional[str], ver: Optional[Verified] = None,
          headers: Optional[dict] = None, retries: int = 4, label: Optional[str] = None, resume: bool = True) -> str:
    """Download url -> dest through a resumable .part file, then check size and SHA-256. Returns 'present' (already
    there and verified) or 'downloaded'.

    Network errors, timeouts, short reads and HTTP 408/425/429/5xx are retried, up to `retries` tries in all, each
    resuming the .part file. A mismatch is not retried: a file that does not have its pinned size (the server
    announces another size, or more bytes arrive) or whose complete download has the wrong SHA-256 is deleted and
    raises IntegrityError at once, since the same address would serve the same wrong file again. The one exception
    is a download that continued a .part left by an earlier run, which may have been stale: it gets one fresh start.
    resume=False (for a file whose bytes may change between requests: a release listing, an archive GitHub builds
    on request) never continues a .part, not even within one call."""
    if sha and size and ver is not None and ver.ok(dest, size, sha):
        return "present"
    if label or (size and size > 50e6):
        log(f"downloading {label or dest.name}" + (f" ({size / 1e6:,.0f} MB)" if size else ""))
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    fresh_start_left = True
    attempt = 0
    while True:
        attempt += 1
        try:
            if not resume:                       # every try starts from the first byte
                part.unlink(missing_ok=True)
            have = part.stat().st_size if part.exists() else 0
            if size and have > size:
                part.unlink()
                have = 0
            resumed = have > 0
            if not (size and have == size):      # a complete .part from an interrupted run needs no request
                req = urllib.request.Request(url, headers={**UA, **(headers or {})})
                if have:
                    req.add_header("Range", f"bytes={have}-")
                with urllib.request.urlopen(req, timeout=60) as r:
                    if have and r.status != 206:     # server ignored the range: start over
                        have, resumed = 0, False
                    announced = remote_size(r, have)
                    if size and announced is not None and announced != size:
                        part.unlink(missing_ok=True)
                        raise IntegrityError(f"{dest.name}: {url} serves {announced:,} bytes, the pinned file has "
                                             f"{size:,}. The file at that address is not the pinned one.")
                    total = size or announced or 0
                    t_last = time.time()
                    done = have
                    with open(part, "ab" if have else "wb") as f:
                        while True:
                            block = r.read(1 << 20)
                            if not block:
                                break
                            f.write(block)
                            done += len(block)
                            if time.time() - t_last > 10:
                                pct = f" {done * 100 // total}%" if total else ""
                                log(f"  {dest.name}: {done / 1e6:,.0f} / {total / 1e6:,.0f} MB{pct}")
                                t_last = time.time()
            got_size = part.stat().st_size
            if size and got_size < size:         # the connection ended early: retried, resuming
                raise SetupError(f"{dest.name}: got {got_size:,} of {size:,} bytes")
            if size and got_size > size:
                part.unlink()
                raise IntegrityError(f"{dest.name}: got {got_size:,} bytes from {url}, the pinned file has {size:,}")
            if sha:
                got = sha256_file(part)
                if got != sha:
                    part.unlink()
                    if resumed and fresh_start_left:
                        fresh_start_left = False
                        attempt -= 1             # a fresh start is not a failed try
                        log(f"  {dest.name}: SHA-256 mismatch after continuing an earlier partial download; "
                            "downloading it once more from the start")
                        continue
                    raise IntegrityError(f"{dest.name}: SHA-256 mismatch (got {got[:16]}…, expected {sha[:16]}…) "
                                         f"for {url}. The file at that address is not the pinned one.")
            os.replace(part, dest)
            if sha and size and ver is not None:
                ver.record(dest, size, sha)      # just hashed above
            return "downloaded"
        except IntegrityError:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 416:                    # a stale .part the server cannot continue: start over
                part.unlink(missing_ok=True)
            elif e.code not in RETRY_HTTP:
                raise SetupError(f"{url}: HTTP {e.code} {e.reason}") from e
            err: Exception = e
        except (urllib.error.URLError, http.client.HTTPException, OSError, SetupError) as e:
            if isinstance(e, OSError) and e.errno == errno.ENOSPC:
                raise SetupError(f"{dest.parent}: the disk is full") from e
            err = e
        if attempt >= retries:
            raise SetupError(f"download failed after {retries} tries: {url}: {err}") from err
        log(f"  {dest.name}: {err} (retrying in {3 * attempt} s)")
        time.sleep(3 * attempt)


def safe_members(names: list[str], dest: Path) -> None:
    root = dest.resolve()
    for n in names:
        target = (dest / n).resolve()
        if root != target and root not in target.parents:
            raise SetupError(f"archive member escapes the target folder: {n}")


def disk_free_gb(path: Path) -> Optional[float]:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / 1e9
    except OSError:
        return None


# ---- python + venv ------------------------------------------------------------------------------
def py_version(cmd: list[str]) -> Optional[tuple[int, int, int]]:
    try:
        p = subprocess.run(cmd + ["-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                           capture_output=True, text=True, timeout=30)
        if p.returncode == 0:
            return tuple(int(x) for x in p.stdout.strip().split("."))  # type: ignore[return-value]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def ok_version(v: Optional[tuple]) -> bool:
    return v is not None and PY_MIN <= v[:2] <= PY_MAX


PY_RANGE = f"{PY_MIN[0]}.{PY_MIN[1]} or {PY_MAX[0]}.{PY_MAX[1]}"


def find_base_python(explicit: Optional[str]) -> tuple[list[str], tuple[int, int, int]]:
    """The interpreter the venv is made from: --python if given, else the first Python 3.11 found (this one, then
    `py -3.11` / python3.11), else the first 3.12 (it should work but has not been tested)."""
    if explicit:
        v = py_version([explicit])
        if not ok_version(v):
            raise SetupError(f"--python {explicit} is {'Python ' + '.'.join(map(str, v)) if v else 'not a working Python'}; "
                             f"the voice engine needs Python {PY_RANGE}")
        return [explicit], v  # type: ignore[return-value]
    cands: list[list[str]] = [[sys.executable]]
    if IS_WIN:
        cands += [["py", "-3.11"], ["py", "-3.12"]]
    cands += [[n] for n in ("python3.11", "python3.12") if shutil.which(n)]
    usable: list[tuple[list[str], tuple[int, int, int]]] = []
    for c in cands:
        v = py_version(c)
        if ok_version(v):
            if v[:2] == PY_TESTED:  # type: ignore[index]
                return c, v  # type: ignore[return-value]
            usable.append((c, v))  # type: ignore[arg-type]
    if usable:
        c, v = usable[0]
        log(f"no Python 3.11 found; using {' '.join(c)} (Python {v[0]}.{v[1]}: it should work, but only 3.11 is tested)")
        return c, v
    raise SetupError(f"the voice engine needs Python {PY_RANGE} (3.11 recommended): scipy, onnxruntime and matplotlib at "
                     "the pinned versions need 3.11+, and upstream pins numpy<2, which has no wheels for 3.13+. Install "
                     "Python 3.11 (python.org; Debian/Ubuntu: python3.11 and python3.11-venv) and re-run, or pass "
                     "--python <path>.")


def venv_python(engine: Path) -> Path:
    return engine / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")


def pip_works(vpy: Path) -> bool:
    try:
        return subprocess.run([str(vpy), "-m", "pip", "--version"], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_venv(engine: Path, explicit_python: Optional[str]) -> Path:
    venv = engine / ".venv"
    vpy = venv_python(engine)
    clear = False
    if vpy.exists():
        v = py_version([str(vpy)])
        if v is not None and not ok_version(v):
            raise SetupError(f"{venv} uses Python {'.'.join(map(str, v))}; the voice engine needs {PY_RANGE}. "
                             "Delete that folder and re-run")
        if v is not None and pip_works(vpy):
            row("OK ", "venv", f"{vpy} (Python {'.'.join(map(str, v))}, existing)")
            return vpy
        # a venv an earlier run did not finish (Debian/Ubuntu without pythonX.Y-venv leaves one without pip)
        log(f"{venv} is incomplete ({'its Python does not start' if v is None else 'it has no pip'}); creating it again")
        clear = True
    elif venv.is_dir() and any(venv.iterdir()):
        clear = True
    base, v = find_base_python(explicit_python)
    log(f"creating {venv} with Python {'.'.join(map(str, v))} ({' '.join(base)})")
    try:
        run(base + ["-m", "venv", *(["--clear"] if clear else []), str(venv)])
    except SetupError as e:
        hint = f" (Debian/Ubuntu: sudo apt install python{v[0]}.{v[1]}-venv)" if IS_LINUX else ""
        raise SetupError(f"{e}{hint}") from e
    if not pip_works(vpy):
        raise SetupError(f"{venv} was created without pip" +
                         (f" (Debian/Ubuntu: sudo apt install python{v[0]}.{v[1]}-venv)" if IS_LINUX else ""))
    run([str(vpy), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-U", "pip", "wheel"])
    row("OK ", "venv", f"{vpy} (Python {'.'.join(map(str, v))}, {'created again' if clear else 'created'})")
    return vpy


def pip(vpy: Path, *args: str) -> None:
    extra = [] if sys.stdout.isatty() else ["--progress-bar", "off"]
    run([str(vpy), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--timeout", "60",
         "--retries", "5", *extra, *args])


def has_nvidia_gpu() -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe and IS_WIN:
        cand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        exe = str(cand) if cand.exists() else None
    if not exe:
        return False
    try:
        p = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=30)
        return p.returncode == 0 and "GPU" in p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def pick_device(args: argparse.Namespace) -> str:
    if args.cpu or args.device == "cpu":
        return "cpu"
    if IS_MAC:
        return "mac"            # PyPI torch: CPU + Apple MPS
    if args.device == "cuda":
        return "cuda"
    return "cuda" if has_nvidia_gpu() else "cpu"


def sysctl(name: str) -> str:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:2])


def check_platform(vpy: Path) -> None:
    """Fail early, with the reason, where the pinned packages have no wheels (pip would fail on a source build)."""
    text_only = "OpenAmika itself still runs without her voice server; she then answers in text only."
    if IS_LINUX:
        lib, ver = platform.libc_ver()
        if lib == "glibc" and ver and version_tuple(ver) < GLIBC_MIN:
            raise SetupError(f"glibc {ver} is too old: torch 2.9.1, onnxruntime 1.31.0 and soundfile 0.14.0 have Linux "
                             f"wheels for glibc {GLIBC_MIN[0]}.{GLIBC_MIN[1]}+ only (Ubuntu 20.04+, Debian 10+, RHEL / "
                             f"Rocky 8+). {text_only}")
    if IS_MAC:
        p = subprocess.run([str(vpy), "-c", "import platform; print(platform.machine())"], capture_output=True, text=True)
        arch = p.stdout.strip() or platform.machine()
        if arch != "arm64":
            if sysctl("hw.optional.arm64") == "1":   # an x86_64 Python under Rosetta on an Apple Silicon Mac
                raise SetupError(f"{vpy} runs as {arch} under Rosetta, and PyTorch / onnxruntime need a native arm64 "
                                 "Python on Apple Silicon. Install Python 3.11 for arm64 (python.org's macOS installer, "
                                 f"or Homebrew in /opt/homebrew), delete {vpy.parent.parent} and re-run this script with "
                                 "that Python from a terminal that is not running under Rosetta.")
            raise SetupError("PyTorch 2.9 has no wheels for Intel Macs; the voice engine needs Windows/Linux "
                             f"(NVIDIA GPU) or an Apple Silicon Mac. {text_only}")
        mac = sysctl("kern.osproductversion") or platform.mac_ver()[0]
        if mac and version_tuple(mac) < MACOS_MIN:
            raise SetupError(f"macOS {mac}: the voice engine needs macOS {MACOS_MIN[0]} or newer (onnxruntime 1.31.0, "
                             f"used by the Chinese front-end, ships a macOS {MACOS_MIN[0]}+ wheel only). {text_only}")


def ensure_torch(vpy: Path, device: str, reinstall: bool) -> Path:
    check_platform(vpy)
    local = "" if device == "mac" else TORCH_LOCAL[device]
    want = f"{TORCH_VERSION}{local}"
    probe = ("import json, torch, torchaudio; print(json.dumps({'torch': torch.__version__, "
             "'torchaudio': torchaudio.__version__, 'cuda': torch.version.cuda}))")
    p = subprocess.run([str(vpy), "-c", probe], capture_output=True, text=True)
    cur = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else {}
    good = cur.get("torch", "").split("+")[0] == TORCH_VERSION and cur.get("torchaudio", "").split("+")[0] == TORCH_VERSION
    if device == "cuda":
        good = good and str(cur.get("cuda") or "").startswith("12.8")
    elif device == "cpu":
        good = good and not cur.get("cuda")
    if good and not reinstall:
        row("OK ", "torch", f"torch {cur['torch']} / torchaudio {cur['torchaudio']} (CUDA {cur.get('cuda') or '-'}, existing)")
    else:
        log(f"installing torch {want} + torchaudio {want} ({'CUDA 12.8' if device == 'cuda' else device})")
        idx = [] if device == "mac" else ["--index-url", TORCH_INDEX[device]]
        pip(vpy, *idx, f"torch=={want}", f"torchaudio=={want}")
        row("OK ", "torch", f"torch {want} / torchaudio {want} (installed)")
    cons = vpy.parent.parent / "openamika-constraints.txt"
    cons.write_text(f"torch=={want}\ntorchaudio=={want}\n", encoding="utf-8")
    return cons


def ensure_requirements(vpy: Path, engine: Path, cons: Path, reinstall: bool) -> None:
    req = engine / "requirements-tts.txt"
    stamp = vpy.parent.parent / "openamika-requirements.sha256"
    digest = hashlib.sha256(req.read_bytes() + cons.read_bytes()).hexdigest()
    if not reinstall and stamp.is_file() and stamp.read_text().strip() == digest:
        row("OK ", "packages", f"{req.name} (unchanged since last install)")
        return
    log(f"installing {req.name} (the torch pair stays pinned by {cons.name})")
    pip(vpy, "-r", str(req), "-c", str(cons))
    stamp.write_text(digest, encoding="utf-8")
    row("OK ", "packages", f"{req.name} (installed)")


# ---- upstream checkout + models -----------------------------------------------------------------
def git(*args: str, cwd: Path, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", "advice.detachedHead=false", *args],
               cwd=cwd, capture=capture, check=check)


def tree_digest(z: zipfile.ZipFile) -> tuple[str, int]:
    """SHA-256 over the lines "<sha256 of file>  <path>\\n", sorted by path, of every file in a GitHub zip (its top
    folder stripped): the same value a git checkout of that commit gives over its tracked files."""
    files: dict[str, str] = {}
    for info in z.infolist():
        if info.is_dir():
            continue
        rel = info.filename.split("/", 1)[1] if "/" in info.filename else info.filename
        files[rel] = hashlib.sha256(z.read(info)).hexdigest()
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(f"{files[rel]}  {rel}\n".encode("utf-8"))
    return h.hexdigest(), len(files)


def check_tts_py(root: Path) -> None:
    """The extracted tree has the pinned commit's GPT_SoVITS/TTS_infer_pack/TTS.py (zip installs, which git cannot vouch for)."""
    rel, want = GPT_SOVITS_TTS_PY
    f = root / rel
    got = sha256_file(f) if f.is_file() else "missing"
    if got != want:
        raise IntegrityError(f"{f}: SHA-256 {got[:16]}… is not the one of GPT-SoVITS {GPT_SOVITS_COMMIT[:10]} "
                             f"({want[:16]}…)")


def git_pin(gsv: Path) -> None:
    """Shallow-fetch the pinned commit into gsv (an initialised repo, possibly from an interrupted run)."""
    if git("remote", "get-url", "origin", cwd=gsv, capture=True, check=False).returncode != 0:
        git("remote", "add", "origin", GPT_SOVITS_GIT, cwd=gsv)
    git("fetch", "--depth", "1", "origin", GPT_SOVITS_COMMIT, cwd=gsv)
    git("checkout", "--force", GPT_SOVITS_COMMIT, cwd=gsv)


def read_git_head(gsv: Path) -> str:
    """The commit a checkout is on, read from .git directly (git itself may not be on PATH): a detached HEAD or a
    branch ref, loose or packed. Same logic as main.py's upstream_commit."""
    git_dir = gsv / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            if (git_dir / ref).is_file():
                head = (git_dir / ref).read_text(encoding="utf-8").strip()
            elif (git_dir / "packed-refs").is_file():
                for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        head = line.split(" ", 1)[0]
                        break
        return head if re.fullmatch(r"[0-9a-f]{40}", head) else ""
    except OSError:
        return ""


def checkout_edits(gsv: Path, has_git: bool) -> str:
    """Check an existing checkout: TTS.py (the module main.py builds on) must be the pinned one; other tracked files
    with local edits are reported. Returns a note for the summary."""
    restore = f'`git -C "{gsv}" checkout -- .` restores it' if has_git else f"move {gsv} away and re-run"
    try:
        check_tts_py(gsv)
    except IntegrityError as e:
        raise IntegrityError(f"{e}: it was edited; {restore}") from e
    if not has_git:
        return "git not on PATH: commit read from .git, TTS.py verified"
    p = git("status", "--porcelain", "--untracked-files=no", cwd=gsv, capture=True, check=False)
    edited = [ln[3:].strip().strip('"') for ln in (p.stdout or "").splitlines() if len(ln) > 3] if p.returncode == 0 else []
    edited = [f for f in edited if f not in GPT_SOVITS_SELF_WRITTEN]
    if not edited:
        return "TTS.py verified, no local edits"
    log(f"WARNING: {len(edited)} tracked file(s) in {gsv} differ from commit {GPT_SOVITS_COMMIT[:10]}: "
        f"{', '.join(edited[:6])}{' ...' if len(edited) > 6 else ''}. `git -C \"{gsv}\" checkout -- .` restores them.")
    return f"TTS.py verified; {len(edited)} other file(s) edited locally, see the warning above"


def ensure_checkout(engine: Path) -> Path:
    gsv = engine / "GPT-SoVITS"
    marker = gsv / ".openamika-commit"
    has_git = shutil.which("git") is not None
    if (gsv / ".git").exists():
        if has_git:
            p = git("rev-parse", "HEAD", cwd=gsv, capture=True, check=False)
            head = p.stdout.strip() if p.returncode == 0 else ""
        else:
            head = read_git_head(gsv)
        if head == GPT_SOVITS_COMMIT:
            row("OK ", "GPT-SoVITS", f"{gsv} @ {head[:10]} (existing; {checkout_edits(gsv, has_git)})")
            return gsv
        if not has_git:
            raise SetupError(f"{gsv} is a git checkout at {head[:10] or 'an unknown commit'}, not the pinned "
                             f"{GPT_SOVITS_COMMIT[:10]}, and git is not on PATH: install git and re-run, or move the "
                             "folder away (the pinned commit is then downloaded as a zip)")
        log(f"GPT-SoVITS is at {head[:10] or 'nothing yet'}; fetching the pinned {GPT_SOVITS_COMMIT[:10]}")
        git_pin(gsv)
        row("OK ", "GPT-SoVITS", f"{gsv} @ {GPT_SOVITS_COMMIT[:10]} (updated)")
        return gsv
    if gsv.is_dir() and marker.is_file():
        if marker.read_text().strip() == GPT_SOVITS_COMMIT:
            try:
                check_tts_py(gsv)
            except IntegrityError as e:
                raise IntegrityError(f"{e}; move {gsv} away and re-run") from e
            row("OK ", "GPT-SoVITS", f"{gsv} @ {GPT_SOVITS_COMMIT[:10]} (existing, from zip; TTS.py verified)")
            return gsv
        raise SetupError(f"{gsv} is a different GPT-SoVITS snapshot; move it away and re-run")
    if gsv.exists() and any(gsv.iterdir()):
        raise SetupError(f"{gsv} exists but is not a pinned GPT-SoVITS checkout; move it away and re-run")
    if has_git:
        gsv.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=gsv)
        git_pin(gsv)
        how = "git, shallow"
    else:
        log("git not found; downloading the pinned commit as a zip from GitHub")
        downloads = engine / "downloads"
        tmp = downloads / f"GPT-SoVITS-{GPT_SOVITS_COMMIT}.zip"
        src = downloads / f"GPT-SoVITS-{GPT_SOVITS_COMMIT}"
        # GitHub builds the archive on request and does not promise stable bytes, so never continue an old .part;
        # the tree digest below checks the files inside instead
        fetch(GPT_SOVITS_ZIP.format(commit=GPT_SOVITS_COMMIT), tmp, None, None, resume=False)
        try:
            with zipfile.ZipFile(tmp) as z:
                digest, count = tree_digest(z)
                if digest != GPT_SOVITS_TREE_SHA256:
                    raise IntegrityError(f"{tmp.name}: its {count} files do not match the pinned GPT-SoVITS "
                                         f"{GPT_SOVITS_COMMIT[:10]} (tree SHA-256 {digest[:16]}…, expected "
                                         f"{GPT_SOVITS_TREE_SHA256[:16]}…); install git and re-run")
                safe_members(z.namelist(), downloads)
                shutil.rmtree(src, ignore_errors=True)    # left over from an interrupted run
                z.extractall(downloads)
            check_tts_py(src)                 # the unpacked tree, before it is moved into place
        except (zipfile.BadZipFile, IntegrityError) as e:
            tmp.unlink(missing_ok=True)      # a re-run downloads it again
            shutil.rmtree(src, ignore_errors=True)
            if isinstance(e, IntegrityError):
                raise
            raise SetupError(f"{tmp.name} is not a valid zip ({e}); re-run to download it again") from e
        if gsv.exists():
            shutil.copytree(src, gsv, dirs_exist_ok=True)
            shutil.rmtree(src)
        else:
            shutil.move(str(src), str(gsv))
        tmp.unlink(missing_ok=True)
        marker.write_text(GPT_SOVITS_COMMIT, encoding="utf-8")
        how = "zip"
    row("OK ", "GPT-SoVITS", f"{gsv} @ {GPT_SOVITS_COMMIT[:10]} (downloaded, {how})")
    return gsv


def ensure_pretrained(gsv: Path, ver: Verified) -> None:
    pm = gsv / "GPT_SoVITS" / "pretrained_models"
    states = []
    for rel, size, sha in PRETRAINED:
        url = f"{HF}/{PRETRAINED_REPO}/resolve/{PRETRAINED_REV}/{rel}"
        states.append(fetch(url, pm / rel, size, sha, ver, label=rel if size > 50e6 else None))
    total = sum(s for _, s, _ in PRETRAINED) / 1e9
    row("OK ", "pretrained models", f"HuBERT, RoBERTa, ERes2NetV2 sv ({total:.1f} GB, "
        f"{'downloaded' if 'downloaded' in states else 'verified'}) from {PRETRAINED_REPO}@{PRETRAINED_REV[:7]}")

    lid = pm / "fast_langdetect" / "lid.176.bin"
    st = fetch(LANGID[0], lid, LANGID[1], LANGID[2], ver)
    row("OK ", "language ID", f"fastText lid.176.bin ({st})")


def ensure_g2pw(engine: Path, gsv: Path, ver: Verified, keep: bool) -> None:
    text_dir = gsv / "GPT_SoVITS" / "text"
    onnx = text_dir / "G2PWModel" / G2PW_ONNX[0]
    needed = ["config.py", "char_bopomofo_dict.json", "bopomofo_to_pinyin_wo_tune_dict.json",
              "POLYPHONIC_CHARS.txt", "MONOPHONIC_CHARS.txt"]
    if ver.ok(onnx, G2PW_ONNX[1], G2PW_ONNX[2]) and all((onnx.parent / n).is_file() for n in needed):
        row("OK ", "G2PW", f"{onnx.parent} (verified)")
        return
    zpath = engine / "downloads" / G2PW_ZIP[0]
    fetch(f"{HF}/{G2PW_REPO}/resolve/{G2PW_REV}/{G2PW_ZIP[0]}", zpath, G2PW_ZIP[1], G2PW_ZIP[2], ver,
          label="G2PW model (zip)")
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        if not all(n.startswith("G2PWModel/") for n in names):
            raise SetupError("unexpected G2PWModel.zip layout")
        safe_members(names, text_dir)
        z.extractall(text_dir)
    if not ver.ok(onnx, G2PW_ONNX[1], G2PW_ONNX[2]):
        raise SetupError(f"{onnx} does not match the pinned SHA-256 after extraction")
    if not keep:
        zpath.unlink(missing_ok=True)
    row("OK ", "G2PW", f"{onnx.parent} (downloaded from {G2PW_REPO}@{G2PW_REV[:7]})")


def ensure_nltk(engine: Path, ver: Verified) -> None:
    base = engine / "nltk_data"
    for rel, size, sha in NLTK:
        dest = base / rel
        fetch(f"https://raw.githubusercontent.com/nltk/nltk_data/{NLTK_COMMIT}/packages/{rel}", dest, size, sha, ver)
        unpacked = dest.with_suffix("")
        done = unpacked / ".openamika-unpacked"      # written last, so an interrupted unzip is redone
        if not (done.is_file() and done.read_text(encoding="utf-8").strip() == sha):
            with zipfile.ZipFile(dest) as z:
                names = z.namelist()
                safe_members(names, dest.parent)
                if not all(n.startswith(unpacked.name + "/") for n in names):
                    raise SetupError(f"unexpected layout of {dest.name}")
                z.extractall(dest.parent)
            done.write_text(sha, encoding="utf-8")
    row("OK ", "nltk data", f"{base} (averaged_perceptron_tagger[_eng], cmudict)")


JA_DICT = r"""
import contextlib, io, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
gsv = sys.argv[1]
sys.path[:0] = [os.path.join(gsv, "GPT_SoVITS"), gsv]
os.chdir(gsv)                     # as main.py runs it: upstream may keep a copy of the dictionary under TEMP/ here
out = io.StringIO()
with contextlib.redirect_stdout(out):
    import text.japanese as ja    # builds ja_userdic/user.dict from userdict.csv when it is missing or stale
path = getattr(ja, "USERDIC_BIN_PATH", "")
print("OK" if path and os.path.isfile(path) else "MISSING", os.path.abspath(path) if path else "-")
"""


def ensure_ja_dict(vpy: Path, gsv: Path) -> None:
    """Let upstream's Japanese front-end build its user dictionary now (21 MB, about a second, ~100 progress lines on
    the console) instead of during the server's first start. Optional: the server builds it itself if needed."""
    try:
        p = subprocess.run([str(vpy), "-I", "-c", JA_DICT, str(gsv)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        out, err, rc = p.stdout or "", p.stderr or "", p.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        out, err, rc = "", f"{type(e).__name__}: {e}", 1
    # the dictionary compiler prints its progress bar straight to stdout (C stdio); the result is the last OK/MISSING
    last = next((ln.strip() for ln in reversed(out.splitlines()) if ln.startswith(("OK ", "MISSING "))), "")
    path = Path(last[3:]) if last.startswith("OK ") else None
    if rc == 0 and path is not None and path.is_file():
        row("OK ", "Japanese dictionary", f"{path} ({path.stat().st_size / 1e6:.0f} MB)")
    else:
        why = last or (err.strip().splitlines() or ["no output"])[-1]
        row("-- ", "Japanese dictionary", f"not built ({why[:200]}); the server builds it on its first start")


# ---- ffmpeg -------------------------------------------------------------------------------------
def ffmpeg_info(exe: str) -> Optional[str]:
    try:
        p = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30,
                           errors="replace")
        v = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30, errors="replace")
        if p.returncode == 0 and v.returncode == 0:
            first = v.stdout.splitlines()[0] if v.stdout else "ffmpeg"
            return first + ("" if "libopus" in p.stdout else "  (WARNING: no libopus encoder)")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def linux_ffmpeg_asset(engine: Path) -> tuple[str, str, int, str]:
    """(file name, URL, size, SHA-256) of the newest release branch's static linux64 build in BtbN's "latest"
    release, from GitHub's asset list (one request instead of a separate checksum file)."""
    listing = engine / "downloads" / "ffmpeg-release.json"
    fetch(FFMPEG_LINUX_RELEASE, listing, None, None, headers={"Accept": "application/vnd.github+json"}, resume=False)
    try:
        assets = json.loads(listing.read_text(encoding="utf-8")).get("assets") or []
    except ValueError as e:
        raise SetupError(f"{FFMPEG_LINUX_RELEASE} did not return JSON: {e}") from e
    finally:
        listing.unlink(missing_ok=True)
    best: Optional[tuple[tuple[int, int], dict]] = None
    for a in assets:
        m = FFMPEG_LINUX_ASSET.fullmatch(str(a.get("name") or ""))
        if m and (best is None or (int(m[1]), int(m[2])) > best[0]):
            best = ((int(m[1]), int(m[2])), a)
    if best is None:
        raise SetupError(f"no ffmpeg-nX.Y-latest-linux64-gpl-X.Y.tar.xz in {FFMPEG_LINUX_RELEASE}")
    a = best[1]
    digest = str(a.get("digest") or "")
    if not digest.startswith("sha256:") or not str(a.get("size") or "").isdigit():
        raise SetupError(f"the release listing gives no SHA-256 for {a['name']}; install ffmpeg with your package "
                         "manager instead")
    return str(a["name"]), str(a["browser_download_url"]), int(a["size"]), digest.split(":", 1)[1].lower()


def download_ffmpeg(engine: Path, dest: Path, ver: Verified) -> Path:
    """Fetch and unpack the ffmpeg build for this platform (Windows or Linux x86_64) into dest; returns the archive."""
    if IS_WIN:
        url, size, sha = FFMPEG_WIN
        arc = engine / "downloads" / url.rsplit("/", 1)[1]
        fetch(url, arc, size, sha, ver, label="ffmpeg 8.0.1 essentials build (gyan.dev)")
        with zipfile.ZipFile(arc) as z:
            for n in z.namelist():
                leaf = n.rsplit("/", 1)[-1]
                if (leaf in ("ffmpeg.exe", "ffprobe.exe") and "/bin/" in n) or leaf == "LICENSE":
                    (dest / leaf).write_bytes(z.read(n))
        return arc
    arc: Optional[Path] = None
    for listing in (1, 2):
        name, url, size, sha = linux_ffmpeg_asset(engine)
        arc = engine / "downloads" / name
        log(f"ffmpeg static build {name} (BtbN, rolling release; checked against the SHA-256 GitHub lists for it)")
        try:
            fetch(url, arc, size, sha, label=name)
            break
        except IntegrityError:
            # BtbN re-publishes the release daily: between the listing and the download the file was replaced
            if listing == 2:
                raise
            log("  the build was re-published while downloading; reading the release listing again")
    assert arc is not None
    with tarfile.open(arc, "r:xz") as t:
        for m in t.getmembers():
            leaf = m.name.rsplit("/", 1)[-1]
            if m.isfile() and leaf in ("ffmpeg", "ffprobe") and "/bin/" in m.name:
                f = t.extractfile(m)
                if f is None:
                    raise SetupError(f"cannot read {m.name} from {arc.name}")
                (dest / leaf).write_bytes(f.read())
                (dest / leaf).chmod(0o755)
    return arc


def ensure_ffmpeg(engine: Path, ver: Verified, keep: bool) -> None:
    dest = engine / "ffmpeg"
    exe = dest / ("ffmpeg.exe" if IS_WIN else "ffmpeg")
    if exe.is_file() and (info := ffmpeg_info(str(exe))):
        row("OK ", "ffmpeg", f"{exe} ({info.split(' Copyright')[0]})")
        return
    system = shutil.which("ffmpeg")
    if not IS_WIN and system and (info := ffmpeg_info(system)) and "WARNING" not in info:
        row("OK ", "ffmpeg", f"{system} (system; {info.split(' Copyright')[0]})")
        return
    if not (IS_WIN or (sys.platform.startswith("linux") and platform.machine() in ("x86_64", "AMD64"))):
        hint = "brew install ffmpeg" if IS_MAC else "install ffmpeg with your package manager"
        row("-- ", "ffmpeg", f"not found: {hint} (only needed for Opus; WAV output works without it)")
        return
    dest.mkdir(parents=True, exist_ok=True)
    try:
        arc = download_ffmpeg(engine, dest, ver)
    except SetupError:
        raise
    except Exception as e:  # noqa: BLE001  (a broken archive or disk error: report it like any other ffmpeg failure)
        raise SetupError(f"ffmpeg download / unpack failed: {type(e).__name__}: {e}") from e
    if not keep:
        arc.unlink(missing_ok=True)
    info = ffmpeg_info(str(exe))
    if not info:
        raise SetupError(f"{exe} does not run")
    row("OK ", "ffmpeg", f"{exe} ({info.split(' Copyright')[0]}, downloaded)")


# ---- voice --------------------------------------------------------------------------------------
HF_SNIPPET = r"""
import os, sys
repo, rev, dest, mode = sys.argv[1:5]
kw = {"voice": {"ignore_patterns": ["live2d/*"]}, "avatar": {"allow_patterns": ["live2d/Amika/*"]}}[mode]
from huggingface_hub import snapshot_download
from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError, HfHubHTTPError
try:
    from huggingface_hub import get_token
except ImportError:
    get_token = lambda: None
tok = os.environ.get("HF_TOKEN") or get_token()
if tok:
    print("[voice] using your Hugging Face token (" + ("HF_TOKEN / --hf-token" if os.environ.get("HF_TOKEN") else "`hf auth login`") + ")", flush=True)
try:
    snapshot_download(repo_id=repo, revision=rev, local_dir=dest, token=tok or None, **kw)
except GatedRepoError as e:
    print("[voice] gated:", str(e).splitlines()[0]); sys.exit(3)
except RepositoryNotFoundError as e:
    print("[voice] not found / not authorized:", str(e).splitlines()[0]); sys.exit(4)
except HfHubHTTPError as e:
    print("[voice] HTTP error:", str(e).splitlines()[0]); sys.exit(5)
"""


def adapter_tags() -> Optional[tuple[list[str], set[str]]]:
    """(V1_IDS, the targets of DEFAULT_MAP) read from speech/voice_adapter.py without importing it; None when that file
    is not there (an engine installed outside an OpenAmika checkout) or cannot be read."""
    import ast
    try:
        tree = ast.parse(VOICE_ADAPTER.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    found: dict[str, Any] = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for t in targets:
            if isinstance(t, ast.Name) and t.id in ("V1_IDS", "DEFAULT_MAP") and node.value is not None:
                try:
                    found[t.id] = ast.literal_eval(node.value)
                except ValueError:
                    return None
    if not isinstance(found.get("V1_IDS"), list) or not isinstance(found.get("DEFAULT_MAP"), dict):
        return None
    return [str(x) for x in found["V1_IDS"]], {str(v) for v in found["DEFAULT_MAP"].values()}


def check_voice(d: Path, ver: Verified) -> tuple[bool, Optional[bool], str]:
    """(usable, weights are the pinned ones, detail); 'pinned' is None when the folder is not usable."""
    try:
        meta = json.loads((d / "character.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False, None, "character.json missing"
    cid = meta.get("character_id") or d.name
    weights = [str(meta.get(key) or default) for key, default in (("llm_model", f"{cid}.ckpt"), ("vocoder_model", f"{cid}.pth"))]
    for name in weights:
        if not (d / name).is_file():
            return False, None, f"{name} missing"
    try:
        table = json.loads((d / "references" / "emotions.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False, None, "references/emotions.json missing"
    tags = {k: v for k, v in table.items() if not k.startswith("_") and isinstance(v, dict)}
    have = sum(1 for v in tags.values() if (d / "references" / str(v.get("audio", ""))).is_file())
    if not have:
        return False, None, "no reference clips"
    notes = [f"{have}/{len(tags)} emotion tags with a clip"]
    with_clip = {k.strip().lower() for k, v in tags.items() if (d / "references" / str(v.get("audio", ""))).is_file()}
    wanted = adapter_tags()
    if wanted is not None:
        ids, defaults = wanted
        missing = [t for t in ids if t not in with_clip]
        if not missing:
            notes.append(f"all {len(ids)} tags OpenAmika's voice adapter can send have one")
        else:
            gone = [t for t in missing if t in defaults]
            notes.append(f"{len(missing)} of the {len(ids)} tags OpenAmika's voice adapter can send have no clip and fall back to neutral1 ({', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''})"
                         + (f", {len(gone)} of them in its default map" if gone else ""))
    pinned = True
    for name in weights:
        f = d / name
        want = VOICE_WEIGHTS.get(name)
        got = ver.sha256(f)
        if want is not None and f.stat().st_size == want[0] and got == want[1]:
            continue
        pinned = False
        notes.append(f"{name} is not the file of revision {VOICE_REV[:8]} (SHA-256 {got[:12]}…)")
    if pinned and len(notes) == 1:
        notes.append(f"weights match revision {VOICE_REV[:8]}")
    return True, pinned, "; ".join(notes)


def ask_voice_license() -> bool:
    """Show the voice license in short on an interactive console and ask once; True when accepted."""
    print(f"\n[voice] Amika's voice and her Live2D model ({HF}/{VOICE_REPO}) are free under MekaHime's personal-use license:\n"
          "          - personal, hobbyist use only, and you must be 18 or older\n"
          "          - no training, fine-tuning or distilling other models on it, no re-hosting or resale\n"
          "          - credit MekaHime visibly when you post videos or streams with her voice\n"
          f"          full text: {HF}/{VOICE_REPO}/blob/main/LICENSE\n", flush=True)
    try:
        answer = input("[voice] type yes to accept and download her voice and model (anything else skips them for now): ")
    except (EOFError, KeyboardInterrupt):
        print(flush=True)
        return False
    return answer.strip().lower() in ("yes", "y")


def ensure_voice(engine: Path, vpy: Path, args: argparse.Namespace, ver: Verified) -> None:
    dest = engine / "Speakers" / VOICE_ID
    page = f"{HF}/{VOICE_REPO}"
    name = f"voice {VOICE_ID}"
    pinned_rev = args.voice_revision == VOICE_REV     # --voice-revision <other> downloads files the pins do not cover
    if args.voice_from:
        src = Path(args.voice_from).expanduser().resolve()
        good, _, why = check_voice(src, ver)
        if not good:
            raise SetupError(f"--voice-from {src}: {why}")
        try:
            shutil.copytree(src, dest, dirs_exist_ok=True)
        except (OSError, shutil.Error) as e:
            raise SetupError(f"copying the voice from {src} to {dest} failed: {e}") from e
        good, _, why = check_voice(dest, ver)
        row("OK " if good else "!! ", name, f"{dest} (copied from {src}; {why})")
        return
    good, pinned, why = check_voice(dest, ver)
    if args.skip_voice:
        row("OK " if good else "-- ", name, f"{dest} ({why}; download skipped)" if good else
            f"skipped (--skip-voice). Later: re-run without --skip-voice and accept her license")
        return
    if good and not args.update_voice:
        if pinned is False and pinned_rev:
            row("!! ", name, f"{dest} ({why}). Download it again with --update-voice, or keep this copy with --skip-voice")
        else:
            row("OK ", name, f"{dest} (existing; {why})")
        return
    if not args.accept_voice_license:
        row("!! ", name, "not downloaded: her voice license was not accepted. Re-run and type yes when asked, "
            f"or pass --accept-voice-license (terms: {page}/blob/main/LICENSE)")
        return
    log(f"downloading Amika's voice from {page} at revision {args.voice_revision[:12]}")
    env = dict(os.environ)
    if args.hf_token:
        env["HF_TOKEN"] = args.hf_token
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("HF_HUB_VERBOSITY", "error")   # failures are reported below in one line
    p = subprocess.run([str(vpy), "-I", "-c", HF_SNIPPET, VOICE_REPO, args.voice_revision, str(dest), "voice"],
                       env=env)
    if p.returncode != 0:
        msg = {3: "the repository is gated", 5: "network / HTTP error",
               4: "repository not found; while it is private, set HF_TOKEN to a token that can read it"}.get(
            p.returncode, f"exit code {p.returncode}")
        row("!! ", name, f"download failed ({msg}) from {page}. Check the internet connection and re-run "
            "this script")
        return
    good, pinned, why = check_voice(dest, ver)
    bad = not good or (pinned is False and pinned_rev)
    row("!! " if bad else "OK ", name, f"{dest} (downloaded from {VOICE_REPO}@{args.voice_revision[:8]}; {why})")


def check_avatar(d: Path, ver: Verified) -> tuple[bool, Optional[bool], str]:
    """(usable, the pinned files match, detail) for a Live2D model folder: its .model3.json and every file it names."""
    models = sorted(d.glob("*.model3.json")) if d.is_dir() else []
    if not models:
        return False, None, "no .model3.json"
    try:
        refs = json.loads(models[0].read_text(encoding="utf-8-sig")).get("FileReferences") or {}
        need = [refs.get("Moc"), refs.get("Physics"), *(refs.get("Textures") or []),
                *(e.get("File") for e in refs.get("Expressions") or []),
                *(m.get("File") for group in (refs.get("Motions") or {}).values() for m in group)]
    except (OSError, ValueError, AttributeError) as e:
        return False, None, f"{models[0].name} cannot be read ({e})"
    missing = [str(n) for n in need if n and not (d / str(n)).is_file()]
    if missing:
        return False, None, f"{len(missing)} file(s) the model names are missing ({', '.join(missing[:4])})"
    pinned = all(ver.ok(d / rel, size, sha) for rel, (size, sha) in AVATAR_FILES.items())
    detail = (f"{models[0].name}, {len(list(d.glob('*.motion3.json')))} motions, "
              f"{len(list(d.glob('*.exp3.json')))} expressions")
    return True, pinned, detail + ("" if pinned else f"; not the files of revision {VOICE_REV[:8]}")


def ensure_avatar(engine: Path, vpy: Path, args: argparse.Namespace, ver: Verified) -> None:
    """Amika's Live2D model from the voice repository (same license) into avatar/models/Amika, where the Use page
    finds it. Without it the stage says "No Live2D model yet" and everything else works."""
    dest = Path(args.avatar_dir).expanduser().resolve()
    page = f"{HF}/{VOICE_REPO}"
    name = f"avatar {AVATAR_ID}"
    pinned_rev = args.voice_revision == VOICE_REV
    good, pinned, why = check_avatar(dest, ver)
    if args.skip_avatar:
        row("OK " if good else "-- ", name, f"{dest} ({why}; download skipped)" if good else
            "skipped (--skip-avatar): the Use page shows \"No Live2D model yet\" until a model is in avatar/models/")
        return
    if good and not args.update_avatar:
        row("OK " if pinned or not pinned_rev else "-- ", name, f"{dest} (existing; {why})"
            + ("" if pinned or not pinned_rev else ". --update-avatar downloads it again"))
        return
    if not args.accept_voice_license:
        row("-- ", name, "not downloaded: her license was not accepted (the same license covers her voice and her "
            "Live2D model). The Use page shows \"No Live2D model yet\"; re-run and type yes to get her")
        return
    log(f"downloading Amika's Live2D model from {page} at revision {args.voice_revision[:12]}")
    tmp = engine / "downloads" / "live2d"
    shutil.rmtree(tmp, ignore_errors=True)
    env = dict(os.environ)
    if args.hf_token:
        env["HF_TOKEN"] = args.hf_token
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("HF_HUB_VERBOSITY", "error")
    p = subprocess.run([str(vpy), "-I", "-c", HF_SNIPPET, VOICE_REPO, args.voice_revision, str(tmp), "avatar"], env=env)
    if p.returncode != 0:
        msg = {3: "the repository is gated", 5: "network / HTTP error",
               4: "repository not found; while it is private, set HF_TOKEN to a token that can read it"}.get(
            p.returncode, f"exit code {p.returncode}")
        row("!! ", name, f"download failed ({msg}) from {page}. Check the internet connection and re-run this script")
        return
    src = tmp / Path(*AVATAR_PATH.split("/"))
    good, pinned, why = check_avatar(src, ver)
    if not good or (pinned is False and pinned_rev):
        shutil.rmtree(tmp, ignore_errors=True)
        row("!! ", name, f"the download from {VOICE_REPO}@{args.voice_revision[:8]} is not usable ({why})")
        return
    dest.mkdir(parents=True, exist_ok=True)
    kept = (dest / "banks.json").is_file()      # emotion banks the Setup page may have edited: never overwritten
    for f in sorted(src.rglob("*")):
        if f.is_file() and not (kept and f.relative_to(src).as_posix() == "banks.json"):
            (dest / f.relative_to(src)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest / f.relative_to(src))
    shutil.rmtree(tmp, ignore_errors=True)
    good, pinned, why = check_avatar(dest, ver)
    row("OK " if good else "!! ", name, f"{dest} (downloaded from {VOICE_REPO}@{args.voice_revision[:8]}; {why}"
        + ("; your banks.json kept" if kept else "") + ")")


# ---- verification -------------------------------------------------------------------------------
IMPORT_PROBE = r"""
import json, importlib, sys
mods = ["torch", "torchaudio", "transformers", "onnxruntime", "pyopenjtalk", "nltk", "jieba", "opencc", "g2p_en",
        "pypinyin", "cn2an", "librosa", "soundfile", "pytorch_lightning", "peft", "socketio", "aiohttp", "split_lang",
        "fast_langdetect", "x_transformers", "matplotlib", "resampy", "ffmpeg", "ToJyutping", "g2pk2"]
out, bad = {}, {}
for m in mods:
    try:
        mod = importlib.import_module(m)
        out[m] = getattr(mod, "__version__", "ok")
    except Exception as e:
        bad[m] = f"{type(e).__name__}: {e}"[:200]
import torch
out["cuda"] = torch.cuda.is_available()
out["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else ""
print(json.dumps({"ok": out, "bad": bad}))
"""


def verify_imports(vpy: Path) -> dict:
    p = subprocess.run([str(vpy), "-I", "-c", IMPORT_PROBE], capture_output=True, text=True, errors="replace")
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SetupError(f"import check failed:\n{p.stderr[-1500:]}")
    bad = {k: v for k, v in res["bad"].items() if k not in ("g2pk2",)}   # Korean is optional
    if bad:
        row("!! ", "imports", "; ".join(f"{k}: {v}" for k, v in bad.items()))
    else:
        ok = res["ok"]
        gpu = f"CUDA OK on {ok['gpu']}" if ok.get("cuda") else "no CUDA (CPU)"
        row("OK ", "imports", f"{len(ok) - 2} packages import; torch {ok.get('torch')}; {gpu}")
    return res


SELF_TEST = r"""
import asyncio, os, socket, subprocess, sys, time, urllib.request, uuid
import socketio
engine, py = sys.argv[1], sys.argv[2]
with socket.socket() as s:
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
log = open(os.path.join(engine, "cache", "self-test.log"), "wb")
proc = subprocess.Popen([py, "main.py", "--port", str(port)], cwd=engine, stdout=log, stderr=subprocess.STDOUT)
t0 = time.time()
try:
    while time.time() - t0 < 600:
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=3).status == 200: break
        except Exception: pass
        if proc.poll() is not None: sys.exit(f"server exited ({proc.returncode}); see cache/self-test.log")
        time.sleep(1)
    else: sys.exit("server did not become ready in 10 min")
    ready = time.time() - t0
    async def go():
        sio = socketio.AsyncClient()
        done, out = asyncio.Event(), {}
        @sio.on("audio_ready")
        async def _a(d):
            out.setdefault("audio", b""); out["audio"] += d["audio"]; out["meta"] = d
            if d.get("is_final"): done.set()
        @sio.on("error")
        async def _e(d): out["error"] = d.get("error"); done.set()
        await sio.connect(f"http://127.0.0.1:{port}", transports=["websocket"])
        t = time.time()
        await sio.emit("tts_request_gateway", {"task_id": str(uuid.uuid4()), "turn_id": "self-test-0",
            "text": "【happy2】.おかえりなさい。今日もおつかれさま。", "character_id": "MekaHime_Amika",
            "audio_format": "wav", "language": "Japanese"})
        await asyncio.wait_for(done.wait(), 120)
        await sio.disconnect()
        return out, time.time() - t
    out, dt = asyncio.run(go())
    if "error" in out: sys.exit("synthesis error: " + out["error"])
    m = out["meta"]
    path = os.path.join(engine, "cache", "self-test.wav")
    open(path, "wb").write(out["audio"])
    print(f"ready after {ready:.0f} s; {m['audio_duration']:.2f} s of audio in {dt:.2f} s -> {path}")
finally:
    proc.terminate()
"""


def self_test(engine: Path, vpy: Path) -> None:
    (engine / "cache").mkdir(exist_ok=True)
    p = subprocess.run([str(vpy), "-I", "-c", SELF_TEST, str(engine), str(vpy)], capture_output=True, text=True,
                       errors="replace")
    msg = (p.stdout.strip() or p.stderr.strip()[-600:]).splitlines()[-1] if (p.stdout or p.stderr) else "no output"
    row("OK " if p.returncode == 0 else "!! ", "self-test", msg)


# ---- main ---------------------------------------------------------------------------------------
def sync_engine_files(engine: Path) -> None:
    if engine.resolve() == SOURCE_ENGINE.resolve():
        return
    engine.mkdir(parents=True, exist_ok=True)
    for name in ENGINE_FILES:
        src = SOURCE_ENGINE / name
        if src.is_file() and (not (engine / name).is_file() or (engine / name).read_bytes() != src.read_bytes()):
            shutil.copy2(src, engine / name)
            log(f"copied {name} -> {engine}")


def long_paths_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            return int(winreg.QueryValueEx(k, "LongPathsEnabled")[0]) == 1
    except (ImportError, OSError, ValueError):
        return False


def check_launcher(engine: Path) -> None:
    """OpenAmika's launcher starts this server from settings.json "services.tts" and sends tts_character (the voice
    adapter: config/voice_map.json "voice") as character_id. Report whether they point here; never edits them."""
    settings = ROOT / "settings.json"
    block = {"enabled": True, "command": [str(venv_python(engine).relative_to(ROOT)) if engine.is_relative_to(ROOT)
                                         else str(venv_python(engine)), "main.py", "--port", "8002", "--host", "127.0.0.1"],
             "cwd": str(engine.relative_to(ROOT)) if engine.is_relative_to(ROOT) else str(engine),
             "health": "http://127.0.0.1:8002/ping", "timeout_s": 240}
    hint = (f'settings.json needs  "tts_character": "{VOICE_ID}"  and  "services": {{"tts": '
            f'{json.dumps(block)}}}  (settings.example.json has both)')
    if not settings.is_file():
        row("-- ", "launcher", "settings.json not found yet: copy settings.example.json to settings.json; its \"services.tts\" block starts this server on port 8002")
        return
    try:
        cfg = json.loads(settings.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        row("!! ", "launcher", f"settings.json cannot be read ({e})")
        return
    tts = (cfg.get("services") or {}).get("tts") or {}
    cmd = [str(c) for c in (tts.get("command") or [])]
    exe = Path(cmd[0]) if cmd else None
    if exe is not None and not exe.is_absolute():
        exe = ROOT / exe
    cwd = Path(str(tts.get("cwd") or "."))
    cwd = cwd if cwd.is_absolute() else ROOT / cwd
    problems = []
    if not tts or tts.get("enabled", True) is False:
        problems.append("services.tts is missing or disabled")
    elif exe is None or exe.resolve() != venv_python(engine).resolve() or "main.py" not in cmd or cwd.resolve() != engine.resolve():
        problems.append(f"services.tts runs {' '.join(cmd) or 'nothing'} in {cwd}, not this server")
    if cfg.get("tts_character") != VOICE_ID:
        problems.append(f"tts_character is {cfg.get('tts_character')!r}, not {VOICE_ID!r}")
    vmap = ROOT / "config" / "voice_map.json"
    try:
        voice = json.loads(vmap.read_text(encoding="utf-8-sig")).get("voice") if vmap.is_file() else None
    except (OSError, ValueError):
        voice = None
    if voice and voice != VOICE_ID:
        problems.append(f"config/voice_map.json speaks through {voice!r} (Setup -> Voice: set the voice to {VOICE_ID})")
    if problems:
        row("-- ", "launcher", "; ".join(problems) + ". " + hint)
    else:
        row("OK ", "launcher", f"settings.json starts this server (services.tts, port from tts_url) with voice {VOICE_ID}")


def write_manifest(engine: Path, vpy: Path, device: str, probe: dict, voice_revision: str) -> None:
    info = {
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "complete": not any(m.strip() == "!!" for m, _, _ in ROWS),   # every step so far succeeded
        "gpt_sovits": {"repo": GPT_SOVITS_GIT, "commit": GPT_SOVITS_COMMIT},
        "torch": {"version": TORCH_VERSION, "variant": device, "cuda_available": bool(probe.get("ok", {}).get("cuda"))},
        "python": ".".join(map(str, py_version([str(vpy)]) or ())),
        "pretrained": {"repo": PRETRAINED_REPO, "revision": PRETRAINED_REV},
        "g2pw": {"repo": G2PW_REPO, "revision": G2PW_REV},
        "nltk_data": NLTK_COMMIT,
        "voice": {"repo": VOICE_REPO, "id": VOICE_ID, "revision": voice_revision},
        "avatar": {"repo": VOICE_REPO, "path": AVATAR_PATH, "id": AVATAR_ID, "revision": voice_revision},
    }
    (engine / "install.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


class Steps:
    """Runs the install steps one by one. A step that raises (or reports a !! row itself) is marked failed and
    the run goes on; a later step that needs a failed one is skipped with a -- row instead of running."""

    def __init__(self) -> None:
        self.failed: list[str] = []

    def run(self, name: str, fn: Callable[..., Any], *args: Any, needs: tuple[str, ...] = ()) -> Any:
        blocked = [n for n in needs if n in self.failed]
        if blocked:
            row("-- ", name, f"skipped: needs {', '.join(blocked)}, which did not finish (see above)")
            self.failed.append(name)
            return None
        rows_before = len(ROWS)
        try:
            result = fn(*args)
        except SetupError as e:
            row("!! ", name, str(e))
        except Exception as e:  # noqa: BLE001  (an unexpected failure is reported like any other)
            traceback.print_exc()
            row("!! ", name, f"unexpected {type(e).__name__}: {e} (traceback above)")
        else:
            if any(m.strip() == "!!" for m, _, _ in ROWS[rows_before:]):
                self.failed.append(name)
            return result
        self.failed.append(name)
        return None


def optional_ffmpeg(engine: Path, ver: Verified, keep: bool) -> None:
    try:
        ensure_ffmpeg(engine, ver, keep)
    except Exception as e:  # noqa: BLE001  (optional component: report it, keep going)
        row("-- ", "ffmpeg", f"{e} (only needed for Opus output; WAV works without it)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the OpenAmika voice server (a MekaHime build of GPT-SoVITS)")
    ap.add_argument("--engine-dir", type=Path, default=SOURCE_ENGINE, help="default: vendor/tts-engine")
    ap.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto",
                    help="torch build: auto = CUDA 12.8 when an NVIDIA GPU is visible (Windows/Linux), else CPU")
    ap.add_argument("--cpu", action="store_true", help="same as --device cpu")
    ap.add_argument("--python", default=None, help="Python 3.11 (or 3.12) interpreter for the venv (default: the first "
                                                     "3.11 found: this one, py -3.11, python3.11; then 3.12)")
    ap.add_argument("--skip-voice", action="store_true", help="do not download Amika's voice")
    ap.add_argument("--voice-from", default=None, help="copy the voice from a local folder instead of Hugging Face")
    ap.add_argument("--update-voice", action="store_true", help="download the voice again (at --voice-revision)")
    ap.add_argument("--voice-revision", default=VOICE_REV,
                    help=f"revision of {VOICE_REPO} (default: the pinned {VOICE_REV[:8]}, whose weights are checked "
                         "by SHA-256)")
    ap.add_argument("--accept-voice-license", action="store_true",
                    help="accept the license of Amika's voice and Live2D model (personal use, 18+) without being "
                         "asked")
    ap.add_argument("--skip-avatar", action="store_true", help="do not download Amika's Live2D model")
    ap.add_argument("--update-avatar", action="store_true",
                    help="download her Live2D model again (a banks.json edited in Setup is kept)")
    ap.add_argument("--avatar-dir", default=str(ROOT / "avatar" / "models" / "Amika"),
                    help="where her Live2D model goes (default: avatar/models/Amika)")
    ap.add_argument("--hf-token", default=None, help="Hugging Face token (optional; the public voice needs none. Also read from HF_TOKEN; only a private copy of the voice repository needs one)")
    ap.add_argument("--skip-ffmpeg", action="store_true")
    ap.add_argument("--reinstall", action="store_true", help="re-run the pip steps even if nothing changed")
    ap.add_argument("--keep-downloads", action="store_true", help="keep the zip archives in downloads/")
    ap.add_argument("--self-test", action="store_true", help="start the server once and synthesize a line")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:  # file names and messages can be non-ASCII; never die on a cp1252 console or log file
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    engine = args.engine_dir.expanduser().resolve()
    log(f"OpenAmika voice setup -> {engine}  ({platform.system()} {platform.machine()}, Python {platform.python_version()})")
    free = disk_free_gb(engine)
    if free is not None and free < 12:
        log(f"WARNING: only {free:.0f} GB free; a full install needs about 9 GB")
    if IS_WIN and len(str(engine)) > 100 and not long_paths_enabled():
        log(f"WARNING: {engine} is {len(str(engine))} characters long and Windows long paths are off: files deep in the "
            "venv and the GPT-SoVITS tree can pass the 260-character limit. Use a shorter folder, or enable "
            "LongPathsEnabled (HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem) and re-run")
    voice_missing = args.update_voice or not (engine / "Speakers" / VOICE_ID / "character.json").is_file()
    avatar_missing = not args.skip_avatar and (args.update_avatar or not any(
        Path(args.avatar_dir).expanduser().glob("*.model3.json")))
    if (((voice_missing and not (args.skip_voice or args.voice_from)) or avatar_missing)
            and not args.accept_voice_license and sys.stdin.isatty()):
        args.accept_voice_license = ask_voice_license()     # asked first, so the long install can run unattended
    t0 = time.time()
    rc = 0
    steps = Steps()
    try:
        steps.run("engine files", sync_engine_files, engine)
        device = pick_device(args)
        log(f"torch build: {'CUDA 12.8' if device == 'cuda' else 'CPU + Apple MPS' if device == 'mac' else 'CPU'}")
        ver = Verified(engine / ".openamika-verified.json")
        # the venv chain: each step needs the one before it
        vpy = steps.run("venv", ensure_venv, engine, args.python)
        cons = steps.run("torch", ensure_torch, vpy, device, args.reinstall, needs=("venv",))
        steps.run("packages", ensure_requirements, vpy, engine, cons, args.reinstall,
                  needs=("engine files", "venv", "torch"))
        # downloads that only need the standard library: they run even when the venv chain failed
        gsv = steps.run("GPT-SoVITS", ensure_checkout, engine)
        steps.run("pretrained models", ensure_pretrained, gsv, ver, needs=("GPT-SoVITS",))
        steps.run("G2PW", ensure_g2pw, engine, gsv, ver, args.keep_downloads, needs=("GPT-SoVITS",))
        steps.run("nltk data", ensure_nltk, engine, ver)
        if args.skip_ffmpeg:
            row("-- ", "ffmpeg", "skipped (--skip-ffmpeg; WAV output works without it)")
        else:
            optional_ffmpeg(engine, ver, args.keep_downloads)
        # the voice download runs huggingface_hub inside the venv; --voice-from only copies files
        steps.run(f"voice {VOICE_ID}", ensure_voice, engine, vpy, args, ver,
                  needs=() if (args.voice_from or args.skip_voice) else ("venv", "packages"))
        steps.run(f"avatar {AVATAR_ID}", ensure_avatar, engine, vpy, args, ver,
                  needs=() if args.skip_avatar else ("venv", "packages"))
        probe = steps.run("imports", verify_imports, vpy, needs=("venv", "torch", "packages"))
        steps.run("Japanese dictionary", ensure_ja_dict, vpy, gsv, needs=("venv", "packages", "GPT-SoVITS"))
        if vpy is not None:
            steps.run("manifest", write_manifest, engine, vpy, device, probe or {}, args.voice_revision)
        check_launcher(engine)
        if args.self_test:
            if any(m.strip() == "!!" for m, _, _ in ROWS):
                row("-- ", "self-test", "skipped: fix the !! lines first")
            else:
                steps.run("self-test", self_test, engine, vpy)
    except KeyboardInterrupt:
        row("!! ", "interrupted", "Ctrl-C (re-run to resume; finished downloads are kept)")
        rc = 130

    width = max((len(n) for _, n, _ in ROWS), default=10)
    print("\n" + "=" * 78)
    print(f"OpenAmika voice setup summary ({time.time() - t0:.0f} s)")
    for mark, name, detail in ROWS:
        print(f"  {mark}{name.ljust(width)}  {detail}")
    bad = rc != 0 or any(m.strip() == "!!" for m, _, _ in ROWS)
    print("=" * 78)
    if bad:
        print("Fix the !! lines above and run this script again (finished steps are skipped).")
    elif IS_WIN:
        print("Done. Next:  Start_OpenAmika.bat  (it starts this server from settings.json \"services.tts\"; "
              "<brain python> launch.py --check shows the TTS row)")
        print(f"Run the voice server alone:  {venv_python(engine)} {engine / 'main.py'} --port 8002")
    else:
        print("Done. Next:  <brain python> launch.py --check   then   <brain python> launch.py")
        print(f"Run the voice server alone:  {venv_python(engine)} {engine / 'main.py'} --port 8002")
    return rc or (1 if bad else 0)


if __name__ == "__main__":
    raise SystemExit(main())
