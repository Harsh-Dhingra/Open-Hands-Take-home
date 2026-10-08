"""HTTP schemas. Engine objects are converted here; no rules live in this file."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.ai import Difficulty, Opponent
from app.engine import Game, Move, Player
from app.limits import DEFAULT_COLS, DEFAULT_K, DEFAULT_ROWS


class CreateGameRequest(BaseModel):
    """Types are checked here (strict ints, no unknown fields); ranges in app.limits."""

    model_config = ConfigDict(extra="forbid")

    rows: StrictInt = DEFAULT_ROWS
    cols: StrictInt = DEFAULT_COLS
    k: StrictInt = DEFAULT_K
    # Playing the computer (3x3 only): which level, and which side the human takes.
    # None means "not given" so the API can tell a default from a misplaced field.
    opponent: Literal["human", "computer"] = "human"
    difficulty: Difficulty | None = None
    human_plays: Player | None = None


class MoveRequest(BaseModel):
    player: Player
    row: StrictInt
    col: StrictInt
    # The game version the client last saw; omit (or null) to skip the check.
    expected_version: Annotated[StrictInt, Field(ge=0)] | None = None


class MoveOut(BaseModel):
    n: int
    player: Player
    row: int
    col: int


class OpponentOut(BaseModel):
    type: Literal["computer"] = "computer"
    difficulty: Difficulty
    computer_player: Player


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
    opponent: OpponentOut | None = None  # None: human vs human


class GameSummary(BaseModel):
    id: str
    rows: int
    cols: int
    k: int
    status: Literal["in_progress", "won", "draw"]
    version: int
    opponent: OpponentOut | None = None


class ConfigDefaults(BaseModel):
    rows: int
    cols: int
    k: int


class ConfigLimits(BaseModel):
    min_size: int
    max_size: int
    min_k: int


class ConfigOut(BaseModel):
    defaults: ConfigDefaults
    limits: ConfigLimits


class ErrorDetail(BaseModel):
    code: str
    message: str
    current_version: int | None = None  # only on stale_version


class ErrorOut(BaseModel):
    error: ErrorDetail


def opponent_out(opponent: Opponent | None) -> OpponentOut | None:
    if opponent is None:
        return None
    return OpponentOut(difficulty=opponent.difficulty, computer_player=opponent.computer_player)


def game_out(game_id: str, game: Game, opponent: Opponent | None = None) -> GameOut:
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
        opponent=opponent_out(opponent),
    )


def move_out(move: Move) -> MoveOut:
    return MoveOut(n=move.n, player=move.player, row=move.row, col=move.col)
