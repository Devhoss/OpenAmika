"""Offline live-runtime tests with invented evidence and metered-callback fakes."""
import copy
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

# PromptOptimized: test this build's payload. The Build F copy pointed at
# <workspace>/build_f_work/payload (the control), which imported the control's f_memory /
# f_method into the shared test process.
ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = Path(__file__).resolve().parents[1] / "payload"
if not PAYLOAD.is_dir():
    PAYLOAD = Path(__file__).resolve().parents[1] / "brain"   # repo layout
sys.path.insert(0, str(PAYLOAD))
if not (PAYLOAD / "f_method.py").exists():
    # Development can run before root copies the untouched method implementation.
    sys.path.insert(0, str(ROOT / "memory_benchmark"))
    sys.modules["f_method"] = importlib.import_module("candidate_f.method")
import f_memory
import f_method
from f_memory import MemoryRuntime, DIMENSIONS, MemoryBusyError, MemoryResetPendingError
from f_method import CandidateF


def event(ident="e1", user="I live in Alder Bay.", day=1):
    return {"id": ident, "time": f"2026-01-{day:02d}T12:00:00Z", "user": user,
            "assistant": "Thanks for telling me.", "source_kind": "conversation"}


def fact(source, ident, value="Alder Bay", replaces=False):
    return {"id": ident, "text": "User lives in " + value, "subject": "user",
            "predicate": "lives_in", "value": value, "source_ids": [source["id"]],
            "date": source["time"][:10], "source_kind": "user", "fact_type": "user_fact",
            "status": "active", "entities": [value], "supersedes": [],
            "replaces_prior": replaces,
            "quotes": [{"source_id": source["id"], "speaker": "user", "quote": source["user"]}]}


class Callbacks:
    def __init__(self):
        self.extractions = []
        self.embeddings = []
        self.fail_extract = False
        self.fail_embed = False

    def extract(self, events, chunk_id):
        self.extractions.append((copy.deepcopy(events), chunk_id))
        if self.fail_extract:
            raise TimeoutError("uncertain extraction result")
        source = events[0]
        correction = "Correction" in source["user"]
        return [fact(source, "fact-" + chunk_id, "Birch Cove" if correction else "Alder Bay", correction)]

    def embed(self, texts, task_type):
        self.embeddings.append((list(texts), task_type))
        if self.fail_embed:
            raise TimeoutError("uncertain embedding result")
        return [[1.0] + [0.0] * (DIMENSIONS - 1) for _ in texts]


class MemoryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # resolved: when TEMP is an 8.3 short path (a long user name), the store resolves its folder to the long name
        self.folder = Path(self.temp.name).resolve()
        self.callbacks = Callbacks()

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, **kwargs):
        return MemoryRuntime(self.folder, self.callbacks.extract, self.callbacks.embed, **kwargs)

    def test_archive_immediate_idempotent_and_public_only(self):
        memory = self.runtime()
        source = event()
        source.update(deep_raw="private thought", thought="private", context_inject="operator note")
        self.assertTrue(memory.archive(source)["queued"])
        self.assertTrue(memory.archive(source)["duplicate"])
        self.assertEqual(self.callbacks.extractions, [])
        self.assertEqual(self.callbacks.embeddings, [])
        stored = CandidateF.reload(self.folder / "state.json").events[0]
        self.assertEqual(stored, event())
        self.assertNotIn("private", json.dumps(stored))
        source["user"] = "conflicting evidence"
        with self.assertRaises(ValueError):
            memory.archive(source)

    def test_flush_persistence_no_duplicate_dispatch_after_restart(self):
        memory = self.runtime()
        memory.archive(event())
        self.assertEqual(memory.flush_pending()["completed"], 1)
        self.assertEqual(len(self.callbacks.extractions), 1)
        self.assertEqual(len(self.callbacks.embeddings), 1)
        self.assertEqual(memory.status()["facts"], 1)
        restarted = self.runtime()
        self.assertEqual(restarted.flush_pending()["attempted"], 0)
        self.assertEqual(restarted.status()["facts"], 1)
        self.assertTrue(restarted.archive(event())["duplicate"])

    def test_extraction_timeout_blocked_no_automatic_retry(self):
        self.callbacks.fail_extract = True
        memory = self.runtime()
        memory.archive(event())
        self.assertEqual(memory.flush_pending()["completed"], 0)
        memory = self.runtime()
        self.callbacks.fail_extract = False
        memory.flush_pending()
        self.assertEqual(len(self.callbacks.extractions), 1)
        self.assertEqual(memory.status()["blocked"], 1)
        self.assertEqual(memory.status()["events"], 1)

    def test_embedding_timeout_preserves_extraction_response(self):
        self.callbacks.fail_embed = True
        memory = self.runtime()
        memory.archive(event())
        memory.flush_pending()
        job = json.loads(next((self.folder / "jobs").glob("*.json")).read_text())
        self.assertEqual(job["state"], "blocked")
        self.assertEqual(len(job["extraction_result"]), 1)
        self.assertEqual(len(job["facts"]), 1)
        self.runtime().flush_pending()
        self.assertEqual(len(self.callbacks.extractions), 1)
        self.assertEqual(len(self.callbacks.embeddings), 1)

    def test_query_cache_survives_restart_and_bounds_prompt(self):
        memory = self.runtime(budget_chars=1000)
        memory.archive(event())
        first = memory.retrieve("Where do I live?")
        second = self.runtime(budget_chars=1000).retrieve("Where do I live?")
        self.assertEqual(first["text"], second["text"])
        self.assertLessEqual(len(first["text"]), 1000)
        self.assertEqual(len(self.callbacks.embeddings), 1)
        self.assertEqual(self.callbacks.embeddings[0][1], "query")

    def test_failed_query_is_lexical_then_retried(self):
        """A query embedding that fails degrades memory for this process and the retrieval falls back to
        BM25 — but the failure is not held against the next run: a restart discards the leftover, and the
        next ask embeds again (2026-11-02: one restart that cut a retrieval short used to leave the brain
        'degraded' for good)."""
        self.callbacks.fail_embed = True
        memory = self.runtime()
        memory.archive(event())
        result = memory.retrieve("Alder Bay")
        self.assertIn("Alder Bay", result["text"])
        self.assertEqual(result["diagnostics"]["retrieval_mode"], "BM25")
        self.assertTrue(result["diagnostics"]["runtime_degraded"])
        self.assertEqual(memory.status()["query_embeddings_blocked"], 1)
        self.assertTrue(memory.status()["degraded"])
        # a restart: the unfinished entry is discarded, nothing is blocked before anyone asks
        restarted = self.runtime()
        self.assertEqual(restarted.status()["query_embeddings_blocked"], 0)
        self.assertFalse(restarted.status()["degraded"])
        # the embedder is back: the same query embeds again and memory is whole
        self.callbacks.fail_embed = False
        again = restarted.retrieve("Alder Bay")
        self.assertEqual(len(self.callbacks.embeddings), 2)
        self.assertFalse(again["diagnostics"]["runtime_degraded"])
        self.assertEqual(restarted.status()["query_embeddings_blocked"], 0)

    def test_concurrent_query_misses_dispatch_only_once(self):
        memory = self.runtime()
        memory.archive(event())
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(memory.retrieve, ["Alder Bay"] * 8))
        self.assertEqual(len(self.callbacks.embeddings), 1)
        self.assertEqual(len({r["text"] for r in results}), 1)

    def test_invalid_fact_is_quarantined_and_raw_evidence_remains(self):
        def bad_extract(events, chunk_id):
            record = fact(events[0], "bad-fact")
            record["quotes"][0]["quote"] = "This quote never occurred."
            return [record]
        memory = MemoryRuntime(self.folder, bad_extract, self.callbacks.embed)
        memory.archive(event())
        self.assertEqual(memory.flush_pending()["completed"], 1)
        self.assertEqual(memory.status()["facts"], 0)
        job = json.loads(next((self.folder / "jobs" / "completed").glob("*[0-9a-f].json")).read_text())
        self.assertEqual(job["quarantined"][0]["id"], "bad-fact")
        self.assertIn("Alder Bay", memory.retrieve("Alder Bay")["text"])

    def test_isolated_parser_rejections_do_not_block_original_embeddings(self):
        def extracted_with_rejections(events, chunk_id):
            return {"facts": [fact(events[0], "good-fact")], "rejected": [
                {"id": "bad-fact", "reason": "Assistant claim is not a user fact", "index": 1,
                 "record": {"text": "Rejected raw proposal must stay outside runtime metadata"}}]}
        memory = MemoryRuntime(self.folder, extracted_with_rejections, self.callbacks.embed)
        memory.archive(event())
        self.assertEqual(memory.flush_pending()["completed"], 1)
        status = memory.status()
        self.assertEqual(status["facts"], 1)
        self.assertEqual(status["quarantined_facts"], 1)
        self.assertEqual(status["blocked"], 0)
        self.assertEqual(len(self.callbacks.embeddings), 1)
        job = json.loads(next((self.folder / "jobs" / "completed").glob("*[0-9a-f].json")).read_text())
        self.assertEqual(job["quarantined"], [{"id": "bad-fact", "reason": "Assistant claim is not a user fact", "index": 1}])
        self.assertNotIn("Rejected raw proposal", json.dumps(job))
        self.assertEqual(self.runtime().status()["quarantined_facts"], 1)

    def test_all_extracted_rows_quarantined_still_embeds_original(self):
        def rejected_only(events, chunk_id):
            return {"facts": [], "rejected": [{"id": "bad", "index": 0, "reason": "bad quote"}]}
        memory = MemoryRuntime(self.folder, rejected_only, self.callbacks.embed)
        memory.archive(event())
        self.assertEqual(memory.flush_pending()["completed"], 1)
        self.assertEqual(memory.status()["quarantined_facts"], 1)
        self.assertEqual(memory.status()["facts"], 0)
        self.assertEqual(len(self.callbacks.embeddings[0][0]), 1)

    def test_correction_survives_persistence(self):
        memory = self.runtime()
        memory.archive(event())
        memory.flush_pending()
        memory.archive(event("e2", "Correction: I live in Birch Cove now, not Alder Bay.", 2))
        memory.flush_pending()
        result = self.runtime().retrieve("Alder Bay home")
        self.assertEqual(result["diagnostics"]["correction_links"], 1)
        # CONTRACT §3.7 (natural-language rendering): the superseded marker replaces the old
        # "superseded; not current" state label.
        self.assertIn("（这条后来被更正了，现在的说法见下）", result["text"])
        self.assertIn("Birch Cove", result["text"])

    def test_seed_used_once_and_empty_store_does_not_embed(self):
        memory = self.runtime()
        memory.retrieve("hello")
        self.assertEqual(self.callbacks.embeddings, [])
        seed_folder = self.folder / "seeded"
        seed_folder.mkdir()
        seed = seed_folder / "seed.json"
        CandidateF([event()], {"facts": []}).persist(seed)
        seeded = MemoryRuntime(seed_folder)
        self.assertEqual(seeded.status()["events"], 1)
        seed.write_text("invalid seed after existing state")
        self.assertEqual(MemoryRuntime(seed_folder).status()["events"], 1)

    def test_restart_recovers_raw_journal_and_blocks_uncertain_stage(self):
        memory = self.runtime()
        memory.archive(event())
        path = next((self.folder / "jobs").glob("*.json"))
        job = json.loads(path.read_text())
        job["state"] = "extracting"
        path.write_text(json.dumps(job))
        CandidateF([], {"facts": []}).persist(self.folder / "state.json")
        restarted = self.runtime()
        self.assertEqual(restarted.status()["events"], 1)
        self.assertEqual(restarted.status()["blocked"], 1)
        restarted.flush_pending()
        self.assertEqual(self.callbacks.extractions, [])

    def test_flush_is_bounded_and_close_has_no_paid_side_effect(self):
        memory = self.runtime(max_jobs_per_flush=1)
        memory.archive(event())
        memory.archive(event("e2", day=2))
        self.assertEqual(memory.flush_pending()["completed"], 1)
        self.assertEqual(memory.status()["pending"], 1)
        memory.close()
        self.assertTrue(memory.flush_pending()["closed"])
        self.assertEqual(len(self.callbacks.extractions), 1)

    def test_received_results_resume_without_repeating_paid_stage(self):
        memory = self.runtime()
        memory.archive(event())
        path = next((self.folder / "jobs").glob("*.json"))
        job = json.loads(path.read_text())
        job.update(state="extraction_received", extraction_result=[fact(event(), "recovered-fact")])
        path.write_text(json.dumps(job))
        memory = self.runtime()
        self.assertEqual(memory.flush_pending()["completed"], 1)
        self.assertEqual(self.callbacks.extractions, [])
        self.assertEqual(len(self.callbacks.embeddings), 1)
        # Simulate the final atomic snapshot succeeding before the job's final
        # checkpoint: repeated local ingest must not duplicate facts or APIs.
        done = self.folder / "jobs" / "completed" / path.name          # finished files are archived there
        job = json.loads(done.read_text())
        job["state"] = "embedding_received"
        path.write_text(json.dumps(job))
        done.unlink()
        memory = self.runtime()
        self.assertEqual(memory.flush_pending()["completed"], 1)
        self.assertEqual(memory.status()["facts"], 1)
        self.assertEqual(len(self.callbacks.embeddings), 1)

    def test_operator_interruption_empty_and_invalid_date_rejected(self):
        memory = self.runtime()
        for changes in ({"source_kind": "operator_cue"}, {"interrupted": True},
                        {"user": "", "assistant": ""}, {"time": "not a date"}):
            source = event()
            source.update(changes)
            with self.assertRaises(ValueError):
                memory.archive(source)
        self.assertEqual(memory.status()["events"], 0)

    def wipe_runtime(self):
        active = self.folder / "memory_f" / "live"
        seed = self.folder / "seed.json"
        original = event("seed-event")
        CandidateF([original], {"facts": [fact(original, "seed-fact")]},
                   {"documents": {"seed-event": [1.0] + [0.0] * (DIMENSIONS - 1)}, "queries": {}}).persist(seed)
        memory = MemoryRuntime(active, self.callbacks.extract, self.callbacks.embed, seed_path=seed)
        return memory, seed, active.parent / "backups" / "reset-one"

    def test_wipe_preserves_backup_clears_every_active_memory_store_and_never_reseeds(self):
        memory, seed, backup = self.wipe_runtime()
        memory.archive(event("completed"))
        memory.flush_pending()
        self.callbacks.fail_extract = True
        memory.archive(event("blocked"))
        memory.flush_pending()
        memory.archive(event("pending"))
        memory.retrieve("Alder Bay")
        quarantine = memory.folder / "quarantine"
        quarantine.mkdir()
        (quarantine / "receipt.json").write_text('{"reason":"invalid quote"}')
        # Sibling cost ledger and the root-owned history backup remain untouched.
        ledger = memory.folder.parent / "usage.sqlite"
        ledger.write_bytes(b"untouched ledger")
        (backup / "brain_context").mkdir(parents=True)
        (backup / "brain_context" / "history.json").write_text("history backup")
        before = {str(p.relative_to(memory.folder)): p.read_bytes()
                  for p in memory.folder.rglob("*") if p.is_file()}
        calls = (len(self.callbacks.extractions), len(self.callbacks.embeddings))
        result = memory.wipe(backup)
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed"]["events"], 4)
        self.assertEqual(result["removed"]["jobs"], 3)
        self.assertEqual(result["removed"]["query_embeddings"], 1)
        self.assertEqual(result["removed"]["quarantine_files"], 1)
        for name, content in before.items():
            self.assertEqual((backup / name).read_bytes(), content)
        self.assertEqual(ledger.read_bytes(), b"untouched ledger")
        self.assertEqual((backup / "brain_context/history.json").read_text(), "history backup")
        self.assertEqual(memory.model.events, [])
        self.assertEqual(memory.model.facts, [])
        self.assertEqual(memory.model._vector_payload(memory.model.vectors), {"documents": {}, "queries": {}})
        status = memory.status()
        self.assertEqual((status["pending"], status["blocked"], status["query_embeddings_blocked"]), (0, 0, 0))
        self.assertEqual(status["last_reset"]["phase"], "complete")
        self.assertTrue(status["seed_disabled"])
        self.assertEqual(memory.retrieve("Alder Bay")["text"], "")
        restarted = MemoryRuntime(memory.folder, self.callbacks.extract, self.callbacks.embed, seed_path=seed)
        self.assertEqual(restarted.status()["events"], 0)
        # Even loss of the empty active snapshot must not re-import the seed.
        restarted.state_path.unlink()
        self.assertEqual(MemoryRuntime(memory.folder, seed_path=seed).status()["events"], 0)
        self.assertEqual(calls, (len(self.callbacks.extractions), len(self.callbacks.embeddings)))

    def test_wipe_refuses_query_worker_and_retrieval_gap_without_mutation(self):
        memory, _, backup = self.wipe_runtime()
        before = memory.state_path.read_bytes()
        for lock in (memory._query_lock, memory._worker_lock):
            lock.acquire()
            try:
                with self.assertRaises(MemoryBusyError):
                    memory.wipe(backup)
            finally:
                lock.release()
        memory._active_retrievals = 1
        with self.assertRaises(MemoryBusyError):
            memory.wipe(backup)
        memory._active_retrievals = 0
        self.assertEqual(memory.state_path.read_bytes(), before)
        self.assertFalse(memory.reset_path.exists())
        self.assertFalse(backup.exists())

    def test_wipe_refuses_busy_state_lock_without_waiting(self):
        memory, _, backup = self.wipe_runtime()
        held, release = threading.Event(), threading.Event()
        def hold():
            with memory._lock:
                held.set()
                release.wait(2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold)
            self.assertTrue(held.wait(1))
            try:
                with self.assertRaises(MemoryBusyError):
                    memory.wipe(backup)
            finally:
                release.set()
            future.result(timeout=2)
        self.assertFalse(memory.reset_path.exists())

    def test_wipe_validates_backup_boundary_and_collision_before_changes(self):
        memory, _, backup = self.wipe_runtime()
        for bad in (self.folder / "outside", memory.folder / "backups" / "reset", Path("relative"),
                    backup / "nested", backup.parent / "bad name"):
            with self.subTest(path=str(bad)), self.assertRaises(ValueError):
                memory.wipe(bad)
        backup.mkdir(parents=True)
        (backup / "state.json").write_text("existing unrelated backup")
        with self.assertRaises(ValueError):
            memory.wipe(backup)
        self.assertEqual(memory.status()["events"], 1)
        self.assertFalse(memory.reset_path.exists())
        self.assertEqual((backup / "state.json").read_text(), "existing unrelated backup")

    def test_interrupted_rename_recovers_empty_before_loading_seed_or_old_jobs(self):
        memory, seed, backup = self.wipe_runtime()
        memory.archive(event("pending"))
        original_state = memory.state_path.read_bytes()
        replace = f_memory.os.replace
        failed = False
        def interrupt_after_jobs(source, destination):
            nonlocal failed
            result = replace(source, destination)
            if not failed and Path(source) == memory.jobs_dir:
                failed = True
                raise OSError("simulated crash after atomic jobs rename")
            return result
        with patch.object(f_memory.os, "replace", side_effect=interrupt_after_jobs):
            with self.assertRaises(OSError):
                memory.wipe(backup)
        self.assertTrue(memory.status()["reset_pending"])
        for action in (lambda: memory.archive(event("new")), lambda: memory.retrieve("old"), memory.flush_pending):
            with self.assertRaises(MemoryResetPendingError):
                action()
        restarted = MemoryRuntime(memory.folder, self.callbacks.extract, self.callbacks.embed, seed_path=seed)
        self.assertEqual(restarted.status()["events"], 0)
        self.assertEqual(restarted.status()["pending"], 0)
        self.assertEqual(restarted.status()["last_reset"]["phase"], "complete")
        self.assertEqual((backup / "state.json").read_bytes(), original_state)
        self.assertEqual(len(list((backup / "jobs").glob("*.json"))), 1)
        self.assertEqual(self.callbacks.extractions, [])
        self.assertEqual(self.callbacks.embeddings, [])

    def test_crash_after_empty_snapshot_before_final_marker_is_recoverable(self):
        memory, seed, backup = self.wipe_runtime()
        writer = f_memory._write
        def interrupt_completion(path, value):
            if Path(path) == memory.reset_path and value.get("phase") == "complete":
                raise OSError("simulated crash before final marker")
            return writer(path, value)
        with patch.object(f_memory, "_write", side_effect=interrupt_completion):
            with self.assertRaises(OSError):
                memory.wipe(backup)
        self.assertEqual(CandidateF.reload(memory.state_path).events, [])
        restarted = MemoryRuntime(memory.folder, seed_path=seed)
        self.assertEqual(restarted.status()["events"], 0)
        self.assertEqual(restarted.status()["last_reset"]["phase"], "complete")

    def test_repeat_reset_uses_new_backup_and_preserves_prior_backup(self):
        memory, seed, first = self.wipe_runtime()
        memory.wipe(first)
        first_state = (first / "state.json").read_bytes()
        memory.archive(event("after-reset"))
        with self.assertRaises(ValueError):
            memory.wipe(first)
        second = first.with_name("reset-two")
        result = memory.wipe(second)
        self.assertEqual(result["removed"]["events"], 1)
        self.assertEqual((first / "state.json").read_bytes(), first_state)
        self.assertEqual(memory.status()["last_reset"]["reset_id"], "reset-two")
        self.assertEqual(MemoryRuntime(memory.folder, seed_path=seed).status()["events"], 0)

    # ───── 2026-10 scaling fixes: A archived job files, B batched snapshot writes, C vector sidecar ─────
    def finished_job(self, name=None):
        done = self.folder / "jobs" / "completed"
        return next(p for p in done.glob("*.json") if p.name != "totals.json" and (name is None or p.name == name))

    def test_finished_job_files_are_archived_and_their_counts_survive_restart(self):
        memory = self.runtime()
        memory.archive(event())
        memory.archive(event("e2", day=2))
        self.assertEqual(memory.flush_pending()["completed"], 2)
        jobs = self.folder / "jobs"
        self.assertEqual(list(jobs.glob("*.json")), [])                        # nothing finished stays in the live folder
        self.assertEqual(len(list((jobs / "completed").glob("*.json"))), 3)   # two receipts + totals.json
        status = memory.status()
        self.assertEqual((status["job_states"], status["pending"], status["unsettled"]), ({"complete": 2}, 0, 0))
        restarted = self.runtime()
        self.assertEqual(restarted.status()["job_states"], {"complete": 2})
        self.assertEqual(restarted.flush_pending()["attempted"], 0)
        (jobs / "completed" / "totals.json").unlink()                         # lost counts are rebuilt once
        self.assertEqual(self.runtime().status()["job_states"], {"complete": 2})
        self.assertTrue((jobs / "completed" / "totals.json").exists())

    def test_snapshot_is_written_once_per_batch_after_the_turn_in_flight(self):
        memory = self.runtime()
        talking = [True]
        memory.persist_gate = lambda: not talking[0]
        real, writes = CandidateF.persist, []

        def counting(model, path, inline=False):
            writes.append(Path(path).name)
            return real(model, path, inline=inline)
        with patch.object(CandidateF, "persist", counting):
            memory.archive(event())
            memory.archive(event("e2", day=2))
            self.assertEqual(writes, [])                                       # deferred: a turn is in flight
            result = memory.flush_pending()
            self.assertEqual((result["completed"], result["settled"]), (2, False))
            self.assertEqual((memory.status()["facts"], memory.status()["unsettled"]), (2, 2))   # in memory already
            self.assertEqual(CandidateF.reload(self.folder / "state.json").events, [])          # not on disk yet
            self.assertEqual(sorted(json.loads(p.read_text())["state"] for p in (self.folder / "jobs").glob("*.json")),
                             ["vectors_ready", "vectors_ready"])
            self.assertEqual(memory.status()["job_states"], {"ingested": 2})
            self.assertEqual(memory.flush_pending()["attempted"], 0)           # not dispatched twice
            self.assertFalse(memory.settle())
            talking[0] = False                                                 # the turn ended
            self.assertTrue(memory.settle())
            self.assertEqual(writes, ["state.json"])                           # one write for the whole batch
        self.assertEqual(len(CandidateF.reload(self.folder / "state.json").facts), 2)
        self.assertEqual(list((self.folder / "jobs").glob("*.json")), [])
        self.assertEqual(memory.status()["job_states"], {"complete": 2})

    def test_crash_before_the_batched_write_loses_nothing_and_repeats_no_api_call(self):
        memory = self.runtime()
        memory.persist_gate = lambda: False
        memory.archive(event())
        memory.flush_pending()
        self.assertEqual(memory.status()["unsettled"], 1)
        calls = (len(self.callbacks.extractions), len(self.callbacks.embeddings))
        revived = self.runtime()                                               # the process died before settle()
        self.assertEqual(revived.status()["events"], 1)                        # the journal restored the event
        self.assertEqual(revived.flush_pending()["completed"], 1)              # resumed from the stored vectors
        self.assertEqual((revived.status()["facts"], revived.status()["unsettled"]), (1, 0))
        self.assertEqual(calls, (len(self.callbacks.extractions), len(self.callbacks.embeddings)))

    def test_deferred_snapshot_write_is_forced_after_the_cap(self):
        clock = [1000.0]
        memory = self.runtime(clock=lambda: clock[0], persist_delay_s=120)
        memory.persist_gate = lambda: False                                    # never a quiet moment
        memory.archive(event())
        memory.flush_pending()
        self.assertFalse(memory.settle())
        self.assertEqual(memory.status()["persist_due_in_s"], 120.0)
        clock[0] += 119
        self.assertFalse(memory.settle())
        clock[0] += 1
        self.assertTrue(memory.settle())
        self.assertIsNone(memory.status()["persist_due_in_s"])
        self.assertEqual(len(CandidateF.reload(self.folder / "state.json").facts), 1)

    def test_vectors_sit_in_a_binary_sidecar_and_round_trip(self):
        values = [0.1 * (i % 7) + 0.01 for i in range(DIMENSIONS)]             # not float32-exact on purpose
        norm = sum(v * v for v in values) ** 0.5
        unit = [v / norm for v in values]
        memory = MemoryRuntime(self.folder, self.callbacks.extract, lambda texts, task_type: [list(unit) for _ in texts])
        memory.archive(event())
        memory.flush_pending()
        state = json.loads((self.folder / "state.json").read_text("utf-8"))
        self.assertNotIn("documents", state["vectors"])
        sidecar = self.folder / state["vectors"]["sidecar"]["file"]
        self.assertEqual(f_method.sidecar_files(self.folder / "state.json"), [sidecar])
        self.assertEqual(sidecar.stat().st_size, 2 * DIMENSIONS * 4)           # event + fact, float32 rows
        self.assertEqual(state["vectors"]["sidecar"]["count"], 2)
        reloaded = CandidateF.reload(self.folder / "state.json")
        stored = reloaded._vector_payload(reloaded.vectors)["documents"]
        self.assertEqual(len(stored), 2)
        self.assertLess(max(abs(a - b) for a, b in zip(stored["e1"], unit)), 1e-6)
        self.assertIn("Alder Bay", memory.retrieve("Alder Bay")["text"])
        # unchanged vectors: a further write touches the JSON only
        with patch.object(f_method, "_replace_file", wraps=f_method._replace_file) as replaced:
            memory.model.persist(self.folder / "state.json")
        self.assertEqual([Path(call.args[0]).name for call in replaced.call_args_list], ["state.json"])
        # the float32 round trip is not a conflict when a job's final checkpoint is repeated after a crash
        done = self.finished_job()
        job = json.loads(done.read_text())
        job["state"] = "embedding_received"
        (self.folder / "jobs" / done.name).write_text(json.dumps(job))
        done.unlink()
        revived = MemoryRuntime(self.folder, self.callbacks.extract, lambda texts, task_type: [list(unit) for _ in texts])
        self.assertEqual((revived.flush_pending()["completed"], revived.status()["blocked"]), (1, 0))

    def test_old_inline_snapshot_is_converted_once_with_a_backup(self):
        original = event("old")
        CandidateF([original], {"facts": [fact(original, "old-fact")]},
                   {"documents": {"old": [1.0] + [0.0] * (DIMENSIONS - 1), "old-fact": [0.0, 1.0] + [0.0] * (DIMENSIONS - 2)},
                    "queries": {}}).persist(self.folder / "state.json", inline=True)
        self.assertEqual(f_method.sidecar_files(self.folder / "state.json"), [])
        memory = self.runtime()
        self.assertEqual(len(memory.startup_notes), 1)
        backups = list(self.folder.glob("state.json.bak_inline_*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text("utf-8"))["vectors"]["documents"]["old"][0], 1.0)
        self.assertEqual(len(f_method.sidecar_files(self.folder / "state.json")), 1)
        self.assertEqual((memory.status()["events"], memory.status()["facts"]), (1, 1))
        self.assertEqual(self.runtime().startup_notes, [])                     # converted once

    def test_wipe_moves_the_vector_sidecar_with_the_snapshot(self):
        memory, _, backup = self.wipe_runtime()                                # the seed holds one document vector
        self.assertEqual(len(f_method.sidecar_files(memory.state_path)), 1)
        memory.wipe(backup)
        self.assertEqual(f_method.sidecar_files(memory.state_path), [])
        self.assertEqual(json.loads(memory.state_path.read_text("utf-8"))["vectors"], {"documents": {}, "queries": {}})
        restored = CandidateF.reload(backup / "state.json")
        self.assertEqual(list(restored._vector_payload(restored.vectors)["documents"]), ["seed-event"])


if __name__ == "__main__":
    unittest.main()
