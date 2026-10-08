"""HTTP schemas. Engine objects are converted here; no rules live in this file."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, StrictInt

from app.engine import Game, Move, Player


class MoveRequest(BaseModel):
    player: Player
    row: StrictInt
    col: StrictInt


class MoveOut(BaseModel):
    n: int
    player: Player
    row: int
    col: int


class GameOut(BaseModel):
    id: str
    rows: int
    cols: int
    k: int
    board: list[list[Player | None]]
    next_player: Player | None
    status: Literal["in_progress", "won", "draw"]
    winner: Player | None
    winning_line: list[tuple[int, int]]
    version: int


class GameSummary(BaseModel):
    id: str
    status: Literal["in_progress", "won", "draw"]
    version: int


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorOut(BaseModel):
    error: ErrorDetail


def game_out(game_id: str, game: Game) -> GameOut:
    return GameOut(
        id=game_id,
        rows=game.rows,
        cols=game.cols,
        k=game.k,
        board=[list(line) for line in game.board],
        next_player=game.next_player,
        status=game.status,
        winner=game.winner,
        winning_line=list(game.winning_line),
        version=game.version,
    )


def move_out(move: Move) -> MoveOut:
    return MoveOut(n=move.n, player=move.player, row=move.row, col=move.col)
