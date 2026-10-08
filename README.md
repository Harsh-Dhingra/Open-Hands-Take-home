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

`DB_PATH` (default `./tictactoe.db`), `PORT` (default `8000`) and `SSE_KEEPALIVE_SECONDS` (default `5`) configure the server; see `.env.example`. To play, open the page, press **New game**, and open the share link in a second tab (that tab plays O), or choose "Both" to play on one screen. Without the UI:

```bash
curl -X POST localhost:8000/games                      # -> {"id": "ab12cd34", ...}
curl -X POST localhost:8000/games/ab12cd34/moves \
     -H 'content-type: application/json' -d '{"player":"X","row":1,"col":1}'
curl localhost:8000/games/ab12cd34/moves               # full move history
curl -X POST localhost:8000/games -H 'content-type: application/json' \
     -d '{"rows": 5, "cols": 7, "k": 4}'               # a 5x7 board, 4 in a row wins
curl localhost:8000/config                             # defaults and limits
```

### Optimistic concurrency

A move may include `expected_version`, the `version` of the game the client last saw (every game response carries it, and it equals the number of moves played):

```bash
curl -X POST localhost:8000/games/ab12cd34/moves -H 'content-type: application/json' \
     -d '{"player":"O","row":0,"col":0,"expected_version":1}'
```

If the game has moved on (or the number is wrong), the server answers `409 stale_version` with `{"error": {..., "current_version": 3}}` and changes nothing, so the client can refresh and decide again. The check happens inside the same atomic step as the write, in both storage backends. It is optional: leave it out (or send `null`) and the request behaves exactly as before. Versions belong to one game, so requests on different games never affect each other. The browser UI sends it on every move and, on a stale answer, shows the latest board instead of retrying for you.

### Live updates (Server-Sent Events)

`GET /games/{id}/events` is an event stream (use `curl -N`). It sends the current game as a `state` event straight away, another `state` event after every accepted move (`id:` is the game version, `data:` is the same JSON as `GET /games/{id}`), and `end` when the game finishes, after which the server closes the stream. An unknown game is a normal JSON 404. A reconnecting client that sends `Last-Event-ID` equal to the current version is not sent a duplicate. Idle streams get a `: keepalive` comment every `SSE_KEEPALIVE_SECONDS` (default 5).

How it works: a small in-process broker keyed by game id only wakes the streams of that game; each wake-up re-reads the game from the database, so what a client receives is never stale or out of order, and a move can't be missed between subscribing and reading. The same periodic re-read means a move saved by another worker process still arrives within a few seconds, though only a single process pushes instantly. The browser UI uses it instead of polling, and falls back to polling (and back again) if the stream drops. `make run` passes `--timeout-graceful-shutdown 3`, because an open stream would otherwise keep the server from stopping.

### Board options

| Field | Default | Allowed |
|---|---|---|
| `rows` | 3 | 3 to 20 |
| `cols` | 3 | 3 to 20 |
| `k` (in a row to win) | 3 | 3 up to the longer side |

No body (or `{}`) gives the classic 3×3. Wrong types or unknown fields return 422 `validation_error`; out-of-range values return 422 `invalid_config` with a message naming the field, and no game is created. A run longer than `k` still wins, and `winning_line` lists the whole run.

## Design

- **`app/engine.py`: the rules, with no I/O.** A game is an append-only log of moves; the board, status, winner and winning line are derived from it. `apply_move` returns a new game and never mutates its input. The win check only looks at lines through the last move. Rows, columns and K are parameters, so any N×M board with K-in-a-row works. Product limits (3 to 20, k at least 3) are enforced separately in `app/limits.py`, so the engine stays general while the service refuses degenerate or huge boards before allocating anything.
- **`app/store.py`: persistence behind a small interface.** The in-memory and SQLite repositories pass the same contract tests. SQLite stores only the move log (no status or winner columns), so state can never disagree with history. `update()` runs under `BEGIN IMMEDIATE`, so the read-modify-write is atomic across threads and processes, and a rejected move saves nothing. Constraints reject a duplicate move number or a cell played twice, and an illegal stored log raises `CorruptGame` instead of guessing.
- **`app/api.py` + `app/models.py`: HTTP only.** Errors have a single shape, `{"error": {"code", "message"}}`, with stable codes: 404 `not_found`; 409 `cell_taken`, `not_your_turn`, `game_over`, `stale_version`; 422 `out_of_bounds`, `invalid_config`, `validation_error`.
- **`app/events.py`: live updates.** The broker wakes subscribers per game; streams always re-read the repository (the source of truth).
- **`app/static/`: the UI renders server state only** and never decides anything; illegal clicks are sent and the server's message is shown. Two tabs stay in sync through the event stream.

## Tests

354 tests, 100% line and branch coverage. The engine has a hard 100% gate in `make test`. They cover every winning line for both players, draws, a win on the last cell beating a draw, every error path, a Hypothesis property test of invariants over random games, the whole API suite on both storage backends, SQLite constraints and rollback, racing writers, and restarting a real server process after both a graceful stop and `kill -9`.

## Beyond the requirements

Games survive restarts with a replayable move history (`GET /games/{id}/moves`), and boards can be any size from 3×3 to 20×20 with any K (the UI has size controls and scrolls wide boards on a phone). Win detection on rectangular boards is checked in all four directions, including against an independent brute-force scan on random games. Two clients can play one game: simultaneous writes are serialised safely, and `expected_version` lets a client that is looking at an outdated board be told so instead of having its move applied.

## AI tools

<!-- TODO (your words): which tools, how you directed them, where you overrode them. -->

## What didn't go as planned / what I'd improve

<!-- TODO (your words). Known gaps to consider mentioning:
- Not built: push across several server processes (they only converge through the periodic re-read), a replay view in the UI, Docker Compose, a computer opponent.
- The UI has no automated browser test; I checked it by hand.
- CI has not run on GitHub yet.
-->
