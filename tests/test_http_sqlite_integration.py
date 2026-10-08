"""API + real SQLite file (temp DB) integration: behaviour no unit layer can show."""

import random
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.engine import Move, replay
from app.store import SqliteRepository


@pytest.fixture
def db(tmp_path):
    return tmp_path / "games.db"


@pytest.fixture
def client(db):
    return TestClient(create_app(SqliteRepository(db)), raise_server_exceptions=False)


def post_move(client, gid, player, row, col, expected_version=None):
    body = {"player": player, "row": row, "col": col}
    if expected_version is not None:
        body["expected_version"] = expected_version
    return client.post(f"/games/{gid}/moves", json=body)


def db_rows(db, gid):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT n, player, row_idx, col_idx FROM moves WHERE game_id=? ORDER BY n", (gid,)
        ).fetchall()
    finally:
        conn.close()


def run_threads(targets):
    threads = [threading.Thread(target=t) for t in targets]
    [t.start() for t in threads]
    [t.join() for t in threads]


def test_concurrent_http_moves_exactly_one_is_accepted(client, db):
    gid = client.post("/games").json()["id"]
    statuses, codes = [], []

    def attempt():
        r = post_move(client, gid, "X", 1, 1)
        statuses.append(r.status_code)
        if r.status_code != 200:
            codes.append(r.json()["error"]["code"])

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert sorted(statuses) == [200] + [409] * 19
    assert set(codes) <= {"not_your_turn", "cell_taken"}
    assert len(client.get(f"/games/{gid}/moves").json()) == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM moves").fetchone() == (1,)
    conn.close()


def test_corrupt_stored_log_is_refused_not_guessed(client, db):
    good = client.post("/games").json()["id"]
    bad = client.post("/games").json()["id"]
    conn = sqlite3.connect(db)
    # X moves twice in a row: not a legal game.
    conn.executemany("INSERT INTO moves VALUES (?, ?, 'X', ?, ?)", [(bad, 1, 0, 0), (bad, 2, 1, 1)])
    conn.commit()
    conn.close()

    assert client.get(f"/games/{bad}").status_code == 500
    assert post_move(client, bad, "O", 2, 2).status_code == 500
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM moves WHERE game_id=?", (bad,)).fetchone() == (2,)
    conn.close()
    # Other games are unaffected.
    assert post_move(client, good, "X", 0, 0).status_code == 200


# --- optimistic concurrency, persisted results -------------------------------


def test_same_version_race_applies_exactly_one_and_persists_it(client, db):
    gid = client.post("/games").json()["id"]
    results = []

    def attempt(cell):  # every client saw version 0 and wants a different cell
        r = post_move(client, gid, "X", *cell, expected_version=0)
        code = r.json()["error"]["code"] if r.status_code != 200 else None
        results.append((r.status_code, code))

    cells = [(r, c) for r in range(3) for c in range(3)] + [(0, 0)] * 11
    run_threads([lambda cell=cell: attempt(cell) for cell in cells[:20]])

    assert sorted(results) == [(200, None)] + [(409, "stale_version")] * 19
    rows = db_rows(db, gid)
    assert [r[0] for r in rows] == [1] and rows[0][1] == "X"
    assert client.get(f"/games/{gid}").json()["version"] == 1

    restarted = TestClient(create_app(SqliteRepository(db)))  # restart: same state from disk
    assert restarted.get(f"/games/{gid}").json() == client.get(f"/games/{gid}").json()


def test_two_clients_chaining_versions_finish_a_game_with_matching_rows(client, db):
    gid = client.post("/games").json()["id"]
    order = [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)]  # X wins on the fifth move
    seen = {"X": 0, "O": 0}  # each client's last-seen version
    for i, (row, col) in enumerate(order):
        me = "XO"[i % 2]
        r = post_move(client, gid, me, row, col, expected_version=seen[me])
        if r.status_code == 409:  # the other client moved since I looked: refresh, retry
            assert r.json()["error"]["code"] == "stale_version"
            seen[me] = r.json()["error"]["current_version"]
            r = post_move(client, gid, me, row, col, expected_version=seen[me])
        assert r.status_code == 200, r.text
        seen[me] = r.json()["version"]
    final = client.get(f"/games/{gid}").json()
    history = client.get(f"/games/{gid}/moves").json()
    rows = db_rows(db, gid)
    assert final["status"] == "won" and final["version"] == len(rows) == len(history) == 5
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5]
    assert [(m["n"], m["player"], m["row"], m["col"]) for m in history] == rows


# --- separate games never conflict logically ------------------------------


def test_one_move_each_on_twenty_games_concurrently_all_succeed(client, db):
    ids = [client.post("/games").json()["id"] for _ in range(20)]
    statuses = []

    def one_move(game_id):
        r = post_move(client, game_id, "X", 1, 1, expected_version=0)
        statuses.append(r.status_code)

    run_threads([lambda g=g: one_move(g) for g in ids])
    assert statuses == [200] * 20  # same cell, same version, different games: no collision
    assert [client.get(f"/games/{g}").json()["version"] for g in ids] == [1] * 20
    assert all(len(db_rows(db, g)) == 1 for g in ids)


def test_interleaved_games_each_match_an_independent_replay(client, db):
    ids = [client.post("/games").json()["id"] for _ in range(8)]
    failures = []

    def play(gid):
        cells = [(r, c) for r in range(3) for c in range(3)]
        rng_local = random.Random(gid)
        rng_local.shuffle(cells)
        version = 0
        for i, (row, col) in enumerate(cells):
            r = post_move(client, gid, "XO"[i % 2], row, col, expected_version=version)
            if r.status_code != 200:
                failures.append((gid, r.text))
                return
            version = r.json()["version"]
            if r.json()["status"] != "in_progress":
                return

    run_threads([lambda g=g: play(g) for g in ids])
    assert not failures, failures
    for gid in ids:
        rows = db_rows(db, gid)
        assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
        independent = replay(3, 3, 3, [Move(*r) for r in rows])
        api = client.get(f"/games/{gid}").json()
        assert api["version"] == len(rows)
        assert api["status"] == independent.status and api["winner"] == independent.winner
        assert api["board"] == [list(line) for line in independent.board]


def test_failures_and_versions_in_one_game_do_not_leak_into_another(client, db):
    a = client.post("/games").json()["id"]
    b = client.post("/games").json()["id"]
    for player, row, col in [("X", 0, 0), ("O", 1, 1), ("X", 2, 2)]:
        assert post_move(client, a, player, row, col).status_code == 200  # a is at version 3

    # a's version is meaningless for b: it is simply stale there.
    r = post_move(client, b, "X", 0, 0, expected_version=3)
    assert r.status_code == 409 and r.json()["error"]["current_version"] == 0
    # A rule error and a stale error in a leave b untouched.
    assert post_move(client, a, "O", 0, 0).json()["error"]["code"] == "cell_taken"
    assert post_move(client, a, "O", 0, 1, expected_version=0).status_code == 409
    assert client.get(f"/games/{b}").json()["version"] == 0 and db_rows(db, b) == []
    # The very same cell and version are fine in b.
    assert post_move(client, b, "X", 0, 0, expected_version=0).status_code == 200
    assert len(db_rows(db, a)) == 3 and len(db_rows(db, b)) == 1
