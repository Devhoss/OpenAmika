"""launch.py pre-flight on a fresh checkout: it must never crash, and it must name what is missing and how to get it,
her voice and her Live2D model above all. Offline: nothing is started and no port of a running stack is touched."""
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("openamika_launch_preflight", ROOT / "launch.py")
launch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launch)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def example_cfg(tmp_path):
    cfg = json.loads((ROOT / "settings.example.json").read_text("utf-8"))
    for holder, key in ((cfg, "brain_python"), (cfg, "speech_python"), (cfg["embedder"], "python"), (cfg["vision"], "python")):
        holder[key] = str(tmp_path / "envs" / "missing" / "python.exe")
    cfg["tts_url"] = f"http://127.0.0.1:{free_port()}"             # never the owner's voice server
    return cfg


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    """launch.py looking at an empty checkout: no environments, no voice, no models."""
    monkeypatch.setattr(launch, "TTS_ENGINE", tmp_path / "vendor" / "tts-engine")
    monkeypatch.setattr(launch, "AVATAR_MODELS", tmp_path / "avatar" / "models")
    monkeypatch.setattr(launch, "listeners", lambda: {})
    return tmp_path


def checks(report):
    return {c["name"]: (c["level"], c["detail"]) for c in report.checks}


def services_for(cfg, engine):
    cfg = json.loads(json.dumps(cfg))
    cfg["services"]["tts"]["cwd"] = str(engine)
    cfg["services"]["tts"]["command"][0] = str(engine / ".venv" / "Scripts" / "python.exe")
    return cfg, launch.start_plan(cfg, None)


def test_nothing_installed_is_named_with_its_fix_and_never_crashes(fresh):
    cfg, (services, held, agents) = services_for(example_cfg(fresh), fresh / "vendor" / "tts-engine")
    emb, vis = cfg["embedder"], {**cfg["vision"], "enabled": True}
    report = launch.preflight(cfg, emb, vis, None, None, services, agents, [], deep=True, held_iot=held)
    got = checks(report)
    assert got["brain python"][0] == "err" and "setup_windows.ps1" in got["brain python"][1]
    assert got["TTS (voice)"] == ("err", "her voice server is not installed — run python " + str(Path("scripts/setup_tts.py")))
    assert got["her voice"][0] == "err" and "type yes to accept her license" in got["her voice"][1]
    assert got["Live2D model"][0] == "err" and "setup_tts.py" in got["Live2D model"][1]
    assert "embedder check" not in got and "vision check" not in got     # their python rows already say it


def test_a_missing_key_is_a_warning_the_wizard_can_fix(fresh, monkeypatch):
    monkeypatch.setattr(launch, "ROOT", fresh)                           # no brain/.env, default (OpenRouter) roles
    cfg, (services, held, agents) = services_for(example_cfg(fresh), fresh / "vendor" / "tts-engine")
    got = checks(launch.preflight(cfg, None, None, None, None, services, agents, [], held_iot=held))
    assert got["API keys"][0] == "warn" and "wizard" in got["API keys"][1]


def install_voice(engine, clips=2, missing_clip=False):
    voice = engine / "Speakers" / "MekaHime_Amika"
    (voice / "references").mkdir(parents=True)
    (voice / "character.json").write_text(json.dumps({"character_id": "MekaHime_Amika"}), "utf-8")
    for name in ("MekaHime_Amika.ckpt", "MekaHime_Amika.pth"):
        (voice / name).write_bytes(b"w")
    table = {f"tag{i}": {"audio": f"clip{i}.wav"} for i in range(clips)}
    (voice / "references" / "emotions.json").write_text(json.dumps(table), "utf-8")
    for i in range(clips - (1 if missing_clip else 0)):
        (voice / "references" / f"clip{i}.wav").write_bytes(b"RIFF")
    venv = engine / ".venv" / "Scripts"
    venv.mkdir(parents=True)
    (venv / "python.exe").write_bytes(b"")
    gsv = engine / "GPT-SoVITS" / "GPT_SoVITS"
    for rel in ["TTS_infer_pack/TTS.py", *("pretrained_models/" + p for p in launch.TTS_PRETRAINED)]:
        (gsv / rel).parent.mkdir(parents=True, exist_ok=True)
        (gsv / rel).write_bytes(b"x")


def install_model(models, name="Amika", role=None):
    folder = models / name
    folder.mkdir(parents=True)
    (folder / f"{name}V4.model3.json").write_text("{}", "utf-8")
    (folder / "idle1.motion3.json").write_text("{}", "utf-8")
    if role:
        (folder / "banks.json").write_text(json.dumps({"config": {"role": role}}), "utf-8")


def test_installed_voice_and_model_are_green(fresh):
    engine = fresh / "vendor" / "tts-engine"
    install_voice(engine)
    install_model(fresh / "avatar" / "models")
    cfg, (services, held, agents) = services_for(example_cfg(fresh), engine)
    got = checks(launch.preflight(cfg, None, None, None, None, services, agents, [], held_iot=held))
    assert got["TTS (voice)"][0] == "warn" and "will be started" in got["TTS (voice)"][1]
    assert got["her voice"] == ("ok", "MekaHime_Amika · 2/2 emotion clips")
    assert got["Live2D model"] == ("ok", "Amika (AmikaV4.model3.json, 1 motions)")


def test_an_incomplete_voice_server_and_an_agent_only_stage(fresh):
    engine = fresh / "vendor" / "tts-engine"
    install_voice(engine, missing_clip=True)
    (engine / "GPT-SoVITS" / "GPT_SoVITS" / "pretrained_models" / "sv" / "pretrained_eres2netv2w24s4ep4.ckpt").unlink()
    install_model(fresh / "avatar" / "models", "Host", role="agent")         # her helpers' stand-in is not her body
    cfg, (services, held, agents) = services_for(example_cfg(fresh), engine)
    got = checks(launch.preflight(cfg, None, None, None, None, services, agents, [], held_iot=held))
    assert got["TTS (voice)"][0] == "err" and "pretrained models missing" in got["TTS (voice)"][1]
    assert got["her voice"][0] == "warn" and "1/2 emotion clips" in got["her voice"][1]
    assert got["Live2D model"][0] == "err"


def test_a_voice_server_elsewhere_is_not_checked_for_files(fresh):
    cfg = example_cfg(fresh)
    del cfg["services"]["tts"]
    services, held, agents = launch.start_plan(cfg, None)
    got = checks(launch.preflight(cfg, None, None, None, None, services, agents, [], held_iot=held))
    assert got["TTS (voice)"][0] == "warn" and "her voice" not in got


def test_check_on_a_fresh_copy_prints_one_line_per_check_and_a_result(tmp_path):
    """The real command, on a copy of the checkout with settings.json and nothing installed."""
    for rel in ("launch.py", "brain/iot_disclaimer.py", "brain/embed_server.py", "config/build_f.json",
                "config/llm_roles.json", "config/word_replace.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, tmp_path / rel)
    cfg = example_cfg(tmp_path)
    for key, holder in (("brain_port", cfg), ("chat_port", cfg)):
        holder[key] = free_port()
    cfg["embedder"]["port"] = free_port()
    cfg["vision"]["port"] = free_port()
    (tmp_path / "settings.json").write_text(json.dumps(cfg), "utf-8")
    p = subprocess.run([sys.executable, "-B", "-X", "utf8", str(tmp_path / "launch.py"), "--check"], cwd=tmp_path,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    out = p.stdout
    assert p.returncode == 1, out + p.stderr
    assert "could not start" not in out + p.stderr
    assert "  !! brain python" in out and "  !! her voice" in out and "  !! Live2D model" in out
    assert "  -- API keys" in out
    assert out.rstrip().endswith("result: fix the !! lines above (-- lines are informational)")
    assert (tmp_path / "logs" / "preflight.json").is_file()
