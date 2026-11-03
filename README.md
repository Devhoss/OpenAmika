<p align="center">
  <a href="https://www.mekahime.com">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/images/mekahime-logo-white.png">
      <img src="docs/images/mekahime-logo-card.png" alt="MekaHime" width="320">
    </picture>
  </a>
</p>

<h1 align="center">OpenAmika</h1>

Amika is MekaHime's AI companion: a Live2D anime girl who talks with you by voice and text, remembers you,
and lives with you in your city. OpenAmika is her whole harness, open source: the three-tier brain, the
evidence-based long-term memory, real-time speech with barge-in, the Live2D stage that keeps her face in sync
with her voice, and the optional tools, smart home, camera and Discord/Telegram channels.

Project page: https://www.mekahime.com/openamika · Try her online, nothing to install: https://www.mekahime.com/amika ·
Source: https://github.com/zeekk0/OpenAmika · Support the project: https://www.mekahime.com/donate

> OpenAmika is the open packaging of the stack behind Amika. Amika Online (the hosted app) runs the same brain
> on MekaHime's servers with her own voice and Live2D model, which are **not** part of this repository: here you
> bring your own Live2D model and your own TTS server (see [What is not included](#what-is-not-included)).

```
 you ──mic──▶ STT (SenseVoiceSmall, local) ──▶ brain: surface ─▶ deep ─▶ reflection ──▶ your TTS server ──▶ 🔊
               Silero VAD · barge-in            │ OpenRouter or Ollama                       │ one emotion tag per sentence
                                                │ evidence memory (BM25 + local bge-m3)      ▼
                                                └─ optional: MCP tools · smart home        Live2D avatar (face + motion
                                                                                           start with the sentence)
```

## What's inside

| folder | what |
|---|---|
| `brain/` | the brain (Flask-SocketIO). Three prompt tiers: **surface** (an instant four-line reflex), **deep** (her real reply: `[register_emotion]"speech"` + 我心想 inner thoughts), **reflection** (a background coach that plans and sometimes makes her speak first). Evidence memory (`f_memory.py`, BM25 + embeddings), per-call cost metering with reservations and settlement, the MCP tool phase (`f_tools.py`), the Ollama transport (`f_ollama.py`), her persona (`scenes.json`, `例子.txt`, `表层.txt`, `思考.txt`; shipped copies in `brain/defaults/`) |
| `speech/` | the gateway (FastAPI): local SenseVoiceSmall STT with Silero VAD and barge-in, streaming sentence splitting, the TTS client and voice adapter, every web page and the browser WebSocket |
| `ui/` | three pages: **Setup** (health, models, voice, avatar, personality), **Tools** (MCP servers, policies, approvals), **Use** (power, mic, Live2D stage, cost and latency telemetry); the interactive architecture map is at `/flow` |
| `avatar/` | where your Live2D models go (`avatar/models/<Name>/`), plus `retarget_motions.py` |
| `vision/` | optional camera: a small local VLM (Qwen3-VL-2B by default) describes what she sees |
| `messengers/` | optional "Text her": Discord and/or Telegram bots, words only |
| `servers/` | optional MCP servers: `openamika-iot` (Zigbee smart home via mosquitto + zigbee2mqtt) and `openamika-agents` (Hermes Agent, nanoMuse) |
| `IoT/` | Windows setup scripts for the smart home (CP210x driver, mosquitto, a zigbee2mqtt serial-port patch) |
| `config/` | non-secret settings: LLM roles and presets, model catalogs and prices, word replacements, examples for tools and messengers |
| `scripts/` | `download_sensevoice.py` fetches the speech-recognition model |
| `launch.py` | one console for the whole stack: pre-flight lights, starts everything in order, Ctrl+C stops it all |
| `docs/` | `ARCHITECTURE.md`, `PERSONALITY.md`, `TTS.md`, `AVATAR.md`, `TOOLS.md`, `MESSENGERS.md` |

## Two ways to run her

| | brain | ears, memory, avatar | needs | cost |
|---|---|---|---|---|
| **With OpenRouter** (default) | OpenRouter: GLM-5 for the deep tier, DeepSeek V4.1 Flash for surface and reflection (changeable in Setup) | on your PC | Windows 10/11, NVIDIA GPU with ~4 GB free VRAM, 16 GB RAM, ~15 GB disk | a fraction of a cent per turn; every call is metered and the Use page shows the live total |
| **Fully local** (Ollama) | Ollama, any model you pull (e.g. `qwen3:8b`) | on your PC | as above + VRAM for the model (an 8B model needs ~6 GB more) | electricity only |

Nothing about you leaves the PC except the LLM calls in OpenRouter mode: speech recognition, embeddings,
memory, transcripts and logs stay local. In fully local mode nothing leaves at all.

### What needs a GPU

| part | runs on | VRAM (approx.) | without an NVIDIA GPU |
|---|---|---|---|
| speech recognition (SenseVoiceSmall) | CUDA | ~1–2 GB | set `"speech_device": "cpu"` in settings.json: works, slower |
| memory embeddings (BAAI/bge-m3) | CUDA | ~1.5 GB | set the embedder's `"device": "cpu", "dtype": "fp32"`: works, slower |
| vision (optional, Qwen3-VL-2B) | CUDA | ~4.3 GB | leave `vision.enabled` false |
| LLM roles | OpenRouter, or Ollama on your GPU | 0, or the model's size | use OpenRouter |
| TTS (your own server) | your server | depends on it | text and subtitles still work |
| Live2D avatar | your browser (WebGL) | – | any modern desktop browser |

Measured on a 16 GB RTX 5080: speech recognition, embeddings, vision and a GPT-SoVITS voice server together use
most of the card, so a 7–8B Ollama model does not fit next to all of them. Turn vision off, pick a smaller model,
or run Ollama on a second PC (Setup → Brain takes its URL).

## Requirements

- **Windows 10/11** (the code is plain Python and should port to Linux/macOS with path tweaks, untested).
- **Python 3.11** environments. The guide below uses [Miniconda](https://docs.anaconda.com/miniconda/) and keeps
  every environment inside the OpenAmika folder (`envs\…`), so settings.json needs no absolute paths.
- An **NVIDIA GPU** with a recent driver (see the table above), and [Git](https://git-scm.com/download/win).
- An [OpenRouter](https://openrouter.ai) key **or** [Ollama](https://ollama.com) with a model.
- Optional: a TTS server for her voice (see `docs/TTS.md`) and a Live2D model for her body (see
  `avatar/models/README.md`). She works without both, as text.

## Install (Windows)

In a terminal with conda available (the Anaconda Prompt, or PowerShell after `conda init`). Downloads are about
6–8 GB, most of it PyTorch.

```powershell
git clone https://github.com/zeekk0/OpenAmika.git
cd OpenAmika

# 1. three environments inside the folder
conda create -y -p envs\brain  python=3.11
conda create -y -p envs\speech python=3.11
conda create -y -p envs\embed  python=3.11

# 2. the brain (no GPU needed)
envs\brain\python.exe -m pip install -r brain\requirements.txt

# 3. speech: CUDA PyTorch first (cu128 = CUDA 12.8 wheels: RTX 20/30/40/50 series; for older cards
#    pick the right command at https://pytorch.org/get-started/locally/), then the rest
envs\speech\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
envs\speech\python.exe -m pip install -r speech\requirements.txt

# 4. the memory embedder (its own env keeps its Hugging Face versions apart from speech)
envs\embed\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
envs\embed\python.exe -m pip install -r brain\requirements-embedder.txt

# 5. the speech-recognition model (~940 MB, checksummed; re-run it after a network error)
envs\speech\python.exe scripts\download_sensevoice.py

# 6. your settings and keys
copy settings.example.json settings.json
copy .env.example brain\.env
notepad brain\.env        # put your key after OPENROUTER_API_KEY=  (skip for fully local)
```

Optional extras:

```powershell
# vision (her camera eyes): its own env, then set "vision": {"enabled": true} in settings.json
conda create -y -p envs\vision python=3.11
envs\vision\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
envs\vision\python.exe -m pip install -r vision\requirements.txt

# Discord for "Text her" (Telegram needs nothing extra)
envs\speech\python.exe -m pip install -r messengers\requirements.txt
```

## Start her

```powershell
envs\brain\python.exe launch.py --check   # validates everything (imports, files, ports, keys); starts nothing
Start_OpenAmika.bat                       # one console: pre-flight, then embedder → brain → vision → speech
```

The browser opens **http://localhost:53000** when the speech gateway is up. The first start downloads the
bge-m3 embedding model (~2.3 GB) into your Hugging Face cache, so give it a few minutes; later starts take
about a minute. `Stop_OpenAmika.bat` (or Ctrl+C in the launcher console) stops everything the launcher started.

On the **Use** page a one-time wizard introduces her: she asks your name (with an optional pronunciation
guide for her voice), your city and her clock (detected from your browser), takes your OpenRouter key if
`brain/.env` has none, and runs the health check. Then press power and talk, or type.

The light at the top right of every page is the system health: green *System Ready*, yellow *Warnings*,
red *System Failures*; click it for what is wrong. Without a TTS server it stays yellow and she answers in text.

### With OpenRouter

Make an account at [openrouter.ai](https://openrouter.ai), add a few dollars of credit, create a key at
[openrouter.ai/keys](https://openrouter.ai/keys) and put it in `brain/.env` (or paste it in the wizard). The
default roles are a reviewed preset; Setup → Brain lists the alternatives with their prices, and the Use page
shows the cost of the session as it happens.

### Fully local with Ollama

1. Install Ollama from [ollama.com/download](https://ollama.com/download) and pull a model:
   `ollama pull qwen3:8b` (or `qwen3:4b` on a smaller card).
2. Start OpenAmika, open **Setup → Brain**, switch to **Ollama**, press **Detect models**, pick one and press
   **Use**: all four roles (surface, deep, reflection, memory writer) now run on it. The wizard offers the same
   switch on first run.
3. No key is needed; `launch.py --check` shows "API keys: none needed" once every role is local. Calls are
   recorded in the same ledger at zero cost. Small models keep her personality less steadily than the
   OpenRouter preset; in Professional mode you can mix (a local model for some roles, OpenRouter for the rest).

### Her voice

OpenAmika speaks to any TTS server that implements the small Socket.IO contract in [`docs/TTS.md`](docs/TTS.md)
(`tts_request_gateway` → `audio_ready`, one emotion tag per sentence). Point `tts_url` in settings.json at it,
or describe it under `services.tts` so the launcher starts it with everything else. No server and no voice
ship with this repository.

### Her body

Drop a Live2D Cubism model folder into `avatar/models/<Name>/` and reload; see
[`avatar/models/README.md`](avatar/models/README.md) for free sample models and the emotion banks.

### From your phone (Tailscale)

The app binds to 127.0.0.1 on purpose. To reach it from your own devices, use
[Tailscale Serve](https://tailscale.com/kb/1312/serve) instead of opening ports:

```text
Tailscale_Serve_OpenAmika.bat          →  https://<your-machine>.<tailnet>.ts.net:8447/
Tailscale_Serve_OpenAmika.bat off      →  remove the mapping
```

tailscaled terminates HTTPS with a real certificate (needs MagicDNS + HTTPS enabled on the tailnet), so the
microphone works from phones. One conversation tab at a time.

## Who she is

Amika (桜井美香, Sakurai Mika) grew up in Kyoto as the heir of a shrine family. At eighteen, tired of a life
where every step was arranged, she flew to **your city** to live with **you** as roommates, after the two of you
became close friends online. Her day runs on her clock: five possibilities per time slot (the morning walk,
the supermarket, an afternoon event, the gym, the shrine routine at night, the late-night scrolling), picked
once per day so her afternoon does not change mid-conversation.

The persona never assumes who you are: it calls you by the name you give her (`{user_name}`, `{city}` are
filled in from the wizard) and uses the gender-neutral TA where Chinese needs a pronoun. Everything is
editable on the Setup page (Personality); [`docs/PERSONALITY.md`](docs/PERSONALITY.md) explains the three tiers,
what is safe to change, and how *Reset everything* restores the shipped persona from `brain/defaults/`.

The prompts are written in Chinese on purpose (her inner thoughts stay Chinese); she answers in the language
you speak (English, Japanese or Chinese), and "say it in Japanese" switches for one turn.

## Configuration

| file | what | in git |
|---|---|---|
| `settings.json` | interpreters, ports, STT device, TTS URL, embedder and vision blocks, optional services | no (copy of `settings.example.json`) |
| `brain/.env` | API keys (`OPENROUTER_API_KEY`, optional providers) | no (copy of `.env.example`) |
| `config/build_f.json` | your name, your city, her clock, memory budgets, reflection switches | yes (the wizard and Setup write your name and city here) |
| `config/llm_roles.json` | the four LLM roles, presets and your selection | yes |
| `config/word_replace.json` | speech-recognition fixes and TTS pronunciations (incl. your name's) | yes |
| `config/tools.json`, `config/messengers.json` | MCP servers and bot tokens (from the `.example.json` files or the Tools / Setup pages) | no |
| `brain/scenes.json` + the three templates | her persona | yes |

Runtime data stays local and is git-ignored: `memory_f/` (her memory and the cost ledger), `brain/memories/`,
`logs/`. If you publish a fork, check `config/build_f.json` and `config/word_replace.json` first: after
onboarding they contain your name and city.

## Ports

| service | port | where |
|---|---|---|
| web pages + speech gateway | 53000 | `settings.json` `chat_port` |
| brain | 5202 | `brain_port` |
| embedder | 8011 | `embedder.port` |
| vision (optional) | 8014 | `vision.port` |
| Discord/Telegram service | 5215 | fixed |
| your TTS server | 8002 | `tts_url` |
| Ollama | 11434 | Setup → Brain |
| MQTT / zigbee2mqtt (optional) | 1883 / 8080 | `services` |

Everything binds to 127.0.0.1.

## Optional extras

- **Tools** (Tools page, `docs/TOOLS.md`): any MCP server, stdio or HTTP, with per-tool `auto` / `confirm` /
  `deny` policies. Personal task agents (Hermes Agent, nanoMuse) plug in through `servers/openamika-agents`.
- **Smart home** (`IoT/README.md`): Zigbee plugs through mosquitto + zigbee2mqtt and the first-party
  `openamika-iot` MCP server; Home Assistant's own MCP server works too.
- **Text her** (`docs/MESSENGERS.md`): a Discord and/or Telegram bot that answers only the user ids you allow.
- **Camera** (`vision/`): she describes what she sees while the camera is on; frames never leave the PC.

## Tests

```powershell
envs\brain\python.exe -m pip install -r requirements-dev.txt
envs\brain\python.exe -m pytest tests -k "not live"
```

About 350 offline tests cover the output contract, both streaming parsers (brain/speech parity), prompt
assembly and caching layout, memory rendering, the usage ledger, the control proxies, the tool clerk and the
launcher. Replay tests that need MekaHime's archived corpora skip themselves.

## What is not included

- **Amika's Live2D model and her voices.** They are MekaHime assets (the hosted Amika Online uses them). Bring
  any Live2D model and any TTS server that speaks `docs/TTS.md`.
- **Model weights.** SenseVoiceSmall is downloaded by `scripts/download_sensevoice.py`; bge-m3 and the vision
  models download into the Hugging Face cache on first use.
- **Third-party stacks** you install yourself if you want them: Ollama, zigbee2mqtt, mosquitto, Hermes Agent,
  nanoMuse, a TTS server.
- **Anyone's data.** No memories, transcripts, logs or keys ship; the shipped identity is the neutral default
  that the first-run wizard replaces.

Code comments that cite `CONTRACT §…`, `TELEMETRY_SPEC §…` or `BUILD_SPEC` refer to MekaHime's internal design
documents from the predecessor builds; they are not part of this repository.

## Credits

Built by MekaHime. Standing on: [SenseVoice / FunASR](https://github.com/FunAudioLLM/SenseVoice) (speech
recognition), [Silero VAD](https://github.com/snakers4/silero-vad), [BAAI bge-m3](https://huggingface.co/BAAI/bge-m3)
(memory embeddings), [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) (vision), [PixiJS](https://pixijs.com) and
[pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) with the
[Live2D Cubism SDK](https://www.live2d.com/en/sdk/about/) (the avatar), [OpenRouter](https://openrouter.ai) and
[Ollama](https://ollama.com) (her brain), the [Model Context Protocol](https://modelcontextprotocol.io) (tools),
[zigbee2mqtt](https://www.zigbee2mqtt.io) and [Eclipse Mosquitto](https://mosquitto.org) (smart home).

## License

- OpenAmika's code and texts: **MIT**, © MekaHime Inc. (`LICENSE`).
- Bundled third-party files keep their licenses (`THIRD_PARTY_NOTICES.md`): SenseVoice model code (MIT),
  PixiJS and pixi-live2d-display (MIT), and **Live2D Cubism Core** (`ui/vendor/live2dcubismcore.min.js`, the Live2D
  Proprietary Software License Agreement, not MIT).
- Downloaded models (SenseVoiceSmall, bge-m3, vision models, LLMs) are under their own licenses.
- Amika's character (her name, design and likeness) is © MekaHime Inc.; the MIT License grants no rights to
  it. You are welcome to reshape the persona into your own companion.

Questions: hi@mekahime.com · Support the open-sourcing work: https://www.mekahime.com/donate
