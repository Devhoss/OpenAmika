# openamika-agents — personal agents as her tools

A tiny MCP server that lets OpenAmika hand whole tasks to a personal task agent you already run,
and relay what comes back through Amika. One bridge process per agent; every agent looks the same
to her: **ask / status / answer / stop**, plus **health** for the brain (is the agent up, is the key
accepted, can it work?). While one is switched on, the Use page seats MekaHime's paper doll beside her
to play the agent's side (`avatar/models/Host`, from `scripts/setup_tts.py`).

Step-by-step setup of both agents, the paper doll and troubleshooting: [docs/TOOLS.md](../../docs/TOOLS.md),
*Her helpers*.

| agent | how it is driven | what you need running |
|---|---|---|
| [Hermes Agent](https://hermes-agent.nousresearch.com) | its API server, the Runs endpoints (`POST /v1/runs`, SSE events, approval / clarify / stop) | `hermes gateway` with an `API_SERVER_KEY` of 16 or more characters (and `API_SERVER_ENABLED=true`) in its `.env`, `%LOCALAPPDATA%\hermes\.env` by default (port 8642) |
| [nanoMuse](https://github.com/nano-muse/nanoMuse) | its server API (`/api/threads/{id}/send`, timeline events, approval cards, stop; `/api/settings` for health) | `nanomuse serve` (port 8787; token in `<data_dir>/server_token`) with a model key in its vault. Not on PyPI: clone the repo, `pip install -e ".[browser]"` into its own Python 3.11+ env, `playwright install chromium`; works natively on Windows with `sandbox.mode = "off"` |

Nothing to install for the bridge itself: the brain starts it with its own interpreter from this folder
(`python -m openamika_agents <agent>`, `cwd` `../servers/openamika-agents`), and `httpx` comes with the
`mcp` SDK the brain already has. (`pip install -e servers/openamika-agents` only if you run it from
somewhere else.)

Add an entry to `config/tools.json` (the hermes and nanomuse entries of `config/tools.example.json` are
complete; or on the Tools page, Professional mode → Raw config):

```json
"hermes": {
  "command": "python", "args": ["-m", "openamika_agents", "hermes"],
  "cwd": "../servers/openamika-agents",
  "autostart": {"command": "%LOCALAPPDATA%/hermes/bin/hermes.exe", "args": ["gateway"], "cwd": "%LOCALAPPDATA%/hermes"},
  "env": {"HERMES_URL": "http://127.0.0.1:8642", "HERMES_API_KEY": "<the API_SERVER_KEY from Hermes's .env>",
          "HERMES_SESSION": "openamika", "HERMES_PROVIDER": "openrouter", "HERMES_MODEL": "z-ai/glm-5",
          "HERMES_INSTRUCTIONS": "You are working for Amika, an AI companion, on behalf of her user …"},
  "enabled": true, "kind": "agent",
  "persona_note": "赫尔墨斯——{user_name}装在他电脑上的一个小帮手程序。你可以让它替你上网查东西……"
}
```

`command: "python"` means the brain's own interpreter. `persona_note` is what she is told about the
agent, in character (Chinese like the rest of her prompts); `{user_name}` is replaced with your name.
`kind: "agent"` makes it a helper (and seats the paper doll).

`autostart` (an object as above, or one command line with quotes around a path that has spaces) is the
agent's own start command: the launcher starts it before her brain, and the brain starts it again when it
is not running (`docs/TOOLS.md`, *Starting the agents for you*). `%VARIABLES%` in it are expanded, and a
relative path is taken from the OpenAmika folder.

Environment variables per agent:

- Hermes: `HERMES_URL` (default `http://127.0.0.1:8642`), `HERMES_API_KEY` (must equal `API_SERVER_KEY` in
  the Hermes home's `.env`), `HERMES_SESSION` (one session = one continuing conversation with the agent; a
  session a copied Hermes home refuses with 409 `profile_mismatch` is replaced once by
  `HERMES_SESSION-<pc>`), `HERMES_INSTRUCTIONS` (extra system instructions sent with every run — tell it who
  it works for and to keep reports short), `HERMES_SURFACE_BROWSER` (default on: after every browser step the
  bridge activates the page the agent is on and raises Hermes's "Chrome for Testing" window, which otherwise
  opens behind your windows with a blank first tab; set `0` to leave the browser alone), `HERMES_PROVIDER` /
  `HERMES_MODEL` (what Hermes is configured to call, e.g. `openrouter` / `z-ai/glm-5`: Hermes reports
  token totals per run but no price, so the brain books them in her ledger under this model, phase
  `agent:hermes`, and the Use page's session cost includes them).
- nanoMuse: `NANOMUSE_URL` (default `http://127.0.0.1:8787`), `NANOMUSE_TOKEN` or
  `NANOMUSE_TOKEN_FILE` (read again until the file exists: `nanomuse serve` writes it on its first start),
  `NANOMUSE_THREAD` (thread title, default `OpenAmika`), `NANOMUSE_LANGUAGE` (the reply language, e.g. `en`,
  `ja`, `zh-CN`).

## How a job flows

1. You ask her for something; she agrees out loud. After she has spoken, the brain's clerk calls
   `ask(task)` (see `docs/TOOLS.md` for the consent rules).
2. The bridge starts the task in the agent and returns a job id at once. The brain polls `status`
   every few seconds: progress steps go to the UI (her bubble and the paper doll's on the Use page),
   questions and approval requests are relayed through her, and the final report is spoken by her in
   her own words.
3. If the agent asks something or wants an approval, your answer in conversation (or a click on the
   Use page's **Her helpers** card) goes back through `answer`. `stop` cancels a run.
4. `health` probes the agent itself: Hermes `GET /health` (no auth) then `GET /health/detailed` with
   the key; nanoMuse `GET /api/health`, then `GET /api/threads` with the token and `GET /api/settings`
   (`llm_ready`). It answers `{ok, reason, detail, url, version}` with reason `ok / unreachable / timeout /
   unauthorized / http_error / starting` (nanoMuse), `no_model` (nanoMuse without a working model key) or
   `refused` (Hermes declined the last task). The brain calls it every 30 s and right before every `ask`; an
   agent that is not ok shows red on the Tools page and she is told not to promise it anything.
5. A job ends `done` with the agent's report, `failed` with its error (Hermes's `run.failed`, nanoMuse's error
   notice such as "The model provider refused the API key", a refused run, 15 minutes without an end) or
   `stopped`.

Run the bridge by hand to poke at it:

```
python -m openamika_agents hermes       # speaks MCP over stdio; logs go to stderr
```

Tests: `tests/test_agents_bridge.py` exercises both adapters against in-process fakes of the two
APIs (`tests/_agent_fakes.py`) and does one real MCP stdio round trip — no agent needs to be installed.
