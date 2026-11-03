"""Cost settlement for the metered OpenRouter ledger (OpenAmika).

Why this exists: OpenRouter bills a cancelled streaming request (the prompt, at the
uncached rate), but a cancelled stream never receives its final usage chunk, so the
ledger row in ``usage.sqlite`` has no cost. This module closes that gap in three ways:

(a) Settlement. Ledger rows that carry a ``provider_request_id`` (the stream's first
    SSE chunk arrived) and have no confirmed cost are looked up with
    ``GET /api/v1/generation?id=`` by a background daemon thread. The authoritative
    charge goes into the ``settled_*`` columns. The recorded columns (``cost_usd``,
    ``tokens_json``, ``pricing_status``) are never overwritten, so a settled amount
    is never presented as "recorded". 404/"not found" (the generation is not
    queryable yet) and transient errors retry after 3 s, 10 s, 30 s and 2 min; after
    that the row is ``failed`` and falls back to an estimate.
(b) Key-usage snapshots (``key_usage_snapshots``) of ``/api/v1/auth/key`` and
    ``/api/v1/credits`` at boot, at every display reset, 30 s after the last request
    finished, and otherwise at most every 5 minutes. The key is shared, so the
    difference between snapshots is an upper bound for this app.
(c) Estimates for rows that can never be looked up (no id: the request was cancelled
    before its first chunk) or whose lookup failed: mean ``total_input_tokens`` of the
    last three completed calls with the same provider+model+phase, times that model's
    uncached input rate from the row's own ``pricing_json`` snapshot, no output tokens.
    Without such calls the row's reservation (a bound on the whole call) is used.
    A row that recorded its own token counts but no charge uses its own
    ``estimated_cost_usd`` instead. Every estimate is flagged by ``estimate_basis``.

Budget rule (used by ``f_usage.UsageMeter._budget``): settled and estimated amounts
count toward the ``budget_scope`` spend exactly the way recorded cost does. A settled
row spends its settled charge and its reservation is released (nothing held). An
estimated row spends recorded + estimate and still holds only the rest of its
reservation, as a partially recorded row always has. Rows awaiting settlement keep
their reservation held.

The worker never blocks a turn (daemon thread, no HTTP under the meter lock), never
raises into its caller, prefixes every log line with ``[settle]`` and never logs the
key or provider response text. The pure functions below (schema, bookkeeping,
estimates, key-usage view) are imported by f_usage; this module does not import
f_usage, so there is no import cycle.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import math
import threading
import time

from f_openrouter import (GenerationNotReady, OpenRouterAuthError, OpenRouterError, OpenRouterHTTPError,
                          fetch_credits, fetch_generation, fetch_key_usage)

SETTLEMENT_STATUSES = ("settled", "pending", "no_id", "failed")
DEFAULT_BACKOFF = (3.0, 10.0, 30.0, 120.0)
IDLE_SNAPSHOT_S = 30.0
PERIODIC_SNAPSHOT_S = 300.0
SHARED_KEY_NOTE = ("This OpenRouter key is shared by Build F, this clone and the benchmark, "
                   "so this figure is an upper bound for this app.")

# Columns added in place to an existing ledger (ALTER TABLE guarded by PRAGMA table_info).
SETTLEMENT_COLUMNS = (
    ("settled_cost_usd", "REAL"), ("settled_prompt_tokens", "INTEGER"),
    ("settled_completion_tokens", "INTEGER"), ("settled_cached_tokens", "INTEGER"),
    ("settled_cancelled", "INTEGER"), ("settled_at", "TEXT"), ("settlement_status", "TEXT"),
    # Retry bookkeeping for pending rows (epoch seconds) and a short error code.
    ("settle_attempts", "INTEGER NOT NULL DEFAULT 0"), ("settle_retry_after", "REAL"),
    ("settle_error", "TEXT"),
    # Per-row estimate for rows that cannot be settled (no id, or lookup failed).
    ("estimated_unrecorded_usd", "REAL"), ("estimate_input_tokens", "REAL"), ("estimate_basis", "TEXT"),
)
SNAPSHOT_EXTRA_COLUMNS = (("reason", "TEXT"), ("reset_id", "TEXT"))
# "drained" rows (f_openrouter.StreamDrainer) are finished calls with exact usage.
_FINAL_SQL = "status IN ('complete','drained')"
_UNCONFIRMED_SQL = f"NOT ({_FINAL_SQL} AND usage_complete=1 AND pricing_status='priced')"


def _iso(epoch: float | None = None) -> str:
    value = time.time() if epoch is None else epoch
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def _finite(value, *, positive=False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if value < 0 or (positive and value == 0):
        return None
    return float(value)


def ensure_schema(conn) -> None:
    """Idempotent additive upgrade of an existing ledger; call inside a write transaction."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
    for name, kind in SETTLEMENT_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {name} {kind}")
    conn.execute("""CREATE TABLE IF NOT EXISTS key_usage_snapshots (
        at TEXT NOT NULL, key_usage REAL, key_usage_daily REAL, credits_total_usage REAL)""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(key_usage_snapshots)")}
    for name, kind in SNAPSHOT_EXTRA_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE key_usage_snapshots ADD COLUMN {name} {kind}")
    conn.execute("CREATE INDEX IF NOT EXISTS request_settlement ON requests(settlement_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS request_phase_model ON requests(provider, model, phase, started_at)")


def is_confirmed(row) -> bool:
    return row["status"] in ("complete", "drained") and bool(row["usage_complete"]) and row["pricing_status"] == "priced"


def has_generation_id(row) -> bool:
    return row["provider"] == "openrouter" and bool(row["provider_request_id"])


def _input_tokens(tokens_json) -> int | None:
    try:
        value = json.loads(tokens_json).get("total_input_tokens") if tokens_json else None
    except (ValueError, AttributeError):
        return None
    return int(value) if _finite(value, positive=True) is not None and int(value) == value else None


def recent_input_tokens(conn, row, limit: int = 3) -> list[int]:
    """Input sizes of the last ``limit`` completed calls with the same provider+model+phase.

    "Last" means the nearest completed calls started before this row; when none exist
    (e.g. the very first call of a session was cancelled), the nearest ones after it.
    """
    base = ("SELECT tokens_json FROM requests WHERE provider=? AND model=? AND phase=? AND id!=? "
            f"AND {_FINAL_SQL} AND usage_complete=1 AND tokens_json IS NOT NULL ")
    args = (row["provider"], row["model"], row["phase"], row["id"], row["started_at"])
    for clause in ("AND started_at<=? ORDER BY started_at DESC, rowid DESC LIMIT 30",
                   "AND started_at>? ORDER BY started_at ASC, rowid ASC LIMIT 30"):
        values = []
        for (tokens_json,) in conn.execute(base + clause, args):
            value = _input_tokens(tokens_json)
            if value:
                values.append(value)
                if len(values) == limit:
                    break
        if values:
            return values
    return []


def estimate_unrecorded(conn, row) -> tuple[float | None, str, float | None]:
    """(usd, basis, input_tokens) for a row whose charge was never recorded."""
    own = _finite(row["estimated_cost_usd"])
    if own is not None:
        # The row recorded its own token counts; only the charge is missing.
        return own, "own_tokens", _input_tokens(row["tokens_json"])
    try:
        rates = (json.loads(row["pricing_json"] or "{}") or {}).get("rates") or {}
    except ValueError:
        rates = {}
    rate = _finite(rates.get("input"), positive=True) if isinstance(rates, dict) else None
    samples = recent_input_tokens(conn, row)
    if samples and rate is not None:
        tokens = sum(samples) / len(samples)
        # Cancelled prompts are billed uncached; no output tokens are assumed.
        return tokens * rate / 1_000_000, "recent_mean", tokens
    reservation = _finite(row["reservation_usd"], positive=True)
    if reservation is not None:
        # The reservation bounds the whole call, so it also bounds its input cost.
        return reservation, "reservation_bound", (reservation * 1_000_000 / rate if rate else None)
    return None, "unavailable", None


def _write_estimate(conn, row, status: str) -> None:
    usd, basis, tokens = estimate_unrecorded(conn, row)
    conn.execute("""UPDATE requests SET settlement_status=?, estimated_unrecorded_usd=?, estimate_input_tokens=?,
                    estimate_basis=? WHERE id=?""", (status, usd, tokens, basis, row["id"]))


def _clear_estimate(conn, row, status: str | None) -> None:
    conn.execute("""UPDATE requests SET settlement_status=?, estimated_unrecorded_usd=NULL, estimate_input_tokens=NULL,
                    estimate_basis=NULL WHERE id=?""", (status, row["id"]))


def refresh_row(conn, request_id: str, *, recompute: bool = True) -> None:
    """Classify one finished ledger row for settlement; call inside a write transaction.

    confirmed -> no settlement state; id + unconfirmed -> ``pending`` (due now);
    no id + nothing recorded -> ``no_id`` with an estimate; no id + a partial recorded
    charge -> ``no_id`` without an estimate (the recorded part already counts);
    ``settled`` rows are final; ``failed`` rows keep (or refine) their estimate.
    ``recompute=False`` only fills missing estimates and refines reservation-bound ones.
    """
    row = conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
    if row is None or row["status"] == "started" or not row["finished_at"]:
        return
    status = row["settlement_status"]
    if status == "settled":
        return
    stale = recompute or row["estimated_unrecorded_usd"] is None or row["estimate_basis"] in (None, "reservation_bound", "unavailable")
    if is_confirmed(row):
        if status is not None or row["estimated_unrecorded_usd"] is not None:
            _clear_estimate(conn, row, None)
        return
    if status == "failed":
        if row["cost_usd"] is not None:
            if row["estimated_unrecorded_usd"] is not None:
                _clear_estimate(conn, row, "failed")
        elif stale:
            _write_estimate(conn, row, "failed")
        return
    if has_generation_id(row):
        if status != "pending" or row["estimated_unrecorded_usd"] is not None:
            _clear_estimate(conn, row, "pending")
        return
    if row["cost_usd"] is not None:
        if status != "no_id" or row["estimated_unrecorded_usd"] is not None:
            _clear_estimate(conn, row, "no_id")
    elif status != "no_id" or stale:
        _write_estimate(conn, row, "no_id")


def backfill(conn) -> int:
    """Classify every finished, unconfirmed row that has no settlement state yet."""
    ids = [r[0] for r in conn.execute(
        f"SELECT id FROM requests WHERE status!='started' AND finished_at IS NOT NULL "
        f"AND settlement_status IS NULL AND {_UNCONFIRMED_SQL}").fetchall()]
    for request_id in ids:
        refresh_row(conn, request_id)
    return len(ids)


def row_settlement(row) -> dict:
    """How one ledger row contributes to the settlement view (see f_usage summaries)."""
    result = {"settled": False, "settled_extra": 0.0, "pending": False, "no_id": False, "failed": False,
              "estimate": None, "partial": False}
    if row["status"] == "started" or not row["finished_at"]:
        return result
    recorded = row["cost_usd"] or 0.0
    if row["settlement_status"] == "settled" and row["settled_cost_usd"] is not None:
        result.update(settled=True, settled_extra=row["settled_cost_usd"] - recorded)
        return result
    if is_confirmed(row):
        return result
    if has_generation_id(row) and row["settlement_status"] != "failed":
        result["pending"] = True
        return result
    result["failed"] = has_generation_id(row)
    if row["cost_usd"] is None:
        result["no_id"] = not result["failed"]
        result["estimate"] = row["estimated_unrecorded_usd"]
    else:
        result["partial"] = True
    return result


def _snapshot_row(conn, where: str, args=(), order: str = "DESC"):
    return conn.execute(f"SELECT rowid AS snapshot_rowid,* FROM key_usage_snapshots WHERE key_usage IS NOT NULL {where} "
                        f"ORDER BY rowid {order} LIMIT 1", args).fetchone()


def key_usage_view(conn, window: dict | None, *, lifetime: bool) -> dict:
    """Key-usage fields for one summary view.

    Display view: latest snapshot minus the snapshot taken at the current display reset
    (fallback: the last snapshot before the reset, which can only overstate). With no
    reset yet, and in the lifetime view, the baseline is the first snapshot ever taken.
    """
    latest = _snapshot_row(conn, "")
    base, basis = None, None
    if latest is not None:
        reset_id = (window or {}).get("reset_id")
        if lifetime or not reset_id:
            base, basis = _snapshot_row(conn, "", order="ASC"), "first_snapshot"
        else:
            base, basis = _snapshot_row(conn, "AND reason='reset' AND reset_id=?", (reset_id,), order="ASC"), "reset"
            if base is None and (window or {}).get("reset_at"):
                base, basis = _snapshot_row(conn, "AND at<=?", (window["reset_at"],)), "before_reset"
    since = None
    if latest is not None and base is not None:
        since = max(0.0, latest["key_usage"] - base["key_usage"])
    return {"key_usage_since_reset_usd": since,
            "key_usage_snapshot_at": latest["at"] if latest is not None else None,
            "key_usage_baseline_at": base["at"] if base is not None else None,
            "key_usage_baseline": basis if base is not None else None,
            "key_usage_total_usd": latest["key_usage"] if latest is not None else None,
            "key_usage_daily_usd": latest["key_usage_daily"] if latest is not None else None,
            "key_usage_shared_note": SHARED_KEY_NOTE}


def _default_log(message: str) -> None:
    print(message, flush=True)


class Settler:
    """Background settlement worker. ``meter`` is an f_usage.UsageMeter.

    ``key_fn`` returns the OpenRouter key at call time (never stored in logs or the
    ledger). ``http`` needs ``get(url, headers=, params=, timeout=, allow_redirects=)``.
    Tests drive ``run_once(now)`` directly; production calls ``start()`` once at boot.
    """

    def __init__(self, meter, http, key_fn, *, enabled: bool = True, log=None, clock=time.time,
                 backoff=DEFAULT_BACKOFF, idle_snapshot_s: float = IDLE_SNAPSHOT_S,
                 periodic_snapshot_s: float = PERIODIC_SNAPSHOT_S, batch: int = 20, timeout=(5, 15)):
        self.meter, self.http, self._key_fn = meter, http, key_fn
        self._log_fn = log or _default_log
        self.clock = clock
        self.backoff = tuple(float(x) for x in backoff)
        self.idle_snapshot_s = float(idle_snapshot_s)
        self.periodic_snapshot_s = float(periodic_snapshot_s)
        self.batch, self.timeout = int(batch), timeout
        self._wake, self._stop = threading.Event(), threading.Event()
        self._state = threading.Lock()
        self._thread = None
        self._snapshots = collections.deque()
        self._last_finish = None      # clock time of the last finished request
        self._idle_done_for = None    # the _last_finish value an idle snapshot has covered
        self._last_snapshot = None    # clock time of the last snapshot attempt
        self._last_reconcile = None
        self._reconcile_due = True
        self._auth_blocked_until = 0.0
        self.info = {"enabled": bool(enabled), "running": False, "key_available": None,
                     "last_pass_at": None, "last_error": None, "settled": 0, "failed": 0, "snapshots": 0}
        meter.add_finish_listener(self.note_request_finished)
        meter.settlement_worker = self.status

    # -- plumbing ---------------------------------------------------------
    def _key(self):
        try:
            key = self._key_fn() if callable(self._key_fn) else self._key_fn
        except Exception:
            return None
        return key if isinstance(key, str) and key else None

    def log(self, message: str) -> None:
        try:
            text = "[settle] " + str(message)
            key = self._key()
            if key and key in text:  # defence in depth; messages never include it
                text = text.replace(key, "***")
            self._log_fn(text)
        except Exception:
            pass

    def status(self) -> dict:
        with self._state:
            return dict(self.info)

    def _error(self, code: str) -> None:
        with self._state:
            self.info["last_error"] = code

    # -- triggers (cheap, never raise) -------------------------------------
    def note_request_finished(self, receipt=None) -> None:
        """Meter finish listener: remember the time and wake the worker."""
        try:
            with self._state:
                self._last_finish = self.clock()
                self._reconcile_due = True
            self._wake.set()
        except Exception:
            pass

    def request_snapshot(self, reason: str, reset_id: str | None = None) -> None:
        try:
            with self._state:
                self._snapshots.append((str(reason), reset_id))
            self._wake.set()
        except Exception:
            pass

    def on_reset(self, window: dict | None) -> None:
        """Display-window reset hook (f_controls): snapshot the key usage baseline."""
        self.request_snapshot("reset", (window or {}).get("reset_id"))

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        try:
            if not self.info["enabled"]:
                self.log("settlement disabled by configuration; estimates still apply")
                return False
            if self._thread is not None and self._thread.is_alive():
                return True
            key_available = self._key() is not None
            with self._state:
                self.info["key_available"] = key_available
            if not key_available:
                self.log("no OpenRouter key; settlement and key snapshots are off (estimates still apply)")
                return False
            self.request_snapshot("boot")
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="F-settle")
            self._thread.start()
            with self._state:
                self.info["running"] = True
            return True
        except Exception as exc:
            self.log("could not start: " + type(exc).__name__)
            return False

    def stop(self, timeout: float | None = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        with self._state:
            self.info["running"] = False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                delay = self.run_once()
            except BaseException as exc:  # never let the worker die silently
                self.log("pass failed: " + type(exc).__name__)
                self._error("pass_" + type(exc).__name__)
                delay = 30.0
            if self._stop.is_set():
                break
            self._wake.wait(timeout=min(max(delay, 0.05), self.periodic_snapshot_s or 300.0))
            self._wake.clear()
        with self._state:
            self.info["running"] = False

    # -- one pass -------------------------------------------------------------
    def run_once(self, now: float | None = None) -> float:
        """Snapshots, estimate reconciliation, due settlements; returns seconds to the next job."""
        now = self.clock() if now is None else now
        key = self._key()
        with self._state:
            self.info["last_pass_at"] = _iso(now)
            self.info["key_available"] = key is not None
            requests, self._snapshots = list(self._snapshots), collections.deque()
            reconcile = self._reconcile_due
            self._reconcile_due = False
        for reason, reset_id in requests:
            if key:
                self.take_snapshot(reason, reset_id, key=key, now=now)
        if reconcile or self._last_reconcile is None or now - self._last_reconcile >= (self.periodic_snapshot_s or 300.0):
            self.reconcile()
            self._last_reconcile = now
        if key and now >= self._auth_blocked_until:
            self.settle_due(key, now)
        with self._state:
            last_finish = self._last_finish
        idle_pending = last_finish is not None and self._idle_done_for != last_finish
        if key and idle_pending and now - last_finish >= self.idle_snapshot_s:
            if self._active_requests():
                # A request is in flight; its finish moves the deadline and wakes us.
                idle_pending = False
            else:
                self.take_snapshot("idle", key=key, now=now)
                self._idle_done_for = last_finish
                idle_pending = False
        if key and self.periodic_snapshot_s > 0 and (
                self._last_snapshot is None or now - self._last_snapshot >= self.periodic_snapshot_s):
            self.take_snapshot("periodic", key=key, now=now)
        # When is the next job due?
        delays = [self.periodic_snapshot_s or 300.0]
        if key:
            if self._last_snapshot is not None and self.periodic_snapshot_s > 0:
                delays.append(self._last_snapshot + self.periodic_snapshot_s - now)
            if idle_pending and last_finish is not None:
                delays.append(max(last_finish + self.idle_snapshot_s - now, 1.0))
            due = self._next_retry_at()
            if due is not None:
                delays.append(max(due, self._auth_blocked_until) - now)
        return max(0.0, min(delays))

    def _active_requests(self) -> bool:
        with self.meter.read_connection() as conn:
            return conn.execute("SELECT 1 FROM requests WHERE status='started' LIMIT 1").fetchone() is not None

    def _next_retry_at(self) -> float | None:
        with self.meter.read_connection() as conn:
            row = conn.execute(f"""SELECT MIN(COALESCE(settle_retry_after, 0)) FROM requests
                WHERE settlement_status='pending' AND {_UNCONFIRMED_SQL}""").fetchone()
        return None if row is None or row[0] is None else float(row[0])

    def reconcile(self) -> int:
        """Classify unclassified rows and refine reservation-bound estimates (DB only)."""
        with self.meter.transaction() as conn:
            count = backfill(conn)
            ids = [r[0] for r in conn.execute("""SELECT id FROM requests WHERE settlement_status IN ('no_id','failed')
                   AND cost_usd IS NULL AND (estimated_unrecorded_usd IS NULL OR estimate_basis IN ('reservation_bound','unavailable'))""")]
            for request_id in ids:
                refresh_row(conn, request_id, recompute=False)
        return count + len(ids)

    # -- (a) settlement -------------------------------------------------------
    def settle_due(self, key: str, now: float) -> int:
        with self.meter.read_connection() as conn:
            rows = conn.execute(f"""SELECT id, provider_request_id, settle_attempts FROM requests
                WHERE provider='openrouter' AND provider_request_id IS NOT NULL AND provider_request_id!=''
                AND status!='started' AND finished_at IS NOT NULL AND settlement_status='pending'
                AND {_UNCONFIRMED_SQL} AND (settle_retry_after IS NULL OR settle_retry_after<=?)
                ORDER BY started_at, rowid LIMIT ?""", (now, self.batch)).fetchall()
        settled = 0
        for row in rows:
            if self._stop.is_set():
                break
            try:
                result = fetch_generation(self.http, key, row["provider_request_id"], timeout=self.timeout)
            except OpenRouterAuthError as exc:
                # Not a property of this row: pause all lookups and leave rows pending.
                self._auth_blocked_until = now + max(60.0, self.periodic_snapshot_s)
                self.log("generation lookup refused (" + str(exc) + "); pausing settlement")
                self._error("auth")
                break
            except GenerationNotReady:
                self._retry(row, now, "not_found")
                continue
            except OpenRouterHTTPError as exc:
                self._retry(row, now, "http_" + str(exc.status))
                continue
            except OpenRouterError as exc:
                self._retry(row, now, str(exc) if str(exc).replace("_", "").isalnum() else "provider_error")
                continue
            except Exception as exc:  # network errors etc.; never the key or response text
                self._retry(row, now, type(exc).__name__)
                continue
            settled += self._write_settled(row, result, now)
        return settled

    def _write_settled(self, row, result: dict, now: float) -> int:
        with self.meter.transaction() as conn:
            changed = conn.execute("""UPDATE requests SET settled_cost_usd=?, settled_prompt_tokens=?,
                settled_completion_tokens=?, settled_cached_tokens=?, settled_cancelled=?, settled_at=?,
                settlement_status='settled', settle_attempts=settle_attempts+1, settle_retry_after=NULL,
                settle_error=NULL, estimated_unrecorded_usd=NULL, estimate_input_tokens=NULL, estimate_basis=NULL
                WHERE id=? AND settlement_status='pending'""",
                (result["total_cost"], result["prompt_tokens"], result["completion_tokens"], result["cached_tokens"],
                 None if result["cancelled"] is None else int(result["cancelled"]), _iso(now), row["id"])).rowcount
        if changed:
            with self._state:
                self.info["settled"] += 1
            self.log(f"settled {row['id'][:8]}: ${result['total_cost']:.6f}"
                     + (" (cancelled)" if result["cancelled"] else ""))
        return int(bool(changed))

    def _retry(self, row, now: float, code: str) -> None:
        attempts = int(row["settle_attempts"] or 0) + 1
        code = code[:40]
        with self.meter.transaction() as conn:
            if attempts > len(self.backoff):
                conn.execute("""UPDATE requests SET settlement_status='failed', settle_attempts=?, settle_retry_after=NULL,
                                settle_error=? WHERE id=? AND settlement_status='pending'""", (attempts, code, row["id"]))
                refresh_row(conn, row["id"])  # failed rows fall back to an estimate
            else:
                conn.execute("""UPDATE requests SET settle_attempts=?, settle_retry_after=?, settle_error=?
                                WHERE id=? AND settlement_status='pending'""",
                             (attempts, now + self.backoff[attempts - 1], code, row["id"]))
        if attempts > len(self.backoff):
            with self._state:
                self.info["failed"] += 1
            self.log(f"gave up on {row['id'][:8]} after {attempts} attempts ({code}); using an estimate")
            self._error(code)

    # -- (b) key-usage snapshots ------------------------------------------------
    def take_snapshot(self, reason: str, reset_id: str | None = None, *, key: str | None = None,
                      now: float | None = None) -> dict | None:
        now = self.clock() if now is None else now
        key = key or self._key()
        self._last_snapshot = now
        if not key:
            return None
        usage = credits = None
        try:
            usage = fetch_key_usage(self.http, key, timeout=self.timeout)
        except Exception as exc:
            self.log(f"key usage snapshot ({reason}) failed: " + (str(exc) if isinstance(exc, OpenRouterError) else type(exc).__name__))
        try:
            credits = fetch_credits(self.http, key, timeout=self.timeout)
        except Exception as exc:
            self.log(f"credits snapshot ({reason}) failed: " + (str(exc) if isinstance(exc, OpenRouterError) else type(exc).__name__))
        if usage is None and credits is None:
            self._error("snapshot")
            return None
        row = {"at": _iso(now), "key_usage": (usage or {}).get("usage"),
               "key_usage_daily": (usage or {}).get("usage_daily"),
               "credits_total_usage": (credits or {}).get("total_usage"), "reason": reason[:20], "reset_id": reset_id}
        with self.meter.transaction() as conn:
            conn.execute("""INSERT INTO key_usage_snapshots(at,key_usage,key_usage_daily,credits_total_usage,reason,reset_id)
                            VALUES (:at,:key_usage,:key_usage_daily,:credits_total_usage,:reason,:reset_id)""", row)
        with self._state:
            self.info["snapshots"] += 1
        return row
