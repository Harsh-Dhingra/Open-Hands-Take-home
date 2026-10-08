"""SSE: broker unit tests, the endpoint's wire format, and in-process streaming."""

import asyncio
import json
import threading

import pytest

from app.api import create_app
from app.engine import apply_move
from app.events import EventBroker
from app.store import InMemoryRepository

WIN_X = [("X", 0, 0), ("O", 1, 0), ("X", 0, 1), ("O", 1, 1), ("X", 0, 2)]


def parse_sse(text):
    """Split an SSE body into messages: {"event","id","data"} or {"comment"}."""
    out = []
    for block in text.strip().split("\n\n"):
        msg = {}
        for line in block.splitlines():
            if line.startswith(":"):
                msg["comment"] = line[1:].strip()
            else:
                key, _, value = line.partition(": ")
                msg[key] = value
        out.append(msg)
    return out


def play(client, gid, moves):
    for player, row, col in moves:
        assert (
            client.post(
                f"/games/{gid}/moves", json={"player": player, "row": row, "col": col}
            ).status_code
            == 200
        )


# --- broker --------------------------------------------------------------


def test_publish_wakes_only_subscribers_of_that_game():
    async def scenario():
        broker = EventBroker()
        a1, a2, b = broker.subscribe("a"), broker.subscribe("a"), broker.subscribe("b")
        broker.publish("a")
        await asyncio.wait_for(a1.wakeup.wait(), 1)
        await asyncio.wait_for(a2.wakeup.wait(), 1)
        await asyncio.sleep(0.05)
        assert not b.wakeup.is_set()  # game b was not touched

    asyncio.run(scenario())


def test_publish_from_another_thread_wakes_the_subscriber():
    async def scenario():
        broker = EventBroker()
        sub = broker.subscribe("g")
        threading.Thread(target=broker.publish, args=("g",)).start()  # like a threadpool handler
        await asyncio.wait_for(sub.wakeup.wait(), 1)

    asyncio.run(scenario())


def test_unsubscribe_removes_and_is_idempotent_and_publish_without_subscribers_is_a_noop():
    async def scenario():
        broker = EventBroker()
        s1, s2 = broker.subscribe("g"), broker.subscribe("g")
        assert broker.subscriber_count("g") == 2 and broker.subscriber_count() == 2
        broker.unsubscribe("g", s1)
        broker.unsubscribe("g", s1)
        assert broker.subscriber_count("g") == 1
        broker.unsubscribe("g", s2)
        broker.unsubscribe("g", s2)  # game already has no entry
        assert broker.subscriber_count() == 0
        broker.publish("g")
        broker.publish("never-subscribed")

    asyncio.run(scenario())


def test_publish_after_the_subscribers_loop_closed_does_not_raise():
    broker = EventBroker()

    async def subscribe_and_leave():
        broker.subscribe("g")  # never unsubscribed: the loop just ends

    asyncio.run(subscribe_and_leave())
    broker.publish("g")


# --- endpoint wire format (finite streams, both storage backends) -----------


def test_finished_game_streams_snapshot_then_end_and_closes(client):
    gid = client.post("/games").json()["id"]
    play(client, gid, WIN_X)
    r = client.get(f"/games/{gid}/events")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    state, end = parse_sse(r.text)
    assert state["event"] == "state" and state["id"] == "5"
    assert json.loads(state["data"]) == client.get(f"/games/{gid}").json()
    assert end["event"] == "end"


def test_unknown_game_is_a_json_404_not_a_stream(client):
    r = client.get("/games/nope/events")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_last_event_id_equal_to_current_version_skips_the_duplicate_snapshot(client):
    gid = client.post("/games").json()["id"]
    play(client, gid, WIN_X)
    r = client.get(f"/games/{gid}/events", headers={"Last-Event-ID": "5"})
    assert [m["event"] for m in parse_sse(r.text)] == ["end"]


@pytest.mark.parametrize("header", ["abc", "", "4"])
def test_other_last_event_ids_still_get_the_current_snapshot(client, header):
    gid = client.post("/games").json()["id"]
    play(client, gid, WIN_X)
    r = client.get(f"/games/{gid}/events", headers={"Last-Event-ID": header})
    assert [m["event"] for m in parse_sse(r.text)] == ["state", "end"]


# --- in-process streaming (minimal ASGI client) ------------------------------


class Stream:
    """Drives the ASGI app directly so a stream can be read, then disconnected."""

    def __init__(self, app, path, headers=None, spec_version="2.3"):
        self.chunks: asyncio.Queue[str] = asyncio.Queue()
        self.disconnected = asyncio.Event()
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": spec_version},
            "http_version": "1.1", "method": "GET", "path": path, "raw_path": path.encode(),
            "query_string": b"", "scheme": "http", "server": ("t", 80), "client": ("t", 1),
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        }  # fmt: skip
        self.task = asyncio.create_task(app(scope, self._receive, self._send))

    async def _receive(self):
        await self.disconnected.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        if self.disconnected.is_set():
            raise OSError("client went away")  # what servers do for ASGI spec >= 2.4
        if message["type"] == "http.response.body" and message.get("body"):
            await self.chunks.put(message["body"].decode())

    async def next(self, timeout=1.0):
        return await asyncio.wait_for(self.chunks.get(), timeout)

    async def next_event(self, timeout=1.0):
        """Next non-comment message."""
        while True:
            msg = parse_sse(await self.next(timeout))[0]
            if "comment" not in msg:
                return msg

    async def no_event_for(self, seconds):
        """Assert nothing but keepalive comments arrive for `seconds`."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while (left := deadline - loop.time()) > 0:
            try:
                msg = parse_sse(await self.next(left))[0]
            except TimeoutError:
                return
            assert "comment" in msg, f"unexpected event {msg}"

    def disconnect(self):
        self.disconnected.set()


def make_app(keepalive_seconds=0.05):
    repo = InMemoryRepository()
    return create_app(repo, keepalive_seconds=keepalive_seconds), repo


def test_a_move_is_pushed_to_the_streaming_client():
    # Keepalive/re-read is 30 s, so only the broker's publish can deliver this in time.
    async def scenario():
        app, repo = make_app(keepalive_seconds=30)
        gid, _ = repo.create()
        stream = Stream(app, f"/games/{gid}/events")
        first = await stream.next_event()
        assert (first["event"], first["id"]) == ("state", "0")

        repo.update(gid, lambda g: apply_move(g, "X", 1, 1))
        app.state.broker.publish(gid)
        pushed = await stream.next_event()
        assert pushed["event"] == "state" and pushed["id"] == "1"
        assert json.loads(pushed["data"])["board"][1][1] == "X"
        stream.disconnect()
        await stream.task

    asyncio.run(scenario())


def test_a_change_without_a_publish_still_arrives_via_the_periodic_reread():
    # Another worker process saved the move; this process was never told.
    async def scenario():
        app, repo = make_app()
        gid, _ = repo.create()
        stream = Stream(app, f"/games/{gid}/events")
        await stream.next_event()
        repo.update(gid, lambda g: apply_move(g, "X", 0, 0))
        assert (await stream.next_event())["id"] == "1"
        stream.disconnect()
        await stream.task

    asyncio.run(scenario())


def test_other_games_changes_are_not_delivered():
    async def scenario():
        app, repo = make_app()
        a, _ = repo.create()
        b, _ = repo.create()
        stream = Stream(app, f"/games/{a}/events")
        await stream.next_event()
        repo.update(b, lambda g: apply_move(g, "X", 0, 0))
        app.state.broker.publish(b)
        await stream.no_event_for(0.3)
        stream.disconnect()
        await stream.task

    asyncio.run(scenario())


def test_reconnect_with_the_current_version_gets_no_duplicate_and_idle_keepalives():
    async def scenario():
        app, repo = make_app()
        gid, _ = repo.create()
        repo.update(gid, lambda g: apply_move(g, "X", 0, 0))
        stream = Stream(app, f"/games/{gid}/events", headers={"Last-Event-ID": "1"})
        assert parse_sse(await stream.next())[0]["comment"] == "keepalive"  # idle ping, no state
        await stream.no_event_for(0.2)
        stream.disconnect()
        await stream.task

    asyncio.run(scenario())


def test_the_game_ending_pushes_the_final_state_then_end_and_closes():
    async def scenario():
        app, repo = make_app()
        gid, _ = repo.create()
        stream = Stream(app, f"/games/{gid}/events")
        await stream.next_event()
        for player, row, col in WIN_X:
            repo.update(gid, lambda g, p=player, r=row, c=col: apply_move(g, p, r, c))
        app.state.broker.publish(gid)
        seen = []
        while True:
            seen.append(await stream.next_event())
            if seen[-1]["event"] == "end":
                break
        assert json.loads(seen[-2]["data"])["status"] == "won"
        await asyncio.wait_for(stream.task, 1)  # the server closed the stream itself
        assert app.state.broker.subscriber_count() == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"], ids=["receive-disconnect", "send-oserror"])
def test_a_disconnect_frees_the_subscriber(spec_version):
    async def scenario():
        app, repo = make_app()
        gid, _ = repo.create()
        stream = Stream(app, f"/games/{gid}/events", spec_version=spec_version)
        await stream.next_event()
        assert app.state.broker.subscriber_count(gid) == 1
        stream.disconnect()
        done, _ = await asyncio.wait({stream.task}, timeout=2)
        assert done, "the response did not end after the client disconnected"
        if not stream.task.cancelled():
            stream.task.exception()  # ClientDisconnect on spec >= 2.4 is expected; just retrieve it
        assert app.state.broker.subscriber_count() == 0

    asyncio.run(scenario())
