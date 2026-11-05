"""nanoMuse over the HTTP API of `nanomuse serve` (http://127.0.0.1:8787, bearer = <data_dir>/server_token).

    GET  /api/threads                       list threads   (we keep one thread titled "OpenAmika")
    POST /api/threads {title}               create one
    POST /api/threads/{id}/send {text, language}   queue a message; returns the user event
    GET  /api/threads/{id}/events?limit=    timeline: {thread: {busy, queued}, events: [{type, ...}]}
         event types: user / assistant{text} / tool / approval{status,summary,tool,risk,...} /
                      question{text,status} / notice{level,text} / artifact / browser / hands
    POST /api/approvals/{id} {approved, scope, reason}
    POST /api/threads/{id}/stop
    GET  /api/health (no auth: {ok, version, auth, starting?})   → used by health()
    GET  /api/settings {llm_ready, ...}                          → health(): is a model (its key) set up?
Env: NANOMUSE_URL, NANOMUSE_TOKEN or NANOMUSE_TOKEN_FILE, NANOMUSE_THREAD (title), NANOMUSE_LANGUAGE.

A nanoMuse without a working model key still answers every route, and a task then ends in an error notice
("The model provider refused the API key…") instead of a reply. So health() reports reason "no_model" when its
settings say llm_ready is false, and an error notice ends the job as failed with nanoMuse's own sentence (not as
"done" with no reply), so she never reports a success that did not happen. That sentence is said once, as the
failure; when nanoMuse goes on after it (a step, a reply), it becomes a passing progress step instead.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from ..jobs import Job, spawn
from .base import Adapter, first_text, probe


def _read_token() -> str:
    token = os.environ.get("NANOMUSE_TOKEN", "")
    if token:
        return token
    path = os.environ.get("NANOMUSE_TOKEN_FILE", "")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


class NanomuseAdapter(Adapter):
    name = "nanomuse"

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 thread_title: str | None = None, language: str | None = None,
                 timeout_s: float = 900.0, poll_s: float = 1.0, client: httpx.Client | None = None):
        self.base = (base_url or os.environ.get("NANOMUSE_URL") or "http://127.0.0.1:8787").rstrip("/")
        self.token = token if token is not None else _read_token()
        self._token_lazy = token is None            # re-read NANOMUSE_TOKEN(_FILE) until something is there
        self.thread_title = thread_title or os.environ.get("NANOMUSE_THREAD") or "OpenAmika"
        self.language = language or os.environ.get("NANOMUSE_LANGUAGE") or ""
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.client = client or httpx.Client(timeout=httpx.Timeout(30.0, read=60.0))
        self._thread_id: str | None = None

    # ── http ──
    def _headers(self) -> dict:
        if not self.token and self._token_lazy:
            self.token = _read_token()               # `nanomuse serve` writes <data_dir>/server_token on its first start
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _post(self, path: str, body: dict | None = None) -> dict:
        response = self.client.post(self.base + path, json=body or {}, headers=self._headers())
        response.raise_for_status()
        doc = response.json() if response.content else {}
        return doc if isinstance(doc, dict) else {"value": doc}

    def _get(self, path: str):
        response = self.client.get(self.base + path, headers=self._headers())
        response.raise_for_status()
        return response.json() if response.content else {}

    # ── thread ──
    def thread_id(self) -> str:
        if self._thread_id:
            return self._thread_id
        listing = self._get("/api/threads")
        threads = listing.get("threads") if isinstance(listing, dict) else listing
        for thread in threads or []:
            if isinstance(thread, dict) and thread.get("title") == self.thread_title and thread.get("id"):
                self._thread_id = str(thread["id"])
                return self._thread_id
        created = self._post("/api/threads", {"title": self.thread_title})
        meta = created.get("thread") if isinstance(created.get("thread"), dict) else created
        ident = meta.get("id") if isinstance(meta, dict) else None
        if not ident:
            raise RuntimeError(f"nanoMuse did not return a thread id: {str(created)[:200]}")
        self._thread_id = str(ident)
        return self._thread_id

    # ── health ──
    def health(self) -> dict:
        """GET /api/health (no auth) says whether `nanomuse serve` is up and whether it is still starting;
        when it requires a token, a GET /api/threads with ours says whether the token is accepted."""
        base = {"url": self.base, "version": None}
        reason, got = probe(self.client, f"{self.base}/api/health")
        if reason != "ok":
            hint = " — is `nanomuse serve` running?" if reason in ("unreachable", "timeout") else ""
            return {"ok": False, "reason": reason, "detail": f"nanoMuse: {got}{hint}", **base}
        doc = {}
        try:
            doc = got.json() or {}
        except Exception:
            pass
        if isinstance(doc, dict):
            base["version"] = doc.get("version")
            if doc.get("starting"):
                return {"ok": False, "reason": "starting", "detail": f"nanoMuse is still starting ({doc['starting']})", **base}
        if not isinstance(doc, dict) or doc.get("auth", True):
            reason, _ = probe(self.client, f"{self.base}/api/threads", self._headers())
            if reason == "unauthorized":
                return {"ok": False, "reason": reason, **base,
                        "detail": "nanoMuse is running but rejects the token "
                                  "(NANOMUSE_TOKEN / NANOMUSE_TOKEN_FILE should hold <data_dir>/server_token)"}
        label = f"nanoMuse {base['version']}" if base["version"] else "nanoMuse"
        if self._llm_ready() is False:
            return {"ok": False, "reason": "no_model", **base,
                    "detail": f"{label} is running but has no working model: its [llm] api_key is empty or unresolved "
                              "(store the key with `nanomuse vault set OPENROUTER_API_KEY` and use "
                              "api_key = \"{{vault:OPENROUTER_API_KEY}}\" in its config.toml; see docs/TOOLS.md)"}
        return {"ok": True, "reason": "ok", "detail": f"{label} online at {self.base}", **base}

    def _llm_ready(self):
        """nanoMuse's own answer to "can the model slot answer a request?" (GET /api/settings llm_ready): True, False,
        or None when it cannot be read (an older nanoMuse, a hiccup): then it is not held against it."""
        reason, got = probe(self.client, f"{self.base}/api/settings", self._headers())
        if reason != "ok":
            return None
        try:
            ready = (got.json() or {}).get("llm_ready")
        except Exception:
            return None
        return ready if isinstance(ready, bool) else None

    # ── lifecycle ──
    def run(self, job: Job) -> None:
        text = job.task
        if job.context:
            text = f"{job.task}\n\n（提问者补充的背景：{job.context}）"
        self._send_and_follow(job, text)

    def _send_and_follow(self, job: Job, text: str) -> None:
        tid = self.thread_id()
        body = {"text": text}
        if self.language:
            body["language"] = self.language
        try:
            sent = self._post(f"/api/threads/{tid}/send", body)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
            self._thread_id = None              # its thread was deleted (in nanoMuse's own app): find or make it again
            tid = self.thread_id()
            sent = self._post(f"/api/threads/{tid}/send", body)
        event = sent.get("event") if isinstance(sent.get("event"), dict) else {}
        job.handle.update({"thread_id": tid, "sent_id": event.get("id")})
        job.note("sent")
        self._follow(job)

    def _follow(self, job: Job) -> None:
        tid = job.handle["thread_id"]
        sent_id = job.handle.get("sent_id")
        seen: dict[str, str] = {}          # event id -> status (to notice pending → answered)
        last_text = ""
        failure = ""                       # nanoMuse's error notice, unless a reply came after it
        notice = ""                        # that notice, held: said as a step only if anything comes after it
        saw_busy = False
        started = time.time()
        deadline = started + self.timeout_s
        while time.time() < deadline and job.handle.get("thread_id") == tid:
            time.sleep(self.poll_s)
            try:
                doc = self._get(f"/api/threads/{tid}/events?limit=200")
            except Exception as error:
                job.note(f"poll failed: {str(error)[:120]}")
                continue
            meta = doc.get("thread") if isinstance(doc, dict) else {}
            events = doc.get("events") if isinstance(doc, dict) else []
            busy = bool((meta or {}).get("busy"))
            queued = int((meta or {}).get("queued") or 0)
            saw_busy = saw_busy or busy or queued > 0
            after = sent_id is None
            for event in events or []:
                if not isinstance(event, dict):
                    continue
                ident = str(event.get("id") or "")
                if not after:
                    if ident == str(sent_id):
                        after = True
                    continue
                kind = str(event.get("type") or event.get("kind") or "")
                status = str(event.get("status") or "")
                previous = seen.get(ident)
                if previous == status:
                    continue
                seen[ident] = status
                if notice:                               # it went on after its error notice: that was a passing step
                    job.add("progress", notice)
                    notice = ""
                if kind == "assistant":
                    text = first_text(event, "text", "content")
                    if text and previous is None:
                        last_text, failure = text, ""
                        job.add("text", text)
                elif kind == "tool":
                    label = first_text(event, "summary", "name", "tool", "text")
                    if label and previous is None:
                        job.add("progress", label)
                elif kind == "approval":
                    if status == "pending":
                        summary = first_text(event, "summary", "tool", "text")
                        job.set_pending("approval", ident, summary, tool=event.get("tool"),
                                        risk=event.get("risk"), args=event.get("args"))
                    elif job.pending and job.pending.get("id") == ident:
                        job.clear_pending(f"approval {status or 'closed'}")
                elif kind == "question":
                    if status == "pending":
                        job.set_pending("question", ident, first_text(event, "text"))
                    elif job.pending and job.pending.get("id") == ident:
                        job.clear_pending(f"question {status or 'closed'}")
                elif kind == "notice":
                    text = first_text(event, "text")
                    if text and previous is None:
                        if str(event.get("level") or "") == "error":     # its model could not answer (key, network…)
                            # held back: when it is the last word, it ends the job as its failure, said once
                            notice = text
                            failure = text + (" (nanoMuse's model key is missing or wrong: see docs/TOOLS.md)"
                                              if event.get("code") == "key" else "")
                        else:
                            job.add("progress", text)
            if job.terminal:
                return
            idle = (not busy) and queued == 0 and job.pending is None
            if idle and (saw_busy or time.time() - started > 3 * self.poll_s + 1):
                if failure:
                    job.set_state("failed", error=failure)
                else:
                    job.set_state("done", result=last_text or "(no reply)")
                return
        if not job.terminal:
            if notice:
                job.add("progress", notice)
            job.set_state("failed", error="timed out waiting for nanoMuse")

    # ── control ──
    def answer(self, job: Job, text: str = "", approval_id: str = "", approved: bool = True) -> str:
        pending = job.pending
        if pending and (pending.get("kind") == "approval" or approval_id):
            self._post(f"/api/approvals/{approval_id or pending.get('id')}",
                       {"approved": bool(approved), "scope": "once", "reason": ""})
            job.clear_pending("approval granted" if approved else "approval denied")
            return "ok"
        if not text:
            raise ValueError("nothing is pending on this job; give text to continue the conversation")
        tid = job.handle.get("thread_id") or self.thread_id()
        if pending and pending.get("kind") == "question":
            body = {"text": text}
            if self.language:
                body["language"] = self.language
            self._post(f"/api/threads/{tid}/send", body)
            job.clear_pending("answered")
            return "ok"
        if not job.terminal:
            raise RuntimeError("the job is still running; wait for it or stop it first")
        job.result, job.error = None, None
        job.set_state("running")
        spawn(lambda j: self._send_and_follow(j, text), job)
        return "continued"

    def stop(self, job: Job) -> str:
        tid = job.handle.get("thread_id")
        if tid and not job.terminal:
            self._post(f"/api/threads/{tid}/stop")
            job.set_state("stopped")
            return "stopped"
        return job.state
