"""Entry point: `python -m openamika_agents hermes` / `python -m openamika_agents nanomuse`.

Speaks MCP over stdio (what config/tools.json spawns). Everything diagnostic goes to stderr —
stdout belongs to the protocol.
"""
from __future__ import annotations

import argparse
import sys

from .adapters import ADAPTERS
from .server import build_server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="openamika_agents",
                                     description="Expose a personal agent to OpenAmika as MCP tools.")
    parser.add_argument("agent", choices=sorted(ADAPTERS), help="which agent this bridge talks to")
    parser.add_argument("--settle", type=float, default=1.5,
                        help="seconds `ask` waits for an immediate failure before returning (default 1.5)")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds a job may run before it is marked failed (default 900)")
    args = parser.parse_args(argv)
    adapter = ADAPTERS[args.agent](timeout_s=args.timeout)
    print(f"[openamika-agents] {args.agent} bridge → {adapter.base}", file=sys.stderr, flush=True)
    build_server(adapter, settle_s=args.settle).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
