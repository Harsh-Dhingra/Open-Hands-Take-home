"""Game persistence behind a small interface.

M2 ships the in-memory implementation; the SQLite one (M3) implements the
same ``GameRepository`` so the API never changes.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from typing import Protocol

from app.engine import Game, new_game


class GameNotFound(Exception):
    code = "not_found"


class GameRepository(Protocol):
    def create(self) -> tuple[str, Game]: ...

    def get(self, game_id: str) -> Game: ...

    def update(self, game_id: str, fn: Callable[[Game], Game]) -> Game:
        """Atomically apply ``fn`` to the stored game and save the result.

        If ``fn`` raises, nothing is saved and the exception propagates.
        """
        ...

    def list(self) -> list[tuple[str, Game]]:
        """All games, newest first."""
        ...


class InMemoryRepository:
    def __init__(self) -> None:
        self._games: dict[str, Game] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, Game]:
        game = new_game()
        with self._lock:
            game_id = secrets.token_hex(4)
            while game_id in self._games:  # pragma: no cover - 32-bit collision
                game_id = secrets.token_hex(4)
            self._games[game_id] = game
        return game_id, game

    def get(self, game_id: str) -> Game:
        try:
            return self._games[game_id]
        except KeyError:
            raise GameNotFound(game_id) from None

    def update(self, game_id: str, fn: Callable[[Game], Game]) -> Game:
        with self._lock:
            updated = fn(self.get(game_id))
            self._games[game_id] = updated
            return updated

    def list(self) -> list[tuple[str, Game]]:
        with self._lock:
            return list(reversed(self._games.items()))
