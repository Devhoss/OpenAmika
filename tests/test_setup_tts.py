"""scripts/setup_tts.py: her voice and Live2D model from Hugging Face, against a local stand-in for the Hub. Offline.

The stand-in serves a tiny synthetic copy of the repository layout (never her real files): public, or private (401
without the right bearer token, as the Hub answers anonymous requests for a private repository). Large files are
answered with a redirect to a second server on another port, like the Hub's CDN, which must never see the token;
the clips are answered with a same-host redirect, which must keep it.
"""
import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("setup_tts", ROOT / "scripts" / "setup_tts.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

TOKEN = "fake-hub-pass"                     # a dummy string the fake Hub accepts; not a Hugging Face token
REPO_FILES = {
    "character.json": json.dumps({"character_id": "MekaHime_Amika"}).encode(),
    "MekaHime_Amika.ckpt": b"gpt-weights" * 900,
    "MekaHime_Amika.pth": b"sovits-weights" * 700,
    "references/emotions.json": json.dumps({"happy1": {"audio": "开心.wav", "text": "x"},
                                            "sad1": {"audio": "难过.wav", "text": "y"}}, ensure_ascii=False).encode(),
    "references/开心.wav": b"RIFF-happy" * 50,
    "references/难过.wav": b"RIFF-sad" * 60,
    "README.md": b"# model card\n",
    "LICENSE": b"personal use only\n",
    ".gitattributes": b"*.ckpt filter=lfs\n",
    "live2d/Amika/AmikaV4.model3.json": json.dumps({"FileReferences": {
        "Moc": "AmikaV4.moc3", "Textures": ["AmikaV4.8192/texture_00.png"],
        "Motions": {"": [{"File": "idle1.motion3.json"}]}}}).encode(),
    "live2d/Amika/AmikaV4.moc3": b"moc" * 3000,
    "live2d/Amika/AmikaV4.8192/texture_00.png": b"png" * 2500,
    "live2d/Amika/idle1.motion3.json": b'{"Version": 3}',
    "live2d/Amika/banks.json": b'{"banks": {}}',
}
CDN_SUFFIXES = (".ckpt", ".pth", ".moc3", ".png")           # served from the "CDN" (another host)


class Hub:
    def __init__(self):
        self.private = False
        self.gone = set()                     # paths answered with 404 EntryNotFound
        self.tampered = {}                    # path -> other bytes of the same length (not the pinned file)
        self.requests = 0
        self.saw_auth = 0                     # requests to the Hub itself that carried a token
        self.cdn_saw_auth = []
        self.same_host_redirect_auth = []
        self.ranges = []

    def denied(self, handler):
        return self.private and handler.headers.get("Authorization") != f"Bearer {TOKEN}"


def make_servers(hub):
    class CDN(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.headers.get("Authorization"):
                hub.cdn_saw_auth.append(self.path)
            rel = urllib.parse.unquote(self.path.split("/blob/", 1)[1])
            data = REPO_FILES[rel]
            start = 0
            if self.headers.get("Range"):
                start = int(self.headers["Range"].split("=")[1].split("-")[0])
                hub.ranges.append((rel, start))
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(data) - start))
            self.end_headers()
            self.wfile.write(data[start:])

        def log_message(self, *args):
            pass

    cdn = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CDN)
    cdn_base = f"http://127.0.0.1:{cdn.server_address[1]}"

    class HF(http.server.BaseHTTPRequestHandler):
        def empty(self, code, **headers):
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):  # noqa: N802
            hub.requests += 1
            hub.saw_auth += bool(self.headers.get("Authorization"))
            path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            if path.startswith("/api/resolve-cache/"):              # the same-host second leg of a redirect
                hub.same_host_redirect_auth.append(bool(self.headers.get("Authorization")))
                if hub.denied(self):
                    return self.empty(401, **{"X-Error-Code": "RepoNotFound"})
                rel = path.split("/", 4)[4]                       # /api/resolve-cache/models/<path>

                return self.send_file(REPO_FILES[rel])
            prefix = f"/{st.VOICE_REPO}/resolve/{st.VOICE_REV}/"
            if not path.startswith(prefix):
                return self.empty(404, **{"X-Error-Code": "RevisionNotFound"})
            if hub.denied(self):
                return self.empty(401, **{"X-Error-Code": "RepoNotFound"})
            rel = path[len(prefix):]
            if rel not in REPO_FILES or rel in hub.gone:
                return self.empty(404, **{"X-Error-Code": "EntryNotFound"})
            if rel.endswith(CDN_SUFFIXES):
                return self.empty(302, Location=f"{cdn_base}/blob/{urllib.parse.quote(rel)}")
            if rel.startswith("references/"):
                return self.empty(307, Location=f"/api/resolve-cache/models/{urllib.parse.quote(rel)}")
            self.send_file(hub.tampered.get(rel, REPO_FILES[rel]))

        def send_file(self, data):
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    hf = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HF)
    for server in (cdn, hf):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return hf, cdn, f"http://127.0.0.1:{hf.server_address[1]}"


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    hub = Hub()
    hf, cdn, base = make_servers(hub)
    monkeypatch.setattr(st, "HF", base)
    monkeypatch.setattr(st, "TOKEN_HOSTS", ("huggingface.co", "127.0.0.1"))   # the stand-in plays huggingface.co
    monkeypatch.setattr(st, "VOICE_ADAPTER", tmp_path / "no-adapter.py")      # the real adapter wants all 67 tags
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_TOKEN_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")                   # never read this machine's saved login
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))                         # no console: never asks
    repo = tmp_path / "repo"
    for rel, data in REPO_FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"repo": st.VOICE_REPO, "revision": st.VOICE_REV, "files": {
        rel: [len(data), hashlib.sha256(data).hexdigest()] for rel, data in REPO_FILES.items()}}), "utf-8")
    hub.repo, hub.manifest = repo, manifest
    hub.engine, hub.avatar = tmp_path / "engine", tmp_path / "avatar" / "Amika"
    yield hub
    for server in (hf, cdn):
        server.shutdown()
        server.server_close()


def run(hub, *extra):
    argv = ["--assets-only", "--engine-dir", str(hub.engine), "--avatar-dir", str(hub.avatar),
            "--manifest", str(hub.manifest), *extra]
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = st.main(argv)
    rows = {name: (mark.strip(), detail) for mark, name, detail in st.ROWS}
    return rc, rows, out.getvalue()


def installed(hub):
    voice = {rel: (hub.engine / "Speakers" / "MekaHime_Amika" / rel) for rel in REPO_FILES
             if not rel.startswith("live2d/") and rel != ".gitattributes"}
    model = {rel: hub.avatar / rel[len("live2d/Amika/"):] for rel in REPO_FILES if rel.startswith("live2d/")}
    return voice, model


VOICE, MODEL = "voice MekaHime_Amika", "Live2D model Amika"


def test_public_repo_installs_her_voice_and_model_and_a_rerun_downloads_nothing(hub):
    rc, rows, out = run(hub, "--accept-voice-license")
    assert rc == 0, out
    assert rows[VOICE][0] == "OK" and "downloaded 8 file(s)" in rows[VOICE][1] and "8 files verified" in rows[VOICE][1]
    assert "2/2 emotion tags with a clip" in rows[VOICE][1]
    assert rows[MODEL][0] == "OK" and "AmikaV4.model3.json, 1 motions" in rows[MODEL][1]
    voice, model = installed(hub)
    for rel, path in {**voice, **model}.items():
        assert path.read_bytes() == REPO_FILES[rel], rel
    assert not (hub.engine / "Speakers" / "MekaHime_Amika" / ".gitattributes").exists()
    assert not list(hub.engine.rglob("*.part")) and not list(hub.avatar.rglob("*.part"))
    assert hub.cdn_saw_auth == []
    served = hub.requests
    rc, rows, out = run(hub)                         # installed: no question, no download, every file re-checked
    assert rc == 0, out
    assert hub.requests == served
    assert rows[VOICE][1].count("existing; 8 files verified") == 1


def test_private_repo_without_a_token_says_what_to_do(hub):
    hub.private = True
    rc, rows, out = run(hub, "--accept-voice-license")
    assert rc == 1
    for name in (VOICE, MODEL):
        mark, detail = rows[name]
        assert mark == "!!" and "HTTP 401" in detail and "not public yet" in detail and "HF_TOKEN" in detail, detail
    assert "Traceback" not in out
    assert "Fix the !! lines above and run this script again" in out
    assert not (hub.engine / "Speakers" / "MekaHime_Amika" / "character.json").exists()
    # said the moment it happens, not only in the summary after the voice server's long install
    assert "[setup] !! her voice: Hugging Face refused the download (HTTP 401)" in out.split("OpenAmika voice setup summary")[0]


def test_a_mirror_never_gets_the_token(hub, monkeypatch):
    monkeypatch.setattr(st, "TOKEN_HOSTS", ("huggingface.co",))        # the stand-in is a mirror (HF_ENDPOINT) now
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    rc, rows, out = run(hub, "--accept-voice-license")
    assert rc == 0, out
    assert hub.saw_auth == 0 and hub.cdn_saw_auth == []
    assert "is not sent to a mirror" in out and TOKEN not in out
    assert st.token_goes_to("https://huggingface.co/x") and st.token_goes_to("https://cdn-lfs.huggingface.co/x")
    assert not st.token_goes_to("https://hf-mirror.com/x") and not st.token_goes_to("https://huggingface.co.example.org/x")


def test_private_repo_with_a_token_works_and_the_cdn_never_sees_it(hub, monkeypatch):
    hub.private = True
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    rc, rows, out = run(hub, "--accept-voice-license")
    assert rc == 0, out
    assert hub.cdn_saw_auth == []                                   # cross-host redirect: the token stays behind
    assert hub.same_host_redirect_auth and all(hub.same_host_redirect_auth)   # same host: it goes along
    assert "using your Hugging Face token (from HF_TOKEN)" in out and TOKEN not in out


def test_a_wrong_token_is_named_as_the_problem(hub, monkeypatch):
    hub.private = True
    monkeypatch.setenv("HF_TOKEN", "not-the-right-one")
    rc, rows, _ = run(hub, "--accept-voice-license")
    assert rc == 1 and "did not accept your Hugging Face token (from HF_TOKEN)" in rows[VOICE][1]


def test_without_the_license_nothing_is_downloaded(hub):
    rc, rows, out = run(hub)
    assert rc == 1 and hub.requests == 0
    for name in (VOICE, MODEL):
        assert rows[name][0] == "!!" and "license was not accepted" in rows[name][1] and "--accept-voice-license" in rows[name][1]


def test_verify_finds_missing_and_damaged_files_and_a_rerun_repairs_them(hub):
    assert run(hub, "--accept-voice-license")[0] == 0
    voice, model = installed(hub)
    voice["references/开心.wav"].write_bytes(b"truncated")
    model["live2d/Amika/idle1.motion3.json"].unlink()
    served = hub.requests
    rc, rows, out = run(hub, "--verify")
    assert rc == 1 and hub.requests == served                       # --verify never downloads
    assert "references/开心.wav" in rows[VOICE][1] and "1 of 8 files" in rows[VOICE][1]
    assert "idle1.motion3.json" in rows[MODEL][1]
    assert "run this script without --verify" in out
    rc, rows, out = run(hub)                                        # installed before: repaired without asking again
    assert rc == 0, out
    assert voice["references/开心.wav"].read_bytes() == REPO_FILES["references/开心.wav"]
    assert model["live2d/Amika/idle1.motion3.json"].is_file()
    assert run(hub, "--verify")[0] == 0


def test_her_edited_banks_json_is_kept(hub):
    assert run(hub, "--accept-voice-license")[0] == 0
    _, model = installed(hub)
    model["live2d/Amika/banks.json"].write_text('{"banks": {"casual_happy": {}}}', "utf-8")
    rc, rows, _ = run(hub, "--update-avatar")
    assert rc == 0 and "your banks.json kept" in rows[MODEL][1]
    assert "casual_happy" in model["live2d/Amika/banks.json"].read_text("utf-8")


def test_an_unedited_banks_json_follows_a_newer_pin_and_an_edited_one_stays(hub, monkeypatch):
    assert run(hub, "--accept-voice-license")[0] == 0
    banks = installed(hub)[1]["live2d/Amika/banks.json"]

    def pin(data):                     # a newer revision of her repository brings another banks.json
        monkeypatch.setitem(REPO_FILES, "live2d/Amika/banks.json", data)
        manifest = json.loads(hub.manifest.read_text("utf-8"))
        manifest["files"]["live2d/Amika/banks.json"] = [len(data), hashlib.sha256(data).hexdigest()]
        hub.manifest.write_text(json.dumps(manifest), "utf-8")

    pin(b'{"banks": {"v": 2}}')
    rc, rows, out = run(hub)
    assert rc == 0, out
    assert banks.read_bytes() == b'{"banks": {"v": 2}}' and "kept" not in rows[MODEL][1]
    banks.write_text('{"banks": {"mine": 1}}', "utf-8")                 # then edited on the Setup page
    pin(b'{"banks": {"v": 3}}')
    rc, rows, _ = run(hub)
    assert rc == 0 and "your banks.json kept" in rows[MODEL][1]
    assert banks.read_text("utf-8") == '{"banks": {"mine": 1}}'


def test_a_file_that_is_not_the_pinned_one_is_named_once_and_kept_out(hub):
    hub.tampered["README.md"] = b"# other card\n"                       # same length, other bytes
    rc, rows, _ = run(hub, "--accept-voice-license")
    detail = rows[VOICE][1]
    assert rc == 1 and rows[VOICE][0] == "!!" and "SHA-256 mismatch" in detail
    assert detail.count("not the pinned one") == 1 and "OpenAmika pins" not in detail and "git pull" in detail
    assert not (hub.engine / "Speakers" / "MekaHime_Amika" / "README.md").exists()


def test_a_damaged_torch_is_installed_again_but_a_fresh_one_once(monkeypatch, tmp_path):
    """pip calls an installed torch satisfied even when its files are damaged: the same build goes in place again
    (--force-reinstall); a fresh venv gets one plain install, and a too-long folder is left to its own hint."""
    calls = []
    monkeypatch.setattr(st, "check_platform", lambda vpy: None)
    monkeypatch.setattr(st, "pip", lambda vpy, *args: calls.append(args))
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    vpy = tmp_path / ".venv" / "Scripts" / "python.exe"
    cases = {"damaged": {"installed": "2.9.1+cu128", "error": "OSError: [WinError 126] Error loading c10.dll"},
             "fresh": {"error": "ModuleNotFoundError: No module named 'torch'"},
             "too long": {"installed": "2.9.1+cu128",
                          "error": "ImportError: DLL load failed while importing _C: The filename or extension is too long."}}
    for name, probe in cases.items():
        calls.clear()
        monkeypatch.setattr(st.subprocess, "run", lambda *a, probe=probe, **k: type("P", (), {
            "stdout": json.dumps(probe), "returncode": 0})())
        st.ensure_torch(vpy, "cuda", False)
        forced = [c for c in calls if "--force-reinstall" in c]
        assert len(calls) == (2 if name == "damaged" else 1), (name, calls)
        assert bool(forced) == (name == "damaged"), name
        assert all("torch==2.9.1+cu128" in c for c in calls)


def test_a_too_long_folder_is_named_with_its_only_fix():
    err = "resampy: ImportError: DLL load failed while importing _box: The filename or extension is too long."
    hint = st.too_long_hint(err)
    assert "C:\\OpenAmika" in hint and "does not help" in hint
    assert st.too_long_hint("resampy: ImportError: No module named 'resampy'") == ""
    if st.IS_WIN:
        assert "DLL" in st.long_folder_warning(Path("C:/" + "x" * 120))
        assert st.long_folder_warning(Path("C:/OpenAmika")) == ""


def test_an_interrupted_download_resumes_from_its_part_file(hub):
    target = hub.engine / "Speakers" / "MekaHime_Amika" / "MekaHime_Amika.ckpt"
    target.parent.mkdir(parents=True)
    half = len(REPO_FILES["MekaHime_Amika.ckpt"]) // 2
    target.with_name(target.name + ".part").write_bytes(REPO_FILES["MekaHime_Amika.ckpt"][:half])
    rc, _, out = run(hub, "--accept-voice-license")
    assert rc == 0, out
    assert ("MekaHime_Amika.ckpt", half) in hub.ranges
    assert target.read_bytes() == REPO_FILES["MekaHime_Amika.ckpt"]


def test_a_file_gone_from_the_revision_says_to_update(hub):
    hub.gone.add("references/难过.wav")
    rc, rows, _ = run(hub, "--accept-voice-license")
    assert rc == 1 and "HTTP 404" in rows[VOICE][1] and "git pull" in rows[VOICE][1]
    assert rows[MODEL][0] == "OK"                                    # the model does not need the voice


def test_from_dir_installs_offline_and_checks_every_file(hub):
    hub.private = True                                               # the Hub would refuse: it must not be asked
    rc, rows, out = run(hub, "--accept-voice-license", "--from-dir", str(hub.repo))
    assert rc == 0, out
    assert hub.requests == 0 and "copied 8 file(s)" in rows[VOICE][1]
    voice, model = installed(hub)
    assert all(p.read_bytes() == REPO_FILES[r] for r, p in {**voice, **model}.items())
    (hub.repo / "references" / "开心.wav").write_bytes(b"RIFF-other" * 50)   # same size, other bytes
    voice["references/开心.wav"].unlink()
    rc, rows, _ = run(hub, "--from-dir", str(hub.repo))
    assert rc == 1 and "SHA-256" in rows[VOICE][1]
    assert not voice["references/开心.wav"].exists()


def test_write_manifest_reproduces_the_pinned_list(hub, tmp_path):
    out = tmp_path / "written.json"
    with contextlib.redirect_stdout(io.StringIO()):
        assert st.main(["--write-manifest", str(hub.repo), "--voice-revision", st.VOICE_REV + "x", "--manifest", str(out)]) == 0
    written = json.loads(out.read_text("utf-8"))
    pinned = json.loads(hub.manifest.read_text("utf-8"))
    assert written["files"] == pinned["files"] and written["revision"] == st.VOICE_REV + "x"


def test_hf_token_sources_and_their_order(monkeypatch, tmp_path):
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_HUB_DISABLE_IMPLICIT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    saved = tmp_path / "token"
    saved.write_text("saved-login\n", "utf-8")
    monkeypatch.setenv("HF_TOKEN_PATH", str(saved))
    assert st.hf_token(None) == ("saved-login", "`hf auth login`")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "older-variable")
    assert st.hf_token(None) == ("older-variable", "HUGGING_FACE_HUB_TOKEN")
    monkeypatch.setenv("HF_TOKEN", "from-env")
    assert st.hf_token(None) == ("from-env", "HF_TOKEN")
    assert st.hf_token("flag") == ("flag", "--hf-token")
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(var)
    monkeypatch.setenv("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    assert st.hf_token(None) == (None, "")


def test_the_shipped_manifest_pins_her_whole_repository():
    m = st.load_manifest(st.MANIFEST)
    assert (m["repo"], m["revision"]) == (st.VOICE_REPO, st.VOICE_REV)
    voice, model = st.split_assets(m["files"])
    assert len(m["files"]) == 250 and len(voice) == 75 and len(model) == 174
    assert {"character.json", "MekaHime_Amika.ckpt", "MekaHime_Amika.pth", "references/emotions.json"} <= set(voice)
    assert sum(r.endswith(".wav") for r in voice) == 69
    assert list(voice)[-1] == "character.json" and list(model)[-1] == "AmikaV4.model3.json"   # written last
    assert sum(r.endswith(".motion3.json") for r in model) == 142 and sum(r.endswith(".exp3.json") for r in model) == 25
    assert all(len(sha) == 64 and size > 0 for size, sha in m["files"].values())
