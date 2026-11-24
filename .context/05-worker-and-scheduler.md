# 05 - Background Worker and Scheduler (task 2)

## Reuse decision
Reuse the worker pattern already in `Job-automation` (`core/fetch.py::worker`, `core/gen.py::worker`, started in `scripts/server.py`):

| Job-automation pattern | Reused here |
|---|---|
| `threading.Thread(target=worker, args=(..., stop), daemon=True)` started with the server | Same, started in FastAPI `lifespan`, `stop` Event set on shutdown |
| `while not stop.is_set(): ...; stop.wait(1.0)` poll loop | Same, `stop.wait(WORKER_POLL_SECONDS)` (default 5 s; 1 s in demo) |
| SQLite table as the queue, `status` column, oldest first, one task at a time | `posts` table is the queue; ordered by `next_attempt_at` |
| `try/except` around each task: "never let the worker die" | Same |
| Crash recovery: tasks left `running` go back to `queued` at startup (`fetch.py` ~line 48) | Reaper: `processing` with no URN goes back to `scheduled` at startup AND every loop for rows locked > 10 min |
| WAL mode so UI reads never block the writer | Same |

Not reused (not needed): per-profile folders, multi-queue kinds.

## Scheduler = the same loop
No separate scheduler library. "Scheduling" is data, not code: a post is due when `status='scheduled' AND next_attempt_at <= now(UTC)`. The loop finds due rows, so:
- first publish at `scheduled_at` (initial `next_attempt_at = scheduled_at`)
- retries use the same column (`next_attempt_at = now + backoff`)
- server restart loses nothing; overdue posts publish on next tick
- precision = poll interval (<= 5 s), fine for social posts

Gate test for APScheduler/Celery: current need none; simpler option (DB poll) already proves idea; cost = dependency + second source of truth for schedule; removal breaks nothing. -> DEFERRED.

## Loop (per tick)
```
reap_stuck()                      # processing & locked_at < now-10m & no URN -> scheduled
while row := claim_due():         # atomic UPDATE...RETURNING, one row at a time
    process(row)                  # publish -> published | scheduled(+backoff) | failed
stop.wait(poll)                   # also exits promptly on shutdown
```
- Drain all due rows each tick (not just one) so a backlog clears quickly; still sequential, one in flight at a time (no concurrency = no LinkedIn rate-limit surprises).
- Startup: run `reap_stuck()` once before the first tick, log `worker started`.
- Shutdown: `stop.set()`, `thread.join(timeout=10)`; a post mid-publish finishes its commit first.
- `/healthz` reports `worker_alive` (thread.is_alive()) and last tick time; a dead thread is restarted by the loop wrapper that catches everything.

## Files
`app/worker.py` holds: `reap_stuck`, `claim_due`, `process`, `backoff`, `run(stop)`. `app/publisher.py` holds the real/dry_run publisher. Nothing else.

## Verification (M4/M5 exit checks)
- dry_run post due in 30 s flips `scheduled -> processing -> published` with events logged.
- `FAULT_INJECT=2`: attempts 1-2 fail with 503, `next_attempt_at` grows (30 s then ~2.5 min), attempt 3 publishes, one row, one URN.
- Kill server while `processing`, restart: row reaped, published once.
- Two worker threads against one row (test): exactly one publish.
