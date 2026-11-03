# Architecture

Interactive version: open **/flow** in the running app (pan/zoom node map with five views).

```
            browser (ui/: Setup · Tools · Use  ·  /flow architecture map)
                 │  WebSocket /ws/dialog (JSON + binary PCM)  ·  HTTP /api/*
┌────────────────▼──────────────────────────────────────────────────────────┐
│  SPEECH LAYER  speech/api.py        FastAPI · default port 53000          │
│   SenseVoiceSmall STT (local GPU) + Silero VAD + barge-in + carry         │
│   streaming parsers (hane_parser) · sentence splitter · word replacer     │
│   TTS client (emotion tag + SFX + pauses) · voice adapter                 │
│   avatar registry/banks · UI pages · control proxies                      │
└───────┬──────────────────────────────────────────────┬───────────────────┘
        │ Socket.IO client                              │ Socket.IO client
        ▼                                               ▼
┌───────────────────────────────┐        ┌──────────────────────────────────┐
│  BRAIN  brain/webui.py        │        │  TTS server (external, :8002)    │
│  Flask-SocketIO · port 5202   │        │  e.g. Vanadis Evo (GPT-SoVITS)   │
│  surface/deep/reflection      │        │  tts_request_gateway→audio_ready │
│  prompts (cache-first)        │        └──────────────────────────────────┘
│  evidence memory (memory_f/)  │
│  usage ledger + settlement    │──► OpenRouter / Alibaba Model Studio (LLMs)
│  tool phase (f_tools, MCP) ───┼──► MCP servers (stdio/HTTP: IoT, HA, …)
└───────┬───────────────────────┘
        ▼ POST /embed
┌───────────────────────────────┐
│  EMBEDDER  embed_server.py    │  bge-m3 → 768 dims · port 8011 · local GPU
└───────────────────────────────┘
```

**Turn flow (voice):** mic PCM → VAD/partials → commit → brain `message` → surface 4-liner →
(tool phase, if triggered) → deep stream → `hane_parser` splits `[emotion]"sentences"` →
TTS per sentence (voice adapter may remap the tag) → `tts_audio {audio_b64, emotion, pause_after}`
to the browser → the avatar engine plays audio + fires expression/motion at playback start →
`tts_playing/finished` acks feed barge-in anchoring and the latency timeline → memory writer archives
the protected turn; reflection watches and may open the next turn proactively.

**Processes** are started by `launch.py` (embedder → brain → speech, each health-gated, logs in
`logs/`), stopped by `Stop_OpenAmika.ps1` strictly by script path. Ports, interpreters, TTS URL and
the embedder block live in `settings.json`.

**Key contracts**
- `po_contract.py` — one module installed identically in `brain/` and `speech/`: emotion vocabulary
  (69 keys), SFX names, deep/surface parsing, template self-check. Parity is test-enforced.
- Socket protocol (brain↔speech): `message/inject/protect/commit/speech_done` in;
  `begin/surface_chunk/surface_done/deep_chunk/deep_done/end/error` + `call_receipt` out.
- Browser protocol (speech↔UI): `ready/partial/commit/ai_begin/ai_surface_done/ai_deep_done/ai_end/`
  `tts_audio/tts_sfx/ai_call_receipt/ai_timeline/...` (see `brain/PROTOCOL.md` and `/flow`).
- Avatar feed: one `{audio, emotion, pause_after}` message per sentence — the atomic coupling that
  keeps face and voice in sync.

**Data on disk (all gitignored):** `memory_f/` (evidence store: `state.json` with its vector sidecar
`state.vectors-*.f32`, pending jobs in `jobs/` and finished ones under `jobs/completed/`, quarantine,
receipts, ledger; recall scores the vectors with numpy, `memory_numpy_recall: false` in `config/build_f.json`
falls back to the per-item Python cosine),
`brain/memories/` (rolling transcript), `logs/`, `brain/.env`, `settings.json`, `config/tools.json`,
`avatar/models/*`.
