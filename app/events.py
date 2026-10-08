"""Live game updates over Server-Sent Events.

The broker is only a wake-up call keyed by game id: it never carries game
state. Every event a client receives is read from the repository (the source
of truth) after waking, so it cannot be stale or reordered.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from app.models import game_out
from app.store import GameRepository


class Subscription:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.wakeup = asyncio.Event()


class EventBroker:
    """In-process pub/sub: subscribers are grouped by game id, so a change in
    one game can only ever wake that game's subscribers."""

    def __init__(self) -> None:
        self._subs: dict[str, set[Subscription]] = {}
        self._lock = threading.Lock()

    def subscribe(self, game_id: str) -> Subscription:
        """Call from the event loop that will await the subscription."""
        sub = Subscription(asyncio.get_running_loop())
        with self._lock:
            self._subs.setdefault(game_id, set()).add(sub)
        return sub

    def unsubscribe(self, game_id: str, sub: Subscription) -> None:
        with self._lock:
            subs = self._subs.get(game_id)
            if subs is not None:
                subs.discard(sub)
                if not subs:
                    del self._subs[game_id]

    def publish(self, game_id: str) -> None:
        """Safe to call from any thread (move handlers run in a threadpool)."""
        with self._lock:
            targets = list(self._subs.get(game_id, ()))
        for sub in targets:
            try:
                sub.loop.call_soon_threadsafe(sub.wakeup.set)
            except RuntimeError:  # that loop has closed; its subscriber is gone
                pass

    def subscriber_count(self, game_id: str | None = None) -> int:
        with self._lock:
            if game_id is not None:
                return len(self._subs.get(game_id, ()))
            return sum(len(s) for s in self._subs.values())


class EventStreamResponse(StreamingResponse):
    """A StreamingResponse that always closes its generator when the response ends.

    When a client disconnects, the server stops iterating but nothing closes the
    suspended generator, so its `finally` (which unsubscribes) would wait for the
    garbage collector. Closing it here makes the cleanup deterministic.
    """

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.body_iterator.aclose()


def sse_message(event: str, data: str, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {data}\n\n"


def parse_last_event_id(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


async def game_event_stream(
    repo: GameRepository,
    broker: EventBroker,
    game_id: str,
    last_event_id: int | None,
    keepalive_seconds: float,
) -> AsyncIterator[str]:
    """Yield a `state` event whenever the game's version changes, `end` when it finishes.

    Subscribe first, then read: a move landing between the two re-arms the
    wake-up, so it is picked up on the next pass instead of being missed. The
    wait also times out every `keepalive_seconds`, which sends a comment to
    keep proxies from closing the stream and re-reads the repository, so a
    change made in another worker process still arrives, just a little later.
    """
    opponent = await run_in_threadpool(repo.opponent_of, game_id)  # fixed for the game's life
    sub = broker.subscribe(game_id)
    last_sent = last_event_id
    try:
        while True:
            sub.wakeup.clear()
            game = await run_in_threadpool(repo.get, game_id)
            if game.version != last_sent:
                payload = game_out(game_id, game, opponent).model_dump_json()
                yield sse_message("state", payload, event_id=game.version)
                last_sent = game.version
            if game.status != "in_progress":
                yield sse_message("end", "{}")
                return
            try:
                await asyncio.wait_for(sub.wakeup.wait(), timeout=keepalive_seconds)
            except TimeoutError:
                yield ": keepalive\n\n"
    finally:
        broker.unsubscribe(game_id, sub)
