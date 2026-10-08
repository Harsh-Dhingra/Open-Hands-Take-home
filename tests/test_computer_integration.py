"""Games against the computer on a real SQLite file: persistence, races, live updates."""

import asyncio
import json
import random
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient
from helpers import Stream

from app.api import create_app
from app.engine import Move, replay
from app.store import InMemoryRepository, SqliteRepository


@pytest.fixture
def db(tmp_path):
    return tmp_path / "games.db"


@pytest.fixture
def client(db):
    return TestClient(create_app(SqliteRepository(db), rng=random.Random(5)))


def move(client, gid, player, row, col, version):
    body = {"player": player, "row": row, "col": col, "expected_version": version}
    return client.post(f"/games/{gid}/moves", json=body)


def db_rows(db, gid):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT n, player, row_idx, col_idx FROM moves WHERE game_id=? ORDER BY n", (gid,)
        ).fetchall()
    finally:
        conn.close()


def free(game):
    return [(r, c) for r, line in enumerate(game["board"]) for c, v in enumerate(line) if v is None]


def play_out(client, difficulty, human, seed):
    """A seeded random human plays a whole game; returns the final game."""
    rng = random.Random(seed)
    g = client.post(
        "/games", json={"opponent": "computer", "difficulty": difficulty, "human_plays": human}
    ).json()
    while g["status"] == "in_progress":
        assert g["next_player"] == human  # the computer never owes a move between turns
        r = move(client, g["id"], human, *rng.choice(free(g)), g["version"])
        assert r.status_code == 200, r.text
        g = r.json()
    return g


# --- whole games over HTTP, persisted -------------------------------------------------------


def test_thirty_hard_games_are_never_lost_and_what_is_stored_is_the_game(client, db):
    ids = []
    for i in range(30):
        human = "XO"[i % 2]
        g = play_out(client, "hard", human, seed=i)
        assert g["winner"] != human  # the human never beats Hard
        ids.append((g, human))
    for g, _ in ids:
        rows = db_rows(db, g["id"])
        assert [r[0] for r in rows] == list(range(1, len(rows) + 1))  # contiguous log
        history = client.get(f"/games/{g['id']}/moves").json()
        assert [(m["n"], m["player"], m["row"], m["col"]) for m in history] == rows
        replayed = replay(3, 3, 3, [Move(*r) for r in rows])  # the log alone gives the game
        assert g["board"] == [list(line) for line in replayed.board]
        assert g["version"] == len(rows) == replayed.version
    # A restart reads back exactly the same games.
    restarted = TestClient(create_app(SqliteRepository(db)))
    for g, _ in ids:
        assert restarted.get(f"/games/{g['id']}").json() == g


@pytest.mark.parametrize("difficulty", ["easy", "medium"])
def test_weaker_levels_also_play_whole_legal_games(client, db, difficulty):
    for i in range(10):
        g = play_out(client, difficulty, "XO"[i % 2], seed=100 + i)
        assert g["status"] in ("won", "draw")
        rows = db_rows(db, g["id"])
        assert replay(3, 3, 3, [Move(*r) for r in rows]).status == g["status"]


def test_a_computer_game_survives_a_restart_and_can_be_continued(client, db):
    g = client.post("/games", json={"opponent": "computer", "human_plays": "O"}).json()
    g = move(client, g["id"], "O", *free(g)[0], g["version"]).json()
    again = TestClient(create_app(SqliteRepository(db), rng=random.Random(9)))
    assert again.get(f"/games/{g['id']}").json() == g
    assert again.get(f"/games/{g['id']}").json()["opponent"]["computer_player"] == "X"
    r = move(again, g["id"], "O", *free(g)[0], g["version"])
    assert r.status_code == 200 and r.json()["version"] == g["version"] + 2


# --- concurrency -------------------------------------------------------------------------------


def run_threads(targets):
    threads = [threading.Thread(target=t) for t in targets]
    [t.start() for t in threads]
    [t.join() for t in threads]


def test_racing_requests_with_the_same_version_apply_one_turn_and_one_reply(client, db):
    gid = client.post("/games", json={"opponent": "computer", "difficulty": "hard"}).json()["id"]
    results = []

    def attempt(cell):
        r = move(client, gid, "X", *cell, 0)
        code = r.json()["error"]["code"] if r.status_code != 200 else None
        results.append((r.status_code, code))

    cells = [(r, c) for r in range(3) for c in range(3)] + [(1, 1)] * 11
    run_threads([lambda cell=cell: attempt(cell) for cell in cells[:20]])

    assert sorted(results) == [(200, None)] + [(409, "stale_version")] * 19
    rows = db_rows(db, gid)
    assert [(r[0], r[1]) for r in rows] == [(1, "X"), (2, "O")]  # one human move, one reply
    restarted = TestClient(create_app(SqliteRepository(db)))
    assert restarted.get(f"/games/{gid}").json() == client.get(f"/games/{gid}").json()


def test_concurrent_computer_games_do_not_interfere(client, db):
    ids = [client.post("/games", json={"opponent": "computer"}).json()["id"] for _ in range(10)]
    statuses = []
    run_threads(
        [lambda g=g: statuses.append(move(client, g, "X", 1, 1, 0).status_code) for g in ids]
    )
    assert statuses == [200] * 10
    for gid in ids:
        rows = db_rows(db, gid)
        assert [(r[0], r[1]) for r in rows] == [(1, "X"), (2, "O")]


# --- live updates -----------------------------------------------------------------------------


def test_a_turn_reaches_subscribers_as_one_event_carrying_both_moves():
    app = create_app(InMemoryRepository(), keepalive_seconds=30, rng=random.Random(1))
    client = TestClient(app)  # its handler runs in another thread, as under a real server
    gid = client.post("/games", json={"opponent": "computer", "difficulty": "hard"}).json()["id"]

    async def scenario():
        stream = Stream(app, f"/games/{gid}/events")
        first = await stream.next_event()
        assert first["id"] == "0" and json.loads(first["data"])["opponent"]["difficulty"] == "hard"
        r = await asyncio.to_thread(move, client, gid, "X", 1, 1, 0)
        assert r.status_code == 200
        pushed = await stream.next_event()
        state = json.loads(pushed["data"])
        assert pushed["id"] == "2" and state["version"] == 2  # human + computer in one event
        assert sum(v is not None for line in state["board"] for v in line) == 2
        await stream.no_event_for(0.3)  # no separate event for the half-finished turn
        stream.disconnect()
        await stream.task

    asyncio.run(scenario())
