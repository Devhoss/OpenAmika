"""servers/openamika-agents — adapters against fake Hermes / nanoMuse servers, plus one real MCP
stdio round trip through `python -m openamika_agents hermes`."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "servers" / "openamika-agents"
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from _agent_fakes import KEY, FakeHermes, FakeNanomuse  # noqa: E402
from openamika_agents.adapters import HermesAdapter, NanomuseAdapter  # noqa: E402
from openamika_agents.jobs import Job, JobStore  # noqa: E402
from openamika_agents.sse import iter_sse  # noqa: E402


def wait_for(predicate, timeout=8.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


@pytest.fixture(scope="module")
def hermes():
    fake = FakeHermes()
    yield fake
    fake.close()


@pytest.fixture(scope="module")
def nanomuse():
    fake = FakeNanomuse()
    yield fake
    fake.close()


def test_sse_reader_joins_data_and_skips_comments():
    lines = [": keepalive", "event: tool.started", 'data: {"a":', 'data: 1}', "", "data: plain", ""]
    assert list(iter_sse(lines)) == [("tool.started", '{"a":\n1}'), (None, "plain")]


# ───────────────────────────── Hermes ─────────────────────────────

def test_hermes_simple_task_reports_result(hermes):
    adapter = HermesAdapter(base_url=hermes.base, api_key=KEY, session_id="t", timeout_s=20)
    store = JobStore()
    job = store.start(adapter, "What is 2+2?")
    assert wait_for(lambda: job.terminal)
    assert job.state == "done"
    assert job.result == "The answer is 4"
    kinds = [e["kind"] for e in job.events]
    assert "progress" in kinds and kinds[-1] == "done"
    assert any("terminal" in e["text"] for e in job.events if e["kind"] == "progress")
    snap = job.snapshot(cursor=0)
    assert snap["job"] == job.id and snap["cursor"] == len(job.events)
    assert job.snapshot(cursor=snap["cursor"])["events"] == []
    run = next(iter(hermes.runs.values()))
    assert run["session_id"] == "t"
    assert snap["usage"]["input_tokens"] == 1200 and snap["usage"]["output_tokens"] == 34 and snap["usage"]["runs"] == 1
    assert snap["usage"]["provider"] == "openrouter"


def test_hermes_usage_accumulates_over_continuations_and_names_the_model(hermes, monkeypatch):
    monkeypatch.setenv("HERMES_MODEL", "z-ai/glm-5")
    adapter = HermesAdapter(base_url=hermes.base, api_key=KEY, session_id="u", timeout_s=20)
    job = JobStore().start(adapter, "What is 2+2?")
    assert wait_for(lambda: job.terminal)
    first = job.handle["run_id"]
    adapter.answer(job, text="and 3+3?")
    assert wait_for(lambda: job.terminal and job.handle["run_id"] != first)
    usage = job.snapshot()["usage"]
    assert usage == {"provider": "openrouter", "model": "z-ai/glm-5", "input_tokens": 2400, "output_tokens": 68, "runs": 2}


def test_hermes_approval_relay_then_done(hermes):
    adapter = HermesAdapter(base_url=hermes.base, api_key=KEY, timeout_s=20)
    store = JobStore()
    job = store.start(adapter, "APPROVE: delete the old notes")
    assert wait_for(lambda: job.state == "waiting_approval")
    assert job.pending["kind"] == "approval" and job.pending["id"] == "p1"
    assert job.pending["generation"] == 1
    assert "old-notes" in job.pending["text"]
    assert adapter.answer(job, approved=True) == "ok"
    assert wait_for(lambda: job.terminal)
    assert job.state == "done" and job.result == "approved-done"


def test_hermes_denied_approval_fails_cleanly(hermes):
    adapter = HermesAdapter(base_url=hermes.base, api_key=KEY, timeout_s=20)
    job = JobStore().start(adapter, "APPROVE: wipe it")
    assert wait_for(lambda: job.state == "waiting_approval")
    adapter.answer(job, approved=False)
    assert wait_for(lambda: job.terminal)
    assert job.state == "failed" and "denied" in (job.error or "")


def test_hermes_stop_cancels(hermes):
    adapter = HermesAdapter(base_url=hermes.base, api_key=KEY, timeout_s=20)
    job = JobStore().start(adapter, "SLOW: count to a million")
    assert wait_for(lambda: job.handle.get("run_id"))
    assert adapter.stop(job) == "stopping"
    assert wait_for(lambda: job.terminal)
    assert job.state == "stopped"


def test_hermes_continue_after_done_starts_new_run(hermes):
    adapter = HermesAdapter(base_url=hermes.base, api_key=KEY, session_id="again", timeout_s=20)
    job = JobStore().start(adapter, "What is 2+2?")
    assert wait_for(lambda: job.terminal)
    first = job.handle["run_id"]
    assert adapter.answer(job, text="and 3+3?") == "continued"
    assert wait_for(lambda: job.terminal and job.handle["run_id"] != first)
    assert job.state == "done"
    assert hermes.runs[job.handle["run_id"]]["session_id"] == "again"


def test_hermes_final_question_keeps_the_job_open_until_answered(hermes):
    adapter = HermesAdapter(base_url=hermes.base, api_key=KEY, session_id="q", timeout_s=20)
    job = JobStore().start(adapter, "ASKCITY: find the nearest Chinese restaurant")
    assert wait_for(lambda: job.state == "waiting_question")
    assert job.pending["kind"] == "question" and job.pending["continuation"] is True
    assert job.pending["text"].endswith("Which city are you in?")
    assert job.result is None
    first = job.handle["run_id"]
    assert adapter.answer(job, text="Kyoto") == "continued"
    assert wait_for(lambda: job.terminal and job.handle["run_id"] != first)
    assert job.state == "done" and job.result.startswith("Golden Dragon")
    assert hermes.runs[job.handle["run_id"]]["session_id"] == "q"


def test_hermes_health_reports_online_and_bad_key(hermes):
    doc = HermesAdapter(base_url=hermes.base, api_key=KEY).health()
    assert doc["ok"] is True and doc["reason"] == "ok" and doc["version"] == "fake" and doc["url"] == hermes.base
    assert "online" in doc["detail"]
    bad = HermesAdapter(base_url=hermes.base, api_key="wrong").health()
    assert bad["ok"] is False and bad["reason"] == "unauthorized" and "API key" in bad["detail"]


def test_hermes_surface_helpers_pick_the_content_tab_and_find_the_devtools_port(tmp_path):
    from openamika_agents.adapters import hermes as hermes_mod
    targets = [{"type": "page", "url": "about:blank", "id": "a"},
               {"type": "page", "url": "https://www.google.com/maps/search/hot+pot", "id": "b"},
               {"type": "page", "url": "https://example.com/", "id": "c"},
               {"type": "service_worker", "url": "https://www.google.com/sw.js", "id": "d"}]
    assert hermes_mod._pick_content_target(targets)["id"] == "b"               # never the blank tab
    assert hermes_mod._pick_content_target(targets, "example.com")["id"] == "c"  # the URL just navigated to
    assert hermes_mod._pick_content_target([{"type": "page", "url": "about:blank", "id": "a"}]) is None
    assert hermes_mod._devtools_port(str(tmp_path)) is None
    old = tmp_path / "agent-browser-chrome-old"; old.mkdir()
    (old / "DevToolsActivePort").write_text("58001\n/devtools/browser/x", encoding="utf-8")
    new = tmp_path / "agent-browser-chrome-new"; new.mkdir()
    (new / "DevToolsActivePort").write_text("58099\n/devtools/browser/y", encoding="utf-8")
    os.utime(old / "DevToolsActivePort", (1, 1))
    assert hermes_mod._devtools_port(str(tmp_path)) == 58099                   # the newest browser wins
    assert hermes_mod._url_hint({"preview": {"url": "https://maps.app/x?q=1", "ok": True}}) == "https://maps.app/x?q=1"
    assert hermes_mod._url_hint({"preview": "clicked"}) is None


def test_hermes_rejects_bad_key(hermes):
    adapter = HermesAdapter(base_url=hermes.base, api_key="wrong", timeout_s=5)
    job = JobStore().start(adapter, "hello")
    assert wait_for(lambda: job.terminal)
    assert job.state == "failed" and "401" in (job.error or "")


# ───────────────────────────── nanoMuse ─────────────────────────────

def test_nanomuse_simple_task(nanomuse):
    adapter = NanomuseAdapter(base_url=nanomuse.base, token=KEY, timeout_s=20, poll_s=0.1)
    job = JobStore().start(adapter, "What is 2+2?")
    assert wait_for(lambda: job.terminal)
    assert job.state == "done" and job.result == "The answer is 4"
    assert any(e["kind"] == "progress" and "example.com" in e["text"] for e in job.events)
    # one thread, reused
    assert len(nanomuse.threads) == 1
    job2 = JobStore().start(adapter, "again?")
    assert wait_for(lambda: job2.terminal)
    assert len(nanomuse.threads) == 1


def test_nanomuse_approval_card(nanomuse):
    adapter = NanomuseAdapter(base_url=nanomuse.base, token=KEY, timeout_s=20, poll_s=0.1)
    job = JobStore().start(adapter, "APPROVE: delete the old notes")
    assert wait_for(lambda: job.state == "waiting_approval")
    assert job.pending["kind"] == "approval" and "rm old-notes" in job.pending["text"]
    assert job.pending["risk"] == "sensitive"
    assert adapter.answer(job, approved=True) == "ok"
    assert wait_for(lambda: job.terminal)
    assert job.state == "done" and job.result == "approved-done"


def test_nanomuse_question_answered_by_text(nanomuse):
    adapter = NanomuseAdapter(base_url=nanomuse.base, token=KEY, timeout_s=20, poll_s=0.1)
    job = JobStore().start(adapter, "ASK: find a restaurant")
    assert wait_for(lambda: job.state == "waiting_question")
    assert "city" in job.pending["text"]
    assert adapter.answer(job, text="Kyoto") == "ok"
    assert wait_for(lambda: job.terminal)
    assert job.state == "done" and job.result == "Got it: Kyoto"


def test_nanomuse_health_reports_online_and_bad_token(nanomuse):
    doc = NanomuseAdapter(base_url=nanomuse.base, token=KEY).health()
    assert doc["ok"] is True and doc["reason"] == "ok" and doc["version"] == "fake"
    bad = NanomuseAdapter(base_url=nanomuse.base, token="wrong").health()
    assert bad["ok"] is False and bad["reason"] == "unauthorized" and "token" in bad["detail"]


def test_health_reports_an_agent_that_is_not_running():
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()                                   # nobody listens here now
    doc = HermesAdapter(base_url=f"http://127.0.0.1:{port}", api_key=KEY).health()
    assert doc["ok"] is False and doc["reason"] == "unreachable" and "hermes gateway" in doc["detail"]
    doc = NanomuseAdapter(base_url=f"http://127.0.0.1:{port}", token=KEY).health()
    assert doc["ok"] is False and doc["reason"] == "unreachable" and "nanomuse serve" in doc["detail"]


def test_nanomuse_token_file_is_read_lazily(nanomuse, tmp_path, monkeypatch):
    token_file = tmp_path / "server_token"                 # does not exist yet: the server writes it on its first start
    monkeypatch.delenv("NANOMUSE_TOKEN", raising=False)
    monkeypatch.setenv("NANOMUSE_TOKEN_FILE", str(token_file))
    adapter = NanomuseAdapter(base_url=nanomuse.base)
    assert adapter.health()["reason"] == "unauthorized"
    token_file.write_text(KEY + "\n", encoding="utf-8")
    assert adapter.health()["ok"] is True


def test_nanomuse_stop(nanomuse):
    adapter = NanomuseAdapter(base_url=nanomuse.base, token=KEY, timeout_s=20, poll_s=0.1)
    job = JobStore().start(adapter, "SLOW: keep going")
    assert wait_for(lambda: job.handle.get("thread_id"))
    assert adapter.stop(job) == "stopped"
    assert job.state == "stopped"


# ───────────────────────────── MCP stdio round trip ─────────────────────────────

def test_mcp_stdio_roundtrip(hermes):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "HERMES_URL": hermes.base, "HERMES_API_KEY": KEY, "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "openamika_agents", "hermes", "--settle", "0.2"],
                                   env=env, cwd=str(BRIDGE))

    async def scenario():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name: t for t in (await session.list_tools()).tools}
                assert set(tools) == {"ask", "status", "answer", "stop", "health"}
                assert "hermes" in (tools["ask"].description or "")
                health = json.loads((await session.call_tool("health", {})).content[0].text)
                assert health["ok"] is True and health["agent"] == "hermes" and health["reason"] == "ok"
                result = await session.call_tool("ask", {"task": "What is 2+2?"})
                doc = json.loads(result.content[0].text)
                job = doc["job"]
                for _ in range(80):
                    status = json.loads((await session.call_tool("status", {"job": job, "cursor": 0})).content[0].text)
                    if status["state"] in ("done", "failed"):
                        break
                    await asyncio.sleep(0.1)
                assert status["state"] == "done" and status["result"] == "The answer is 4"
                listing = json.loads((await session.call_tool("status", {})).content[0].text)
                assert listing["jobs"][0]["job"] == job
                unknown = json.loads((await session.call_tool("stop", {"job": "nope"})).content[0].text)
                assert "error" in unknown

    asyncio.run(scenario())


def test_job_store_keeps_recent_terminal_jobs():
    class Instant:
        name = "x"

        def run(self, job):
            job.set_state("done", result="ok")

    store = JobStore(keep=2)
    jobs = [store.start(Instant(), f"t{i}") for i in range(3)]
    assert wait_for(lambda: all(j.terminal for j in jobs))
    assert store.get(jobs[0].id) is None and store.get(jobs[2].id) is jobs[2]
    assert Job("x", "t").snapshot()["state"] == "running"
