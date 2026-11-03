"""Hermes Agent (Nous Research) over its API server — the Runs endpoints.

`hermes gateway` with API_SERVER_ENABLED=true serves http://127.0.0.1:8642 (API_SERVER_KEY as bearer).
    POST /v1/runs {input, session_id, instructions}   → {run_id}
    GET  /v1/runs/{id}/events (SSE)                   → tool.started / tool.completed / message.interim /
                                                        approval.request / run.completed|failed|cancelled
    GET  /v1/runs/{id}                                → {status, output, pending_controls}
    GET  /health (no auth) · GET /health/detailed (bearer)   → used by health()
    POST /v1/runs/{id}/approval {request_id, execution_generation, choice}
    POST /v1/runs/{id}/clarify  {request_id, execution_generation, answer}
    POST /v1/runs/{id}/stop
Env: HERMES_URL, HERMES_API_KEY, HERMES_SESSION (one session = one continuing conversation),
HERMES_INSTRUCTIONS (extra system instructions sent with every run), HERMES_SURFACE_BROWSER (default 1:
after each browser step, bring Hermes's headed Chrome for Testing to the front with the content tab active),
HERMES_PROVIDER / HERMES_MODEL (what Hermes is configured to call, e.g. openrouter / z-ai/glm-5: Hermes reports
token totals per run but no cost, so the brain prices them under this model in her ledger).

Hermes binds every API session to the Hermes home directory that created it, so a home copied from
another machine keeps sessions this install may not use (POST /v1/runs → 409 profile_mismatch). The
first such refusal makes the bridge continue in a session of this install's own
(HERMES_SESSION + "-" + hostname) and say so; any other HTTP refusal is remembered for a while and
reported by `health` (reason "refused"), so the Tools page and her capability block learn that the
agent is up but not taking tasks instead of staying green.
"""
from __future__ import annotations

import glob
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time

import httpx

from ..jobs import Job, spawn
from ..sse import iter_sse
from .base import Adapter, first_text, probe

TERMINAL_STATUS = {"completed": "done", "failed": "failed", "cancelled": "stopped",
                   "interrupted": "failed", "unknown": "failed"}
REFUSAL_TTL_S = 600.0       # how long `health` keeps reporting a task Hermes refused (a success clears it sooner)


class HermesHTTPError(RuntimeError):
    """Hermes answered an HTTP error. `status` and Hermes's own `code` (e.g. profile_mismatch) are kept so the
    bridge can react and the brain can tell the person what happened instead of a bare status line."""

    def __init__(self, method: str, path: str, status: int, code: str | None, message: str):
        self.method, self.path, self.status, self.code, self.message = method, path, status, code, message
        what = f"HTTP {status}" + (f" {code}" if code and code != message else "")
        super().__init__(f"Hermes refused {method} {path}: {what}" + (f" — {message}" if message else ""))


def _checked(response: httpx.Response, method: str, path: str) -> dict:
    """The JSON body of a 2xx reply ({} when empty); any other status becomes HermesHTTPError."""
    if response.status_code >= 400:
        code, message = None, response.text[:200]
        try:
            doc = response.json()
            err = doc.get("error") if isinstance(doc, dict) else None
            if isinstance(err, dict):
                code = str(err.get("code") or "")[:60] or None
                message = str(err.get("message") or code or "")[:200]
            elif isinstance(err, str):
                code, message = err[:60], err[:200]
        except ValueError:
            pass
        raise HermesHTTPError(method, path, response.status_code, code, message)
    return response.json() if response.content else {}


def _preview(doc: dict) -> str:
    value = doc.get("preview")
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return (str(value) if value else "")[:200]


def looks_like_question(text: str) -> bool:
    """A final report whose last line ends in a question mark is the agent asking the person
    something (its instructions tell it to ask one clear question and stop). Such a job stays open
    as a pending question; the answer continues the same session."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return bool(lines) and lines[-1].endswith(("?", "？"))


# ── surfacing the headed browser ──
# Hermes drives "Chrome for Testing" through agent-browser, launched by a background service: the window
# comes up behind whatever the person has open, its first tab is about:blank, and the page the agent
# navigates to is not necessarily the active tab — so the person "sees nothing happen". After each
# browser step we activate the content tab (DevTools /json/activate) and raise the window (Win32).
_BROWSER_TOOL_RE = re.compile(r"browser", re.I)
_CFT_TITLE_SUFFIX = " - Google Chrome for Testing"
_surface_lock = threading.Lock()
_surface_last = 0.0


def _devtools_port(temp_dir: str | None = None):
    """Port of the newest agent-browser Chrome (it writes <user-data-dir>/DevToolsActivePort)."""
    base = temp_dir or tempfile.gettempdir()
    files = glob.glob(os.path.join(base, "agent-browser-chrome-*", "DevToolsActivePort"))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for path in files:
        try:
            port = int(open(path, encoding="utf-8").readline().strip())
            if port > 0:
                return port
        except (OSError, ValueError):
            continue
    return None


def _pick_content_target(targets, hint: str | None = None):
    """The page to show: an http(s) page (never about:blank or devtools), preferring one whose URL
    contains the hint (the URL the agent just navigated to)."""
    pages = [t for t in (targets or []) if isinstance(t, dict) and t.get("type") == "page"
             and str(t.get("url") or "").startswith(("http://", "https://"))]
    if not pages:
        return None
    if hint:
        for t in pages:
            if hint in str(t.get("url") or ""):
                return t
    return pages[0]


def _raise_windows_titled(suffix: str) -> int:
    """Win32: bring every visible top-level window whose title ends with `suffix` to the front.
    A window a background process created cannot simply take the foreground; a minimize/restore
    pair gets it there. Returns how many windows were raised (0 off Windows)."""
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    found = []

    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.endswith(suffix):
                    found.append(hwnd)
        return True

    user32.EnumWindows(proc_type(visit), 0)
    for hwnd in found:
        if user32.GetForegroundWindow() == hwnd:
            continue
        if not user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 6)      # SW_MINIMIZE
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE (restoring brings it to the front)
        user32.SetForegroundWindow(hwnd)
    return len(found)


def surface_browser(hint: str | None = None, min_gap_s: float = 3.0) -> bool:
    """Best effort, rate limited, never raises: activate Hermes's content tab and raise its window."""
    global _surface_last
    with _surface_lock:
        now = time.time()
        if now - _surface_last < min_gap_s:
            return False
        _surface_last = now
    try:
        port = _devtools_port()
        if port:
            targets = httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=2.0).json()
            pick = _pick_content_target(targets, hint)
            if pick and pick.get("id"):
                httpx.get(f"http://127.0.0.1:{port}/json/activate/{pick['id']}", timeout=2.0)
        _raise_windows_titled(_CFT_TITLE_SUFFIX)
        return True
    except Exception:
        return False


def _url_hint(doc: dict) -> str | None:
    preview = doc.get("preview")
    text = json.dumps(preview, ensure_ascii=False) if isinstance(preview, (dict, list)) else str(preview or "")
    match = re.search(r"https?://[^\s\"'}\]]+", text)
    return match.group(0)[:200] if match else None


class HermesAdapter(Adapter):
    name = "hermes"

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 session_id: str | None = None, instructions: str | None = None,
                 timeout_s: float = 900.0, poll_s: float = 2.0, client: httpx.Client | None = None):
        self.base = (base_url or os.environ.get("HERMES_URL") or "http://127.0.0.1:8642").rstrip("/")
        self.key = api_key if api_key is not None else os.environ.get("HERMES_API_KEY", "")
        self.session_id = session_id or os.environ.get("HERMES_SESSION") or "openamika"
        self.instructions = (instructions if instructions is not None
                             else os.environ.get("HERMES_INSTRUCTIONS", ""))
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.client = client or httpx.Client(timeout=httpx.Timeout(30.0, read=90.0))
        self.surface_browser = os.environ.get("HERMES_SURFACE_BROWSER", "1").strip().lower() not in ("0", "false", "no", "off")
        self.provider = os.environ.get("HERMES_PROVIDER", "openrouter").strip() or "openrouter"
        self.model = os.environ.get("HERMES_MODEL", "").strip()
        self.configured_session = self.session_id   # what the config asked for; session_id may move off it (see _launch)
        self.session_note: str | None = None        # why it moved, reported by health() as "note"
        self.last_refusal: dict | None = None       # the last HTTP refusal of a task: {"at", "status", "code", "detail"}
        self._fallback_tried = False

    def _account(self, job: Job, run_doc: dict) -> None:
        """Fold a run's token usage into the job (Hermes reports per-run totals, no cost), so the brain can
        book it in her ledger under the model Hermes is configured with."""
        usage = run_doc.get("usage") if isinstance(run_doc, dict) and isinstance(run_doc.get("usage"), dict) else None
        if not usage:
            return
        acc = job.handle.setdefault("usage", {"provider": self.provider, "model": self.model,
                                              "input_tokens": 0, "output_tokens": 0, "runs": 0})
        acc["input_tokens"] += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        acc["output_tokens"] += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        acc["runs"] += 1

    def _maybe_surface(self, doc: dict) -> None:
        if self.surface_browser:
            hint = _url_hint(doc)
            threading.Thread(target=surface_browser, args=(hint,), daemon=True, name="hermes-surface").start()

    # ── http ──
    def _headers(self, **extra) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        headers.update(extra)
        return headers

    def _post(self, path: str, body: dict) -> dict:
        response = self.client.post(self.base + path, json=body, headers=self._headers())
        return _checked(response, "POST", path)

    def _get(self, path: str) -> dict:
        response = self.client.get(self.base + path, headers=self._headers())
        return _checked(response, "GET", path)

    # ── health ──
    def health(self) -> dict:
        """GET /health says whether `hermes gateway` is up at all (no auth); GET /health/detailed with our
        bearer key says whether the key is accepted. Anything else the authed probe answers still means
        the agent is up."""
        base = {"url": self.base, "version": None}
        reason, got = probe(self.client, f"{self.base}/health")
        if reason != "ok":
            hint = " — is `hermes gateway` running?" if reason in ("unreachable", "timeout") else ""
            return {"ok": False, "reason": reason, "detail": f"Hermes: {got}{hint}", **base}
        try:
            base["version"] = (got.json() or {}).get("version")
        except Exception:
            pass
        reason, _ = probe(self.client, f"{self.base}/health/detailed", self._headers())
        if reason == "unauthorized":
            return {"ok": False, "reason": reason, **base,
                    "detail": "Hermes is running but rejects the API key "
                              "(HERMES_API_KEY must equal API_SERVER_KEY in hermes/.env)"}
        label = f"Hermes {base['version']}" if base["version"] else "Hermes"
        refusal = self.last_refusal
        if refusal and time.time() - float(refusal.get("at") or 0) < REFUSAL_TTL_S:
            # up and authenticated, yet it would not take the last task: not green
            return {"ok": False, "reason": "refused", **base,
                    "detail": f"{label} is running but refused the last task ({refusal['detail']}); "
                              f"check the Hermes gateway and HERMES_SESSION — a task it accepts clears this"}
        doc = {"ok": True, "reason": "ok", "detail": f"{label} online at {self.base}", **base}
        if self.session_note:
            doc["note"] = self.session_note
        return doc

    # ── lifecycle ──
    def run(self, job: Job) -> None:
        text = job.task
        if job.context:
            text = f"{job.task}\n\n(Context from the person asking: {job.context})"
        self._launch(job, text)

    def _fallback_session_id(self) -> str:
        host = re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-")[:24] or "local"
        return f"{self.configured_session}-{host}"

    def _launch(self, job: Job, text: str) -> None:
        body = {"input": text, "session_id": self.session_id}
        if self.instructions:
            body["instructions"] = self.instructions
        try:
            try:
                doc = self._post("/v1/runs", body)
            except HermesHTTPError as error:
                if not (error.status == 409 and error.code == "profile_mismatch" and not self._fallback_tried):
                    raise
                # Hermes binds an API session to the home directory that created it; this one came from
                # another install (a copied/moved home). Continue in a session of this install's own.
                self._fallback_tried = True
                fallback = self._fallback_session_id()
                self.session_note = (f"Hermes refused the configured session '{self.configured_session}' "
                                     f"(profile_mismatch: it was created by another Hermes install); "
                                     f"using session '{fallback}' on this install instead")
                print(f"[openamika-agents] hermes: {self.session_note}", file=sys.stderr, flush=True)
                job.add("progress", self.session_note)
                self.session_id = body["session_id"] = fallback
                doc = self._post("/v1/runs", body)
        except HermesHTTPError as error:
            self.last_refusal = {"at": time.time(), "status": error.status, "code": error.code,
                                 "detail": str(error)[:300]}
            raise
        self.last_refusal = None
        run_id = doc.get("run_id") or doc.get("id")
        if not run_id:
            raise RuntimeError(f"Hermes returned no run_id: {str(doc)[:200]}")
        job.handle["run_id"] = run_id
        job.add("progress", "started", run_id=run_id)
        self._follow(job, run_id)

    def _follow(self, job: Job, run_id: str) -> None:
        deadline = time.time() + self.timeout_s
        terminal = False
        try:
            with self.client.stream("GET", f"{self.base}/v1/runs/{run_id}/events",
                                    headers=self._headers(Accept="text/event-stream"),
                                    timeout=httpx.Timeout(30.0, read=None)) as response:
                response.raise_for_status()
                for name, data in iter_sse(response.iter_lines()):
                    if self._event(job, run_id, name, data):
                        terminal = True
                        break
                    if time.time() > deadline or job.handle.get("run_id") != run_id:
                        break
        except Exception as error:
            job.add("progress", f"event stream closed: {str(error)[:120]}")
        if not terminal and job.handle.get("run_id") == run_id:
            self._poll(job, run_id, deadline)

    def _event(self, job: Job, run_id: str, name: str | None, data: str) -> bool:
        try:
            doc = json.loads(data) if data else {}
        except ValueError:
            doc = {"text": data}
        if not isinstance(doc, dict):
            doc = {"value": doc}
        name = (name or doc.get("type") or doc.get("event") or "").strip()
        if name == "tool.started":
            job.add("progress", f"{doc.get('tool') or 'tool'}: {_preview(doc)}".strip(": "), tool=doc.get("tool"))
        elif name in ("tool.completed", "tool.failed"):
            flag = "error" if (doc.get("error") or name == "tool.failed") else "ok"
            job.add("progress", f"{doc.get('tool') or 'tool'} → {flag}: {_preview(doc)}".rstrip(": "), tool=doc.get("tool"))
            if _BROWSER_TOOL_RE.search(str(doc.get("tool") or "")):
                self._maybe_surface(doc)        # the page just changed: show it
        elif name in ("message.interim", "assistant.commentary"):
            text = first_text(doc, "text", "content")
            if text:
                job.add("text", text)
        elif name == "approval.request":
            self._park(job, run_id, doc, "approval")
        elif "clarif" in name:
            self._park(job, run_id, doc, "question")
        elif name == "run.completed":
            run_doc = self._safe_run(run_id)
            self._account(job, run_doc)
            output = first_text(doc, "output", "result", "text", "content") or first_text(run_doc, "output")
            self._finish(job, run_id, output)
            return True
        elif name in ("run.failed", "run.interrupted"):
            self._account(job, self._safe_run(run_id))
            job.set_state("failed", error=first_text(doc, "error", "message", "detail", default=name))
            return True
        elif name == "run.cancelled":
            self._account(job, self._safe_run(run_id))
            job.set_state("stopped")
            return True
        return False

    def _safe_run(self, run_id: str) -> dict:
        try:
            return self._get(f"/v1/runs/{run_id}")
        except Exception:
            return {}

    def _finish(self, job: Job, run_id: str, output: str) -> None:
        """A completed run either ends the job or, when the agent's last word is a question for
        the person, parks it as a pending question whose answer continues the session."""
        output = output or "(no output)"
        if any(_BROWSER_TOOL_RE.search(str(e.get("tool") or "")) for e in job.events):
            self._maybe_surface({})             # leave the last page in front while she reports
        if looks_like_question(output):
            job.result = None
            job.set_pending("question", f"final:{run_id}", output, continuation=True)
            return
        job.set_state("done", result=output)

    def _park(self, job: Job, run_id: str, doc: dict, kind: str) -> None:
        """Park the job on a pending approval/clarification; identity comes from pending_controls
        when the server exposes them, else from the event itself."""
        control = {}
        run_doc = self._safe_run(run_id)
        controls = run_doc.get("pending_controls")
        if isinstance(controls, dict):
            controls = [controls]
        if isinstance(controls, list) and controls:
            control = next((c for c in controls if isinstance(c, dict)), {})
        ident = (first_text(control, "prompt_id", "request_id", "id")
                 or first_text(doc, "request_id", "prompt_id", "id") or run_id)
        generation = control.get("execution_generation", doc.get("execution_generation"))
        text = (first_text(control, "text", "prompt", "message", "preview", "summary")
                or first_text(doc, "text", "prompt", "message", "preview", "summary", "tool")
                or ("needs your approval" if kind == "approval" else "has a question"))
        job.set_pending(kind, ident, text, generation=generation, tool=doc.get("tool"))

    def _poll(self, job: Job, run_id: str, deadline: float) -> None:
        while time.time() < deadline and job.handle.get("run_id") == run_id:
            doc = self._safe_run(run_id)
            status = str(doc.get("status") or "")
            if status in TERMINAL_STATUS:
                state = TERMINAL_STATUS[status]
                self._account(job, doc)
                if state == "done":
                    self._finish(job, run_id, first_text(doc, "output", "result"))
                elif state == "failed":
                    job.set_state("failed", error=first_text(doc, "error", "message", default=status))
                else:
                    job.set_state("stopped")
                return
            if status == "waiting_for_approval" and job.pending is None:
                self._park(job, run_id, doc, "approval")
            time.sleep(self.poll_s)
        if not job.terminal:
            job.set_state("failed", error="timed out waiting for Hermes")

    # ── control ──
    def answer(self, job: Job, text: str = "", approval_id: str = "", approved: bool = True) -> str:
        run_id = job.handle.get("run_id")
        pending = job.pending
        if pending and (pending.get("kind") == "approval" or approval_id):
            body = {"request_id": approval_id or pending.get("id"), "choice": "once" if approved else "deny"}
            if pending.get("generation") is not None:
                body["execution_generation"] = pending["generation"]
            self._post(f"/v1/runs/{run_id}/approval", body)
            job.clear_pending("approval granted" if approved else "approval denied")
            return "ok"
        if pending and pending.get("kind") == "question":
            if not text:
                raise ValueError("the agent asked a question; give it an answer text")
            if pending.get("continuation"):
                # the question was the run's final message: answering starts the next run in the
                # same session, so the agent keeps its context
                job.clear_pending("answered")
                job.result, job.error = None, None
                spawn(lambda j: self._launch(j, text), job)
                return "continued"
            body = {"request_id": pending.get("id"), "answer": text}
            if pending.get("generation") is not None:
                body["execution_generation"] = pending["generation"]
            self._post(f"/v1/runs/{run_id}/clarify", body)
            job.clear_pending("answered")
            return "ok"
        if not text:
            raise ValueError("nothing is pending on this job; give text to continue the conversation")
        if not job.terminal:
            raise RuntimeError("the job is still running; wait for it or stop it first")
        job.result, job.error = None, None
        job.set_state("running")
        job.add("progress", "continuing")
        spawn(lambda j: self._launch(j, text), job)
        return "continued"

    def stop(self, job: Job) -> str:
        run_id = job.handle.get("run_id")
        if run_id and not job.terminal:
            try:
                self._post(f"/v1/runs/{run_id}/stop", {})
            except Exception as error:
                job.add("progress", f"stop failed: {str(error)[:120]}")
                raise
            job.add("progress", "stop requested")
            return "stopping"
        return job.state
