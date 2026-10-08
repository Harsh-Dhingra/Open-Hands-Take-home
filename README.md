# Tic-Tac-Toe with a real backend

A tic-tac-toe service where the backend owns every rule (turns, valid moves, win and draw), games are stored in SQLite and survive a restart, and a small web page lets two people play from two tabs. See `Plan.md` for the scope and milestones.

## Run it and test it

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).

```bash
make install      # create the venv from uv.lock
make run          # http://localhost:8000  (API docs at /docs)
make test         # all tests + coverage gates
make lint         # ruff check + format check
```

`DB_PATH` (default `./tictactoe.db`) and `PORT` (default `8000`) configure the server; see `.env.example`. To play, open the page, press **New game**, and open the share link in a second tab (that tab plays O), or choose "Both" to play on one screen. Without the UI:

```bash
curl -X POST localhost:8000/games                      # -> {"id": "ab12cd34", ...}
curl -X POST localhost:8000/games/ab12cd34/moves \
     -H 'content-type: application/json' -d '{"player":"X","row":1,"col":1}'
curl localhost:8000/games/ab12cd34/moves               # full move history
```

## Design

- **`app/engine.py`: the rules, with no I/O.** A game is an append-only log of moves; the board, status, winner and winning line are derived from it. `apply_move` returns a new game and never mutates its input. The win check only looks at lines through the last move. Rows, columns and K are parameters, so the engine is already N×M / K-in-a-row (the API just doesn't expose it yet).
- **`app/store.py`: persistence behind a small interface.** The in-memory and SQLite repositories pass the same contract tests. SQLite stores only the move log (no status or winner columns), so state can never disagree with history. `update()` runs under `BEGIN IMMEDIATE`, so the read-modify-write is atomic across threads and processes, and a rejected move saves nothing. Constraints reject a duplicate move number or a cell played twice, and an illegal stored log raises `CorruptGame` instead of guessing.
- **`app/api.py` + `app/models.py`: HTTP only.** Errors have a single shape, `{"error": {"code", "message"}}`, with stable codes: 404 `not_found`; 409 `cell_taken`, `not_your_turn`, `game_over`; 422 `out_of_bounds`, `validation_error`.
- **`app/static/`: the UI renders server state only** and never decides anything; illegal clicks are sent and the server's message is shown. Two tabs stay in sync by polling.

## Tests

141 tests, 100% line and branch coverage. The engine has a hard 100% gate in `make test`. They cover every winning line for both players, draws, a win on the last cell beating a draw, every error path, a Hypothesis property test of invariants over random games, the whole API suite on both storage backends, SQLite constraints and rollback, racing writers, and restarting a real server process after both a graceful stop and `kill -9`.

## Beyond the requirements

Games survive restarts with a replayable move history (`GET /games/{id}/moves`), and the engine supports any board size. Two clients can play one game, and simultaneous writes are serialised safely (only one of two racing moves into the same cell is accepted).

## AI tools

<!-- TODO (your words): which tools, how you directed them, where you overrode them. -->

## What didn't go as planned / what I'd improve

<!-- TODO (your words). Known gaps to consider mentioning:
- Not built: board size/K options in the API, stale-move detection (`expected_version`), a replay view in the UI, Docker Compose, a computer opponent.
- Polling (1 s) instead of push updates.
- The UI has no automated browser test; I checked it by hand.
- CI has not run on GitHub yet.
-->
