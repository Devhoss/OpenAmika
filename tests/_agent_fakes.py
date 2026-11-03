"""In-process fakes of the two agent APIs the bridge speaks to (Hermes Runs API, nanoMuse server API).

They are small threading HTTP servers that follow the documented request/response shapes so the
adapters are exercised end-to-end over real HTTP, without either agent installed.
Scenarios are chosen by words in the task text:
  "APPROVE" → the agent parks on an approval before finishing
  "ASK"     → the agent asks a question (nanoMuse) before finishing
  "SLOW"    → the agent keeps working for a few seconds (for stop tests)
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "test-key"


class _Base(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _auth_ok(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {KEY}"

    def _json(self, status: int, doc) -> None:
        body = json.dumps(doc).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")      # no keep-alive: httpx must not reuse a socket this server closes
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        cached = getattr(self, "_cached_body", None)
        if cached is not None:
            return cached
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            doc = json.loads(raw or b"{}")
        except ValueError:
            doc = {}
        self._cached_body = doc if isinstance(doc, dict) else {}
        return self._cached_body


# ───────────────────────────── Hermes ─────────────────────────────

class FakeHermes:
    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()

    def _emit(self, run: dict, name: str, data: dict):
        with self.lock:
            run["events"].append((name, data))

    def _work(self, run_id: str):
        run = self.runs[run_id]
        text = run["input"]
        time.sleep(0.1)
        self._emit(run, "tool.started", {"tool": "terminal", "preview": "echo hi"})
        if "SLOW" in text:
            for _ in range(30):
                if run["status"] == "cancelled":
                    self._emit(run, "run.cancelled", {})
                    return
                time.sleep(0.1)
        if "APPROVE" in text:
            run["status"] = "waiting_for_approval"
            run["pending_controls"] = [{"prompt_id": "p1", "execution_generation": 1, "kind": "approval",
                                        "text": "delete old-notes.txt?"}]
            self._emit(run, "approval.request", {"request_id": "p1", "tool": "terminal"})
            if not run["decided"].wait(5):
                run["status"] = "failed"
                self._emit(run, "run.failed", {"error": "approval timed out"})
                return
            run["pending_controls"] = []
            if run["choice"] != "once":
                run["status"] = "failed"
                self._emit(run, "run.failed", {"error": "BLOCKED: denied"})
                return
        self._emit(run, "tool.completed", {"tool": "terminal", "preview": "hi", "error": False})
        if "ASKCITY" in text:
            run["output"] = "I opened the map, but I need to know where you are.\n\nWhich city are you in?"
        elif text.startswith("Kyoto"):
            run["output"] = "Golden Dragon, Kyoto — 5 minutes away, open till 22:00."
        else:
            run["output"] = "approved-done" if "APPROVE" in text else "The answer is 4"
        run["status"] = "completed"
        self._emit(run, "run.completed", {"output": run["output"]})

    def _handler(self):
        fake = self

        class Handler(_Base):
            def do_POST(self):
                self._body()                       # consume the request first, or a 401 races the client's write
                if not self._auth_ok():
                    return self._json(401, {"error": "unauthorized"})
                parts = self.path.split("/")
                if self.path == "/v1/runs":
                    body = self._body()
                    run_id = "run_" + uuid.uuid4().hex[:6]
                    fake.runs[run_id] = {"input": body.get("input", ""), "session_id": body.get("session_id"),
                                         "status": "running", "output": None, "events": [],
                                         "pending_controls": [], "decided": threading.Event(), "choice": None,
                                         "stopped": False, "instructions": body.get("instructions")}
                    threading.Thread(target=fake._work, args=(run_id,), daemon=True).start()
                    return self._json(200, {"run_id": run_id, "status": "started"})
                if len(parts) == 5 and parts[1] == "v1" and parts[2] == "runs":
                    run = fake.runs.get(parts[3])
                    if run is None:
                        return self._json(404, {"error": "no such run"})
                    if parts[4] == "approval":
                        body = self._body()
                        if body.get("request_id") != "p1" or body.get("execution_generation") != 1:
                            return self._json(409, {"error": "stale"})
                        run["choice"] = body.get("choice")
                        run["status"] = "running"
                        run["decided"].set()
                        return self._json(200, {"ok": True})
                    if parts[4] == "stop":
                        run["status"] = "cancelled"
                        return self._json(200, {"status": "stopping"})
                return self._json(404, {"error": "unknown"})

            def do_GET(self):
                if self.path == "/health":          # no auth, like the real one
                    return self._json(200, {"status": "ok", "platform": "hermes-agent", "version": "fake"})
                if not self._auth_ok():
                    return self._json(401, {"error": "unauthorized"})
                if self.path == "/health/detailed":
                    return self._json(200, {"gateway_running": True})
                parts = self.path.split("?")[0].split("/")
                if len(parts) >= 4 and parts[1] == "v1" and parts[2] == "runs":
                    run = fake.runs.get(parts[3])
                    if run is None:
                        return self._json(404, {"error": "no such run"})
                    if len(parts) == 4:
                        return self._json(200, {"object": "hermes.run", "run_id": parts[3], "status": run["status"],
                                                "output": run["output"], "pending_controls": run["pending_controls"],
                                                "usage": {"input_tokens": 1200, "output_tokens": 34, "total_tokens": 1234}})
                    if parts[4] == "events":
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.end_headers()
                        sent = 0
                        deadline = time.time() + 20
                        while time.time() < deadline:
                            with fake.lock:
                                pending = run["events"][sent:]
                            for name, data in pending:
                                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode("utf-8"))
                                self.wfile.flush()
                                sent += 1
                                if name.startswith("run."):
                                    return
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            time.sleep(0.05)
                        return
                return self._json(404, {"error": "unknown"})

        return Handler


# ───────────────────────────── nanoMuse ─────────────────────────────

class FakeNanomuse:
    def __init__(self):
        self.threads: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()

    def _meta(self, thread: dict) -> dict:
        return {"id": thread["id"], "title": thread["title"], "busy": thread["busy"], "queued": 0,
                "events": len(thread["events"])}

    def _add(self, thread: dict, event: dict) -> dict:
        event.setdefault("id", "e" + uuid.uuid4().hex[:6])
        with self.lock:
            thread["events"].append(event)
        return event

    def _work(self, thread: dict, text: str):
        time.sleep(0.1)
        self._add(thread, {"type": "tool", "name": "browser", "summary": "open example.com"})
        if "SLOW" in text:
            for _ in range(30):
                if not thread["busy"]:
                    return
                time.sleep(0.1)
        if "APPROVE" in text:
            card = self._add(thread, {"type": "approval", "tool": "shell", "summary": "run rm old-notes.txt",
                                      "risk": "sensitive", "status": "pending", "grant_options": ["once"]})
            thread["approval"] = card
            if not thread["decided"].wait(5):
                card["status"] = "expired"
                thread["busy"] = False
                return
            if not thread["approved"]:
                self._add(thread, {"type": "assistant", "text": "Okay, I won't."})
                thread["busy"] = False
                return
        if "ASK" in text:
            question = self._add(thread, {"type": "question", "text": "Which city are you in?", "status": "pending"})
            thread["question"] = question
            if not thread["answered"].wait(5):
                question["status"] = "expired"
                thread["busy"] = False
                return
        self._add(thread, {"type": "assistant", "text": "approved-done" if "APPROVE" in text else
                           ("Got it: " + thread.get("answer", "") if "ASK" in text else "The answer is 4")})
        thread["busy"] = False

    def _handler(self):
        fake = self

        class Handler(_Base):
            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/api/health":           # no auth, like the real one
                    return self._json(200, {"ok": True, "version": "fake", "auth": True})
                if not self._auth_ok():
                    return self._json(401, {"error": "unauthorized"})
                parts = path.split("/")
                if path == "/api/threads":
                    return self._json(200, {"threads": [fake._meta(t) for t in fake.threads.values()]})
                if len(parts) == 5 and parts[2] == "threads" and parts[4] == "events":
                    thread = fake.threads.get(parts[3])
                    if thread is None:
                        return self._json(404, {"error": "no thread"})
                    with fake.lock:
                        events = [dict(e) for e in thread["events"]]
                    return self._json(200, {"thread": fake._meta(thread), "events": events, "has_more": False})
                return self._json(404, {"error": "unknown"})

            def do_POST(self):
                self._body()                       # consume the request first, or a 401 races the client's write
                if not self._auth_ok():
                    return self._json(401, {"error": "unauthorized"})
                parts = self.path.split("/")
                if self.path == "/api/threads":
                    body = self._body()
                    ident = "t" + uuid.uuid4().hex[:6]
                    fake.threads[ident] = {"id": ident, "title": body.get("title") or "untitled", "busy": False,
                                           "events": [], "decided": threading.Event(), "approved": None,
                                           "answered": threading.Event(), "answer": ""}
                    return self._json(200, fake._meta(fake.threads[ident]))
                if len(parts) == 5 and parts[2] == "threads":
                    thread = fake.threads.get(parts[3])
                    if thread is None:
                        return self._json(404, {"error": "no thread"})
                    if parts[4] == "send":
                        body = self._body()
                        text = body.get("text") or ""
                        if thread.get("question") and thread["question"].get("status") == "pending":
                            thread["question"]["status"] = "answered"
                            thread["answer"] = text
                            thread["answered"].set()
                            return self._json(200, {"event": {"id": "ans", "type": "user", "text": text},
                                                    "thread": fake._meta(thread)})
                        event = fake._add(thread, {"type": "user", "text": text})
                        thread["busy"] = True
                        threading.Thread(target=fake._work, args=(thread, text), daemon=True).start()
                        return self._json(200, {"event": event, "thread": fake._meta(thread)})
                    if parts[4] == "stop":
                        thread["busy"] = False
                        fake._add(thread, {"type": "notice", "level": "info", "text": "stopped"})
                        return self._json(200, {"ok": True})
                if len(parts) == 4 and parts[2] == "approvals":
                    body = self._body()
                    for thread in fake.threads.values():
                        card = thread.get("approval")
                        if card and card["id"] == parts[3] and card.get("status") == "pending":
                            card["status"] = "approved" if body.get("approved") else "denied"
                            thread["approved"] = bool(body.get("approved"))
                            thread["decided"].set()
                            return self._json(200, {"ok": True})
                    return self._json(404, {"error": "no pending approval with that id"})
                return self._json(404, {"error": "unknown"})

        return Handler
