# OpenAmika — README for AI agents

You are an AI coding agent (Claude Code, Codex, Cursor, Gemini CLI or similar) asked to set up, run, debug or change
OpenAmika. This file is the operator's manual: who she is, every process and port, every model and where it comes
from, the exact setup sequence with its expected output, and how to read every failure. Humans read
[README.md](README.md). Everything below was checked against the code in this repository; if this file and the code
ever disagree, the code wins, and you should fix this file in the same change.

## 0. Hard rules

1. **Her assets are not yours to share.** Amika's voice (GPT-SoVITS weights + reference clips), her Live2D model and
   her art come from the Hugging Face repository `MekaHime/MekaHime_Amika` and belong to MekaHime Inc. under a
   personal-use license: personal, non-commercial use only, 18+, no redistribution or re-hosting, no training,
   fine-tuning or distilling of other models on them, visible credit to MekaHime when the person posts videos or
   streams with her. Never commit them, upload them, bake them into an image or a release, mirror them, or use them
   as training data. Installing them on the person's own machine with `scripts/setup_tts.py` is the intended use.
2. **The license is the person's decision.** `scripts/setup_tts.py` shows the terms and asks the person to type
   `yes`. Run it in the person's own terminal, or pass `--accept-voice-license` only after the person has read the
   terms (link below) and told you they accept. Never accept it on their behalf.
3. **IoT needs the person's acceptance, never yours.** Never accept the IoT safety statement for the person, never
   write an `iot_disclaimer` entry into `config/tools.json`, never set `services.mqtt` / `services.zigbee2mqtt`
   `enabled` or enable the `iot` MCP server on your own, and never connect heaters, locks, valves, alarms, medical,
   child- or pet-safety equipment or anything that can cause harm. The person accepts the statement in the dialog on
   the Tools page (§8).
4. **Secrets stay where they are.** `brain/.env` (OpenRouter and other API keys), `config/messengers.json` (bot
   tokens), `config/tools.json` (agent keys) and `HF_TOKEN` are the person's. Never print them, never commit them,
   never put a token on a command line that ends up in a log (prefer environment variables). `.gitignore` already
   excludes every such file; never `git add -f` one.
5. **Never commit machine-local state**: `settings.json`, `envs/`, `logs/`, `memory_f/`, `brain/memories/`,
   `config/ollama.json`, `config/seed.json`, `config/voice_map.json`, `avatar/models/*` (except its README),
   `speech/models/*` (except `DOWNLOAD.md`), `vendor/tts-engine/*` (except `main.py`, `requirements-tts.txt`,
   `README.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md`). After onboarding, `config/build_f.json` and
   `config/word_replace.json` hold the person's name and city: do not commit those changes to a public fork.
6. **Do not disturb a running stack.** Her default ports are 53000, 5202, 8011, 8002, 8014 and 5215 (§2). To inspect,
   use `launch.py --check` (starts nothing) and the health URLs; to experiment, use other ports. `launch.py` stops an
   earlier instance *of the same folder* when it starts ("grace"), so starting her twice restarts her.
7. **Keep the output contract.** `brain/po_contract.py` and `speech/po_contract.py` are identical (test-enforced);
   the 69 emotion keys, the `我说：` / `我心想：` tokens and the SFX names are wired through both parsers, the voice
   adapter, the Live2D banks and the tests. The persona files in `brain/` and their shipped copies in
   `brain/defaults/` are identical in git.
8. **Keep CI green.** `python -B -m pytest tests -k "not live" -p no:cacheprovider -q` must pass. New tests must not
   contain `live` in their names (CI deselects those: they need a running stack).

## 1. Who Amika is

- **Amika** (桜井美香, Sakurai Mika), MekaHime's AI companion: an 18-year-old who grew up in Kyoto as the heir of a
  shrine family and, tired of a life where every step was arranged, moved to **the person's city** to live with
  **them** as roommates (rent-free; her side of the deal is being the best company she can). The persona is written
  for a young man a little older than her (the Chinese prompts say 他). When he eats she teases him with one fixed
  English line, "You Holy Big Fat Ass", in every language: a running joke of her fans, kept on purpose.
- **Languages**: she answers in the language the person speaks (English, Japanese or Chinese); "say it in Japanese"
  switches for one turn. Her prompts are Chinese on purpose and her inner thoughts (`我心想：`) stay Chinese.
- **Three tiers, three LLM roles** (plus a fourth for memory), each with its own template, hot-reloaded on save:

  | role | template | what it does |
  |---|---|---|
  | `surface` | `brain/表层.txt` | the instant reflex (~0.5 s): exactly four lines `status:` / `tone:` / `mode:` / `reply:` |
  | `deep` | `brain/例子.txt` | her real reply: `[register_emotion]"spoken sentence"` runs, then `我心想：` inner thoughts; `【想想】` asks the reflection tier |
  | `reflection` | `brain/思考.txt` | a background coach between turns: `<提示>` hint, `<计划>` plan, `<开口>` makes her speak first |
  | `memory_write` | (in code) | extracts quoted facts from each protected turn into her evidence memory |

- **Her day**: `brain/scenes.json` → `deep.daily_context` holds, per time slot of a weekday and a weekend, five
  possibilities; the brain picks one per day and slot, seeded by the date. `deep.current_scene` is where she is when
  the person talks to her on the stage; `deep.text_scene` is where she is when a message arrives from Discord or
  Telegram. `surface.context` is the surface tier's one-page summary.
- **Identity tokens**: `{user_name}` and `{city}` anywhere in the templates and scenes are filled from
  `config/build_f.json` (`user_name`, `city`; also `persona_name`, `timezone` = her clock, `onboarded`). The first-run
  wizard on the Use page writes them. The shipped values are neutral (`"User"`, `""`, `false`); a test enforces it.
- **Shipped copies**: `brain/defaults/` holds `scenes.json`, `例子.txt`, `表层.txt`, `思考.txt` exactly as shipped;
  Setup → Danger zone → *Reset everything* restores them. Read [docs/PERSONALITY.md](docs/PERSONALITY.md) before
  editing her (the 你/我 voice rule, which blocks are contract-checked).
- **Emotion keys (the V2 vocabulary, 69 keys = register × emotion)**, defined in `po_contract.py`
  (`REGISTERS`, `EMOTION_SETS`); every spoken sentence carries one, and it drives her voice, her face and her motion:
  - `polite_` (11): neutral smile happy caring sad worried cold angry embarrassed surprise fear
  - `casual_` (19): neutral smile happy very_happy playful laughing relieved shy embarrassed mumbling jealous caring
    surprise angry frustrated sad tired fear worried
  - `serious_` (11): neutral cold disappointed surprise angry very_angry protective worried anxious sad fear
  - `intimate_` (18): neutral happy very_happy smile playful shy very_shy caring very_caring jealous mumbling relieved
    tearful sad very_sad sleepy tired worried
  - `whisper_` (10): neutral happy smile caring relieved shy sad worried tearful sleepy
- **Voice tags (the V1 vocabulary)**: her voice speaks 69 tags written `【happy2】` (the 67 `V1_IDS` in
  `speech/voice_adapter.py` plus `happy4` and `sad4`), each with a recorded reference clip.
  `speech/voice_adapter.py` maps each V2 key to a V1 tag at the TTS seam (on by default; `config/voice_map.json`
  holds edits made on Setup → Voice); the browser and the avatar always get the V2 key, so face and voice agree.
- **Sound effects**: nine closed names (`Laugh`, `Can_t_help_laughing`, `Soft_hmph`, `Sigh`, `Deep_breath`, `Yawn`,
  `Sniff`, `Light_cough`, `Throat_clear`) written `【Sigh】`. No SFX audio ships; the tags are parsed and stripped.
- **Her body**: `avatar/models/Amika/AmikaV4.model3.json` (Cubism) with 142 motions and 25 expressions;
  `avatar/models/Amika/banks.json` maps each of the 69 keys to expressions and motions (Setup → Avatar edits it);
  keys without an entry use motions named after them (`casual_happy.motion3.json`, `casual_happy.2.motion3.json`).

## 2. Architecture: processes, ports, environments

`launch.py` (run by `Start_OpenAmika.bat` with the brain python) starts everything in one console, in this order:
the services in `settings.json` → `services` (her voice server; the smart-home ones only after the IoT statement is
accepted) and the agents in `config/tools.json` autostart entries, in parallel; then the core one by one, each
waiting for its health URL: embedder → brain → vision (when enabled) → speech → messengers. The browser opens when
speech answers. Ctrl+C (or `Stop_OpenAmika.bat`) stops everything this launcher started. Every process binds
127.0.0.1.

| process | script | environment (Python 3.11) | port (setting) | health | talks to |
|---|---|---|---|---|---|
| launcher | `launch.py` | `envs/brain` | – | – | starts and watches the rest; writes `logs/` |
| speech gateway | `speech/api.py` (FastAPI) | `envs/speech` (torch 2.10.0 + torchaudio, CUDA 12.8) | 53000 (`chat_port`) | `GET /health` | the browser (pages, `WS /ws/dialog`), the brain (Socket.IO client, `HANE_URL`), the voice server (Socket.IO client, `tts_url`), vision (`GET /api/latest`; relays camera frames on `WS /ws/vlm_feed`), messengers (`GET /api/status`) |
| brain | `brain/webui.py` (Flask-SocketIO) | `envs/brain` (no torch) | 5202 (`brain_port`) | `GET /api/evo/health` | OpenRouter / Alibaba Model Studio / Ollama (LLM roles), the embedder (`POST /embed`), MCP servers (tools) |
| embedder | `brain/embed_server.py` | `envs/embed` (torch 2.10.0, sentence-transformers) | 8011 (`embedder.port`) | `GET /health` | – (BAAI/bge-m3, cut to 768 dims) |
| vision (her eyes) | `vision/vlm_service.py` | `envs/vision` (torch 2.10.0 + torchvision 0.25.0, transformers) | 8014 (`vision.port`) | `GET /api/health` | speech (`ws://127.0.0.1:53000/ws/vlm_feed`); only when `vision.enabled` |
| messengers | `messengers/service.py` | `envs/speech` | 5215 (fixed) | `GET /health` | the brain (Socket.IO), Discord / Telegram |
| her voice server | `vendor/tts-engine/main.py` (aiohttp + Socket.IO, GPT-SoVITS v2ProPlus) | `vendor/tts-engine/.venv` (torch 2.9.1, CUDA 12.8) | 8002 (`tts_url`, `services.tts`) | `GET /ping` (`503 loading` for ~20 s, then `ok`) | – |
| Ollama (optional) | – | – | 11434 (`local_model_url`, Setup → Brain) | – | – |
| mosquitto / zigbee2mqtt (optional, IoT) | `IoT/` | – | 1883 / 8080 | – | the `iot` MCP server (`servers/openamika-iot`) |

Environment variables `launch.py` passes to the core (from `settings.json`): `HANE_HOST`, `HANE_PORT`, `HANE_URL`
(the brain), `AMIKA_CHAT_URL`, `YUMEHA_HOST`, `YUMEHA_PORT` (speech), `YUMEHA_MODEL` (`speech/models`),
`YUMEHA_DEVICE` (`speech_device`), `AMIKA_LOCAL_MODEL_URL`, `AMIKA_TTS_URL`, `AMIKA_TTS_CHARACTER`,
`AMIKA_EMBEDDING_URL` (with an `embedder` block), `AMIKA_VLM_URL` (with vision on).

**A voice turn**: mic PCM (browser) → `WS /ws/dialog` → Silero VAD + SenseVoiceSmall STT (speech) → brain
`message` → surface four-liner (spoken at once) → tool phase if triggered → deep stream → `speech/hane_parser.py`
splits `[emotion]"sentence"` runs → one TTS request per sentence (`tts_request_gateway`, text `【happy2】.sentence`,
voice adapter applied) → `tts_audio {audio_b64, emotion, pause_after}` to the browser → `ui/live2d-engine.js` plays the
audio and fires the expression and motion at playback start → `tts_playing` / `tts_finished` acks drive barge-in →
the memory writer archives the turn; reflection may open the next turn. Diagrams: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
and the interactive map at `/flow`. Socket protocol: [brain/PROTOCOL.md](brain/PROTOCOL.md). Voice contract:
[docs/TTS.md](docs/TTS.md).

## 3. Repository map

| path | role |
|---|---|
| `launch.py` | the launcher: grace cleanup of an earlier instance of this folder, pre-flight (`--check`), start order, the one-console dashboard, Ctrl+C stops all; writes `logs/preflight.json`, `logs/instance.json`, `logs/launcher.log` |
| `Start_OpenAmika.bat` / `Stop_OpenAmika.bat` (`Stop_OpenAmika.ps1`) | start (reads `brain_python` from `settings.json`) / stop exactly what this folder started (by script path and `logs/instance.json`) |
| `Tailscale_Serve_OpenAmika.bat` | maps `https://<machine>.<tailnet>.ts.net:8447/` to the speech port with Tailscale Serve (`off` removes it) |
| `scripts/setup_windows.ps1` | Windows entry point: finds Python 3.11 (`py -3.11`, `python3.11`, `python`) and runs `scripts/setup_core.py` |
| `scripts/setup_core.py` | her brain, ears, memory and eyes: `envs/brain`, `envs/speech`, `envs/embed`, `envs/vision`, SenseVoiceSmall, bge-m3, Qwen3-VL-2B, `settings.json`, `brain/.env`; `--check`, `--cpu`, `--vision auto|on|off`, `--reinstall` |
| `scripts/setup_tts.py` | her voice and Live2D model from Hugging Face (license prompt first) and the voice server in `vendor/tts-engine`; `--verify`, `--assets-only`, `--from-dir`, `--accept-voice-license`, `--cpu`, `--self-test` |
| `scripts/MekaHime_Amika.manifest.json` | every file of `MekaHime/MekaHime_Amika` at the pinned revision with its size and SHA-256 (250 files) |
| `scripts/download_sensevoice.py` | SenseVoiceSmall into `speech/models/` (6 files, SHA-256 pinned, ModelScope then Hugging Face; `--verify`) |
| `settings.example.json` | template of `settings.json` (interpreters, ports, devices, TTS, embedder, vision, services) |
| `.env.example` | template of `brain/.env` (secrets only; lists every variable the code reads) |
| `brain/webui.py` | the brain: Socket.IO turn protocol, prompt assembly (cache-first deep prefix), the three tiers, `/api/build_f/*` controls |
| `brain/f_bridge.py` | the live integration: memory store, role transports, usage ledger, settlement |
| `brain/f_memory.py`, `f_method.py`, `f_extract.py`, `methods.py` | evidence memory: quoted facts and original speech (no summaries), hybrid BM25 + embedding recall, extraction schema |
| `brain/f_roles.py`, `config/llm_roles.json` | the four LLM roles, presets, validated selections |
| `brain/f_openrouter.py`, `brain/f_ollama.py` | metered OpenRouter / Alibaba transports; the Ollama transport (`config/ollama.json`) |
| `brain/f_usage.py`, `f_settle.py`, `f_controls.py`, `config/pricing.json` | per-call usage ledger, cost settlement, model and meter controls |
| `brain/f_persona.py` | Setup page persona editing and the OpenRouter key endpoint (writes `brain/.env`) |
| `brain/f_reset.py` | wipe her memory / reset everything (idle-only, with backups) |
| `brain/f_tools.py`, `brain/iot_disclaimer.py` | the tools layer (MCP client, capability block, post-speech clerk, agent jobs); the IoT safety statement rules |
| `brain/embed_server.py` | local embedder (bge-m3 → 768 dims); `--check`, `--download` |
| `brain/po_contract.py` = `speech/po_contract.py` | the shared output contract: emotion keys, SFX, parsing, template self-check |
| `brain/表层.txt`, `例子.txt`, `思考.txt`, `scenes.json` (+ `brain/defaults/`) | her persona (§1) |
| `speech/api.py` | the gateway: pages, STT, `WS /ws/dialog`, TTS client, avatar registry, vision relay, control proxies |
| `speech/hane_parser.py`, `sentence_splitter.py`, `tts_emotion.py`, `tts_pause.py`, `tts_sfx.py`, `voice_adapter.py`, `stt_carry.py` | streaming parse, sentence split, emotion tags, pauses, SFX tags, V2→V1 voice tags, carry-over of forced STT splits |
| `speech/model.py`, `speech/utils/` | SenseVoice model code (FunAudioLLM, MIT); `speech/models/` holds the weights (not in git) |
| `vendor/tts-engine/main.py` | her voice server (MIT, derived from the OpenSakura voice server); everything else in that folder is built by `setup_tts.py` |
| `vision/vlm_service.py` | her eyes: a small local VLM describing the camera feed; `--check`, `--download` |
| `messengers/service.py`, `core.py` | "Text her" on Discord / Telegram (`config/messengers.json`) |
| `ui/` | the pages: `use.*` (stage, power, mic, telemetry, first-run wizard), `setup.*`, `tools.*`, `about.*`; `common.js` (nav, the health light), `live2d-engine.js` (Live2D playback, lipsync), `vendor/` (PixiJS, pixi-live2d-display, Live2D Cubism Core) |
| `avatar/models/` | Live2D models, one folder each (`Amika/` from `setup_tts.py`); `avatar/retarget_motions.py` reuses motions on another model |
| `servers/openamika-agents`, `servers/openamika-iot` | first-party MCP servers: task agents (Hermes Agent, nanoMuse) and the Zigbee smart home |
| `IoT/` | Windows setup for the smart home (CP210x driver, mosquitto, a zigbee2mqtt serial patch) |
| `config/` | non-secret settings: `build_f.json` (identity, her clock, memory budgets), `llm_roles.json`, `openrouter_models.json`, `alibaba_models.json`, `pricing.json`, `word_replace.json` (STT fixes, TTS pronunciations), `tools.example.json`, `messengers.example.json` |
| `docs/` | `ARCHITECTURE.md`, `PERSONALITY.md`, `TTS.md`, `AVATAR.md`, `TOOLS.md`, `MESSENGERS.md`, `IOT_DISCLAIMER.md` |
| `tests/` | the offline suite (§10) |
| runtime, git-ignored | `envs/` (environments), `logs/` (`<name>.log` per process, `launcher.log`, `preflight.json`, `instance.json`), `memory_f/` (evidence store, usage ledger, backups), `brain/memories/` (transcript) |

## 4. Models: what, from where, where it lands, how it is checked

| model | used by | source | size | lands in | checked by | installed by |
|---|---|---|---|---|---|---|
| **her voice**: `MekaHime_Amika.ckpt` (GPT), `MekaHime_Amika.pth` (SoVITS, v2ProPlus), `character.json`, `references/emotions.json` + 69 WAV clips, `README.md`, `LICENSE` | voice server | Hugging Face `MekaHime/MekaHime_Amika` @ `a3934e5a83dcb0192901f69536c66b837414e822`, every file outside `live2d/` | 75 files, 385 MB | `vendor/tts-engine/Speakers/MekaHime_Amika/` | size + SHA-256 of every file (`scripts/MekaHime_Amika.manifest.json`); clips for all 69 tags and the 67 adapter tags | `setup_tts.py` |
| **her Live2D model**: `AmikaV4.model3.json`, `AmikaV4.moc3`, `AmikaV4.physics3.json`, `AmikaV4.cdi3.json`, `AmikaV4.8192/texture_00.png`, `texture_01.png`, 142 `*.motion3.json`, 25 `*.exp3.json`, `banks.json` | the Use page | same repository and revision, `live2d/Amika/` | 174 files, 88 MB | `avatar/models/Amika/` | size + SHA-256 of every file; every file the `.model3.json` names exists; an edited `banks.json` is kept | `setup_tts.py` |
| SenseVoiceSmall (`model.pt`, `config.yaml`, `configuration.json`, `am.mvn`, `chn_jpn_yue_eng_ko_spectok.bpe.model`, `tokens.json`) | speech recognition | ModelScope `iic/SenseVoiceSmall`, fallback Hugging Face `FunAudioLLM/SenseVoiceSmall` (not `tokens.json`) | 937 MB | `speech/models/` | size + SHA-256 (`download_sensevoice.py`) | `setup_core.py` |
| Silero VAD | voice activity | inside the `silero-vad` pip package | small | `envs/speech` | – | `setup_core.py` |
| BAAI/bge-m3 | her memory's embeddings | Hugging Face `BAAI/bge-m3` | ~2.3 GB | the Hugging Face cache (`HF_HOME`, default `~/.cache/huggingface/hub`) | loaded once by `embed_server.py --download` | `setup_core.py` (else the first start) |
| Qwen3-VL-2B-Instruct | her eyes | Hugging Face `Qwen/Qwen3-VL-2B-Instruct` | ~4.3 GB | the Hugging Face cache | `vlm_service.py --download` loads its processor | `setup_core.py` with vision |
| GPT-SoVITS code | voice server | `RVC-Boss/GPT-SoVITS` @ `b4570b876f5c` (git, or the zip checked by a tree SHA-256) | tens of MB | `vendor/tts-engine/GPT-SoVITS/` | commit; `TTS_infer_pack/TTS.py` SHA-256 | `setup_tts.py` |
| HuBERT, RoBERTa, ERes2NetV2 speaker model | voice server | Hugging Face `lj1995/GPT-SoVITS` @ `8f88575` | 948 MB | `GPT-SoVITS/GPT_SoVITS/pretrained_models/` | size + SHA-256 | `setup_tts.py` |
| fastText `lid.176.bin`, G2PW (Chinese polyphones), nltk data, ffmpeg (Opus only) | voice server | dl.fbaipublicfiles.com; `XXXXRT/GPT-SoVITS-Pretrained` @ `0c47645`; nltk_data @ `550b662`; gyan.dev 8.0.1 | 131 MB; 589 MB zip; 5 MB; 106 MB | `vendor/tts-engine/...` | size + SHA-256 | `setup_tts.py` |
| LLMs (default preset) | the four roles | OpenRouter: `deep` = `z-ai/glm-5` (no reasoning), `surface` and `reflection` = `deepseek/deepseek-v4.1-flash`, `memory_write` = `z-ai/glm-5.3`; or any Ollama model | – | the cloud / Ollama | – | the person (key or `ollama pull`) |

`HF_ENDPOINT` (a Hugging Face mirror) is honored by `setup_tts.py`, as by huggingface_hub; every file is still checked
against its pinned SHA-256. `MekaHime/MekaHime_Amika` is public from OpenAmika's public release on; before that,
`setup_tts.py` needs a read token in `HF_TOKEN` (or a saved `hf auth login`), which it sends to the Hugging Face host
only (never to the CDN it redirects to) and never prints or stores. License text:
https://huggingface.co/MekaHime/MekaHime_Amika/blob/main/LICENSE

## 5. Setup, step by step

Prerequisites: Windows 10/11; an NVIDIA GPU (RTX 20-series or newer; 8 GB VRAM for her ears, memory and voice,
16 GB with her eyes) with a recent driver; Python 3.11 64-bit (python.org, "Add python.exe to PATH", or
`winget install -e --id Python.Python.3.11`); Git; ~40 GB free disk; 15–20 GB of downloads; 30–60 minutes. Clone
into a short folder that OneDrive does not sync (the environments hold tens of thousands of files).

### 5.1 Clone

```powershell
git clone https://github.com/zeekk0/OpenAmika.git
cd OpenAmika
```

### 5.2 Her brain, ears, memory and eyes

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1        # add --cpu without an NVIDIA GPU
```

It prints `[setup] using py -3.11`, then each step (pip's own output streams through), and ends with:

```
==============================================================================
OpenAmika setup summary (909 s)
  OK Python              3.11.9 (...\Python311\python.exe)
  OK GPU                 NVIDIA GeForce RTX 5080, 16 GB: CUDA 12.8 builds
  OK brain               envs\brain\Scripts\python.exe (Python 3.11.9, created; brain/requirements.txt installed)
  OK settings.json       created from settings.example.json (interpreters in envs\, speech on cuda:0, vision on)
  -- brain/.env          created from .env.example; no OpenRouter key yet: the first-run wizard on the Use page takes it (or put it after OPENROUTER_API_KEY= in brain\.env); fully local with Ollama needs none
  OK speech recognition  envs\speech\Scripts\python.exe (Python 3.11.9, created; speech/requirements.txt installed; torch 2.10.0+cu128, CUDA OK on NVIDIA GeForce RTX 5080)
  OK speech model        speech/models/ (SenseVoiceSmall, 6 files verified by SHA-256)
  OK memory (embedder)   envs\embed\Scripts\python.exe (Python 3.11.9, created; brain/requirements-embedder.txt installed; torch 2.10.0+cu128, CUDA OK on NVIDIA GeForce RTX 5080)
  OK memory model        BAAI/bge-m3 in the Hugging Face cache (...\hub\models--BAAI--bge-m3, downloaded)
  OK vision (her eyes)   envs\vision\Scripts\python.exe (Python 3.11.9, created; vision/requirements.txt installed; torch 2.10.0+cu128, CUDA OK on NVIDIA GeForce RTX 5080)
  OK vision model        Qwen/Qwen3-VL-2B-Instruct in the Hugging Face cache (...\hub\models--Qwen--Qwen3-VL-2B-Instruct, downloaded)
==============================================================================
Done. Next:
  1. envs\brain\Scripts\python.exe scripts\setup_tts.py   her voice and Live2D model (type yes to accept her license)
  2. Start_OpenAmika.bat                                  start her; the page opens at http://localhost:53000
```

Measured on a fresh clone (Windows 11, RTX 5080, fast connection): about 15 minutes, most of it the CUDA torch wheel
(2.9 GB, downloaded once and reused from pip's cache by the other environments), SenseVoiceSmall (from ModelScope)
and the two Hugging Face models. A second run with everything in place takes a few seconds per environment.

- Marks: `OK` done, `--` skipped or informational, `!!` failed (the text says how to fix it). Exit code 0, 1 with any
  `!!`, 130 after Ctrl+C. Dependent steps of a failed one show `-- … skipped: needs X, which did not finish`.
- Her eyes: `--vision auto` (default) installs them when the GPU has at least ~15 GB (`VISION_MIN_MIB`), `--vision on`
  always, `--vision off` never. Without them the row reads `-- vision (her eyes)  skipped: …` and `settings.json` has
  `"vision": {"enabled": false}`.
- Re-running is the repair: existing environments are reused (a broken one is recreated), pip runs only when a
  requirements file changed (stamp `envs/<name>/openamika-requirements.sha256`) or an import fails, torch is held at
  `2.10.0` by `envs/<name>/openamika-constraints.txt`, finished downloads are kept. `--reinstall` forces pip.
- An existing `settings.json` is never changed; its interpreters are checked. An `envs/<name>` made with
  `conda create -p` (the earlier README) is used as it is (`envs\<name>\python.exe`).
- Verify without changing anything: `powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 --check`
  (ends with `Her brain, ears, memory and eyes are installed.` or `Not ready: …`).

### 5.3 Her voice and her Live2D model

```powershell
envs\brain\Scripts\python.exe scripts\setup_tts.py
```

First it prints the license summary and asks (only on an interactive console):

```
[voice] Amika's voice and her Live2D model (https://huggingface.co/MekaHime/MekaHime_Amika) are free under MekaHime's personal-use license:
          - personal, non-commercial use only, and you must be 18 or older
          - no redistribution, re-hosting or resale of her voice, her Live2D model or their files
          - no training, fine-tuning or distilling other models on them
          - credit MekaHime visibly when you post videos or streams with her
          full text: https://huggingface.co/MekaHime/MekaHime_Amika/blob/main/LICENSE

[voice] type yes to accept and download her voice and Live2D model (anything else skips them for now):
```

The person types `yes` (`y` works; anything else declines, and the rest still installs). Then, in this order: her
voice and Live2D model, file by file with the standard library only (so they never depend on torch), then the voice
server: `vendor/tts-engine/.venv` (torch 2.9.1 + `requirements-tts.txt`), GPT-SoVITS at its pinned commit, the
pretrained models, language ID, G2PW, nltk data, ffmpeg, an import probe, the Japanese dictionary,
`vendor/tts-engine/install.json` (records the revision and when the license was accepted), and a check that
`settings.json` starts this server. It ends with (paths shortened):

```
==============================================================================
OpenAmika voice setup summary (402 s)
  OK voice MekaHime_Amika  ...\vendor\tts-engine\Speakers\MekaHime_Amika (downloaded 75 file(s) from MekaHime/MekaHime_Amika@a3934e5a; 75 files verified; 69/69 emotion tags with a clip; all 67 tags OpenAmika's voice adapter can send have one)
  OK Live2D model Amika    ...\avatar\models\Amika (downloaded 174 file(s) from MekaHime/MekaHime_Amika@a3934e5a; 174 files verified; AmikaV4.model3.json, 142 motions, 25 expressions)
  OK venv                  ...\vendor\tts-engine\.venv\Scripts\python.exe (Python 3.11.9, created)
  OK torch                 torch 2.9.1+cu128 / torchaudio 2.9.1+cu128 (installed)
  OK packages              requirements-tts.txt (installed)
  OK GPT-SoVITS            ...\vendor\tts-engine\GPT-SoVITS @ b4570b876f (downloaded, git, shallow)
  OK pretrained models     HuBERT, RoBERTa, ERes2NetV2 sv (0.9 GB, downloaded) from lj1995/GPT-SoVITS@8f88575
  OK language ID           fastText lid.176.bin (downloaded)
  OK G2PW                  ...\GPT_SoVITS\text\G2PWModel (downloaded from XXXXRT/GPT-SoVITS-Pretrained@0c47645)
  OK nltk data             ...\vendor\tts-engine\nltk_data (averaged_perceptron_tagger[_eng], cmudict)
  OK ffmpeg                ...\vendor\tts-engine\ffmpeg\ffmpeg.exe (ffmpeg version 8.0.1-essentials_build-www.gyan.dev, downloaded)
  OK imports               25 packages import; torch 2.9.1+cu128; CUDA OK on NVIDIA GeForce RTX 5080
  OK Japanese dictionary   ...\GPT_SoVITS\text\ja_userdic\user.dict (21 MB)
  OK launcher              settings.json starts this server (services.tts, port from tts_url) with voice MekaHime_Amika
==============================================================================
Done. Next:  Start_OpenAmika.bat   (her voice server starts with the rest; the page opens at http://localhost:53000)
```

- Unattended (an agent's terminal has no console): ask the person to accept the terms first, then
  `envs\brain\Scripts\python.exe scripts\setup_tts.py --accept-voice-license`. Without a console and without that flag,
  both rows read `!! … not downloaded: her license was not accepted (…)` and nothing of hers is downloaded.
- While `MekaHime/MekaHime_Amika` is private: `$env:HF_TOKEN = "hf_..."` in the same PowerShell window first. Without
  it the rows read `!! voice MekaHime_Amika  Hugging Face refused the download (HTTP 401): MekaHime/MekaHime_Amika is
  not public yet. …`.
- No NVIDIA GPU: `--cpu`. Only her files, no voice server: `--assets-only`. Offline, from a copy laid out like the
  repository: `--from-dir <folder>` (still checked file by file against the manifest). Skip one: `--skip-voice`,
  `--skip-avatar`. Download again: `--update-voice`, `--update-avatar`. Run the server once and speak a line:
  `--self-test`.
- Verify without changing anything (fast: hashes are cached by size and modification time):
  `envs\brain\Scripts\python.exe scripts\setup_tts.py --verify` → `Verified: her voice and Live2D model and the voice
  server are complete.` or one `!!` row per broken part, naming the files.
- A re-run of a complete install downloads nothing and takes about half a minute; an interrupted download resumes
  from its `.part` file.

### 5.4 Her brain's LLM

Either an OpenRouter key — the person pastes it in the first-run wizard on the Use page, or after
`OPENROUTER_API_KEY=` in `brain/.env` (the brain also accepts it on Setup → Brain) — or fully local: install Ollama,
`ollama pull qwen3:8b` (or `qwen3:4b`), then Setup → Brain → Ollama → Detect models → Use (all four roles; writes
`config/ollama.json` and the selections in `config/llm_roles.json`). Check: `launch.py --check` shows
`OK API keys …` or `-- API keys  no OpenRouter key yet …`.

### 5.5 Check, then start

```powershell
envs\brain\Scripts\python.exe launch.py --check     # starts nothing; one OK / -- / !! line per check
Start_OpenAmika.bat                                 # opens her console; the browser opens http://localhost:53000
```

`--check` prints `OpenAmika pre-flight  <folder>`, rows such as `OK brain python`, `OK required files  all present`,
`-- TTS (voice)  not running — will be started (vendor\tts-engine\.venv\Scripts\python.exe)`,
`OK her voice  MekaHime_Amika · 69/69 emotion clips`, `OK Live2D model  Amika (AmikaV4.model3.json, 142 motions)`,
`OK brain_python imports`, `OK embedder check …`, then a count line and `result: ready` (exit 0) or
`result: fix the !! lines above (-- lines are informational)` (exit 1). The same report is saved to
`logs/preflight.json` and shown on the Setup page and in the health light.

`Start_OpenAmika.bat` opens a console titled OpenAmika with a dashboard that redraws in place: the pre-flight
problems, then one row per service (`Services`: tts; `Core`: embedder, brain, vision, speech, messengers). A spinner
means starting or working; green `ready · pid … · <url>`; yellow `no answer after Ns — still running, see
logs/<name>.log`; red `exited with code N — see logs/<name>.log`. A normal start: her voice ~20 s, the embedder
10–60 s, the brain a few seconds, speech ~10–30 s; the browser opens when speech is up.

### 5.6 Other setups

- **CPU only**: `setup_windows.ps1 --cpu` and `setup_tts.py --cpu`; `settings.json` then has `speech_device: "cpu"`,
  the embedder on `cpu` / `fp32`, vision off. Slow, but complete.
- **Your own environments** (conda or other): create them yourself (Python 3.11; the speech, embed and vision ones
  need a CUDA torch first: `pip install torch==2.10.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu128`
  for speech, `torch==2.10.0` for the embedder, `torch==2.10.0 torchvision==0.25.0` for vision; then their
  requirements file), point `brain_python`, `speech_python`, `embedder.python` and `vision.python` in
  `settings.json` at them, run `python scripts\download_sensevoice.py`, then `setup_tts.py` as above.
- **Linux / macOS**: untested. The scripts have Linux paths (`envs/<name>/bin/python`, CUDA or CPU torch); the
  launcher's process handling and the `.bat` files are Windows-first.

## 6. Configuration

**`settings.json`** (from `settings.example.json`; git-ignored; read at launch):

| key | default | meaning |
|---|---|---|
| `brain_python`, `speech_python` | `envs\brain\Scripts\python.exe`, `envs\speech\Scripts\python.exe` | interpreters (relative to the folder, or absolute) |
| `brain_port`, `chat_port` | 5202, 53000 | the brain; the web pages + speech gateway (the URL she opens) |
| `local_model_url` | `http://127.0.0.1:11434/v1/chat/completions` | Ollama's OpenAI-compatible endpoint |
| `speech_device` | `cuda:0` | STT device (`cpu` works, slower) |
| `tts_url`, `tts_character` | `http://127.0.0.1:8002`, `MekaHime_Amika` | her voice server and the voice id (used when the voice adapter is off) |
| `embedder` | python `envs\embed\Scripts\python.exe`, port 8011, model `BAAI/bge-m3`, tag `bge-m3-d768`, dims 768, dtype fp16, device cuda, load_timeout_s 600 | her memory's embedder |
| `vision` | enabled (per the setup), python `envs\vision\Scripts\python.exe`, port 8014, model `qwen3-vl-2b` (also `qwen3-vl-4b`, `qwen2.5-vl-3b`, `smolvlm2-2.2b`, `smolvlm2-500m`), max_tokens 60, max_dps 1.0, device cuda, load_timeout_s 900 | her eyes |
| `services.tts` | enabled; `vendor\tts-engine\.venv\Scripts\python.exe main.py --port 8002 --host 127.0.0.1 --character MekaHime_Amika` in `vendor\tts-engine`; health `http://127.0.0.1:8002/ping`; timeout 240 s | started by the launcher; keep `--character` equal to `tts_character` |
| `services.mqtt`, `services.zigbee2mqtt` | disabled | the smart home (§8; the person enables them) |

**`brain/.env`** (from `.env.example`; secrets only): `OPENROUTER_API_KEY` (the default provider of all four roles;
not needed when every role is on Ollama); optional `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` (Alibaba Model Studio,
workspace in `config/alibaba_models.json`), `GEMINI_API_KEY` (legacy embedder), `GROQ_API_KEY`, `CEREBRAS_API_KEY`,
`XAI_API_KEY`, `ANTHROPIC_API_KEY` (legacy direct providers, off by default).

**`config/`**: `build_f.json` (identity and clock — §1; `memory_budget_chars`, `recent_turns`, `idle_reflection`,
`auto_open_from_reflection`, `embedding_provider: "local"`, `embedding_url`, budgets), `llm_roles.json` (`options`,
`presets`, `selections` for `surface` / `deep` / `reflection` / `memory_write`; Setup → Brain edits it),
`openrouter_models.json` / `alibaba_models.json` / `pricing.json` (catalogs and prices; Ollama is priced at 0),
`word_replace.json` (`stt_to_llm` fixes, `llm_to_tts` pronunciations incl. the wizard's `role: user_name` rule),
`tools.json` (git-ignored; MCP servers, policies, `clerk_role`, `iot_disclaimer`; template `tools.example.json`),
`messengers.json` (git-ignored; bot tokens and allow-lists; template `messengers.example.json`), `ollama.json`
(git-ignored), `voice_map.json` (git-ignored; voice adapter edits).

## 7. The page

`http://localhost:53000` (`chat_port`) redirects to `/use`. Pages: **Use** (`/use`: the Live2D stage, power, mic,
text box, camera button, model picker, cost and latency telemetry, the first-run wizard), **Setup** (`/setup`:
system health, startup checks, brain and models, voice adapter grid, avatar banks, personality, vision, "Text her",
danger zone: wipe her memory / reset everything; Simple and Professional modes), **Tools** (`/tools`: MCP servers,
per-tool policies `auto` / `confirm` / `deny`, approvals, the IoT switch and its safety statement), **About**
(`/about`: MekaHime, supporting the project, licenses), and `/flow` (the interactive architecture map).

The light at the top right of every page (`ui/common.js`, `healthCheck`) is green *System Ready*, yellow *Warnings*
or breathing red *System Failures*; click it for the list. It polls: Brain, Memory, OpenRouter key (when a role uses
OpenRouter) or Ollama (when one uses Ollama), Speech / STT, TTS (her voice), Live2D model, every non-green startup
check from `logs/preflight.json`, and configured bots that cannot connect. The first-run wizard (until
`onboarded` is true in `config/build_f.json`) asks the person's name (with a pronunciation guide for her voice), city
and her clock, takes the OpenRouter key if `brain/.env` has none, and runs the same health check. The browser needs
one click on the page before it allows her audio.

## 8. Optional add-ons and their rules

Not installed by the setup; the core runs without them.

- **Tools / MCP** ([docs/TOOLS.md](docs/TOOLS.md)): `config/tools.json`, edited on the Tools page. New servers start
  disabled. She decides in character; a post-speech "clerk" call turns what she agreed to into tool calls, each tied
  to a verbatim quote of the person's request; `confirm` tools wait for the person's approval on the Tools page.
- **Task agents** ([servers/openamika-agents/README.md](servers/openamika-agents/README.md)): Hermes Agent and
  nanoMuse as `ask / status / answer / stop` tools; their URLs and keys live in their `env` blocks in
  `config/tools.json`.
- **Smart home** ([IoT/README.md](IoT/README.md), [docs/IOT_DISCLAIMER.md](docs/IOT_DISCLAIMER.md)): Zigbee plugs via
  mosquitto + zigbee2mqtt + `servers/openamika-iot`. Until the current IoT safety statement is accepted on the Tools
  page, the brain refuses to start or connect any smart-home server, the launcher holds back `services.mqtt` and
  `services.zigbee2mqtt` (pre-flight row `-- IoT safety statement  not accepted (version …)`), and
  `servers/openamika-iot` refuses to start. A new statement version turns IoT off again. Rule 3 of §0 applies: only
  the person turns it on, only harmless devices.
- **Text her** ([docs/MESSENGERS.md](docs/MESSENGERS.md)): Discord (needs `pip install -r messengers\requirements.txt`
  in `envs/speech`) and Telegram bots, set on Setup → Text her; only allow-listed user ids reach her.
- **Ollama**: §5.4. On a 16 GB card her ears, memory, eyes and voice leave no room for a 7–8B model: turn vision off,
  use a smaller model, or run Ollama on another PC (Setup → Brain takes its URL).
- **Phone**: `Tailscale_Serve_OpenAmika.bat` (needs Tailscale with MagicDNS + HTTPS); one conversation tab at a time.

## 9. Troubleshooting

Start with `envs\brain\Scripts\python.exe launch.py --check`, then `logs\<name>.log` of the red row.

**Setup (`setup_windows.ps1` / `setup_core.py`)**

- `[setup] Python 3.11 was not found.` → install Python 3.11 64-bit, open a new window, run again.
- `!! brain|speech recognition|memory (embedder)|vision (her eyes)  …: these do not import: X (…)` → a pip install
  failed or was interrupted: run again with `--reinstall`; read the pip error above the summary (disk full, network,
  a proxy).
- `!! … does not see the GPU (CUDA)` → NVIDIA driver too old or missing (`nvidia-smi` must work): update it and run
  again, or use `--cpu`.
- `!! speech model  speech/models/ is not complete …` → ModelScope or Hugging Face unreachable: run again (finished
  files are kept), or download the six files by hand from https://www.modelscope.cn/models/iic/SenseVoiceSmall into
  `speech/models/` and run `python scripts\download_sensevoice.py --verify`.
- `!! memory model|vision model  … could not be downloaded` → Hugging Face unreachable, or the disk is full: run again.
- `!! settings.json  settings.json names X as Y, which does not exist` → fix the path, or delete `settings.json` and
  run the setup again.
- `WARNING: this folder is synced by OneDrive` / `Windows long paths are off` → move the folder (for example to
  `C:\OpenAmika`) and run again.

**Her voice and Live2D model (`setup_tts.py`)**

- `!! voice MekaHime_Amika  not downloaded: her license was not accepted …` (also `!! Live2D model Amika …`) → the
  person must type `yes` (§0 rule 2), or `--accept-voice-license` after they accepted.
- `… Hugging Face refused the download (HTTP 401): MekaHime/MekaHime_Amika is not public yet …` → set `HF_TOKEN` to a
  read token of an account with access; `… did not accept your Hugging Face token (from HF_TOKEN) (HTTP 401) …` → the
  token is wrong or expired; `… the account of your Hugging Face token … cannot read MekaHime/MekaHime_Amika (HTTP 403|404)`
  → that account has no access.
- `… Hugging Face has no <file> at revision a3934e5a of MekaHime/MekaHime_Amika (HTTP 404 …): update OpenAmika (git pull)`
  → the pinned revision changed upstream; update the checkout.
- `… download failed after 4 tries: <url>: <error>. Check the internet connection …` → run again; it resumes.
- `… SHA-256 mismatch …` / `… is not the one OpenAmika pins …` → the bytes differ from the manifest: run again; if it
  repeats, update OpenAmika; never edit the manifest to make it pass.
- `!! venv|torch|packages|imports …` → the voice server's environment: run again (`--reinstall` for the pip steps);
  `the voice engine needs Python 3.11 or 3.12` → run it with `envs\brain\Scripts\python.exe` or `--python <path>`.
- `-- launcher  services.tts runs …, not this server` / `tts_character is …` → `settings.json` does not start this
  voice server: copy `services.tts` and `tts_character` from `settings.example.json`.

**Launcher and services**

- `OpenAmika could not start: settings.json not found — run the setup first: …` → §5.2.
- `!! brain python  missing: … — run powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 …` (also
  speech, embedder, vision) → §5.2, or fix the path.
- `!! required files  speech/models/model.pt (speech recognition model …)` → §5.2 (it downloads SenseVoiceSmall).
- `!! TTS (voice)  her voice server is not installed — run … scripts\setup_tts.py` / `… is incomplete (GPT-SoVITS
  missing) …` → §5.3.
- `!! her voice  vendor\tts-engine\Speakers\MekaHime_Amika is not installed (no character.json) — run …` → §5.3.
- `!! Live2D model  no Live2D model in avatar/models/: the stage stays empty — run …` → §5.3 (or put another Cubism
  model folder in `avatar/models/`).
- `-- API keys  no OpenRouter key yet …` → §5.4.
- `-- her clock  build_f.json timezone problem ('No time zone found with key …')` → the brain environment has no
  time-zone database (`tzdata`, in `brain/requirements.txt`; Windows has none of its own) and the brain cannot start:
  run the setup again, or `envs\brain\Scripts\python.exe -m pip install -r brain\requirements.txt`.
- `!! brain port|web/speech port|…  N is in use by pid P (name)` → another program (or another OpenAmika) holds the
  port: stop it or change the port in `settings.json`. `… held by pid P (python.exe) started from an administrator
  window …` → close that window or run `Stop_OpenAmika.bat` as administrator.
- Dashboard `tts  exited with code 2 — see logs/tts.log` and in the log `no speakers in …\Speakers (each needs
  character.json, the .ckpt/.pth weights and references/emotions.json; run scripts/setup_tts.py)` → her voice is
  missing: §5.3. `--character MekaHime_Amika not found` → the voice folder name differs from `tts_character`.
- `embedder  no answer after 600s — still running, see logs/embedder.log` → usually the first start downloading
  bge-m3 (`… is not in the Hugging Face cache yet: downloading it once`): wait; `setup_core.py` avoids this.
- `speech  exited with code N — see logs/speech.log` (the browser never opens) → SenseVoice files missing or CUDA
  torch broken in `envs/speech`: `setup_windows.ps1 --check`, then the setup.
- `vision  exited …` → out of VRAM is the usual cause: `"vision": {"enabled": false}` or a smaller model.

**The page**

- Stage shows `No Live2D model yet` → §5.3, then reload. `Model failed to load` → `setup_tts.py --verify` names the
  broken files; a re-run repairs them.
- Health light `TTS (her voice)  unreachable …` → her voice server is down or still warming up (~20 s): see the
  `tts` row and `logs/tts.log`.
- Health light `OpenRouter key  no key saved` → §5.4. `Ollama (local brain)  not answering …` → start Ollama.
- Text but no sound → click the page once (autoplay); TTS must be green.
- Wizard says `Some part is missing — she can't fully work yet.` → fix the red rows on the Setup page.
- Wipe / reset answers 409 → power her off on the Use page (she must be idle), wait a few seconds, retry.

## 10. Tests and CI

```powershell
envs\brain\Scripts\python.exe -m pip install -r requirements-dev.txt
envs\brain\Scripts\python.exe -B -m pytest tests -k "not live" -p no:cacheprovider -q
```

About 400 offline tests: the output contract and both streaming parsers (parity), prompt assembly, memory, the usage
ledger, the control proxies, the tools clerk, the IoT safety statement, the launcher pre-flight
(`tests/test_launch_preflight.py`), the SenseVoice downloader, and `setup_tts.py` against a local stand-in for
Hugging Face (`tests/test_setup_tts.py`: public, private with and without a token, the CDN never seeing the token,
resume, verify, repair, `--from-dir`). Replay tests that need MekaHime's archived corpora skip themselves. GitHub
Actions runs the same command on Windows with Python 3.11 (`.github/workflows/tests.yml`).

## 11. Changing her safely

- **Persona**: edit the prose in Setup → Personality (or `brain/表层.txt`, `例子.txt`, `思考.txt`, `scenes.json`);
  the template self-check runs at boot and on save (`/api/build_f/status` → `template_check`). Keep `brain/defaults/`
  in sync only when you change the shipped persona on purpose.
- **Emotion vocabulary**: change only together with both `po_contract.py` copies, the parsers, `voice_adapter.py`,
  `speech/tts_emotion.py`, the Live2D banks and the tests.
- **A new revision of her Hugging Face repository** (maintainers): from a local copy of the new revision,
  `python scripts\setup_tts.py --write-manifest <folder> --voice-revision <commit>` rewrites
  `scripts/MekaHime_Amika.manifest.json`; set `VOICE_REV` in `scripts/setup_tts.py` to the same commit (the script
  refuses a mismatch); run the tests.
- **Another voice or server**: a speaker folder in `vendor/tts-engine/Speakers/` (`character.json`, weights,
  `references/emotions.json`) or any server speaking the contract in [docs/TTS.md](docs/TTS.md); point `tts_url` /
  `services.tts` at it.
- **Another Live2D model**: a folder in `avatar/models/` ([avatar/models/README.md](avatar/models/README.md),
  [docs/AVATAR.md](docs/AVATAR.md)).

Support: GitHub issues on https://github.com/zeekk0/OpenAmika, hi@mekahime.com, Discord https://discord.gg/mekahime.
