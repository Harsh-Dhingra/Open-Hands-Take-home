"""Contract of choose_move that the exhaustive suite (test_ai_verification.py) does not cover."""

import random

import pytest

from app import ai
from app.engine import InvalidConfig, apply_move, new_game


@pytest.mark.parametrize("difficulty", ai.DIFFICULTIES)
def test_same_seed_gives_the_same_move(difficulty):
    g = apply_move(new_game(), "X", 0, 0)
    assert ai.choose_move(g, difficulty, random.Random(7)) == ai.choose_move(
        g, difficulty, random.Random(7)
    )


@pytest.mark.parametrize("difficulty", ai.DIFFICULTIES)
@pytest.mark.parametrize("board", [(3, 4, 3), (4, 4, 4), (5, 5, 3)])
def test_only_3x3_is_supported(difficulty, board):
    with pytest.raises(ai.UnsupportedBoard) as exc:
        ai.choose_move(new_game(*board), difficulty, random.Random(0))
    assert isinstance(exc.value, InvalidConfig) and exc.value.code == "invalid_config"
