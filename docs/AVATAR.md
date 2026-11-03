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
- Models are licensed assets and are **gitignored**: Amika's comes from `scripts/setup_tts.py` (personal-use
  license), any other model you bring yourself (Cubism Editor exports, or Live2D's sample models under their
  license).

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
