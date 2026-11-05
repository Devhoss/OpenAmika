"""The gateway's Live2D registry: character vs agent models, placement hints, and a Setup-page save
that must not strip what it does not edit. Functions are lifted from speech/api.py by source so the
heavy gateway module (STT, VAD, sockets) never imports. Also how the Use page plays the paper doll (by source)."""
import ast
import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "speech" / "api.py"
WANTED = {"_AVATAR_STAGE_KEYS", "_AVATAR_EDITABLE_KEYS", "_avatar_stage_config", "_avatar_dir",
          "_avatar_model3", "_avatar_banks_doc", "avatar_models", "avatar_banks_set"}


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


def _load(models_dir: Path):
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    pieces = []
    for node in tree.body:
        names = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if names & WANTED:
            pieces.append(ast.get_source_segment(text, node))
    assert len(pieces) == len(WANTED), f"missing from api.py: {WANTED - set().union(*[set(p.split('(')[0].split()[-1:]) for p in pieces])}"
    ns = {"Path": Path, "json": json, "os": __import__("os"), "Optional": __import__("typing").Optional, "HTTPException": HTTPException,
          "Request": object, "logger": SimpleNamespace(warning=lambda *a, **k: None),
          "voice_adapter": SimpleNamespace(V2_KEYS=["casual_happy", "polite_neutral"]),
          "AVATAR_MODELS_DIR": models_dir}
    exec("\n\n".join(pieces), ns)
    return ns


def _model(models_dir: Path, name: str, config: dict | None = None, banks: dict | None = None):
    d = models_dir / name
    d.mkdir(parents=True)
    (d / f"{name}.model3.json").write_text("{}", encoding="utf-8")
    (d / "idle1.motion3.json").write_text("{}", encoding="utf-8")
    if config is not None or banks is not None:
        (d / "banks.json").write_text(json.dumps({"config": config or {}, "banks": banks or {}}), encoding="utf-8")
    return d


def test_registry_tells_her_models_from_the_agent_avatar_and_carries_placement(tmp_path):
    _model(tmp_path, "Amika", {"display_name": "Amika", "busy_expression": "Loading"})
    _model(tmp_path, "Host", {"display_name": "Host (agent avatar)", "role": "agent",
                              "scale": 0.42, "nx": 0.84, "ny": 0.7, "head_y": 0.26, "mouth_open_param": "ParamMouthOpenY"})
    _model(tmp_path, "Plain")
    ns = _load(tmp_path)
    doc = asyncio.run(ns["avatar_models"]())
    by_id = {m["id"]: m for m in doc["models"]}
    assert by_id["Amika"]["role"] == "character" and by_id["Plain"]["role"] == "character"
    assert by_id["Host"]["role"] == "agent"
    assert by_id["Host"]["stage"] == {"scale": 0.42, "nx": 0.84, "ny": 0.7, "head_y": 0.26}
    assert by_id["Amika"]["stage"] == {"busy_expression": "Loading"}
    assert by_id["Plain"]["stage"] == {}


def test_stage_config_ignores_nonsense():
    ns = _load(Path("."))
    cfg = ns["_avatar_stage_config"]({"scale": "big", "nx": True, "ny": 7.5, "head_y": 0.3, "busy_expression": "  "})
    assert cfg == {"head_y": 0.3}


def test_a_setup_page_save_keeps_role_and_placement_it_does_not_edit(tmp_path):
    _model(tmp_path, "Host", {"display_name": "Host", "role": "agent", "nx": 0.84, "ny": 0.7, "head_y": 0.26, "scale": 0.42})
    ns = _load(tmp_path)

    class Req:
        async def json(self):
            return {"config": {"display_name": "Hermes doll", "scale": 0.5, "mouth_open_param": "ParamMouthOpenY"},
                    "banks": {"casual_happy": {"emotes": [], "movements": [{"name": "talk1", "likelihood": 80}]},
                              "bogus_key": {"movements": []}}}

    out = asyncio.run(ns["avatar_banks_set"]("Host", Req()))
    assert out == {"ok": True}
    saved = json.loads((tmp_path / "Host" / "banks.json").read_text(encoding="utf-8"))
    assert saved["config"] == {"display_name": "Hermes doll", "scale": 0.5, "mouth_open_param": "ParamMouthOpenY",
                               "role": "agent", "nx": 0.84, "ny": 0.7, "head_y": 0.26}
    assert saved["banks"] == {"casual_happy": {"emotes": [], "movements": [{"name": "talk1", "likelihood": 80}]}}


def test_tts_audio_tells_the_page_which_tier_spoke():
    # the Use page drops her "loading" emote when the first deep sentence starts playing
    text = SOURCE.read_text(encoding="utf-8")
    block = text[text.index('"type": "tts_audio",\n            "turn_id": tts_turn_id'):][:1200]
    assert '"kind": inflight.get("kind")' in block


def _function(source: str, signature: str) -> str:
    body = source[source.index(signature):]
    return body[:body.index("\n  }\n") + 4]


def test_the_paper_doll_plays_only_what_its_state_loop_picks():
    """The gateway serves a model3.json without Motions (the paper doll's) with every loose motion in one "Idle"
    group, and the Live2D library starts a random motion of its idle group whenever nothing plays: the doll was
    surprised while idle and happy mid-job, and its state loop (think* while a job runs, idle* otherwise) rarely got
    a turn. loadAgent points the library's idle group at one that does not exist, before the loop starts."""
    api = SOURCE.read_text(encoding="utf-8")
    assert 'refs["Motions"] = {"Idle": motions}' in api                 # why: every loose motion is "Idle"
    vendor = (ROOT / "ui" / "vendor" / "cubism4.min.js").read_text(encoding="utf-8")
    assert "startRandomMotion(this.groups.idle" in vendor                # what the library plays when nothing does
    engine = (ROOT / "ui" / "live2d-engine.js").read_text(encoding="utf-8")
    load = _function(engine, "function loadAgent()")
    guard = load.index("mm.groups.idle = AGENT_NO_IDLE_GROUP")
    assert load.index("injectExternalMotions(mm, opt)") < guard < load.index("startAgentLoop()")
    group = re.search(r'const AGENT_NO_IDLE_GROUP = "([^"]*)"', engine).group(1)
    assert group and group not in ("Idle", "idle")
    # the loop itself picks by state, and only when nothing of its own plays
    loop = _function(engine, "function startAgentLoop()")
    assert "if (!agentMotionPlaying()) agentPlay(agentPick(agent.state));" in loop


def test_the_paper_doll_never_says_the_bridges_own_steps():
    """Steps the agents bridge makes itself ("sent", "started", "stop requested"…) carry `internal`: the Use page
    keeps them in the tool dialogue but gives them no speech bubble on the doll."""
    page = (ROOT / "ui" / "use.js").read_text(encoding="utf-8")
    bubbles = _function(page, "function bubbleNewLines(entries)")
    assert "!e.internal" in bubbles
