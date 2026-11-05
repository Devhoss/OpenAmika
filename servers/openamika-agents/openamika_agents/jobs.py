"""Job bookkeeping shared by every adapter: states, the event timeline, snapshots for `status`."""
from __future__ import annotations

import threading
import time
import uuid

TERMINAL = ("done", "failed", "stopped")
STATES = ("running", "waiting_approval", "waiting_question") + TERMINAL


class Job:
    """One delegated task. Adapters append events; the brain reads snapshots by cursor."""

    def __init__(self, agent: str, task: str, context: str = ""):
        self.id = uuid.uuid4().hex[:8]
        self.agent = agent
        self.task = task
        self.context = context
        self.state = "running"
        self.step = ""            # latest progress label (shown under the avatar)
        self.result = None        # final report text (state == done)
        self.error = None         # failure text (state == failed)
        self.events: list[dict] = []
        self.pending: dict | None = None   # {"kind": approval|question, "id", "text", ...}
        self.handle: dict = {}    # adapter-private ids (run_id, thread_id, ...)
        self.created = time.time()
        self.updated = self.created
        self.lock = threading.RLock()

    # ── timeline ──
    def add(self, kind: str, text: str = "", **extra) -> dict:
        event = {"i": 0, "ts": round(time.time(), 3), "kind": kind, "text": (text or "")[:2000]}
        event.update({k: v for k, v in extra.items() if v is not None})
        with self.lock:
            event["i"] = len(self.events) + 1
            self.events.append(event)
            self.updated = time.time()
            if kind == "progress" and text:
                self.step = text[:160]
        return event

    def note(self, text: str, **extra) -> dict:
        """A progress step of the bridge's own ("sent", "started", "stop requested", a refused session…), not something
        the agent said or did: `internal` keeps it in the transcript but off the paper doll's speech bubbles."""
        return self.add("progress", text, internal=True, **extra)

    def set_pending(self, kind: str, ident: str, text: str, **extra) -> dict:
        pending = {"kind": kind, "id": ident, "text": (text or "")[:1500]}
        pending.update({k: v for k, v in extra.items() if v is not None})
        with self.lock:
            self.pending = pending
            self.state = "waiting_approval" if kind == "approval" else "waiting_question"
            self.updated = time.time()
        self.add(kind, text, id=ident)
        return pending

    def clear_pending(self, note: str = ""):
        with self.lock:
            self.pending = None
            if not self.terminal:
                self.state = "running"
            self.updated = time.time()
        if note:
            self.note(note)

    def set_state(self, state: str, *, result=None, error=None):
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        with self.lock:
            self.state = state
            self.updated = time.time()
            if result is not None:
                self.result = result
            if error is not None:
                self.error = error
            if state in TERMINAL:
                self.pending = None
        if state in TERMINAL:
            self.add(state, self.result if state == "done" else (self.error or ""))

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def snapshot(self, cursor: int = 0, limit: int = 60) -> dict:
        with self.lock:
            events = [e for e in self.events if e["i"] > cursor][:limit]
            new_cursor = events[-1]["i"] if events else max(cursor, len(self.events))
            doc = {"job": self.id, "agent": self.agent, "task": self.task[:300], "state": self.state,
                   "step": self.step, "pending": self.pending, "events": events, "cursor": new_cursor,
                   "started": round(self.created, 3), "updated": round(self.updated, 3)}
            if self.result is not None:
                doc["result"] = self.result
            if self.error:
                doc["error"] = self.error
            if self.handle.get("usage"):
                doc["usage"] = dict(self.handle["usage"])   # the agent's own LLM spend: {provider, model, input_tokens, output_tokens, runs}
            return doc

    def brief(self) -> dict:
        with self.lock:
            return {"job": self.id, "agent": self.agent, "task": self.task[:120], "state": self.state,
                    "step": self.step, "pending": self.pending, "updated": round(self.updated, 3)}


def _guarded(fn, job: Job):
    try:
        fn(job)
    except Exception as error:  # the worker must never die silently
        if not job.terminal:
            job.set_state("failed", error=f"{type(error).__name__}: {str(error)[:400]}")


def spawn(fn, job: Job) -> threading.Thread:
    thread = threading.Thread(target=_guarded, args=(fn, job), daemon=True, name=f"job-{job.id}")
    thread.start()
    return thread


class JobStore:
    def __init__(self, keep: int = 50):
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.keep = keep
        self.lock = threading.Lock()

    def start(self, adapter, task: str, context: str = "") -> Job:
        job = Job(adapter.name, task, context)
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
            while len(self.order) > self.keep:
                old = self.order.pop(0)
                stale = self.jobs.get(old)
                if stale is not None and stale.terminal:
                    self.jobs.pop(old, None)
                else:
                    self.order.append(old)
                    break
        spawn(adapter.run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id or "")

    def all(self) -> list[Job]:
        with self.lock:
            return [self.jobs[j] for j in self.order if j in self.jobs]
