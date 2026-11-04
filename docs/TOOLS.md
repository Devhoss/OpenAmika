# Tools: agents and MCP servers as her hands

OpenAmika is an **MCP client**. Any server speaking the
[Model Context Protocol](https://modelcontextprotocol.io) — stdio command or streamable-HTTP URL —
can give her hands. Personal task agents you already run (**Hermes Agent**, **nanoMuse**) plug in
through `servers/openamika-agents`, which shows every agent to her as the same four tools:
`ask / status / answer / stop`. Configuration lives in `config/tools.json` (gitignored; template:
`config/tools.example.json`), fully editable from the Tools page. Enabling a server puts it into her
context; disabling takes it out.

## She decides, the clerk transcribes

1. **She knows what she has.** Every enabled server is described to her, in character, in a
   `[你手边的工具]` block inside both the surface and the deep prompt (`persona_note` per server, with
   `{user_name}` filled in), together with the rules: only when *you* asked for something this turn
   and *she* is willing; what she agrees to starts running after she has finished speaking, so she
   must not claim it is already done; she may refuse, stall, grumble or bargain; she can never start
   anything you did not ask for.
2. **She answers exactly as always** — surface line first, deep reply after. Nothing runs before she
   has spoken. "Fine, I'll have it looked up" or "Do it yourself" is the decision.
3. **The clerk.** Once the turn's text is final, a small metered call (ledger phase `tools`, on the
   `clerk_role` model — `surface` by default, DeepSeek-class is plenty) reads what you said and what
   she said out loud and outputs only the calls she committed to. Each call must **quote your own
   words** that constitute the request; the brain verifies the quote against your text in code
   (punctuation- and space-insensitive). No quote, no call. Her inner thoughts are reference only.
   Injected turns (tool relays, cues) never run the clerk; an idle *open* turn runs it only against
   your most recent message (see *Health & open turns* below).
4. **Agents work in the background.** `ask` returns a job id at once; the brain polls `status` every
   few seconds. Progress steps go to the UI (`tool_progress` socket event); a question or an
   approval request is relayed through her as an injected turn; the final report is handed to her as
   a `[工具执行记录]` context block and she tells you in her own words. She is the personality relay:
   the agent never talks to you directly.
   A result reaches her two ways at once. It becomes an injected turn, spoken at the first quiet
   moment. Until she has told you, it also sits in her capability block as a fresh, not-yet-heard
   result, so a turn of yours that lands first already knows it. After each turn the clerk reports
   which results she conveyed (`told`); a relay she no longer needs is dropped, and a relay turn you
   talked over (the gateway discards proactive speech when you speak) is queued again until it is
   actually spoken. While a job runs, the block says so with the elapsed time and forbids reporting
   any name, address or number for it. If she still reports a result no helper has delivered, the
   clerk flags it (`fabricated`) and she is told at once to take it back; the real result, when it
   comes, tells her to correct herself.
5. **Approvals belong to you.** When an agent asks for approval (nanoMuse's Sentinel card, Hermes's
   approval policy), she asks you. Only your explicit yes on a later turn (clerk, quote-verified) or
   a click on the Tools page releases it. She has no approve path of her own.

## Policies

Agent bridge tools (`ask / status / answer / stop` on a server with `kind: agent`) are **auto** —
their consent is the quote check. Plain MCP tools keep the three policies: **auto** (runs when she
agreed) · **confirm** (parks as *pending*; you approve on the Tools page, it runs, she reports) ·
**deny** (hidden from the clerk). Unknown plain tools default by name: `list_*`/`get_*`/`read_*`/
`search_*`/`health`/`status` → auto, everything else → confirm. New servers start **disabled**.

## The agents

- **Hermes Agent** (Nous Research) — `hermes gateway` with its API server on (`API_SERVER_ENABLED=true`,
  `API_SERVER_KEY`). The bridge uses the Runs API: `POST /v1/runs`, the SSE event stream
  (`tool.started`, `message.interim`, `approval.request`, `run.completed`…), `approval`, `clarify`,
  `stop`. One `HERMES_SESSION` keeps the conversation with the agent continuous across jobs.
  `browser.headed: true` in Hermes's config gives a visible browser window.
- **nanoMuse** — `nanomuse serve`. The bridge keeps one thread titled *OpenAmika*: `send`, the
  timeline (`assistant`, `tool`, `approval`, `question`, `notice` events), `/api/approvals/{id}`,
  `stop`. Its `nanomuse mcp` server (screen and hands only) can be added as a plain MCP server too.
  nanoMuse is not on PyPI: clone the repo and `pip install -e ".[browser]"` into its own
  environment (Python 3.11+), then `playwright install chromium`; it runs natively on Windows with
  `sandbox.mode = "off"`. Point it at OpenRouter with `[llm] provider = "openai"`,
  `base_url = "https://openrouter.ai/api/v1"`, `[browser] enabled = true, headless = false` for a
  visible window, `[cloud] required = false` to skip the account, and give the bridge
  `NANOMUSE_TOKEN_FILE = <data_dir>/server_token`.
- **Smart home** — `servers/openamika-iot/`, an MCP wrapper around mosquitto + zigbee2mqtt (ported
  from the Amika_IoT bridge): `list_devices`, `get_state`, `set_state`, `set_nickname`,
  `activate_scene`, `health`. Routed like any plain server; see its README. Needs the accepted IoT
  safety statement (below).
- **Home Assistant** — its MCP Server integration URL + a long-lived token. Counts as IoT too.
- **OpenClaw** — `openclaw mcp serve` exposes channel conversations (read/send messages), not its
  toolset; add it as a plain server if that is what you want.

Details and the bridge's environment variables: `servers/openamika-agents/README.md`.

## Safety and responsibility (IoT)

Smart-home servers give her hands in the physical world, so they come with a statement you must
read and accept first: [`docs/IOT_DISCLAIMER.md`](IOT_DISCLAIMER.md) (English, 日本語, 简体中文). In
short: she runs on non-deterministic language models; she can mishear, misunderstand, act on the
wrong device or at the wrong time, repeat or skip an action, or be manipulated by what she reads or
hears (prompt injection); models, providers, networks, bridges, Zigbee/MQTT and firmware can fail.
There is no guarantee that she will not cause harm to your devices, property, data, people or
animals. Everything you connect is at your own risk, and MekaHime Inc. and the contributors are not
responsible for any outcome, damage, loss or injury, to the maximum extent the law allows (the
software is provided as is, MIT License). Never connect anything that could cause danger if misused
or abused (heaters and other heating appliances, valves and pumps, locks, garage doors and gates,
alarms, medical, child- or pet-safety equipment, vehicles, machinery, high-power loads). The quote
check, the policies and the `confirm` step are best-effort, never a guarantee; keep manual, physical
control of every device, and turn IoT off at any time on the Tools page.

How it is enforced: a smart-home server is the `openamika-iot` bridge (named `iot` or started as
`openamika_iot`), Home Assistant's MCP server (recognised by its name or its URL), or any entry with
`"kind": "iot"`. Turning one on from the Tools page shows the statement in a dialog: an unticked
"I have read and accept this" box and a separate **Enable IoT** button; Cancel leaves IoT off. The
acceptance is stored in `config/tools.json` as `"iot_disclaimer": {"version", "accepted_at"}` (only
`POST /api/build_f/iot/disclaimer` writes it; a config save keeps what is on disk). Until the current
version is accepted, the gateway and the brain refuse a config that switches a smart-home server on
(409, `iot_disclaimer_required`), the brain's MCP hub refuses to start, connect or call one on every
path (her turns, the health loop, the Smart home panel, Test, approvals), the launcher holds
mosquitto and zigbee2mqtt back (and starts them as soon as you accept), and the bridge itself
refuses to start without the accepted version (`OPENAMIKA_IOT_DISCLAIMER_ACCEPTED`, set by the
brain, or `--accept-iot-disclaimer <version>` when you run it yourself). An install that already had
IoT on keeps it off, and the Tools page says why; when the statement changes, its version
(`IOT_DISCLAIMER_VERSION` in `brain/iot_disclaimer.py`) changes, and the dialog asks again.

## Health & open turns

Every agent bridge also exposes `health` (is the agent process up, does it accept our key?). The brain
probes it every 30 s in the background and again right before every `ask`. The server card on the
Tools page shows it (green = bridge up and agent online, red = bridge up but the agent unreachable,
amber = bridge not up, with the reason); her capability block says *联系不上* so she does not promise
what cannot run; and an `ask` to an unreachable agent is **not sent** — she tells you the helper is
down instead of promising and failing later. A job that dies of a connection error is relayed in
plain words ("联系不上 hermes", not a socket error) and marks the agent unreachable until the next
probe. The block also says when nothing is running, so she cannot believe a helper is "still on it".

**Starting the agents for you.** An agent entry may carry `autostart`, its own start command (a
string, or `{command, args, cwd, env}`; the Tools page has a *Start command* field on every agent
card). When a probe finds the agent unreachable, the brain launches that command detached from
itself (so it survives a brain restart), the card shows *agent starting…* for up to 90 s, and the
clerk waits a moment for a just-launched agent before giving up on an ask. After three failed
attempts the card shows *start failed* with the exit code; the output is in `logs/agent-<name>.log`
and the **Start** button tries again. With no `autostart`, enabling only connects the bridge and the
card stays red until you start the agent yourself.

She may also agree on one of her own turns (an idle *open* turn right after your request: "fine,
having Hermes look it up"). The clerk then runs with your most recent message (within 10 minutes) as
the only source of quotes, at most once per message, and never takes approvals or answers from such a
turn. The thinking layer, which writes those open turns, sees a third-person copy of the block
(`[她手边的工具]`) so it does not script results that do not exist.

## The tool dialogue on the Use page

Under her stage the Use page shows a transcript of her side of every delegation and the helper's
side: what she asked (with the words of yours that authorised it), each step the agent reports,
what it says back, its questions and approval requests, your answers (spoken or clicked), the final
result or failure, and the brain's own notes (a request that was not sent because the agent was
down, a result she invented and was told to retract). It is the `dialogue` list of
`/api/build_f/tools`, polled every couple of seconds while you are connected. Hermes's headed browser is
also brought to the front with the page it is on after every browser step, so you can watch it work.

### On stage: the agent avatar, speech bubbles, her loading emote

The same exchange plays out on her stage. A second Live2D model marked `role: "agent"` in its
`banks.json` (see `avatar/models/README.md`; the bundled example is the paper-doll "host") stands in
for whichever helper is working. It is seated bottom-right whenever an agent server is enabled, and
slides away when none is. Each new line of the tool dialogue becomes a speech bubble: hers out of
her, the helper's out of the doll, and the two turn to look at each other while a bubble is up. The
doll plays motions by state: `talk*` while it has the word, `listen*` while she talks to it,
`think*` while a job is running, `happy*` on a result, `worried*` on an error, `surprise*` when it
has a question for you. The bundled set is retargeted from her own recordings with
`avatar/retarget_motions.py`. Lines that were already in the log when the page opened do not get a
bubble; the transcript below the stage keeps everything. You can drag or zoom the doll like her;
"Re-center" resets both.

Her animated "loading" emote (the model's `Loading` expression plus the loop named by
`busy_motion` in her `banks.json`, `Scene1` on the bundled model) is layered over whatever face she
is making while she is waiting on herself: from the moment the
surface tier hands a turn to the deep layer (`say+think` / `think`) until the deep's first sentence
starts playing (the gateway tags each `tts_audio` with its tier). It switches off on the deep's first
words, at the end of the turn, on a barge-in or a new turn. A helper still working is not her
waiting: that shows on the doll (its `think*` motions), never as her icon.

## Costs & privacy

The clerk is one small call per user turn while any server is enabled (phase `tools` in the ledger /
Use page); job polling is local. What a helper spends on its own model is booked too when it reports
it: Hermes returns token totals per run, and the bridge hands them to the brain, which records a
receipt under `agent:hermes`. OpenRouter did not confirm that charge, so the ledger keeps it as an
*estimate* at the model's list price (`HERMES_MODEL`; cached input is not discounted), separate from
the confirmed figure. The Use page's session cost shows confirmed plus estimated and says how much is
estimated. nanoMuse exposes no usage through its API, so its spend is not in the ledger; the
OpenRouter key's own usage counter remains the only complete number. What an agent brings back (a web page summary, an email) enters her
context for the relay and may be remembered like anything she said. The Tools page shows exactly
what is enabled, every call with the words of yours that authorised it, and every job; the master
switch removes everything from her context.

## Requirements

`pip install "mcp>=1.2,<2"` in the **brain** environment (in `brain/requirements.txt`), plus whatever
each server needs: `pip install -e servers/openamika-agents` (httpx) for the agents, Hermes or
nanoMuse themselves installed and running, `pip install -e servers/openamika-iot` for smart home.
