# Plan.md — Tic-Tac-Toe with a Real Backend

**Status:** M0–M5, B1 (configurable board) and B2-I (optimistic concurrency) complete (see git history). B2-II (SSE) and B3–B5 not started.

## 1. Objectives & Definition of Success

**Objective:** a tic-tac-toe service where the backend owns all state and rules, built in ≤2h in small, reviewable steps that show engineering depth, clear communication and deliberate use of AI.

**Done when (core):**
- Backend enforces turns, valid moves, win and draw; the UI holds no game logic.
- Playable via a game-style web page and `curl`/OpenAPI docs.
- `make test` green: engine branch coverage 100%, overall ≥85%.
- ≥8 meaningful commits/PRs; README covers run/test, design, extras, AI usage, what went wrong (own words).
- Recording with audio narrating decisions and where AI output was overridden.

## 2. Scope

**In (core):** game model, rules engine, REST API with typed errors, SQLite persistence, playable static UI, tests, CI, README.

**Backlog (Going Further — planned in detail only after core is complete):**
1. N×M board with K-in-a-row (engine written rule-agnostic from the start).
2. Optimistic concurrency for two clients + threaded race test (+ optional SSE).
3. Replay endpoint / UI over the move log.
4. Docker Compose one-command run.
5. Stretch: minimax bot.

**Out:** auth/accounts, matchmaking, animations/sound beyond a basic playable look, deployment.

## 3. Architecture

- **Stack:** Python 3.12, FastAPI + Pydantic, stdlib `sqlite3` (WAL, no ORM), pytest + Hypothesis + pytest-cov, ruff, Makefile, static HTML/CSS/vanilla JS.
- **State model:** `Game{id, rows, cols, k, status(in_progress|won|draw), winner, version}` plus append-only `Move{game_id, n, player, row, col}`. The board is derived by replaying moves; the log is the source of truth. Core uses 3×3/K=3 but the fields exist from day one so the backlog needs no migration.
- **Layers (dependencies point inward):**
  - `app/engine.py` — pure functions, no I/O: `new_game`, `apply_move`; win check scans only lines through the last move.
  - `app/store.py` — SQLite repo; `UNIQUE(game_id, n)` guards against duplicate moves.
  - `app/api.py` — FastAPI routes; maps domain errors to HTTP.
  - `app/models.py` — schemas and error codes.
  - `app/static/` (`index.html`, `app.js`, `style.css`) — renders server state only (incl. server-provided winning cells), polls.
- **Game response:** `board`, `next_player`, `status`, `winner`, `winning_line`, `version`.
- **API:** `POST /games`, `GET /games/{id}`, `POST /games/{id}/moves {player,row,col}`, `GET /games/{id}/moves`, `GET /games`, `GET /healthz`.
- **Errors (stable codes):** 404 `not_found`; 409 `cell_taken|not_your_turn|game_over`; 422 `out_of_bounds|invalid_config`.
- **Config:** DB path and port via env vars (`.env.example` committed with placeholders only).

## 4. Milestones (each ends in a green commit/PR)

**Core — do first, in order**

| # | Deliverable | Notes |
|---|---|---|
| M0 | Repo skeleton: `pyproject.toml`, Makefile, ruff, `.gitignore`, `.env.example`, CI, `CLAUDE.md`, this Plan.md | ~10 min |
| M1 | Engine: tests first, then `apply_move`, win/draw (3×3, k-aware signature) | pure, 100% coverage |
| M2 | API on in-memory repo + typed errors + API tests | repo behind an interface |
| M3 | SQLite store + move log; swap in; restart/replay tests | state survives restart |
| M4 | Playable game-style UI: new game/restart, clickable board with hover + X/O marks, turn indicator, win-line highlight, win/draw banner, inline errors, game id to share/rejoin (two tabs via polling) | responsive; no rules client-side; time-boxed ~20 min |
| M5 | README (own words), coverage gate, CI green, cleanup | then record/submit |

**Backlog — plan in detail only after M5**
B1 N×M/K generalisation + property tests · B2 optimistic concurrency (`expected_version`, 409 stale) + race test · B3 replay view · B4 Docker Compose · B5 minimax bot.

## 5. Testing Strategy

- **Game rules (engine, unit):** every row/column/both diagonals win for X and O; draw; win on the final cell beats draw; X moves first; alternating turns; no move after game over. Hypothesis: random legal sequences never produce invalid state and end in exactly one terminal state.
- **Edge cases:** out of bounds (negative and ≥ size), occupied cell, wrong player, unknown game id, malformed JSON/missing fields; backlog adds invalid config (K<1, K>dim).
- **API integration (`TestClient`, real app + temp DB):** full game to win and to draw over HTTP; every error code/status; response shape; history order; `healthz`.
- **Storage:** CRUD round-trip; moves ordered; `UNIQUE(game_id,n)` violation rejected; replaying stored log equals stored state; fresh DB created from schema.
- **Concurrency (backlog B2):** N threads post the same move to one game → exactly one 2xx, the rest 409; no gaps in `n`; repeat 20× to catch flakiness.
- **Restart:** play partway, recreate the app on the same DB file, assert identical state and history, then finish the game.
- **Gates:** `make lint test`; engine 100% branch, overall ≥85%; CI runs the same commands.

## 6. Git Workflow & Secrets

- `git init`; `main` plus short-lived branches `m1-engine`, `m2-api`, …; one PR (merge commit, no squash) per milestone; commits ≤ ~150 changed lines where practical.
- Conventional messages (`feat(engine): detect diagonal wins`, `test: …`, `chore: …`) with a body explaining *why*; tests land with or before the code; no "WIP" or mega commits.
- Review every AI diff before committing; note overrides in the PR description for the recording.
- **Secrets:** `.gitignore` in the first commit — `.env`, `*.db`, `*.sqlite*`, `.venv/`, `__pycache__/`, `.coverage`, `htmlcov/`, `.idea/`, `.DS_Store`. Commit only `.env.example` with placeholders. No API keys are needed; if any appear, read from env, never hardcode. Before pushing: review `git diff --cached`; `git ls-files | grep -E '\.env$|\.db$'` must be empty (optionally `gitleaks` in CI/pre-commit).
- Stage by path, never blind `git add -A`; never force-push `main`.

## Verification (end-to-end, after M5)
1. `make test` → green with coverage gates.
2. `make run`, open two tabs, play to win and to draw; hit each error code with `curl`.
3. Kill and restart the server mid-game; state and history intact.
4. `git log --oneline` shows ≥8 incremental commits; `git ls-files` contains no `.env`/`.db`.
