"""API + real SQLite file (temp DB) integration: behaviour no unit layer can show."""

import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.store import SqliteRepository


@pytest.fixture
def db(tmp_path):
    return tmp_path / "games.db"


@pytest.fixture
def client(db):
    return TestClient(create_app(SqliteRepository(db)), raise_server_exceptions=False)


def post_move(client, gid, player, row, col):
    return client.post(f"/games/{gid}/moves", json={"player": player, "row": row, "col": col})


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
