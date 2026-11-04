"""scripts/setup_core.py: the decisions it makes and the files it writes, without installing anything. Offline."""
import contextlib
import importlib.util
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("setup_core", ROOT / "scripts" / "setup_core.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


@pytest.fixture()
def checkout(tmp_path, monkeypatch):
    """setup_core.py looking at an empty checkout (the shipped templates only)."""
    for rel in ("settings.example.json", ".env.example"):
        shutil.copyfile(ROOT / rel, tmp_path / rel)
    (tmp_path / "brain").mkdir()
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(core, "ENVS", tmp_path / "envs")
    core.ROWS.clear()
    return tmp_path


def rows():
    return {name: (mark.strip(), detail) for mark, name, detail in core.ROWS}


def test_gpu_line_parsing():
    assert core.parse_gpu("NVIDIA GeForce RTX 5080, 16303\n") == ("NVIDIA GeForce RTX 5080", 16303)
    assert core.parse_gpu("NVIDIA GeForce RTX 3060, 12288\nNVIDIA GeForce GT 710, 2048\n") == ("NVIDIA GeForce RTX 3060", 12288)
    assert core.parse_gpu("") is None and core.parse_gpu("No devices were found") is None


def test_her_eyes_need_a_16_gb_card_unless_asked():
    big, small = ("RTX 5080", 16303), ("RTX 4070", 12282)
    assert core.vision_wanted("auto", "cuda", big)[0] is True
    on, why = core.vision_wanted("auto", "cuda", small)
    assert on is False and "--vision on" in why and "12 GB" in why
    assert core.vision_wanted("auto", "cpu", None)[0] is False
    assert core.vision_wanted("on", "cuda", small)[0] is True
    assert core.vision_wanted("off", "cuda", big) == (False, "skipped (--vision off)")


def test_settings_point_at_the_new_environments(checkout):
    cfg = core.settings_from_example("cuda", vision_on=True)
    venv = ["Scripts", "python.exe"] if core.IS_WIN else ["bin", "python"]
    assert Path(cfg["brain_python"]).parts == ("envs", "brain", *venv)
    assert Path(cfg["embedder"]["python"]).parts == ("envs", "embed", *venv)
    assert cfg["vision"]["enabled"] is True and cfg["speech_device"] == "cuda:0"
    cpu = core.settings_from_example("cpu", vision_on=False)
    assert cpu["speech_device"] == "cpu" and cpu["embedder"]["device"] == "cpu" and cpu["embedder"]["dtype"] == "fp32"
    assert cpu["vision"]["enabled"] is False
    assert cpu["services"]["tts"]["command"][0].endswith("python.exe")        # her voice server block is kept


def test_a_conda_environment_from_the_old_readme_is_used_as_it_is(checkout):
    if not core.IS_WIN:
        pytest.skip("conda -p layout is a Windows path")
    (checkout / "envs" / "brain").mkdir(parents=True)
    (checkout / "envs" / "brain" / "python.exe").write_bytes(b"")
    assert core.env_python(checkout / "envs" / "brain") == checkout / "envs" / "brain" / "python.exe"
    assert core.settings_from_example("cuda", False)["brain_python"] == str(Path("envs/brain/python.exe"))


def test_settings_json_is_created_once_and_never_changed(checkout):
    args = core.argparse.Namespace(check=False)
    core.ensure_settings("cuda", True, args)
    path = checkout / "settings.json"
    assert json.loads(path.read_text("utf-8"))["vision"]["enabled"] is True
    assert rows()["settings.json"][0] == "OK" and "created" in rows()["settings.json"][1]
    with pytest.raises(core.SetupError, match="does not exist"):              # its interpreters are not there yet
        core.ensure_settings("cuda", True, args)
    for name in ("brain", "speech", "embed", "vision"):
        exe = core.env_python(checkout / "envs" / name)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"")
    before = path.read_bytes()
    core.ROWS.clear()
    core.ensure_settings("cpu", False, args)                                  # other choices: the file stays as it is
    assert path.read_bytes() == before and "not changed" in rows()["settings.json"][1]


def test_brain_env_file_is_copied_and_a_missing_key_is_informational(checkout):
    args = core.argparse.Namespace(check=False)
    core.ensure_env_file(args)
    assert (checkout / "brain" / ".env").read_text("utf-8") == (checkout / ".env.example").read_text("utf-8")
    mark, detail = rows()["brain/.env"]
    assert mark == "--" and "no OpenRouter key yet" in detail and "wizard" in detail
    (checkout / "brain" / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-abcdefabcdef1234\n", "utf-8")
    core.ROWS.clear()
    core.ensure_env_file(args)
    assert rows()["brain/.env"] == ("OK", "OpenRouter key set (...1234)")


def test_onedrive_detection_uses_windows_own_variables(tmp_path, monkeypatch):
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    named_like_it = tmp_path / "C--Users-x-OneDrive-Desktop" / "OpenAmika"
    named_like_it.mkdir(parents=True)
    assert core.synced_by_onedrive(named_like_it) is False                    # the word alone is not a sync
    monkeypatch.setenv("OneDrive", str(tmp_path / "C--Users-x-OneDrive-Desktop"))
    assert core.synced_by_onedrive(named_like_it) is True


def test_check_on_an_empty_checkout_lists_everything_and_changes_nothing(checkout, monkeypatch):
    monkeypatch.setattr(core, "gpu_info", lambda: ("NVIDIA GeForce RTX 5080", 16303))
    monkeypatch.setattr(core, "hf_cached", lambda repo: None)
    monkeypatch.setattr(core, "ensure_speech_model", lambda args: (_ for _ in ()).throw(
        core.SetupError("speech/models/ is not complete: run this script without --check")))
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = core.main(["--check"])
    got = rows()
    assert rc == 1
    assert got["brain"][0] == "!!" and "not created yet" in got["brain"][1]
    assert got["settings.json"][0] == "!!" and got["speech model"][0] == "!!"
    assert got["memory model"][0] == "--" and "skipped: needs memory (embedder)" in got["memory model"][1]
    assert got["vision (her eyes)"] == ("--", "not installed yet: this script installs them on this GPU")
    assert "Not ready: run this script without --check" in out.getvalue()
    assert sorted(p.name for p in checkout.iterdir()) == [".env.example", "brain", "settings.example.json"]


def test_the_windows_entry_point_parses_and_needs_no_bom():
    ps1 = ROOT / "scripts" / "setup_windows.ps1"
    raw = ps1.read_bytes()
    assert raw.isascii(), "non-ASCII text in a .ps1 needs a UTF-8 BOM for Windows PowerShell 5.1"
    assert b"setup_core.py" in raw and b"@args" in raw
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell here")
    script = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($env:OA_PS1, [ref]$null, [ref]$e); "
              "if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }")
    import os
    import subprocess
    p = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                       env={**os.environ, "OA_PS1": str(ps1)}, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
