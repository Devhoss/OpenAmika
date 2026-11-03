# openamika-agents — personal agents as her tools

A tiny MCP server that lets OpenAmika hand whole tasks to a personal task agent you already run,
and relay what comes back through Amika. One bridge process per agent; every agent looks the same
to her: **ask / status / answer / stop**, plus **health** for the brain (is the agent up, is the key
accepted?).

| agent | how it is driven | what you need running |
|---|---|---|
| [Hermes Agent](https://hermes-agent.nousresearch.com) | its API server, the Runs endpoints (`POST /v1/runs`, SSE events, approval / clarify / stop) | `hermes gateway` with `API_SERVER_ENABLED=true` and an `API_SERVER_KEY` in its `.env` (port 8642) |
| [nanoMuse](https://github.com/nano-muse/nanoMuse) | its server API (`/api/threads/{id}/send`, timeline events, approval cards, stop) | `nanomuse serve` (port 8787; token in `<data_dir>/server_token`). Not on PyPI: clone the repo, `pip install -e ".[browser]"` into its own Python 3.11+ env, `playwright install chromium`; works natively on Windows with `sandbox.mode = "off"` |

Install into the **brain** environment (it needs the same `mcp` SDK the brain uses):

```
pip install -e servers/openamika-agents
```

Then add an entry to `config/tools.json` (or on the Tools page → Add a server):

```json
"hermes": {
  "command": "python", "args": ["-m", "openamika_agents", "hermes"],
  "cwd": "../servers/openamika-agents",
  "env": {"HERMES_URL": "http://127.0.0.1:8642", "HERMES_API_KEY": "the API_SERVER_KEY from hermes/.env",
          "HERMES_SESSION": "openamika",
          "HERMES_INSTRUCTIONS": "You are working for Amika, an AI companion, on behalf of her user …"},
  "enabled": true, "kind": "agent",
  "persona_note": "赫尔墨斯——{user_name}装在他电脑上的一个小帮手程序。你可以让它替你上网查东西、看他的屏幕、读写邮件、网上下单……"
}
```

`command: "python"` means the brain's own interpreter. `persona_note` is what she is told about the
agent, in character (Chinese like the rest of her prompts); `{user_name}` is replaced with your name.

Add `"autostart": "C:\\Users\\you\\AppData\\Local\\hermes\\bin\\hermes.exe gateway"` (or
`{"command", "args", "cwd", "env"}`) and the brain starts the agent itself when it is not running
(`docs/TOOLS.md`, *Starting the agents for you*).

Environment variables per agent:

- Hermes: `HERMES_URL` (default `http://127.0.0.1:8642`), `HERMES_API_KEY`, `HERMES_SESSION` (one
  session = one continuing conversation with the agent), `HERMES_INSTRUCTIONS` (extra system
  instructions sent with every run — tell it who it works for and to keep reports short),
  `HERMES_SURFACE_BROWSER` (default on: after every browser step the bridge activates the page the
  agent is on and raises Hermes's "Chrome for Testing" window, which otherwise opens behind your
  windows with a blank first tab; set `0` to leave the browser alone), `HERMES_PROVIDER` /
  `HERMES_MODEL` (what Hermes is configured to call, e.g. `openrouter` / `z-ai/glm-5`: Hermes reports
  token totals per run but no price, so the brain books them in her ledger under this model, phase
  `agent:hermes`, and the Use page's session cost includes them).
- nanoMuse: `NANOMUSE_URL` (default `http://127.0.0.1:8787`), `NANOMUSE_TOKEN` or
  `NANOMUSE_TOKEN_FILE`, `NANOMUSE_THREAD` (thread title, default `OpenAmika`), `NANOMUSE_LANGUAGE`.

## How a job flows

1. You ask her for something; she agrees out loud. After she has spoken, the brain's clerk calls
   `ask(task)` (see `docs/TOOLS.md` for the consent rules).
2. The bridge starts the task in the agent and returns a job id at once. The brain polls `status`
   every few seconds: progress steps go to the UI, questions and approval requests are relayed
   through her, and the final report is spoken by her in her own words.
3. If the agent asks something or wants an approval, your answer in conversation (or a click on
   the Tools page) goes back through `answer`. `stop` cancels a run.
4. `health` probes the agent itself: Hermes `GET /health` (no auth) then `GET /health/detailed` with
   the key; nanoMuse `GET /api/health` then `GET /api/threads` with the token. It answers
   `{ok, reason, detail, url, version}` with reason `ok / unreachable / timeout / unauthorized /
   http_error / starting`. The brain calls it every 30 s and right before every `ask`; an unreachable
   agent shows red on the Tools page and she is told not to promise it anything.

Run the bridge by hand to poke at it:

```
python -m openamika_agents hermes       # speaks MCP over stdio; logs go to stderr
```

Tests: `tests/test_agents_bridge.py` exercises both adapters against in-process fakes of the two
APIs and does one real MCP stdio round trip — no agent needs to be installed.
