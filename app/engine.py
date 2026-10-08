"""Pure game rules: no I/O, no clocks, no randomness.

A game is an append-only log of moves; every derived field (board, status,
winner, winning line) is a function of that log. ``apply_move`` returns a new
``Game`` and never mutates its input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Player = Literal["X", "O"]
Status = Literal["in_progress", "won", "draw"]
Cell = tuple[int, int]
Board = tuple[tuple[Player | None, ...], ...]

# Each direction is checked both ways from the last move, so 4 axes cover all lines.
_AXES: tuple[Cell, ...] = ((0, 1), (1, 0), (1, 1), (1, -1))


class EngineError(Exception):
    """Base class; ``code`` is the stable identifier the API maps to HTTP."""

    code = "engine_error"


class InvalidConfig(EngineError):
    code = "invalid_config"


class OutOfBounds(EngineError):
    code = "out_of_bounds"


class CellTaken(EngineError):
    code = "cell_taken"


class NotYourTurn(EngineError):
    code = "not_your_turn"


class GameOver(EngineError):
    code = "game_over"


@dataclass(frozen=True)
class Move:
    n: int  # 1-based position in the log
    player: Player
    row: int
    col: int


@dataclass(frozen=True)
class Game:
    rows: int
    cols: int
    k: int
    moves: tuple[Move, ...]
    board: Board
    status: Status
    winner: Player | None
    winning_line: tuple[Cell, ...]

    @property
    def next_player(self) -> Player | None:
        if self.status != "in_progress":
            return None
        return "X" if len(self.moves) % 2 == 0 else "O"

    @property
    def version(self) -> int:
        return len(self.moves)


def new_game(rows: int = 3, cols: int = 3, k: int = 3) -> Game:
    if rows < 1 or cols < 1 or k < 1 or k > max(rows, cols):
        raise InvalidConfig(f"invalid config rows={rows} cols={cols} k={k}")
    board = tuple(tuple(None for _ in range(cols)) for _ in range(rows))
    return Game(rows, cols, k, (), board, "in_progress", None, ())


def apply_move(game: Game, player: Player, row: int, col: int) -> Game:
    if game.status != "in_progress":
        raise GameOver("game is already finished")
    if not (0 <= row < game.rows and 0 <= col < game.cols):
        raise OutOfBounds(f"({row}, {col}) is outside the {game.rows}x{game.cols} board")
    if player != game.next_player:
        raise NotYourTurn(f"it is {game.next_player}'s turn")
    if game.board[row][col] is not None:
        raise CellTaken(f"({row}, {col}) is already taken")

    board = tuple(
        tuple(player if (r, c) == (row, col) else v for c, v in enumerate(line))
        for r, line in enumerate(game.board)
    )
    moves = game.moves + (Move(len(game.moves) + 1, player, row, col),)
    line = _winning_line(board, game.k, player, row, col)
    if line:
        status: Status = "won"
    elif len(moves) == game.rows * game.cols:
        status = "draw"
    else:
        status = "in_progress"
    return Game(
        game.rows,
        game.cols,
        game.k,
        moves,
        board,
        status,
        player if line else None,
        line,
    )


def replay(rows: int, cols: int, k: int, moves: list[Move] | tuple[Move, ...]) -> Game:
    """Rebuild state from a stored log, re-validating every move."""
    game = new_game(rows, cols, k)
    for m in moves:
        game = apply_move(game, m.player, m.row, m.col)
    return game


def _winning_line(board: Board, k: int, player: Player, row: int, col: int) -> tuple[Cell, ...]:
    """Cells of a run of >= k ``player`` marks through (row, col), else ().

    Only lines through the last move can newly complete, so this is O(k) per
    axis rather than a full-board scan.
    """
    rows, cols = len(board), len(board[0])

    def run(dr: int, dc: int) -> list[Cell]:
        cells: list[Cell] = []
        r, c = row + dr, col + dc
        while 0 <= r < rows and 0 <= c < cols and board[r][c] == player:
            cells.append((r, c))
            r, c = r + dr, c + dc
        return cells

    for dr, dc in _AXES:
        line = [*reversed(run(-dr, -dc)), (row, col), *run(dr, dc)]
        if len(line) >= k:
            return tuple(line)
    return ()
