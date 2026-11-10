# Perf investigation: 60-player load

## Verdict
The backend was NOT the bottleneck. The single-process asyncio/httpx load generator (`load60.py`)
was pegged at 100% of one core (pyinstrument on the client: 63 s CPU in a 67 s run), so it only
managed ~45 pulls/s + ~33 /me/s and measured its own queueing (p50 ~600 ms, p95 ~2 s, max ~6 s).

## Evidence (own backend on :8010, pyinstrument + loop-lag monitor in-process)
- Server profile over a 150 s run containing the 60 s load: 139 s of 150 s in
  `GetQueuedCompletionStatus` (idle); only ~7 s inside the ASGI app (pulls handler 1.7 s, of which
  `_authed_player` ~0.8 s). Process CPU 19 s of 150 s.
- Event-loop lag sampled every 10 ms: p95 5.9 ms, max 42 ms. No stalls, no lock convoy visible.
- Independent probe client (1 serial GET /me every 50 ms, own connection) DURING the same load:
  p50 2.6 ms, p95 3.9 ms, max 30.7 ms.
- Same dev backend, load generator replaced by `loadmp.py` (4 worker processes, open-loop 2 Hz per
  phone, 60 players): numbers below, targets met before any server change.

## Numbers (60 players, 60 s match, offered ~120 pulls/s + 60 /me/s)
| | pulls/s | /me/s | pull p50 | p95 | max | accepted==recorded |
|---|---|---|---|---|---|---|
| load60.py (1 process), HEAD~ | 42-46 | ~33 | 560-625 ms | 2.0-2.4 s | 4.9-6.0 s | true |
| loadmp.py (4 procs), before fixes | 116.9 | 66.0 | 31 ms | 93 ms | 313 ms | true |
| loadmp.py (4 procs), after fixes | 116.1 | 64.5 | 47 ms | 141 ms | 453 ms | true |

(Before/after server numbers are within noise: the generator shares the machine's cores.)

## Fixes (small, evidence-independent hardening + the correctness gap)
1. `POST /pulls` returns 409 `NOT_RUNNING_DETAIL` once `ends_at` has passed, so a 202 means "will be
   counted" (closes the race where a pull accepted while RUNNING was enqueued after the loop's final
   receive). Test: `test_pull_after_ends_at_is_409_and_not_enqueued`.
2. `seen_batches` pruning is amortized to at most once per second (`Container.seen_pruned_at`);
   previously an O(n) scan per request.
Not changed (no evidence they matter): queue polling (the consumer calls `receive_batch(.., 0)`,
so it never polls), route sync/async mix, SQLite lock (e82e334 thread-safety kept as is).

## Load tooling note
Use a multi-process generator (like `loadmp.py`) for 60+ players; a single httpx process saturates
a core at ~80 req/s on this machine. Also set `max_keepalive_connections` to the pool size.
