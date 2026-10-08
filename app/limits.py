"""Supported board configurations (product limits, stricter than the engine's maths).

The engine accepts any 1 <= k <= max(rows, cols); the service only offers
boards where the game is meaningful (k >= 3) and replay stays cheap (<= 20x20).
"""

from __future__ import annotations

from app.engine import InvalidConfig

DEFAULT_ROWS = 3
DEFAULT_COLS = 3
DEFAULT_K = 3
MIN_SIZE = 3
MAX_SIZE = 20
MIN_K = 3


def validate_config(rows: int, cols: int, k: int) -> None:
    """Raise InvalidConfig with a specific message; allocates nothing."""
    for name, value in (("rows", rows), ("cols", cols)):
        if not MIN_SIZE <= value <= MAX_SIZE:
            raise InvalidConfig(f"{name} must be between {MIN_SIZE} and {MAX_SIZE}, got {value}")
    longest = max(rows, cols)
    if not MIN_K <= k <= longest:
        raise InvalidConfig(
            f"k must be between {MIN_K} and {longest} for a {rows}x{cols} board, got {k}"
        )


def validate_opponent(
    rows: int, cols: int, k: int, opponent: str, difficulty: str | None, human_plays: str | None
) -> None:
    """The computer only plays 3x3; difficulty/side only make sense against it."""
    if opponent == "human":
        if difficulty is not None or human_plays is not None:
            raise InvalidConfig("difficulty and human_plays only apply when opponent is 'computer'")
        return
    if (rows, cols, k) != (3, 3, 3):
        raise InvalidConfig(
            f"the computer plays 3x3 with 3 in a row only, got {rows}x{cols} with k={k}"
        )


def describe() -> dict:
    """Defaults and limits as served by GET /config."""
    return {
        "defaults": {"rows": DEFAULT_ROWS, "cols": DEFAULT_COLS, "k": DEFAULT_K},
        "limits": {"min_size": MIN_SIZE, "max_size": MAX_SIZE, "min_k": MIN_K},
    }
