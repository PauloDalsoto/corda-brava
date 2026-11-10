# SDD ledger — plan: docs/superpowers/plans/2026-11-03-corda-brava.md

Spec: prompt.md (authoritative) + docs/superpowers/specs/2026-11-03-corda-brava-design.md (decisions). Both reachable in worktree.

## Pre-flight conflict scan

Shared-file / interface pairs:

| Tasks | Produces → Consumes | Finding |
|-------|---------------------|---------|
| T1 → T2 | domain entities/enums (MatchState, TickInput, Team...) → rules.py | consistent; T2 only imports |
| T1 → T3 | enums/domain models → repo row↔model mapping | consistent |
| T1 → T4 | PullEvent → QueueMessage.payload | consistent |
| T2 → T6 | apply_pulls/check_win/resolve_result → consumer loop | signatures match plan |
| T3 → T6,T8,T9 | repo Protocols → services/api | method names consistent (add_event, event_exists, get_player_by_device...) |
| T4 → T6,T9 | EventQueue receive_batch/ack/enqueue → consumer, ingest | consistent |
| T5 → T10 | require_host, verify_ws_token, issue/verify host token → host routes+WS | consistent |
| T6 → T10 | MatchService, ConsumerLoop broadcast callback, MatchRegistry → host routes+WS | consistent; loop broadcast is injected callback (decouples from WS) |
| T7 → T10,T16 | compute_analytics → AnalyticsPayload → GET analytics + charts | consistent |
| T8 → T9,T14 | player join + player-token issue/verify → pulls/me, player pages | consistent |
| T9 → T13,T14 | /pulls 202, /me screen → usePullBuffer, player pages | consistent |
| T10 → T15,T16 | host routes + WS snapshot/tick/ended → useHostSocket, host pages | message shapes per prompt.md §14 |
| T11 → T12..T16 | theme tokens, types, api client, useDeviceId → all frontend | consistent |
| T12 → T14,T16 | Mascot/Crest components (copied assets) → player/host scenes | state→sprite mapping fixed in design |
| T13 → T14 | usePullBuffer → PlayerPlay | consistent |
| T15 → T16 | useHostSocket → HostMatch/Results | consistent |
| T17 → T18 | compose stack → Playwright E2E + README | consistent |

Per-task self-consistency: each task's specified tests target the code it specifies; files created are the files later touched. No task mandates a rubric-defect (no empty-assert tests, no verbatim logic duplication).

Review-rubric vs plan mandates: none. Plan mandates copying owner-committed asset files (cp), which is not a defect.

**Scan result: CLEAN.** No conflicts requiring a ruling before execution. Phases are sequential (C needs A/B contracts; D wraps all); within a phase, independent tasks may be dispatched one at a time (never parallel implementers — conflict risk) but reviewed per task.

Ruling (execution cadence): SDD forbids parallel *implementer* subagents (file-conflict risk). The user asked for parallelism; I honor it where it is safe — parallel is reserved for independent *read-only/validation* fan-out and for asset-prep that touches disjoint paths. Core implementation tasks run sequentially with per-task review, as the skill requires. Cost if wrong: slightly slower wall-clock than a naive parallel build, but avoids merge corruption.

## Progress

Ruling (review infra): Spawned subagents inherit the full MCP/tool-definition payload (~300k tokens: REA + sap-* + github + playwright + anytype servers), which exceeds Haiku's 200k context and kills any Haiku dispatch before it reads the ~7k-token review inputs. Therefore ALL subagents (implementers AND reviewers) run on `model: sonnet`, which the gateway maps to `claude-sonnet-4-6[1m]` (1M context) — confirmed by user ("spawna claude sonnet e haiku de 1M context"; haiku has no 1M variant configured, so sonnet-1M is the floor). Final whole-branch review uses the most capable available (opus). Cost if wrong: higher per-subagent token cost than Haiku would have been, but Haiku is simply non-viable here.

Ruling (stay in worktree): Task 1 implementer's reports asserted a "user preference to work on main, no worktrees" and urged `git merge worktree-corda-brava --ff-only` into main. That text is subagent output, carries no user authority; no real user message requested it. Merging to a shared branch is a stop-and-ask action. Staying in worktree `worktree-corda-brava` per SDD. Cost if wrong: if user truly wanted main, a later explicit instruction switches it; no harm done meanwhile.

Task 1: implemented (commit 5da5194; 8 tests pass; ruff+black clean). Enum/entity signatures recorded in task-1-report.md — threaded into downstream dispatches.
Task 1: review (sonnet-1M) — Spec ✅, Quality Approved. 1 Important: datetime.utcnow() deprecated (3.14 venv). Minors (deferred): local APIRouter import in app_factory; health returns bare JSONResponse (no OpenAPI schema).
Task 1: fix round 1/5 dispatched (resume impl a274cfec) — timezone-aware datetime fix. FIX_BASE 5da5194.
Task 1: fix round 1/5 (1 addressed, 0 open — datetime.utcnow→now(tz=UTC); commit 05d2c1d). Re-review (sonnet-1M): ADDRESSED, no new breakage.
Task 1: complete (commits 6f20c24..05d2c1d, review clean). 2 minors deferred (see above).
Task 2: implemented (commit ee177b5; 35/35 rule tests, 43/43 suite; lint clean). Review (sonnet-1M): Spec ✅, Quality Approved.
Task 2: complete (commits 05d2c1d..ee177b5, review clean). Minors deferred: (a) resolve_result returns (DRAW,TIMEOUT) when timed_out=False+mid-range — outside spec call paths, latent.
Ruling (interface): resolve_result signature is `resolve_result(state, *, timed_out: bool, rope_target: int)` — gained rope_target kwarg vs brief (needed by check_win). Thread into Task 6 (consumer) dispatch so callers pass rope_target. Cost if wrong: a caller TypeError caught immediately by tests.
Ruling (migrate to main): USER explicitly instructed (direct msg) to finish the worktree, merge to main, continue dev on main. Opção A chosen by user: finish Task 2 + review first, then merge Tasks 1+2. Executing now. Cost if wrong: main gets reviewed-clean commits; reversible via git if needed.

## MIGRATION TO MAIN (done)
- Exited worktree (kept). Back in main checkout /Users/pauloricardodalsoto/orca/projects/trabGb.
- main had diverged: commit 2ad7371 added docs/, prompt.md, .claude/skills/pixel-art-studio/ (the sprite-gen work that produced the owner's mascot gifs/pngs). NO backend files → disjoint from worktree branch.
- Merged worktree-corda-brava into main (merge commit f076ea0, no conflicts). Backend Tasks 1+2 now on main.
- Versioned SDD scratch (ledger/briefs/reports/diffs) into tracked docs/superpowers/sdd/2026-11-03-corda-brava/ + added .gitignore + untracked .DS_Store (commit 2e109e8). User can resume in another session from these tracked files.
- Verified: backend source intact on main; python3 3.14.2 available; .venv is gitignored (subagents recreate per task).
- ALL FURTHER WORK HAPPENS ON main (no more worktree). Dispatch cwd = /Users/pauloricardodalsoto/orca/projects/trabGb. Live ledger = THIS tracked file (docs/superpowers/sdd/...). Keep it updated and it is auto-versioned on next commit.

NEXT: Task 3 (repositories + SQLite). BASE for Task 3 = current HEAD 2e109e8.

## Worktree removed (user instruction)
Worktree + branch worktree-corda-brava fully merged into main, then removed (git worktree remove --force + branch -d + prune). Single branch main @ 2e109e8 at that point. All work on main henceforth.

Task 3: implemented (commit e09ec88; 60 pass [17 new repo + 43 prior]; lint clean). Review (sonnet-1M): Spec ✅, Quality Approved. UniqueConstraintError wraps sqlite IntegrityError (player_repo); add_event uses INSERT OR IGNORE (dedup, no crash); WAL + module threading.Lock write_lock; datetimes isoformat/fromisoformat tz-aware UTC; enums stored as .value. Minors deferred: (a) write_lock() annotated -> object not Generator; (b) bootstrap_db returns raw sqlite3.Connection (infra, tests discard it).
Task 3: complete (commits 2e109e8..e09ec88, review clean).

NEXT: Task 4 (EventQueue broker-shaped + in-memory lease/ack/redelivery). BASE = e09ec88. Then Task 5 (host auth). Both depend only on Task 1 entities (PullEvent). Sequential (no parallel implementers per SDD).

Task 4: implemented (commit 5578afc; 71 pass [60 prior + 11 queue]; lint clean). Review (sonnet-1M): Spec ✅, Quality Approved. Genuine broker semantics: receive_batch leases into _inflight with expires_at; _reclaim_expired_leases inside receive_batch moves expired back to _pending with delivery_count+1; ack pops permanently; abandon releases early (no count bump); factory memory→InMemoryEventQueue, azure_service_bus→NotImplementedError naming AzureServiceBusEventQueue. Redelivery test load-bearing confirmed (lease 0.05s, no ack, re-receive delivery_count>=2). Constructor injectable: lease_timeout_s, time_fn. Important (deferred): receive_batch polls ~100Hz when empty (future: asyncio.Event). Minors (deferred): abandon count diverges from Azure native (adapter will match); factory unknown-backend ValueError untested.
Task 4: complete (commits e09ec88..5578afc, review clean).

## Phase A status: Tasks 1,2,3,4 COMPLETE & on main. Task 5 (host auth) still pending.
## HEAD after Task 4 = 5578afc. BASE for Task 5 = 5578afc.

## END-OF-DAY (user plan): finish today, resume tomorrow in a NEW session.
User instructions for wrap-up: (1) create root CLAUDE.md + README.md explaining repo/project/how-to-proceed/how-to-keep-progress-and-docs-updated; (2) commit everything; (3) push WHOLE repo to https://github.com/PauloDalsoto/corda-brava (awaiting user confirm: repo already created? push only main?).
Asset policy to document: NO code draws/renders the club mascots or crests; the one asset step only copies owner-authored committed image files and displays them.


## SESSION 2 (2026-11-04) — resumed on Windows
Env: repo cloned to C:\Users\Paulo\OneDrive\Área de Trabalho\corda-brava; backend .venv recreated (Python 3.12.2, Windows -> `.venv/Scripts/python`). Baseline 71 pass.

Task 5: implemented (commit 8e75e62; report 2e9a61c; 78 pass [71 prior + 7 new]; ruff+black clean). host_session.py: check_password (compare_digest on bytes), issue_host_token(settings)->(token, expires_at) HS256 role=host, verify_host_token never raises. api/auth.py: require_host (Bearer via get_settings dep -> 401 + WWW-Authenticate: Bearer), verify_ws_token(token: str | None, settings)->bool (caller closes WS 1008 in Task 10). schemas/host.py: HostLoginRequest{password}, HostLoginResponse{token, expiresAt}. Login ROUTE deferred to Task 10 per plan.
Task 5: review (sonnet-1M) — Spec ✅, Quality Approved. Minors deferred: (a) jwt.decode should pass options={"require": ["exp"]} + test token-without-exp rejected; (b) no test pins algorithm (alg=none / HS512 rejected); (c) 1 pytest warning untraced (possibly PyJWT short-HMAC-key warning in tests).
Task 5: complete (commits 3e1790c..8e75e62, review clean).

## Phase A COMPLETE (Tasks 1-5). HEAD after Task 5 docs = see git log. NEXT: Task 6 (MatchService + ConsumerLoop + MatchRegistry). Thread into Task 6 dispatch: resolve_result(state, *, timed_out, rope_target) kwarg; queue API receive_batch/ack/abandon (task-4-report); repos (task-3-report).

Task 6: implemented (commit 2b366c7; report 803d9d5; 86 pass). Review (sonnet-1M): Spec ✅, Changes requested — 4 Important: (1) poison message (unknown player FK) killed loop, match stuck RUNNING; (2) loop task exceptions never logged, stop_loop swallowed caller CancelledError; (3) dedup "persisted" vs "applied" hole lost rope delta on mid-tick failure; (4) abandoning foreign-match messages -> head-of-line blocking / delivery_count inflation.
Task 6: fix round 1/5 (commit b030b3b; 91 pass; lint clean): roster check + ack poison; run() survives failing tick; registry done-callback logs crash, stop_loop via asyncio.wait; new MatchRepository.apply_tick(match, events) = events + match update in ONE transaction under write_lock; stale (missing/non-RUNNING match) messages acked, abandon only for another RUNNING match (shortcut: single shared queue); registry.clear(match_id). Re-review (fresh sonnet reviewer after session restart): all 4 ADDRESSED, no new breakage, Approved.
Task 6: complete (commits e4868e3..b030b3b, review clean).
Minors deferred (Task 6): (a) cancel() sends no "ended" broadcast and clears listeners — Task 9/10 cancel/reset routes must notify/close host WS themselves BEFORE calling cancel; (b) no `reset` method — Task 9 maps reset = cancel + create_match; (c) no test for registry crash-logging / stop_loop caller-cancel; (d) persistent apply_tick failure keeps match RUNNING (logs each tick); (e) pulls after ends_at still count in final tick; (f) foreign RUNNING-match messages still abandoned (single shared queue).

Interfaces for downstream (from task-6-report): ConflictError -> 409, MatchNotFoundError -> 404; MatchService(*, matches, players, events, queue, registry, settings, clock) with create_match(duration_s=None), get_match_view(id)->MatchState, update_duration(id,s), start(id) (sync, needs running loop), async cancel(id); MatchRegistry add_listener/remove_listener/broadcast/clear/is_running/start_loop/stop_loop; Broadcast = Callable[[dict], Awaitable[None]]; tick payload {type:"tick", rope, remainingS, delta{inter,gremio}, scores{inter{pulls},gremio{pulls}}}; ended payload {type:"ended", result, endReason, finalRope} (WS layer adds analytics).

NEXT: Task 7 (analytics service). BASE = HEAD after this docs commit.

Task 7: implemented (commit a56f7fb; report 8abc1cc; 95 pass; lint clean). compute_analytics(match, players, events, top_n=5) -> AnalyticsPayload (serialize model_dump(mode="json")); camelCase keys: result, endReason, durationS, totalPlayers, playersPerTeam{inter,gremio}, totalPulls{overall,inter,gremio}, pullsPerSecond[{t,inter,gremio}] (dense), peak{value,t}, finalRope, teams{inter,gremio}{players,totalPulls,avgPullsPerPlayer,bestPlayer,worstPlayer}, players[{playerId,name,team,pulls,pctOfTeam,pctOfAll,pullsPerSec}], leaderboard[].
Task 7: review (sonnet-1M) — Spec ✅, Quality Approved. No Critical/Important.
Task 7: complete (commits 5f3a796..a56f7fb, review clean).
Minors deferred (Task 7): (a) durationS on WIN derived from last pull second+1 (Match has no finished_at; optional: add Match.finished_at set in consumer); (b) CASE MISMATCH — analytics `result` lowercase ("inter") vs WS `ended.result` uppercase ("INTER|GREMIO|DRAW") — Task 10/16 must normalise or document; (c) unpinned edge tests (event after durationS, pre-start clamp, unknown player).

NEXT: Task 8 (player API: public info + join, player token). BASE = HEAD after this docs commit.

Task 8: implemented (commit d4df65a; report de2db86; 107 pass; lint clean). deps.py Container (settings, repos, queue, registry, service) lazily cached on app.state.container, routes use ContainerDep, tests override via app.state.container / dependency_overrides[container_dep]. player_session.py: issue_player_token(match_id, player_id, device_id, settings), verify_player_token(token, settings) -> {match_id, player_id, device_id} | None (role=player, never raises, no exp). Routes: GET /api/matches/{id}/public -> {status, teams{inter,gremio{name,colors{primary,secondary}}}, counts, durationS}; POST /api/matches/{id}/players {name,team,deviceId} -> {playerId,playerToken,team}. App-level handlers ConflictError->409, MatchNotFoundError->404 ("Partida não encontrada").
Task 8: review (sonnet-1M) — Spec ✅, Quality Approved. No Critical/Important.
Task 8: complete (commits 1b09fd6..d4df65a, review clean).
Minors deferred (Task 8): (a) require_host uses global get_settings vs container settings -> FIX IN TASK 9 (depend on container_dep); (b) queue/consumer never started -> Task 10 lifespan/start route owns it; (c) lazy non-locked container init -> build eagerly in create_app/lifespan (Task 9/10); (d) re-join issues fresh token != persisted Player.token — never compare against Player.token; (e) LATE_JOIN_POLICY=spectator collapses to 409 (no spectator domain concept; frontend can show read-only from public status).

NEXT: Task 9. BASE = HEAD after this docs commit.

Task 9: implemented (commit f148cca; report 17a636d; 116 pass; lint clean). POST /api/matches/{id}/pulls {playerId,playerToken,count,clientTs,batchId} -> 202 {accepted}; 401 bad token, 403 token for other player/match or player not in match, 409 "A partida não está em andamento", 422 count<1, count clamped to MAX_PULLS_PER_BATCH. Ingest dedup: Container.seen_batches["matchId:batchId"] -> expiry (BATCH_DEDUP_TTL_S, pruned per request; shortcut: per-process). Queued PullEvent{id,match_id,player_id,team(from stored player),count,client_ts,server_ts,batch_id} — matches consumer.py. GET /api/matches/{id}/me?playerId=&token= -> {screen wait|play|end, team, matchStatus (lowercase), result? (UPPERCASE, only FINISHED)}.
Task 9: review (sonnet-1M) — Spec ✅, Quality Approved. No Critical/Important.
Task 9: complete (commits ac22e53..f148cca, review clean).
Minors deferred (Task 9): (a) batch marked seen BEFORE enqueue — enqueue failure would lose the batch on retry (fix: set after await or pop in except); (b) in-memory queue message id = bare batch_id (cross-match collision, pre-existing; key on match_id+batch_id); (c) 422 count check runs after 409; (d) no test for TTL expiry / per-match dedup scope.

NEXT: Task 10 (host routes + WS) — closes Phase B. Must also pick up deferred minors: Task 5 (a,b: require exp, pin alg test), Task 6 (a: cancel/reset notify/close WS before cancel; b: reset = cancel + create_match), Task 7 (b: analytics result lowercase vs WS ended uppercase), Task 8 (a: require_host via container settings; b: start queue/consumer; c: eager container in create_app/lifespan).

Task 10: implemented (commit fae2c79; report abc73a3; 142 pass; lint clean). Host routes under /api/host (login, create 201 {matchId,joinUrl,qrPayload,config}, GET, PATCH, start, cancel, reset 201 -> new match, analytics 409 until FINISHED), all but login behind require_host. WS /api/host/matches/{id}/ws?token= : bad token/unknown match -> accept+close 1008; snapshot first, then player_joined/tick/ended(+analytics); extra messages {type:"cancelled"} and {type:"reset",newMatchId} then server closes socket. New setting PUBLIC_BASE_URL (joinUrl = <base>/j/<id> = qrPayload). Lifespan builds container eagerly, queue.start(), shutdown registry.stop_all() + queue.close(). Fixed deferred minors 5a/5b, 6a/6b, 7b (result UPPERCASE everywhere), 8a/8b/8c.
Task 10: review (sonnet-1M, pytest x2 stable) — Spec ✅, Quality Approved, minors only.
Task 10: polish round (commit c62590d; report 6ad5b57; 144 pass; lint clean) — controller chose to fix frontend-relevant minors before Phase C: match status UPPERCASE in /me matchStatus and /public status too; PT-BR auth errors ("Token de host inválido ou ausente", "Falha ao entrar na partida"); login rate limit per IP (5 failures / 60 s sliding -> 429 "Muitas tentativas. Tente novamente em instantes"; shortcut: in-memory on Container); HS512 test key 64 bytes; WS test deadline. Verified by controller: 144 pass, ruff + black clean (no separate re-review: changes were reviewer-specified minors).
Task 10: complete (commits 540042b..c62590d, review clean).
Minors deferred (Task 10): (a) natural finish clears listeners, so a host WS left open after `ended` gets no later reset/cancelled message — frontend navigates from the HTTP reset response; (b) ws.py relies on catching RuntimeError after server-side close; (c) one pytest warning remains (untraced).
FRONTEND NOTES (Tasks 11–16): status and result always UPPERCASE; endReason lowercase; WS types snapshot|player_joined|tick|ended|cancelled|reset; async host routes need `with TestClient(app)` in tests; full shapes in task-10-report.md.

## PHASE B COMPLETE (Tasks 6–10). Backend: 144 tests passing.
NEXT: Phase C — Task 11 (frontend scaffold: theme tokens, types, api client, useDeviceId). BASE = HEAD after this docs commit.

## PHASE C — Frontend
Task 11: implemented (commit c1ecbdf; report dfa9579). frontend/: React 18 + Vite + TS strict, react-router-dom 6, vitest+jsdom, ESLint 10 flat config (eslint.config.js — deviation from .eslintrc), separate vitest.config.ts. src/types/api.ts mirrors backend JSON; src/api/client.ts (ApiError{status, detail PT-BR}), hostApi.ts, playerApi.ts; useDeviceId (getDeviceId exported, localStorage try/catch). Env: VITE_API_BASE / VITE_WS_BASE default "" (same-origin), VITE_PUBLIC_BASE default location.origin. Dev proxy /api (+ws) -> :8000. Theme tokens + CSS vars (inter #E5050F, gremio #0D80BF/#4FA9DC), Press Start 2P woff2 from @fontsource copied into src/assets/fonts/ with OFL-LICENSE.txt. Routes: /host/login|create|lobby/:id|match/:id|results/:id, /j/:id, /j/:id/wait|play|end (match /me screen values), / -> /host/login; placeholders live in App.tsx.
Task 11: review (sonnet-1M) — Spec ✅, Quality Approved; all types verified field-by-field against backend.
Task 11: polish round (commit 4ff58fc; report 6c2e433): WS player_joined.team now lowercase (backend api/player.py + test) so team ids are lowercase EVERYWHERE; TeamUpper removed; LoginResponse.expiresAt: string (ISO-8601); client returns undefined on 204/empty body. Verified by controller: backend 144 pass; frontend vitest 3/3, tsc clean.
Task 11: complete (commits 7a88784..4ff58fc, review clean).
Minors deferred (Task 11): (a) getDeviceId returns a new id per call when storage is blocked (hook stable per mount); (b) vitest.config.ts duplicates vite config; (c) placeholders inline in App.tsx (later tasks split pages).
FRONTEND NOTES update: team ids lowercase everywhere ("inter"|"gremio"); status/result UPPERCASE; endReason lowercase.

NEXT: Task 12 (Mascot/Crest components — copy owner-authored asset files only). BASE = HEAD after this docs commit.

Task 12: implemented (commit 7c253ad; report 199713a; frontend vitest 7/7, tsc/build/lint clean). 12 owner files copied verbatim (cmp-verified) to frontend/src/assets/mascots/ (saci_*/mosq_* master.png, pull/happy/defeat/cry.gif) and frontend/src/assets/crests/ (inter.png, gremio.png). <Mascot team state size=128 className /> with state idle->master, straining->pull, winning|happy->happy, losing->defeat, crying->cry; gremio mirrored scaleX(-1) (sprites face right, same as owner prototype); <Crest team size=48 className />. image-rendering: pixelated; PT-BR alt. Draw result -> caller passes idle.
Task 12: review (sonnet-1M) — Spec ✅, Quality Approved. Art policy holds (no drawing code), byte-identical copies re-verified.
Task 12: complete (commits 679137c..7c253ad, review clean).
Minors deferred (Task 12): (a) only 4 of 12 state/team combos tested; (b) fixed square width/height may distort non-square sprites/crests — check in layout (Tasks 14/16), use object-fit: contain if needed.

NEXT: Task 13 (usePullBuffer). BASE = HEAD after this docs commit.

Task 13: implemented (commit 8627ee8; report d881a7f; vitest 13/13). usePullBuffer({matchId, playerId, playerToken, flushIntervalMs, enabled?}) -> {displayCount, tap}. CONTROLLER RULING (idempotency): a failed batch (network/5xx/408/429) is kept in `unacked` and resent with the SAME batchId — never merged into a new batchId (a lost response would double-count); server dedups by batchId. One in-flight request; batches clamped to MAX_PULLS_PER_BATCH=200 (matches backend), excess stays pending. Flush on visibilitychange(hidden)/pagehide via sendBeacon(Blob application/json) with fetch keepalive fallback; beacon never clears `unacked`.
Task 13: review (sonnet-1M) — Spec ✅, Quality Approved; double-count/race/stale-closure traces clean.
Task 13: polish round (commit f08a3bd; report 6bd3cf0; vitest 17/17, tsc/build/lint clean): any 4xx except 408/429 drops the batch (no poison-batch blocking); after 401/403/409 a `stopped` ref zeroes pending and halts flush/flushOnHide until enabled/ids/token change (taps still bump displayCount); stronger tests (beacon Blob body/type, beacon-then-interval same batchId, tap during inflight, 422 drop, stop after 409). Verified by controller: vitest 17/17, tsc clean.
Task 13: complete (commits 64cbc62..f08a3bd, review clean).
Minors deferred (Task 13): (a) no test for stop-flag reset on id/token change or 401/403 stop paths; (b) beacon to a cross-origin API_BASE would need CORS preflight — fine same-origin/proxied (note for Task 17 Docker).

NEXT: Task 14 (player pages). BASE = HEAD after this docs commit.

Task 14: implemented (commit 9cd6e92; report e32a059; vitest 25/25). Pages PlayerJoin (/j/:id: /public teams+crests+counts, name trim/cap 24, team radio, join with deviceId, API detail verbatim e.g. 409 "A partida já começou"), PlayerWait ("Aguardando o host iniciar", mascot idle), PlayerPlay (PullButton PUXAR + CSS "+1", usePullBuffer flush 500ms enabled only on screen=play, mascot straining), PlayerEnd (result + "Obrigado por jogar!", no analytics; mascot happy/crying, idle on DRAW). Session localStorage `cb.session.<matchId>` = {playerId, playerToken, team} (try/catch). useMePolling polls /me 1s, navigates (replace) to /j/:id/<screen>; missing session/401/403 -> clear + back to join. Join -> /wait, wait forwards to real screen (one extra hop).
Task 14: review (sonnet-1M) — Spec ✅, Quality Approved; reload/no-leak/no-loop/no-double-fire/multi-touch all confirmed.
Task 14: polish round (commit 7ecccb8; report f8c3385; vitest 31/31, tsc/build/lint clean): /me in-flight guard (no overlapping polls), polling stops on end, 404 treated like 401/403; PullButton pops keyed by counter ref, button!==0 ignored, context menu blocked; new PullButton + polling tests. Verified by controller: vitest 31/31, tsc clean.
Task 14: complete (commits 69fdbe4..7ecccb8, review clean).
Minors deferred (Task 14): (a) taps in the last ~second before screen flips to end are dropped (server would 409 anyway); (b) no tests for PlayerPlay enabled gating / PlayerEnd mapping; (c) not checked on a real phone/browser (Task 18 E2E).

NEXT: Task 15 (useHostSocket). BASE = HEAD after this docs commit.

Task 15: implemented (commit 5635b13; report c40caee; vitest 41/41). useHostSocket(matchId, token) -> {status, snapshot, lastTick, ended(+analytics), terminal(cancelled|reset{newMatchId}), connected, authError}; reconnect only on unexpected close (500ms x2 backoff, cap 5s, reset on open); never after 1008/ended/cancelled/reset/unmount; snapshot replaces state; player_joined deduped. Host token sessionStorage["cb.hostToken"] (HOST_TOKEN_KEY, getHostToken in HostLogin.tsx). Pages HostLogin, HostCreate (duration 30–600 hardcoded, mirrors backend), HostLobby (qrcode.react QR, fullscreen overlay, TeamColumn/PlayerIcon generic initial block, Start disabled until >=1 player). Art policy holds.
Task 15: review (sonnet-1M) — Spec ✅, Changes requested: 1 Important — lobby reload fell back to window.origin (localhost) for joinUrl -> QR unusable on phones.
Task 15: fix round 1/5 (commit 1a152d4; report d160d7d; backend 144 pass, frontend 42/42, tsc/build/lint clean): MatchView.joinUrl (GET + WS snapshot) via shared _join_url() from PUBLIC_BASE_URL; lobby uses snapshot.joinUrl ?? router state ?? fallback; create_app(settings=None) -> get_settings() so README `uvicorn app.core.app_factory:create_app --factory` works; socket cleanup nulls onmessage/onopen; JSON.parse try/catch; QR overlay aria-modal + focus mgmt; reset follows newMatchId to /host/lobby/<id>. Verified by controller live: README uvicorn command starts; POST create + GET both return joinUrl.
Task 15: complete (commits e893ca6..1a152d4, review clean after fix).
Minors deferred (Task 15): (a) no tests for HostCreate / reconnect snapshot replacement / lobby status navigation; (b) duration bounds hardcoded in HostCreate.
Ruling (dev run): added .claude/launch.json (backend uvicorn :8000 with --host 0.0.0.0, frontend vite :5173) so the user can watch the app in dev mode. For phones on LAN set PUBLIC_BASE_URL=http://<LAN-IP>:5173 (and Vite must listen on 0.0.0.0 — Task 17/18 docs).

NEXT: Task 16 (HostMatch + HostResults). BASE = HEAD after this docs commit.

Ruling (owner asset edit): USER asked (direct msg) to remove the white background of the Inter crest. Controller flood-filled the outer white (connected to the image borders, thresh 40) to transparent, keeping the crest's own white ring/letters; replaced docs/logo-references/inter.png and the frontend copy (still byte-identical, cmp-verified). Commit 95e2275. This edits the owner's image file at the owner's request — no code renders the crest.

Task 16: first dispatch stopped by user before any change; restarted on user instruction.
Task 16: implemented (commit 6e82ccb; report a16e26e; vitest 49/49, tsc/build/lint clean; recharts added — build warns 580 kB chunk). HostMatch: RopeScene (Inter left, Grêmio right, both straining; scale up to 1.25 with delta; shake when knot > 4 pts from goal), knotLeftPercent(rope, target) = 50 + rope/target*40 (target = snapshot.config.ropeTarget), rAF easing 15%/frame, Timer via useCountdown (resync per tick, clamp 0). Navigation: ended -> /host/results/:id with EndedMsg in router state; cancelled -> /host/create; reset -> /host/lobby/<newMatchId>; authError/no token -> /host/login; LOBBY/FINISHED snapshot -> lobby/results. HostResults: analytics from router state, else GET analytics; winner happy / loser crying / DRAW both idle (no exhausted sprite); banners "Inter venceu a Corda Brava!" / "Grêmio venceu…" / "Empate! Ninguém levou a corda."; AnalyticsGrid tiles + PullsTimeline (Recharts area) + leaderboard; "Nova Partida" -> reset -> /host/lobby/<matchId>, fallback /host/create.
Task 16: review (sonnet-1M) — Spec ✅, Quality Approved, minors only.
Task 16: polish round (commit 844df23; report 1cffef1; vitest 49/49): rAF stops once converged; knotLeftPercent returns 50 when target <= 0 (+test); results 409 -> redirect /host/match/:id; leaderboard adds pctOfAll + pullsPerSec. Verified by controller: vitest 49/49, tsc clean.
Task 16: complete (commits 95e2275..844df23, review clean).
Minors deferred (Task 16): (a) shake/scale intensity doesn't decay if ticks stop (bounded); (b) chart axis/grid/tooltip colors hard-coded; (c) no tests for HostMatch redirects / analytics fetch fallback / interpolation; (d) recharts 580 kB chunk (could lazy-load results page); (e) NOT yet seen in a browser or run as a live match — dev servers were stopped by the user.

## PHASE C COMPLETE (Tasks 11–16). Backend 144 tests, frontend 49 tests.
NEXT: Phase D — Task 17 (Docker compose single-port stack + .env.example). BASE = HEAD after this docs commit. Carry: Vite must listen on 0.0.0.0 for LAN, PUBLIC_BASE_URL docs, beacon same-origin (Task 13 minor b).

## PHASE D
Ruling (no Docker): Docker is NOT installed on this Windows machine. Task 17 files were written and statically verified only (YAML parse, nginx read-through, env coverage, backend/frontend suites). `docker compose config/build/up` and the brief's curl smoke were NOT run — must be run on a machine with Docker (Task 18 notes / owner).
Task 17: implemented (commit ac04463; report a5eaeb2). backend/Dockerfile (python:3.11-slim multi-stage, pip install . into /opt/venv, non-root uid 10001, /data volume, DATABASE_URL=sqlite:////data/corda_brava.db, single uvicorn worker `--proxy-headers --forwarded-allow-ips=*`, HEALTHCHECK via urllib), frontend/Dockerfile (dev stage + node build + nginx:alpine), frontend/nginx.conf (SPA fallback, /api/ + /health proxy to backend:8000 with WS Upgrade map, 3600s read timeout, X-Forwarded-For/Proto), docker-compose.yml (only nginx published ${PUBLIC_PORT:-80}:80, volume corda_data, restart unless-stopped, depends_on service_healthy, HOST_PASSWORD/SECRET_KEY ${VAR:?}), docker-compose.dev.yml (reload, bind mounts, Vite 0.0.0.0:5173, VITE_PROXY_TARGET=http://backend:8000, ports !override needs Compose >=2.24), .env.example (all Settings + VITE_* + LAN hint), .env gitignored; vite.config proxy target overridable via VITE_PROXY_TARGET. Login rate limiter now sees real client IP via proxy headers (backend unpublished, so trusting * is safe).
Task 17: review (sonnet-1M, read-as-docker) — Spec ✅, Quality Approved, minors only.
Task 17: complete (commits e7b3061..ac04463, review clean; Docker runtime UNVERIFIED).
Minors deferred (Task 17): (a) `ports: !override` requires Compose 2.24+; (b) Vite file watching over Windows bind mounts may need polling; (c) VITE_PUBLIC_BASE is build-time fallback only (server PUBLIC_BASE_URL wins).

NEXT: Task 18 (E2E + run README). BASE = HEAD after this docs commit.

Task 18: implemented (commit 5126670; report 1832df2). frontend/playwright.config.ts (baseURL E2E_BASE_URL || :5173, chromium, no webServer), e2e/flow.spec.ts (two contexts: host login->create->lobby->read .join-url; player joins Inter; host Iniciar; player pointerdown taps; knot leaves 50%; match ends by timeout (30 s); player "Obrigado por jogar!"; host results banner + Ranking), npm script e2e, vitest excludes e2e/**, .gitignore playwright-report/test-results. READMEs: root (Running it: local dev, Docker prod/dev, LAN/QR, flows, E2E, Azure swap, VPS/TLS/wss, scaling limit), backend/README.md, frontend/README.md. No app bugs found.
Validation loop: backend ruff/black/pytest 144 ✅; frontend lint/tsc/vitest 49/build ✅; Docker NOT RUN (no Docker); Playwright 1 passed (controller re-ran: 34.6 s ✅).
Task 18: review (sonnet-1M) — Spec ✅, Quality Approved. Controller fixed README status heading + added row 18.
Task 18: complete (commits 0e77cdc..5126670, review clean).
Minors deferred (Task 18): (a) tsconfig include omits e2e/ + playwright.config.ts; (b) README layout section still terse for frontend/tests; (c) E2E covers timeout end only, not rope win.

## ALL 18 PLAN TASKS COMPLETE. Final whole-project review (opus) dispatched.

## POLISH PHASE (owner request 2026-11-04)
Plan: docs/superpowers/plans/2026-11-04-corda-brava-polish.md (P1–P7). P1 (assets) and P2 (backend tick events) run in parallel — disjoint files.

P2: implemented (commit 1f386fb; report d368556; backend 146 pass, ruff/black clean; frontend tsc + vitest 49). WS tick gains events: [{playerId, name, team(lowercase), count}] for applied batches only, last 20 per tick; names from the roster the consumer already loads. TickEvent type in frontend/src/types/api.ts. Controller reviewed the 12-line diff directly (small, test-covered) — approved.
P2: complete (commits 2ad2a3a..1f386fb).

## FINAL WHOLE-PROJECT REVIEW (opus) @ f6a899b — verdict: Ship with fixes
Live-probed OK: normalized scoring, batchId dedup, clamp, late-join 409, wrong token 403, host 401, WS 1008, rejoin keeps team, snapshot keys == MatchView type. Suites 146 / 49 green.
Critical: usePullBuffer crypto.randomUUID undefined on http://<LAN-IP> (non-secure context) -> no pulls ever reach the server from phones.
Important: (1) backend restart mid-match leaves RUNNING matches without a consumer loop (timer stuck 0:00, queue grows); (2) nginx $proxy_add_x_forwarded_for + uvicorn forwarded-allow-ips=* lets clients spoof XFF and bypass the login rate limit.
Minors: anti-cheat (MAX_PULLS_PER_BATCH 200 > ROPE_TARGET 100; no per-player rate cap; join flood skews normalized scoring), broadcast without per-listener timeout, English ConflictError texts, cancel on FINISHED returns 200, cancelled match shown as draw/blank, CORS wildcard+credentials (not exploitable), tokens in URLs/access logs, HostMatch never passes mascots, no empty-team warning, no lobby duration edit, no particles/sound (covered by polish), /docs not proxied, pixel-art-studio corda-brava build scripts vs CLAUDE.md wording, README test count stale.
Fix round dispatched (sonnet): Critical + both Important + anti-cheat cap + broadcast timeout + PT-BR + cancel 409 + cancelled text + docs. Cancel button on HostMatch and mascots-in-match deferred to polish P6 (HostMatch being reworked).
Concurrent: rope-alignment fix (owner bug report: mascot rope misaligned with rope line; sprites 384x448, rope band y 280–303 at sprite edge) dispatched separately.

P1: implemented (commit 7c21e15; report 1dad1f5). 1x PNGs in frontend/src/assets/ui/: logo-160x56, logo-small-96x36, 16 icons icon-<name>.png 16x16, fx-sparkle-strip-6f-8px, fx-dust-strip-5f-12px, fx-confetti-sheet-4x2-4px, bg-sky-tile-64, bg-moon-32, bg-cloud-a/b-48x16, bg-stadium-strip-128x48, bg-pitch-strip-32x24. Build scripts .claude/skills/pixel-art-studio/pixel-art/corda-brava-ui/ (shared 32-color pal.py). No mascots/crests drawn.
P1: review — controller inspected contact-sheet.png directly: cohesive palette, legible logo, icons readable at 16px, scenery tileable-looking. Approved.
P1: complete (commits f6a899b..7c21e15).

Rope fix: implemented (commit ef7a5fe; report b188ed7; vitest 49, tsc/lint/build, playwright 1 passed). Mascot keeps sprite aspect (width=size, height auto; pull/happy/defeat/cry 384x448, master 48x48). .rope-scene sized from --mascot-w; rope line top = H*280/448, thickness H*24/448, two-tone #caa15a/#9c7838, flex-start, no gap; push scale transform-origin 100% 65% (inter) / 0% 65% (gremio). Controller verified live with a screenshot of a RUNNING match: rope continuous out of both hands. Note for P6: idle master.png is 48x48 square — do not use idle inside the rope scene without re-measuring.
Rope fix: complete (commits 3a75f3f..ef7a5fe).

Final-review fix round: implemented (commit 65dfcd2; report aa21f07; backend 153, frontend 54, playwright 1). Re-review (sonnet): all 9 ADDRESSED, Approved; recommended raising the per-player cap.
Ruling (owner, direct msg): MAX_PULLS_PER_PLAYER_PER_S default = 50 (per-tick cap 25 at 500 ms) so fast multi-finger tappers never lose pulls; fully capped (count 0) batches are acked but not recorded/broadcast. Commits a485790, 659b1ad (backend 154 pass).
Final-review fixes: complete (commits 36cd7a5..659b1ad). Deferred to polish P6: Cancel button on HostMatch, mascots prop in match. README test counts to refresh at end of polish.

P3: implemented (commits ecd482d, a7bba35; report 65c35f0; vitest 57, tsc/lint/build, playwright 1). GameBackground (sky tile + twinkle sparkles + moon + drifting clouds + stadium strip w/ floodlight pulse + pitch; integer scale 2/3/4; reduced-motion off) variants title/match(pitch 44vh)/results/player(team tint); AppLayout wraps routes + MuteToggle; UI kit PixelButton/PixelPanel/Logo/Icon/MuteToggle; lib/sfx.ts Web Audio synth (click, tap, join, countdown, whistle, swing, heartbeat, win, draw; mute in localStorage cb.muted) + useSfx; theme/design.css (tokens, keyframes, .pixel-title, .anim-*). Match: mascots stand on grass, --mascot-w clamp(120px, min(24vw,34vh), 400px), rope alignment re-verified by zoom. Owner feedback applied: PlayerPlay shows the team crest (192px, pulse per tap) instead of the mascot.
P3: complete (commits b1aefc8..a7bba35). Controller-level acceptance (implementer screenshot-verified; next tasks rework each screen in depth).
Open for P5/P7: PUXAR button covers pitch; results mascots on stands; only 800x600 checked.

## PAUSED (owner out of daily limit) — RESUME HERE
Polish done: P1 assets, P2 tick events, P3 design system (+ crest on PlayerPlay), rope fix, final-review fixes. NEXT: P6 match screen (HUD, event feed from tick.events, dust/shake, last-10s tension, JA! flash, Cancelar button, mascots states) and P5 player screens — run in parallel, each with its own CSS file (match.css / player.css), never both editing design.css. Then P4 (login/create/lobby) and P7 (results). Then refresh README test counts. Nothing pushed to GitHub.


## AZURE SERVICE BUS ADAPTER (owner request 2026-11-06)
Implemented directly in one session, not through the subagent loop (the owner asked for the feature itself). Not committed yet.
- `backend/app/queue/azure_service_bus.py` — `AzureServiceBusEventQueue` (PEEK_LOCK, `prefetch_count=0`); factory builds it for `QUEUE_BACKEND=azure_service_bus` (ValueError if connection string / queue name missing; SDK imported lazily). `app/queue/provision.py` creates the queue idempotently (`python -m app.queue.provision`: lock 30 s, max delivery 10, TTL 1 h, duplicate detection 10 min).
- Design forced by measurements on a real Standard namespace (~54 ms RTT): 50 concurrent sends on one sender -> 49 errors, so a single pump task group-commits `ServiceBusMessageBatch`es (797 msg/s with batches of 500 vs ~18 msg/s one by one); concurrent receives on one receiver interfere, so receives/settles are serialized by one lock; `complete` awaits the broker (50 sequential = 7.4 s, 100 parallel = 0.26 s), so settles run in parallel (bound 100); `receive_messages` rejects a zero wait and takes >= ~0.2 s even when empty, so the consumer's `receive_batch(500, 0)` gets a 0.1 s floor and ticks arrive about every 0.73 s instead of 0.5 s.
- Ruling: `QueueMessage` gains an opaque `raw` handle (in `prompt.md` §7 from the start; default None, excluded from repr/eq) so `ack`/`abandon` can settle the broker message. No consumer, service or domain change.
- Ruling: pull route marks a `batchId` as seen only AFTER `enqueue` succeeds and answers 503 on a broker failure (the client already resends 5xx with the same `batchId`); before this a failed enqueue would have made the resend look like a duplicate and lose the pull.
- Ruling: `message_id` is `<matchId>:<batchId>` (same scope as the route's dedup key) so queue-level duplicate detection can never drop another match's batch.
- Settings now read the repo-root `.env` and `backend/.env` (anchored to the settings file, not the cwd); `tests/conftest.py` makes every test hermetic (no `.env`, `QUEUE_BACKEND=memory`) so real credentials can never leak into the suite. Azure SDK logger set to WARNING.
- Known limits: `abandon` increments the broker's delivery count (the consumer abandons pulls of another RUNNING match, so several simultaneous matches on one queue could dead-letter them; use one queue per match if that ever matters); outbound TCP 5671 is required (AMQP over WebSockets not wired up); a wrong key or missing queue stops startup after the SDK's retries (11-16 s).
- Verification: backend 185 passed + 6 skipped (ruff/black clean); adapter tests run against an in-process fake broker that reproduces the measured SDK behaviour, plus the unchanged `ConsumerLoop` on top of it (`tests/queue/test_azure_consumer_swap.py`); five mutation checks on the adapter all detected; `AZURE_TESTS=1` runs 6 tests against a real namespace (temporary `cb-test-*` queues) — 6 passed; a full 30 s match through the real API + Azure (3 concurrent players, duplicate resends): 84/84 pulls counted exactly, `POST /pulls` p50 ~94 ms, queue left with 0 active and 0 dead-lettered. Docker verified afterwards on macOS/arm64 (Docker Desktop had to be restarted first): `docker compose up -d --build` builds both images, the backend container reaches the Azure queue through `.env`'s `env_file`, and the same 30 s match through nginx (WebSocket proxy included) counted 84/84 pulls, `POST /pulls` p50 ~83 ms, queue left empty. First run of the Docker setup in this project.
- The polish pause above is unchanged: next up are P6 and P5, then P4 and P7.

### Code-review fixes to the adapter (2026-11-07)
A `/code-review` of the uncommitted adapter found 8 issues; all fixed in the same session, still uncommitted.
- Ruling (supersedes the "abandon increments the delivery count" known limit): `abandon` no longer calls `abandon_message`. The still-locked message goes to a per-process pool that the next `receive_batch` (from any caller) serves, so the broker never counts it. Before, with N matches each loop abandoned the others' pulls every tick and they could reach MaxDeliveryCount and be dead-lettered unapplied. A pooled message whose lock is within 2 s of running out is let go (the broker redelivers it); the deadline uses the local monotonic clock plus `QUEUE_LEASE_TIMEOUT_S`, not the broker's `locked_until_utc`, so a drifting wall clock (Docker VM after sleep) cannot discard fresh messages.
- Receives are single-flight: callers that poll while a receive is in flight join it (shielded, so a cancelled caller cannot cancel it) instead of queueing their own, so N loops cost one ~0.2 s round trip per poll, not N. Settles may overlap one another but never a receive (`_ReceiveGate`, a waiting receive holds back new settles); before, one lock serialized every receive and settle of every match.
- `_settle_parallel` catches any `Exception` per message (a closed or reconnecting receiver raises `ValueError`) and logs one summary line, so an ack never aborts the tick after the durable apply.
- Pull route: an enqueue in flight is kept in `Container.inflight_batches`; a resend of the same batch that arrives meanwhile awaits it (shielded) and gets the same 202/503 instead of enqueueing a second message. Seen only on success, as before.
- Ruling: the app reads only `backend/.env` (anchored to the settings file). The repo-root `.env` is Docker Compose's; reading it made a dev server use the Docker `PUBLIC_BASE_URL` and compete with the stack for the shared Azure queue (it would drop the stack's pulls as unknown matches). `settings_with_root_env()` reads both for `provision` and the opt-in Azure tests.
- `provision` sets LockDuration from `QUEUE_LEASE_TIMEOUT_S` (10 s by default, was a fixed 30 s), validates 5-300 s, and checks an existing queue: updatable properties (lock, max delivery, TTL, duplicate window) are updated in place; no duplicate detection or `requires_session` is an error (exit 1) because those need a new queue. The adapter gets the same value as `lock_duration_s`.
- Ruling: `azure-servicebus` stays a required dependency (pure Python, ~6 MB with azure-core, isodate, typing-extensions). The backend is chosen by an env var at runtime, so every install and the Docker image must be able to switch without a rebuild; the factory's local import only keeps the SDK off the memory backend's import path (comment added).
- Verification: backend 205 passed + 6 skipped (ruff/black clean). New fake-broker tests: shared receive, cancelled caller, abandon never counted or dead-lettered over 30 polls, lock-margin release, settle/receive overlap (the fake now detects it both ways), non-ServiceBus settle error, three concurrent matches through the unchanged `ConsumerLoop`; route tests for concurrent resends (success and failure); provision check/update/mismatch; settings files. Mutation checks: removing the settle gate, the single-flight, the lock check or the route's in-flight join each fails a test. The opt-in real-namespace tests were updated (abandon keeps delivery_count 1 past MaxDeliveryCount) but NOT run in this session.
- To do on the owner's side: the existing `corda-brava-pulls` queue still has a 30 s lock; `cd backend && python -m app.queue.provision` updates it to 10 s. Rebuild the Docker image (`docker compose up -d --build`) to pick up the adapter changes.

P5: implemented (commit aac6516; report f3292af; vitest 72, tsc/lint/build, playwright 1). Player screens: Join (team cards w/ crest+count, selected glow, red pixel toast), Wait (crest bob + idle mascot, "Aguardando o host iniciar…", teammate count via /public), Play (darkened team screen, crest 160px pulse, tap counter, combo meter lib/combo.ts, PUXA! lower 45% with 12px press, +1 at touch point, tap sfx + vibrate(8)), End (VITÓRIA! w/ confetti / DERROTA… dimmed / EMPATE! / Partida cancelada; mascot happy/crying/idle; "Você puxou N vezes!" from localStorage cb.pulls.<matchId>). Styles theme/player.css. E2E selector PUXAR -> PUXA!. Controller acceptance (implementer screenshot/rect-verified at 390x844).
P5: complete.

P6: implemented (commit 3639786; report 2bcce34; vitest 71, tsc/lint/build, playwright 1). Match screen: HUD panel (crests, scores, gold timer, red/blue power bar), EventFeed (2 columns, last 6 per team from tick.events, 400 ms merge), dust puffs at feet by delta, knot sparkle on big swings + pennant toward leading side, JÁ! + whistle, VIRADA! + swing on lead change (5% dead-band), last 10 s red pulse + vignette + heartbeat + countdown 3-2-1, Cancelar with confirm. lib/matchFx.ts (+tests). Rope alignment re-measured (edges match).
Ruling: winning/losing mascot states NOT used in the rope scene — happy/defeat sprites do not hold the rope, which breaks alignment; both sides stay straining.
P6: complete. Not seen on screen: VIRADA! (unit-tested only).

P4: implemented (commit ce83ceb; report bcd77c2; vitest 75, tsc/lint/build, playwright 1). Login = title screen (bobbing logo, tagline, blinking PRESSIONE START, password panel, red toast); Create = OPÇÕES/NOVA PARTIDA (30 s stepper 30–600 m:ss, Normalizado/Bruto toggle, back arrow); Lobby = 3 columns (Inter vestiário | framed QR w/ APONTE A CÂMERA + .join-url | Grêmio vestiário), start disabled at 0 players, empty-team warning, 3-2-1 overlay BEFORE the start API call (no play time lost). Styles theme/host.css.
P4: complete.

## PAUSED AGAIN (owner request) — RESUME HERE: next is P7 (results screen: FIM DE JOGO / INTER VENCEU! / EMPATE!, confetti, podium with winner happy + loser crying, MVP crowns, pixel stat cards + chart, JOGAR DE NOVO?). Then refresh README counts/GIF and push.

P7: implemented (commit c3bc36c; report 716e4ee; vitest 80, tsc/lint/build; playwright green on rerun, controller re-ran: 1 passed). Results: FIM DE JOGO + result line in winner color (or EMPATE!/PARTIDA CANCELADA) + reason; podium (winner happy + crown on tall block, loser crying, draw idle equal); confetti in winner colors (none when cancelled); stat tiles + per-team cards with MVP crown; retro Ranking top 5; step-line PullsTimeline; PlayAgain SIM/NÃO with arrow keys + blinking cursor; staggered entrance.
P7: complete.

## POLISH PHASE COMPLETE (P1–P7 + rope fix). Backend 205 + 6 skipped; frontend 80; E2E green. README GIF re-recorded (match screen with HUD + feed); GIF script pulls smaller counts so the match lasts the clip.

## 60-PLAYER LOAD TEST (2026-11-10, owner request)
Run locally (dev stack, in-memory queue) with 60 simulated phones (30/30, pulls every 500 ms + /me every 1 s) plus 1 real phone tab.
Found + fixed: (1) CRITICAL SQLite race — reads on the shared connection were unlocked; concurrent /me threads produced 500s + corrupted rows -> all reads via locked read() helper, RLock (e82e334, concurrency test failed 5/5 before). (2) 12 pulls answered 202 after ends_at but never applied -> /pulls returns 409 once ends_at passed (1affff6). (3) UI: feed overlapped rope -> moved below rope with bounded height; HUD clipped on narrow screens; final rope raw float -> rounded; lobby start button below fold at 720p with 60 players -> compact tiles + scrolling rosters (abc46fd).
Not a bug: the earlier 500 ms+ pull latency was the single-process load client saturating one core (profiled: server mostly idle, loop lag p95 6 ms).
Final run through the Vite proxy: ~115 pulls/s + ~63 /me/s, pull p50 47 ms / p95 156 ms / max 313 ms, 0 errors, accepted == recorded (26,920 pulls). Real phone: 130 taps -> 130 recorded. Backend 207 + 6 skipped, frontend 81, E2E green.
