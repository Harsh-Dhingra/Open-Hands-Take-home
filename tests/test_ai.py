"""Smoke tests for app.ai: legality, error cases and each branch. The exhaustive
proofs (oracle comparison, never-lose enumeration) live in test_ai_verification.py."""

import random

import pytest

from app import ai
from app.engine import GameOver, InvalidConfig, apply_move, legal_moves, new_game


def play(moves):
    g = new_game()
    for row, col in moves:
        g = apply_move(g, g.next_player, row, col)
    return g


POSITIONS = {
    "empty": [],
    "opening": [(1, 1)],
    "midgame": [(0, 0), (1, 1), (2, 2), (0, 2)],
    "one-cell-left": [(0, 0), (0, 1), (0, 2), (1, 1), (1, 0), (1, 2), (2, 1), (2, 0)],
}


@pytest.mark.parametrize("difficulty", ai.DIFFICULTIES)
@pytest.mark.parametrize("name", POSITIONS)
def test_every_policy_returns_a_legal_move(difficulty, name):
    g = play(POSITIONS[name])
    for seed in range(5):
        move = ai.choose_move(g, difficulty, random.Random(seed))
        assert move in legal_moves(g)
        apply_move(g, g.next_player, *move)  # accepted by the real rules


@pytest.mark.parametrize("difficulty", ai.DIFFICULTIES)
def test_same_seed_gives_the_same_move(difficulty):
    g = play([(0, 0)])
    assert ai.choose_move(g, difficulty, random.Random(7)) == ai.choose_move(
        g, difficulty, random.Random(7)
    )


def test_medium_takes_a_win_then_blocks_then_plays_anything():
    win = play([(0, 0), (1, 0), (0, 1), (1, 1)])  # X to move: (0,2) wins (also (2,1) would block)
    assert ai.choose_move(win, "medium", random.Random(0)) == (0, 2)
    block = play([(0, 0), (1, 1), (2, 2), (1, 0)])  # X has no win; O threatens (1,2)
    assert ai.choose_move(block, "medium", random.Random(0)) == (1, 2)
    quiet = play([(1, 1)])
    assert ai.choose_move(quiet, "medium", random.Random(0)) in legal_moves(quiet)


def test_hard_takes_an_immediate_win():
    g = play([(0, 0), (1, 0), (0, 1), (1, 1)])
    assert ai.choose_move(g, "hard", random.Random(0)) == (0, 2)


@pytest.mark.parametrize("board", [(3, 4, 3), (4, 4, 4), (5, 5, 3)])
def test_only_3x3_is_supported(board):
    with pytest.raises(ai.UnsupportedBoard) as exc:
        ai.choose_move(new_game(*board), "hard", random.Random(0))
    assert isinstance(exc.value, InvalidConfig) and exc.value.code == "invalid_config"


@pytest.mark.parametrize("difficulty", ai.DIFFICULTIES)
def test_no_move_exists_in_a_finished_game(difficulty):
    won = play([(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)])
    with pytest.raises(GameOver):
        ai.choose_move(won, difficulty, random.Random(0))


def test_warm_up_solves_every_reachable_position():
    assert ai.warm_up() == 5478
    assert ai.warm_up() == 5478  # idempotent
