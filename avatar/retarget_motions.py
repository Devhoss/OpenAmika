#!/usr/bin/env python
"""Retarget Live2D motion recordings from one Cubism model to another.

A ``*.motion3.json`` is a bundle of parameter curves keyed by parameter id. When two models share
the standard Cubism ids (ParamAngleX, ParamEyeLOpen, ParamMouthOpenY, ParamBreath, ...), a recording
made for one model animates the other: curves for ids the target does not have are simply dropped.
This script does that filtering explicitly, so the target's folder holds clean, self-describing
motion files, and nothing is ever written into the source model's folder.

    python avatar/retarget_motions.py --source avatar/models/Amika --target avatar/models/Host \
        --map idle=idle1,idle2,idle3 --map talk=neutral1,neutral1.1

Each ``--map PREFIX=stem,stem,...`` writes ``PREFIX1.motion3.json``, ``PREFIX2.motion3.json``, ...
into the target folder from the listed source stems (a stem is the file name without
``.motion3.json``; a missing stem is skipped with a note). Parameters the target's physics drives
(the outputs in its ``*.physics3.json``) are left to physics unless ``--keep-physics`` is given.
``--scale ParamEyeLOpen=0.67`` rescales one curve's values (parameter ranges differ between models).

``--preset agent`` writes the set the OpenAmika agent avatar plays (idle / talk / listen / think /
happy / worried / surprise) from the Amika recordings' emotion families; see avatar/models/README.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Emotion-family recordings → the states the agent avatar's loop plays (see ui/live2d-engine.js).
AGENT_PRESET = {
    "idle": [f"idle{i}" for i in range(1, 16)],
    "talk": ["neutral1", "neutral1.1", "neutral1.2", "neutral1.3", "neutral1.4", "neutral1.5", "neutral1.6",
             "happy1", "happy1.1", "happy1.2", "happy1.3", "happy1.4", "happy1.5", "happy1.6"],
    "listen": ["caring1", "caring1.1", "caring1.2", "caring2", "caring2.1", "caring2.2", "caring3"],
    "think": ["mumbling", "mumbling1.1", "monotone", "whisper1", "firm1", "firm1.1"],
    "happy": ["happy2", "happy2.1", "happy2.2", "happy2.3", "happy3", "happy3.1", "happy3.2", "happy3.3", "happy3.4"],
    "worried": ["worried1", "worried1.1", "worried2", "worried2.1", "anxiety1", "anxiety1.1"],
    "surprise": ["surprise1", "surprise1.1", "surprise1.2", "surprise1.3", "surprise1.4", "surprise2", "surprise2.1"],
}
# Amika's eye-open recordings sit around 1.5 (her range is wider than the standard 0..1); her mouth
# peaks above 1. The standard ranges the host model uses want these brought down.
AGENT_PRESET_SCALES = {"ParamEyeLOpen": 0.67, "ParamEyeROpen": 0.67, "ParamMouthOpenY": 0.8}

# segment type → how many (t, v) points follow the marker
_POINTS_PER_SEGMENT = {0: 1, 1: 3, 2: 1, 3: 1}


def model_parameter_ids(model_dir: Path) -> set[str]:
    """Parameter ids of the model in `model_dir` (from its *.cdi3.json display info)."""
    for cdi in sorted(model_dir.glob("*.cdi3.json")) or sorted(model_dir.rglob("*.cdi3.json")):
        doc = json.loads(cdi.read_text(encoding="utf-8-sig"))
        return {p["Id"] for p in doc.get("Parameters", []) if isinstance(p, dict) and p.get("Id")}
    raise SystemExit(f"no *.cdi3.json in {model_dir}: cannot tell which parameters the target has")


def physics_output_ids(model_dir: Path) -> set[str]:
    out: set[str] = set()
    for phys in sorted(model_dir.glob("*.physics3.json")) or sorted(model_dir.rglob("*.physics3.json")):
        doc = json.loads(phys.read_text(encoding="utf-8-sig"))
        for setting in doc.get("PhysicsSettings", []):
            for o in setting.get("Output", []):
                dest = (o.get("Destination") or {}).get("Id")
                if dest:
                    out.add(dest)
    return out


def scale_segments(segments: list, factor: float) -> list:
    """Multiply every value in a curve's Segments list by `factor` (times stay)."""
    out = list(segments)
    if len(out) < 2:
        return out
    out[1] = out[1] * factor
    i = 2
    while i < len(out):
        kind = int(out[i])
        n = _POINTS_PER_SEGMENT.get(kind)
        if n is None:
            break
        for k in range(n):
            out[i + 2 + 2 * k] = out[i + 2 + 2 * k] * factor
        i += 1 + 2 * n
    return out


def count_segments(segments: list) -> tuple[int, int]:
    """(segment count, point count) of a curve, the way Meta.TotalSegmentCount/TotalPointCount count."""
    segs, points = 0, 1
    i = 2
    while i < len(segments):
        kind = int(segments[i])
        n = _POINTS_PER_SEGMENT.get(kind)
        if n is None:
            break
        segs += 1
        points += n
        i += 1 + 2 * n
    return segs, points


def retarget(doc: dict, keep_ids: set[str], scales: dict[str, float] | None = None) -> dict:
    """A copy of motion `doc` with only Parameter curves whose id is in `keep_ids`, values rescaled
    per `scales`, Meta counts recomputed and the source model's UserData dropped."""
    scales = scales or {}
    curves = []
    for c in doc.get("Curves", []):
        if not isinstance(c, dict) or c.get("Target") != "Parameter" or c.get("Id") not in keep_ids:
            continue
        nc = {k: v for k, v in c.items() if k != "Segments"}
        segs = list(c.get("Segments") or [])
        if c["Id"] in scales:
            segs = scale_segments(segs, float(scales[c["Id"]]))
        nc["Segments"] = segs
        curves.append(nc)
    total_segments = total_points = 0
    for c in curves:
        s, p = count_segments(c["Segments"])
        total_segments += s
        total_points += p
    meta = dict(doc.get("Meta") or {})
    meta.update({"CurveCount": len(curves), "TotalSegmentCount": total_segments,
                 "TotalPointCount": total_points, "UserDataCount": 0, "TotalUserDataSize": 0})
    return {"Version": doc.get("Version", 3), "Meta": meta, "Curves": curves}


def write_set(source: Path, target: Path, mapping: dict[str, list[str]], keep_ids: set[str],
              scales: dict[str, float] | None = None, echo=print) -> list[Path]:
    written: list[Path] = []
    for prefix, stems in mapping.items():
        n = 0
        for stem in stems:
            src = source / f"{stem}.motion3.json"
            if not src.is_file():
                echo(f"  skip {prefix}: no {src.name} in {source}")
                continue
            doc = json.loads(src.read_text(encoding="utf-8-sig"))
            out = retarget(doc, keep_ids, scales)
            if not out["Curves"]:
                echo(f"  skip {src.name}: none of its curves fit the target")
                continue
            n += 1
            dest = target / f"{prefix}{n}.motion3.json"
            dest.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            written.append(dest)
        echo(f"  {prefix}: {n} motion(s)")
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, type=Path, help="folder of the model the recordings were made for")
    ap.add_argument("--target", required=True, type=Path, help="folder of the model that should play them")
    ap.add_argument("--map", action="append", default=[], metavar="PREFIX=stem,stem,...")
    ap.add_argument("--preset", choices=["agent"], help="write the OpenAmika agent-avatar set")
    ap.add_argument("--scale", action="append", default=[], metavar="ParamId=factor")
    ap.add_argument("--keep-physics", action="store_true", help="also carry curves for the target's physics outputs")
    args = ap.parse_args(argv)

    mapping: dict[str, list[str]] = {}
    scales: dict[str, float] = {}
    if args.preset == "agent":
        mapping.update(AGENT_PRESET)
        scales.update(AGENT_PRESET_SCALES)
    for item in args.map:
        prefix, _, stems = item.partition("=")
        if not prefix or not stems:
            ap.error(f"--map wants PREFIX=stem,stem,... (got {item!r})")
        mapping[prefix.strip()] = [s.strip() for s in stems.split(",") if s.strip()]
    for item in args.scale:
        pid, _, factor = item.partition("=")
        try:
            scales[pid.strip()] = float(factor)
        except ValueError:
            ap.error(f"--scale wants ParamId=factor (got {item!r})")
    if not mapping:
        ap.error("nothing to write: give --map or --preset")

    keep = model_parameter_ids(args.target)
    if not args.keep_physics:
        keep -= physics_output_ids(args.target)
    print(f"target keeps {len(keep)} parameter(s): {', '.join(sorted(keep))}")
    args.target.mkdir(parents=True, exist_ok=True)
    written = write_set(args.source, args.target, mapping, keep, scales)
    print(f"wrote {len(written)} motion file(s) into {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
