# OpenAmika Tools Layer — research & design proposal

*Researched 2026-10-20 (web + legacy-code review). Status: **superseded** — kept for the reasoning and the
deferred items only. What runs today is described in [docs/TOOLS.md](../TOOLS.md): she speaks first and a
post-speech clerk hands over only what she agreed to (the pre-deep tool pass below is gone), and personal task
agents come in through `servers/openamika-agents` (Hermes Agent and nanoMuse adapters), with MekaHime's paper
doll standing in for them on her stage. "Muse" below was guessed to be Meta's assistant and "never
product-specific adapters" was the plan; what shipped is a small adapter each for Hermes Agent and nanoMuse
behind one bridge.*

## Decisions

1. **Build an MCP client inside OpenAmika** (official `mcp` Python SDK). Users register servers
   (stdio command or streamable-HTTP URL) in `config/tools.json`, edited from the Tools page. MCP is
   the industry-default tool protocol, governed with A2A under the Linux Foundation's Agentic AI
   Foundation (spec revision 2026-07-28: stateless HTTP core, cacheable list results, hardened OAuth;
   stdio stays the local transport).
2. **Tool calls run only around the deep tier.** The surface stays tool-free and fast. v1 runs the
   tool loop as a *pre-pass before the deep prompt* (prompt-based JSON calling through the metered
   text-only transport, phase `tools`), its outcome injected as per-turn context — the deep model then
   speaks about it in persona. This keeps the frozen transport untouched and works with every model;
   native `tool_calls` streaming remains a possible v2.
3. **IoT default: the first-party `openamika-iot` MCP server** wrapping the existing mosquitto +
   zigbee2mqtt stack — the legacy bridge's MQTT core ported verbatim (registry, watchdog reconnect,
   confirm-on-command, nicknames), REST/HTML layer dropped. **No OpenClaw anywhere.** Home Assistant's
   official MCP server is the documented alternative (its own OAuth; everything HA controls).
4. **Agent-to-agent v1 = "agents are just MCP servers."** OpenClaw ships `openclaw mcp serve`
   (messages/events/channels over its gateway) — one optional config entry, never an embedded
   dependency (it is an entire second assistant with a large security surface). **A2A deferred**:
   real and growing but enterprise-weighted; no consumer personal agent exposes a public A2A endpoint
   today. The hub's connector seam (list/call/health) accepts an A2AConnector later without touching
   the chat loop.
5. **Safety:** servers start disabled; per-tool `auto`/`confirm`/`deny` with read-only name heuristics
   as defaults; `confirm` parks the call for UI approval (she asks out loud, you click, she reports
   next turn); per-call timeout, per-turn call cap, phase deadline, master switch; results are
   summarized in her voice — a standing rule forbids reading JSON/identifiers aloud; errors become
   in-character apologies while the technical payload goes to the Tools-page log.

## What the legacy Amika_IoT actually was

Zigbee plugs → SONOFF dongle → stock zigbee2mqtt 2.9.1 → mosquitto (2-line conf) → a FastAPI bridge
(~960 lines) → **OpenClaw as a pure HTTP client** (two markdown guides told it which `curl` to run).
OpenClaw contributed zero code; its role — "LLM decides, then calls the API" — is exactly what the
deep tier + MCP now do natively. Reused verbatim: `mosquitto.conf`, the z2m config shape, and the
bridge's MQTT core. Dropped: the REST layer, the inline dark-mode HTML panel, the macOS launch scripts,
the OpenClaw prompt guides (their "health-check then retry once" wisdom now lives in the tool
descriptions, where any model inherits it).

## "Hermes" and "Muse"

- **Hermes Agent** (Nous Research, github.com/NousResearch/hermes-agent): open-source self-improving
  agent harness, OpenRouter-compatible — and an **MCP client** like us. Interop = shared servers:
  anything from its curated catalog plugs into OpenAmika unchanged. Post-v1 charm: expose OpenAmika's
  *own* small MCP server (`ask_amika`, `amika_memory_search`) so Hermes/Claude/anything can talk to her.
- **Muse** = most plausibly **Meta Muse** (Meta Superintelligence Labs' personal agent, launched
  2026-09): closed consumer product, no public MCP/A2A endpoint — nothing to integrate today. (Unity
  Muse is sunset; Microsoft's Muse is a gameplay world-model.) Either way the conclusion holds:
  **integrate via open protocols, never product-specific adapters.**

## Key references

MCP spec & 2026-07-28 revision — modelcontextprotocol.io · blog.modelcontextprotocol.io/posts/2026-07-28 ·
python SDK: github.com/modelcontextprotocol/python-sdk — OpenRouter tool-calling & MCP bridging:
openrouter.ai/docs/guides/features/tool-calling · openrouter.ai/docs/cookbook/coding-agents/mcp-servers —
Home Assistant MCP Server: home-assistant.io/integrations/mcp_server — OpenClaw MCP:
docs.openclaw.ai/cli/mcp/serve — A2A: Linux Foundation / Agentic AI Foundation (v1.0 2026-03) —
Hermes Agent MCP docs: hermes-agent.nousresearch.com/docs/user-guide/features/mcp — Meta Muse launch:
about.fb.com/news/2026/09/introducing-muse-personal-ai-agent.

## Deferred (post-v1)

- OpenAmika's own MCP server (`ask_amika`, memory search) — small effort, high charm.
- A2A connector behind the hub seam.
- Parallel tool execution; native `tool_calls` streaming inside the deep stream.
- In-conversation verbal confirmation for `confirm` tools (v1 confirms on the Tools page).
- Action-detector upgrades: seed the trigger regex from the IoT device list; surface-model intent tag.
