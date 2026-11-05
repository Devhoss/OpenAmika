# Avatar: Live2D models, banks, lipsync

The engine is `ui/live2d-engine.js` — pixi-live2d-display 0.4.0 on PixiJS legacy 6.5.8 with the
Cubism Core, all vendored in `ui/vendor/` (no CDN, works offline). It is a direct port of the
battle-tested MekaHime portable viewer.

## The sync model (why her face never drifts)

Every TTS sentence arrives as one message: `{audio, emotion, pause_after, text}`. The engine queues
them and applies the **emotion at playback start of its own sentence** — expression roll, motion roll,
subtitle and voice begin on the same frame. The surface tier's `tone` may turn her face *early* while
nothing is speaking (a reaction before the voice), never during speech.

## Installing a model

```
avatar/models/MyChar/
├── MyChar.model3.json      ← exactly one per folder
├── MyChar.moc3 / physics / textures
├── idle1.motion3.json …    ← idle* motions loop when she's not speaking
├── casual_happy.motion3.json, casual_happy.2.motion3.json …
├── *.exp3.json             ← expressions (optional)
└── banks.json              ← written by Setup → Avatar (optional)
```

- If `model3.json` declares no `Motions`/`Expressions`, the server injects the sibling files on the
  fly (otherwise pixi-live2d-display would ignore them).
- Rescan on Setup → Avatar; the Use page's model picker switches models as a stage swap (both stay
  preloaded).
- Models are licensed assets and are **gitignored**: Amika's and the paper doll come from
  `scripts/setup_tts.py` (personal-use license), any other model you bring yourself (Cubism Editor exports, or
  Live2D's sample models under their license).

## The agent avatar: the paper doll (`role: agent`)

A model whose `banks.json` says `"role": "agent"` is not one she wears: it stands in for her helpers (task agents
such as Hermes Agent and nanoMuse, see [TOOLS.md](TOOLS.md)). OpenAmika's own is MekaHime's paper doll, a
folded-paper shrine doll that `scripts/setup_tts.py` installs into `avatar/models/Host/` (Hugging Face
`MekaHime/MekaHime_Amika`, `live2d/Host/`, 70 files, same license as her model):

```
avatar/models/Host/
├── host.model3.json        ← names the moc, texture, physics, display info; no motion list (injected)
├── host.moc3, host.physics3.json, host.cdi3.json, host.2048/texture_00.png
├── idle1…15, talk1…14, listen1…7, think1…6, happy1…9, worried1…6, surprise1…7 .motion3.json
└── banks.json              ← {"config": {"display_name": "Host (agent avatar)", "role": "agent",
                                          "scale": 0.42, "nx": 0.84, "ny": 0.70, "head_y": 0.26, …}, "banks": {}}
```

- **Found by** the gateway's registry (`GET /api/avatar/models` marks it `"role": "agent"` and passes its `stage`
  placement). Her model picker on the Use page never offers it; the Setup page lists it as `Host (agent avatar)
  (agent)`, and a save of its banks there keeps its role and placement. `launch.py --check` reports it on its own
  `paper doll` row instead of as a model of hers. The first agent model in folder-name order is used; one doll
  stands in for every helper.
- **Seated** by `ui/live2d-engine.js` (`loadAgent`, after her own model has loaded) whenever the Use page's poll of
  `/api/build_f/tools` finds the tool layer on and an agent server enabled: it slides in at the bottom right and
  slides away when none is. Placement comes from `banks.json` (`scale` = height as a fraction of the stage,
  `nx`/`ny` = position, `head_y` = where its head sits, for the bubbles and glances); a drag or a zoom is kept per
  browser, "Re-center" clears it.
- **Driven** by the tool dialogue, with no banks: motions are picked by file-name prefix, by its own state loop
  only (the library's random idle motions are switched off on the doll). Its base state is `think*` while any
  job runs (else `idle*`); each new dialogue line (but the bridge's own `internal` steps, such as `sent`) becomes
  a speech bubble, the helper's out of
  the doll (`talk*`, or the line's mood: `happy*` on a result, `worried*` on an error or a stop, `surprise*` on a
  question or an approval), hers out of her while the doll listens (`listen*`). A missing family falls back to
  another (`think` → `idle`, `talk` → `happy`, `listen` → `idle`, `happy` → `talk`, `worried` and `surprise` →
  `listen`), and with none of them any motion plays.
- **Your own doll**: any Cubism model with that `banks.json` role and motions named by those prefixes.
  `avatar/retarget_motions.py --preset agent` makes them from her recordings when the two share the standard
  parameter ids (the paper doll's set was made that way); see `avatar/models/README.md`.

## Emotion banks

For each of the 69 emotion keys, a bank entry picks what plays:

```jsonc
{
  "config": { "display_name": "MyChar", "mouth_open_param": "ParamMouthOpenY",
              "mouth_form_param": "ParamMouthForm", "scale": 0.85 },
  "banks": {
    "casual_happy":  { "emotes":    [{ "name": "smile",  "likelihood": 80 }],
                       "movements": [{ "name": "casual_happy",   "likelihood": 100 },
                                     { "name": "casual_happy.2", "likelihood": 60 }] },
    "__idle__":      { "movements": [{ "name": "idle1", "likelihood": 100 }] }
  }
}
```

- `likelihood` is an independent 0–100 roll per entry (the original engine's behavior); motions chain
  back-to-back while she speaks, idles resume ~0.4 s after the audio queue drains, and immediate
  repeats are avoided.
- **Convention fallback:** any key without a bank entry automatically uses motions named after it
  (`casual_happy.motion3.json`, `casual_happy.2.…`). Zero-config if you name your motions that way.
- No `__idle__` entry → files starting with `idle`; none of those → all motions.

## Lipsync

Volume + formant analysis of the actual audio (no phoneme data needed): an FFT classifies A/I/U/E/O
and drives `ParamMouthOpenY` (+ `ParamMouthForm` when the model has it). Both parameter names are
**configurable per model** in `banks.json → config` — set them if your rig names its mouth params
differently, otherwise only the jaw moves. Lipsync gain and output volume persist per browser.
