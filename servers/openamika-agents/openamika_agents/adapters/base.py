"""Adapter contract. An adapter drives one agent's own API and translates it into job events."""
from __future__ import annotations

import httpx

from ..jobs import Job


class Adapter:
    name = "agent"

    def run(self, job: Job) -> None:
        """Blocking: start the task and follow it until the job reaches a terminal state
        (or parks it as waiting_* and keeps following). Runs in a worker thread."""
        raise NotImplementedError

    def answer(self, job: Job, text: str = "", approval_id: str = "", approved: bool = True) -> str:
        """Resolve a pending approval/question, or continue the conversation with `text`."""
        raise NotImplementedError

    def stop(self, job: Job) -> str:
        raise NotImplementedError

    def health(self) -> dict:
        """Is the agent itself up and talking to us? {"ok": bool, "reason": str, "detail": str, "url": str,
        "version": str|None}; reason is ok / unreachable / timeout / unauthorized / http_error / error
        (adapters may add their own: nanoMuse's "starting" and "no_model", Hermes's "refused"). Quick (a few seconds
        at most), never raises."""
        raise NotImplementedError


def probe(client: httpx.Client, url: str, headers: dict | None = None, timeout_s: float = 3.0):
    """One GET for a health probe: ("ok", response) or (reason, one-line detail). Never raises."""
    try:
        response = client.get(url, headers=headers or {}, timeout=httpx.Timeout(timeout_s))
    except httpx.ConnectError as error:
        return "unreachable", f"nothing answers at {url} ({str(error)[:80]})"
    except httpx.TimeoutException:
        return "timeout", f"no reply from {url} within {timeout_s:g}s"
    except Exception as error:
        return "error", f"{type(error).__name__}: {str(error)[:120]}"
    if response.status_code in (401, 403):
        return "unauthorized", f"{url} rejected our credentials (HTTP {response.status_code})"
    if response.status_code >= 400:
        return "http_error", f"{url} answered HTTP {response.status_code}"
    return "ok", response


def first_text(doc: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = first_text(value, *keys)
            if nested:
                return nested
    return default
