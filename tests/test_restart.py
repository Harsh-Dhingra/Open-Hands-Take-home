import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.store import SqliteRepository

ROOT = Path(__file__).resolve().parent.parent
MOVES = [("X", 0, 0), ("O", 1, 0), ("X", 0, 1), ("O", 1, 1)]


def post_move(client, gid, player, row, col):
    return client.post(f"/games/{gid}/moves", json={"player": player, "row": row, "col": col})


def boot(db):
    """A fresh app instance on the same file == a server restart."""
    return TestClient(create_app(SqliteRepository(db)))


# --- in-process restart ------------------------------------------------------


def test_state_and_history_survive_restart(tmp_path):
    db = tmp_path / "games.db"
    first = boot(db)
    gid = first.post("/games").json()["id"]
    for m in MOVES:
        assert post_move(first, gid, *m).status_code == 200
    state, history = first.get(f"/games/{gid}").json(), first.get(f"/games/{gid}/moves").json()
    del first

    second = boot(db)
    assert second.get(f"/games/{gid}").json() == state
    assert second.get(f"/games/{gid}/moves").json() == history
    assert gid in [g["id"] for g in second.get("/games").json()]
    assert state["version"] == 4 and state["next_player"] == "X"

    # Play on after the restart and finish the game.
    assert post_move(second, gid, "X", 0, 2).json()["status"] == "won"

    third = boot(db)
    final = third.get(f"/games/{gid}").json()
    assert final["status"] == "won" and final["winner"] == "X"
    assert final["winning_line"] == [[0, 0], [0, 1], [0, 2]]
    assert post_move(third, gid, "O", 2, 2).json()["error"]["code"] == "game_over"
    assert len(third.get(f"/games/{gid}/moves").json()) == 5


def test_draw_survives_restart(tmp_path):
    db = tmp_path / "games.db"
    draw = [(0, 0), (0, 1), (0, 2), (1, 1), (1, 0), (1, 2), (2, 1), (2, 0), (2, 2)]
    first = boot(db)
    gid = first.post("/games").json()["id"]
    for i, (row, col) in enumerate(draw):
        assert post_move(first, gid, "XO"[i % 2], row, col).status_code == 200

    after = boot(db)
    state = after.get(f"/games/{gid}").json()
    assert state["status"] == "draw" and state["winner"] is None and state["next_player"] is None
    assert post_move(after, gid, "X", 0, 0).json()["error"]["code"] == "game_over"


def test_create_app_defaults_to_db_path_env(tmp_path, monkeypatch):
    db = tmp_path / "from_env.db"
    monkeypatch.setenv("DB_PATH", str(db))
    gid = TestClient(create_app()).post("/games").json()["id"]
    assert db.exists()
    assert TestClient(create_app()).get(f"/games/{gid}").status_code == 200


# --- real server process -----------------------------------------------------


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, db):
        self.db, self.port, self.proc = db, free_port(), None
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--factory",
                "app.api:create_app",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],  # fmt: skip
            cwd=ROOT,
            env={**os.environ, "DB_PATH": str(self.db)},
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.url}/healthz").status_code == 200:
                    return self
            except httpx.TransportError:
                time.sleep(0.1)
        self.stop(signal.SIGKILL)
        raise RuntimeError("server did not start")

    def stop(self, sig):
        self.proc.send_signal(sig)
        self.proc.wait(timeout=10)


@pytest.mark.slow
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGKILL], ids=["graceful", "kill-9"])
def test_real_server_restart_keeps_acknowledged_moves(tmp_path, sig):
    server = Server(tmp_path / "games.db").start()
    try:
        gid = httpx.post(f"{server.url}/games").json()["id"]
        for player, row, col in MOVES:
            r = httpx.post(
                f"{server.url}/games/{gid}/moves", json={"player": player, "row": row, "col": col}
            )
            assert r.status_code == 200
        before = httpx.get(f"{server.url}/games/{gid}").json()
    finally:
        server.stop(sig)

    server = Server(tmp_path / "games.db").start()
    try:
        assert httpx.get(f"{server.url}/games/{gid}").json() == before
        r = httpx.post(f"{server.url}/games/{gid}/moves", json={"player": "X", "row": 0, "col": 2})
        assert r.json()["status"] == "won"
    finally:
        server.stop(signal.SIGTERM)
