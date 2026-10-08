"""Game persistence behind a small interface.

M2 ships the in-memory implementation; the SQLite one (M3) implements the
same ``GameRepository`` so the API never changes.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from app.ai import Opponent
from app.engine import EngineError, Game, Move, replay
from app.limits import DEFAULT_COLS, DEFAULT_K, DEFAULT_ROWS


class GameNotFound(Exception):
    code = "not_found"


class CorruptGame(Exception):
    """The stored move log is not a legal game; refuse to guess a state."""

    code = "corrupt_game"


class GameRepository(Protocol):
    def create(
        self,
        rows: int = DEFAULT_ROWS,
        cols: int = DEFAULT_COLS,
        k: int = DEFAULT_K,
        opponent: Opponent | None = None,
        moves: Sequence[Move] = (),
    ) -> tuple[str, Game]:
        """Store a new game, optionally against the computer and with opening moves.

        The moves are replayed through the engine first (an illegal log is
        rejected), and the game and its moves are saved all-or-nothing.
        """
        ...

    def opponent_of(self, game_id: str) -> Opponent | None:
        """The computer opponent, or None for a human-vs-human game. Immutable."""
        ...

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
        self._opponents: dict[str, Opponent] = {}
        self._lock = threading.Lock()

    def create(
        self,
        rows: int = DEFAULT_ROWS,
        cols: int = DEFAULT_COLS,
        k: int = DEFAULT_K,
        opponent: Opponent | None = None,
        moves: Sequence[Move] = (),
    ) -> tuple[str, Game]:
        game = replay(rows, cols, k, moves)  # validates the config and every move first
        with self._lock:
            game_id = secrets.token_hex(4)
            while game_id in self._games:  # pragma: no cover - 32-bit collision
                game_id = secrets.token_hex(4)
            self._games[game_id] = game
            if opponent is not None:
                self._opponents[game_id] = opponent
        return game_id, game

    def opponent_of(self, game_id: str) -> Opponent | None:
        self.get(game_id)  # unknown game -> GameNotFound
        return self._opponents.get(game_id)

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


# Opponent columns are NULL for human-vs-human games. They were added after the
# first release, so a database created earlier gets them through _migrate().
_OPPONENT_COLUMNS = {
    "computer_player": "TEXT CHECK (computer_player IN ('X', 'O'))",
    "difficulty": "TEXT CHECK (difficulty IN ('easy', 'medium', 'hard'))",
}

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS games (
    id     TEXT PRIMARY KEY,
    n_rows INTEGER NOT NULL,
    n_cols INTEGER NOT NULL,
    k      INTEGER NOT NULL,
    computer_player {_OPPONENT_COLUMNS["computer_player"]},
    difficulty {_OPPONENT_COLUMNS["difficulty"]}
);
CREATE TABLE IF NOT EXISTS moves (
    game_id TEXT    NOT NULL REFERENCES games(id),
    n       INTEGER NOT NULL,
    player  TEXT    NOT NULL CHECK (player IN ('X', 'O')),
    row_idx INTEGER NOT NULL,
    col_idx INTEGER NOT NULL,
    PRIMARY KEY (game_id, n),
    UNIQUE (game_id, row_idx, col_idx)
);
"""


class SqliteRepository:
    """Games persisted as an append-only move log.

    Only the log is stored; board, status and winner are derived on read with
    ``engine.replay``. A connection is opened per operation so the repository
    is safe to use from the API's threadpool (and from several processes).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns a database created by an older version lacks. Idempotent."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
        for name, definition in _OPPONENT_COLUMNS.items():
            if name in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE games ADD COLUMN {name} {definition}")
            except sqlite3.OperationalError as exc:
                # Another process migrated between our check and our ALTER.
                if "duplicate column" not in str(exc):
                    raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # isolation_level=None: autocommit; update() manages its own transaction.
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def create(
        self,
        rows: int = DEFAULT_ROWS,
        cols: int = DEFAULT_COLS,
        k: int = DEFAULT_K,
        opponent: Opponent | None = None,
        moves: Sequence[Move] = (),
    ) -> tuple[str, Game]:
        game = replay(rows, cols, k, moves)  # validates the config and every move first
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")  # the game and its opening moves: all or nothing
            try:
                game_id = self._insert_game(conn, game, opponent)
                self._insert_moves(conn, game_id, game.moves)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return game_id, game

    @staticmethod
    def _insert_game(conn: sqlite3.Connection, game: Game, opponent: Opponent | None) -> str:
        values = (game.rows, game.cols, game.k)
        values += (opponent.computer_player, opponent.difficulty) if opponent else (None, None)
        while True:
            game_id = secrets.token_hex(4)
            try:
                conn.execute(
                    "INSERT INTO games (id, n_rows, n_cols, k, computer_player, difficulty)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (game_id, *values),
                )
                return game_id
            except sqlite3.IntegrityError:  # pragma: no cover - 32-bit collision
                continue

    @staticmethod
    def _insert_moves(conn: sqlite3.Connection, game_id: str, moves: Sequence[Move]) -> None:
        conn.executemany(
            "INSERT INTO moves (game_id, n, player, row_idx, col_idx) VALUES (?, ?, ?, ?, ?)",
            [(game_id, m.n, m.player, m.row, m.col) for m in moves],
        )

    def opponent_of(self, game_id: str) -> Opponent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT computer_player, difficulty FROM games WHERE id = ?", (game_id,)
            ).fetchone()
        if row is None:
            raise GameNotFound(game_id)
        return Opponent(*row) if row[0] is not None else None

    def get(self, game_id: str) -> Game:
        with self._connect() as conn:
            return self._load(conn, game_id)

    def update(self, game_id: str, fn: Callable[[Game], Game]) -> Game:
        with self._connect() as conn:
            # IMMEDIATE takes the write lock up front, so the read-modify-write
            # below is atomic across threads and processes.
            conn.execute("BEGIN IMMEDIATE")
            try:
                before = self._load(conn, game_id)
                after = fn(before)
                self._insert_moves(conn, game_id, after.moves[len(before.moves) :])
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return after

    def list(self) -> list[tuple[str, Game]]:
        with self._connect() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM games ORDER BY rowid DESC")]
            return [(i, self._load(conn, i)) for i in ids]

    @staticmethod
    def _load(conn: sqlite3.Connection, game_id: str) -> Game:
        row = conn.execute(
            "SELECT n_rows, n_cols, k FROM games WHERE id = ?", (game_id,)
        ).fetchone()
        if row is None:
            raise GameNotFound(game_id)
        moves = [
            Move(n, player, r, c)
            for n, player, r, c in conn.execute(
                "SELECT n, player, row_idx, col_idx FROM moves WHERE game_id = ? ORDER BY n",
                (game_id,),
            )
        ]
        if [m.n for m in moves] != list(range(1, len(moves) + 1)):
            raise CorruptGame(f"{game_id}: move numbers are not 1..{len(moves)}")
        try:
            return replay(*row, moves)
        except EngineError as exc:
            raise CorruptGame(f"{game_id}: {exc}") from exc
