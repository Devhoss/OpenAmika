"""openamika-agents — personal task agents (nanoMuse, Hermes Agent) exposed to OpenAmika as MCP tools.

One bridge process per agent (`python -m openamika_agents hermes` / `nanomuse`). Each exposes the
same four tools — ask / status / answer / stop — so the brain's clerk and relay code never has to
know which agent is behind them. Jobs run in the background inside the bridge; the brain polls
`status` and relays progress, questions, approval requests and the final report through Amika.
"""
__version__ = "0.1.0"
