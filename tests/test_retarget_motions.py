"""avatar/retarget_motions.py: recordings made for one Cubism model, replayed by another."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "avatar"))
import retarget_motions as rm  # noqa: E402


def _motion(curves, duration=2.0):
    return {"Version": 3,
            "Meta": {"Duration": duration, "Fps": 60.0, "Loop": True, "AreBeziersRestricted": True,
                     "CurveCount": len(curves), "TotalSegmentCount": 99, "TotalPointCount": 99,
                     "UserDataCount": 1, "TotalUserDataSize": 7},
            "Curves": curves,
            "UserData": [{"Time": 0.5, "Value": "blink"}]}


LINEAR = [0, 0.2, 0, 1.0, 0.8]                        # start (0, .2) → linear to (1, .8)
BEZIER = [0, 10, 1, 0.3, 12, 0.6, 14, 1.0, 16]         # start (0, 10) → one bezier segment to (1, 16)
STEPPED = [0, 1, 2, 0.5, 0, 3, 1.0, 1]                 # stepped then inverse-stepped


def test_only_the_targets_parameters_survive_and_counts_are_recomputed():
    doc = _motion([
        {"Target": "Parameter", "Id": "ParamAngleX", "Segments": BEZIER},
        {"Target": "Parameter", "Id": "Param33", "Segments": LINEAR},            # hers only
        {"Target": "PartOpacity", "Id": "PartArmL", "Segments": LINEAR},         # her parts
        {"Target": "Parameter", "Id": "ParamEyeLOpen", "Segments": STEPPED},
    ])
    out = rm.retarget(doc, {"ParamAngleX", "ParamEyeLOpen", "ParamBreath"})
    assert [c["Id"] for c in out["Curves"]] == ["ParamAngleX", "ParamEyeLOpen"]
    assert out["Meta"]["CurveCount"] == 2
    assert out["Meta"]["TotalSegmentCount"] == 1 + 2            # one bezier + two stepped
    assert out["Meta"]["TotalPointCount"] == (1 + 3) + (1 + 1 + 1)
    assert out["Meta"]["UserDataCount"] == 0 and "UserData" not in out
    assert out["Meta"]["Duration"] == 2.0 and out["Meta"]["Loop"] is True


def test_values_rescale_but_times_do_not():
    doc = _motion([{"Target": "Parameter", "Id": "ParamEyeLOpen", "Segments": BEZIER}])
    out = rm.retarget(doc, {"ParamEyeLOpen"}, {"ParamEyeLOpen": 0.5})
    assert out["Curves"][0]["Segments"] == [0, 5, 1, 0.3, 6, 0.6, 7, 1.0, 8]
    assert rm.scale_segments(STEPPED, 2) == [0, 2, 2, 0.5, 0, 3, 1.0, 2]


def test_write_set_numbers_files_per_prefix_and_leaves_physics_to_physics(tmp_path):
    source, target = tmp_path / "Amika", tmp_path / "Host"
    source.mkdir(); target.mkdir()
    (target / "host.cdi3.json").write_text(json.dumps({"Parameters": [
        {"Id": "ParamAngleX"}, {"Id": "ParamMouthOpenY"}, {"Id": "ParamHairFront"}, {"Id": "Param"}]}), encoding="utf-8")
    (target / "host.physics3.json").write_text(json.dumps({"PhysicsSettings": [
        {"Input": [{"Source": {"Id": "ParamAngleX"}}], "Output": [{"Destination": {"Id": "ParamHairFront"}}, {"Destination": {"Id": "Param"}}]}]}),
        encoding="utf-8")
    for stem in ("idle1", "idle2", "neutral1"):
        (source / f"{stem}.motion3.json").write_text(json.dumps(_motion([
            {"Target": "Parameter", "Id": "ParamAngleX", "Segments": LINEAR},
            {"Target": "Parameter", "Id": "ParamHairFront", "Segments": LINEAR},   # Amika's unrelated param, same id
            {"Target": "Parameter", "Id": "Param", "Segments": LINEAR},
        ])), encoding="utf-8")
    (source / "mouthonly.motion3.json").write_text(json.dumps(_motion([
        {"Target": "Parameter", "Id": "ParamBodyAngleX", "Segments": LINEAR}])), encoding="utf-8")

    rc = rm.main(["--source", str(source), "--target", str(target),
                  "--map", "idle=idle1,idle2,missing", "--map", "talk=neutral1,mouthonly"])
    assert rc == 0
    names = sorted(p.name for p in target.glob("*.motion3.json"))
    assert names == ["idle1.motion3.json", "idle2.motion3.json", "talk1.motion3.json"]   # missing + no-fit skipped
    doc = json.loads((target / "talk1.motion3.json").read_text(encoding="utf-8"))
    assert [c["Id"] for c in doc["Curves"]] == ["ParamAngleX"]                            # knots + paper left to physics
    assert (source / "idle1.motion3.json").read_text(encoding="utf-8").count("ParamHairFront") == 1   # source untouched


def test_agent_preset_covers_every_state_the_engine_plays():
    engine = (ROOT / "ui" / "live2d-engine.js").read_text(encoding="utf-8")
    for prefix in ("idle", "talk", "listen", "think", "happy", "worried", "surprise"):
        assert prefix in rm.AGENT_PRESET and rm.AGENT_PRESET[prefix]
        assert f'"{prefix}"' in engine or f"'{prefix}'" in engine
    with pytest.raises(SystemExit):
        rm.main(["--source", "x", "--target", "y"])   # nothing to write
