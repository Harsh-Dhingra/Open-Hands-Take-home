"""Computer opponent policies for 3x3 tic-tac-toe. Pure: no I/O, no clocks.

Every look-ahead goes through the engine (``apply_move``, ``legal_moves``,
``completes_line``), so the computer plays by exactly the rules that validate
a human move. Randomness comes from an injected ``random.Random``.

- easy:   a uniformly random legal move.
- medium: win now if possible; else block the opponent's immediate win; else
          a random legal move.
- hard:   minimax (negamax) over the full game tree, memoised by board. It
          prefers faster wins and slower losses and never loses.
"""

from __future__ import annotations

import random
from typing import Literal

from app.engine import (
    Board,
    Cell,
    Game,
    GameOver,
    InvalidConfig,
    Player,
    apply_move,
    completes_line,
    legal_moves,
    new_game,
)

Difficulty = Literal["easy", "medium", "hard"]
DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard")

WIN_SCORE = 10  # a win on move n scores WIN_SCORE - n for the player who made it


class UnsupportedBoard(InvalidConfig):
    """The computer only plays 3x3 with three in a row."""


def other(player: Player) -> Player:
    return "O" if player == "X" else "X"


def choose_move(game: Game, difficulty: Difficulty, rng: random.Random) -> Cell:
    """Pick a legal move for the side to move."""
    if (game.rows, game.cols, game.k) != (3, 3, 3):
        raise UnsupportedBoard(
            f"the computer plays 3x3 with 3 in a row, not {game.rows}x{game.cols} with {game.k}"
        )
    moves = legal_moves(game)
    if not moves:
        raise GameOver("the game is finished: there is no move to choose")
    if difficulty == "easy":
        return rng.choice(moves)
    if difficulty == "medium":
        return _medium(game, moves, rng)
    return rng.choice(_best_moves(game, moves))


# --- medium ----------------------------------------------------------------


def _medium(game: Game, moves: tuple[Cell, ...], rng: random.Random) -> Cell:
    me = game.next_player
    assert me is not None  # the game is in progress
    wins = [m for m in moves if completes_line(game, me, *m)]
    if wins:
        return rng.choice(wins)
    blocks = [m for m in moves if completes_line(game, other(me), *m)]
    if blocks:
        return rng.choice(blocks)
    return rng.choice(moves)


# --- hard ------------------------------------------------------------------

# value of a position for the side to move; the board alone determines it
# (X always opens, so the mark count fixes whose turn it is).
_VALUES: dict[Board, int] = {}


def _value(game: Game) -> int:
    cached = _VALUES.get(game.board)
    if cached is not None:
        return cached
    if game.status == "won":  # the player who just moved won on move `version`
        value = -(WIN_SCORE - game.version)
    elif game.status == "draw":
        value = 0
    else:
        me = game.next_player
        assert me is not None
        value = max(-_value(apply_move(game, me, *m)) for m in legal_moves(game))
    _VALUES[game.board] = value
    return value


def _best_moves(game: Game, moves: tuple[Cell, ...]) -> list[Cell]:
    me = game.next_player
    assert me is not None
    scored = [(-_value(apply_move(game, me, *m)), m) for m in moves]
    best = max(score for score, _ in scored)
    return [m for score, m in scored if score == best]


def warm_up() -> int:
    """Solve the whole game once (every reachable position); returns how many.

    Called at startup so no request ever waits for a cold search.
    """
    _value(new_game())
    return len(_VALUES)
