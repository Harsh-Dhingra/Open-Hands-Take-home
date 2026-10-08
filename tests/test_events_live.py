"""SSE against a real uvicorn process: clients on separate connections."""

import json
import signal
import threading
import time

import httpx2 as httpx
import pytest
from helpers import Server

pytestmark = pytest.mark.slow


class Listener:
    """Reads an event stream on its own connection and records what arrives."""

    def __init__(self, url):
        self.events, self.comments = [], []
        self.closed = threading.Event()  # set when the server ends the stream
        self._stop = threading.Event()
        self._response = None
        self.error = None
        self._thread = threading.Thread(target=self._run, args=(url,), daemon=True)
        self._thread.start()

    def _run(self, url):
        current = {}
        try:
            with httpx.Client(timeout=httpx.Timeout(10, read=5.0)) as client:
                with client.stream("GET", url) as response:
                    self._response = response
                    for line in response.iter_lines():
                        if line.startswith(":"):
                            self.comments.append(line)
                        elif line == "":
                            if current:
                                self.events.append(current)
                            current = {}
                        else:
                            key, _, value = line.partition(": ")
                            current[key] = value
            self.closed.set()  # the server ended the stream
        except httpx.HTTPError as exc:  # dropped by close() or by the server shutting down
            self.error = exc

    def wait_for(self, predicate, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for event in list(self.events):
                if predicate(event):
                    return event
            time.sleep(0.02)
        return None

    def states(self):
        return [json.loads(e["data"])["version"] for e in self.events if e["event"] == "state"]

    def close(self):
        """Drop the connection from the client side."""
        self._stop.set()
        if self._response is not None:
            self._response.close()
        self._thread.join(timeout=0.2)  # the daemon thread ends on its own


def _server(tmp_path, keepalive):
    srv = Server(tmp_path / "games.db", SSE_KEEPALIVE_SECONDS=keepalive).start()
    try:
        yield srv
    finally:
        srv.stop(signal.SIGTERM)


@pytest.fixture
def server(tmp_path):
    """Keepalive/re-read every 30 s: within a test, only a real push can deliver a move."""
    yield from _server(tmp_path, "30")


@pytest.fixture
def fast_keepalive_server(tmp_path):
    yield from _server(tmp_path, "0.3")


def new_game(server):
    return httpx.post(f"{server.url}/games").json()["id"]


def move(server, gid, player, row, col):
    r = httpx.post(
        f"{server.url}/games/{gid}/moves", json={"player": player, "row": row, "col": col}
    )
    assert r.status_code == 200, r.text


def test_a_move_by_another_client_is_pushed_within_two_seconds(server):
    gid = new_game(server)
    listener = Listener(f"{server.url}/games/{gid}/events")
    try:
        assert listener.wait_for(lambda e: e["id"] == "0"), "no initial snapshot"
        start = time.time()
        move(server, gid, "X", 1, 1)
        pushed = listener.wait_for(lambda e: e["id"] == "1")
        assert pushed is not None and time.time() - start < 2
        assert json.loads(pushed["data"])["board"][1][1] == "X"
        assert listener.states() == [0, 1]  # no duplicates
    finally:
        listener.close()


def test_every_move_of_a_game_is_delivered_in_order_to_every_subscriber(server):
    gid = new_game(server)
    listeners = [Listener(f"{server.url}/games/{gid}/events") for _ in range(3)]
    try:
        for listener in listeners:
            assert listener.wait_for(lambda e: e["id"] == "0"), "no initial snapshot"
        for player, row, col in [("X", 0, 0), ("O", 1, 0), ("X", 0, 1)]:
            move(server, gid, player, row, col)
            time.sleep(0.05)
        for listener in listeners:
            assert listener.wait_for(lambda e: e["id"] == "3")
            assert listener.states() == [0, 1, 2, 3]
    finally:
        [listener.close() for listener in listeners]


def test_changes_in_another_game_are_not_delivered(server):
    a, b = new_game(server), new_game(server)
    listener = Listener(f"{server.url}/games/{a}/events")
    try:
        assert listener.wait_for(lambda e: e["id"] == "0")
        move(server, b, "X", 0, 0)
        move(server, b, "O", 1, 1)
        time.sleep(1.0)
        assert listener.states() == [0]  # only its own snapshot
    finally:
        listener.close()


def test_the_final_move_sends_end_and_the_server_closes_the_stream(server):
    gid = new_game(server)
    listener = Listener(f"{server.url}/games/{gid}/events")
    try:
        assert listener.wait_for(lambda e: e["id"] == "0")
        for player, row, col in [("X", 0, 0), ("O", 1, 0), ("X", 0, 1), ("O", 1, 1), ("X", 0, 2)]:
            move(server, gid, player, row, col)
        assert listener.wait_for(lambda e: e["event"] == "end")
        assert listener.closed.wait(2), "server did not close the stream"
        last_state = [e for e in listener.events if e["event"] == "state"][-1]
        assert json.loads(last_state["data"])["status"] == "won"
    finally:
        listener.close()


def test_idle_streams_receive_keepalive_comments(fast_keepalive_server):
    server = fast_keepalive_server
    gid = new_game(server)
    listener = Listener(f"{server.url}/games/{gid}/events")
    try:
        deadline = time.time() + 3
        while not listener.comments and time.time() < deadline:
            time.sleep(0.05)
        assert listener.comments and listener.comments[0].startswith(": keepalive")
        assert listener.states() == [0]
    finally:
        listener.close()
