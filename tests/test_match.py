"""One turn against the computer: human move, then reply, as a pure function."""

import random
from collections import Counter

import pytest

from app import ai
from app.ai import Opponent
from app.engine import (
    CellTaken,
    GameOver,
    NotYourTurn,
    OutOfBounds,
    StaleVersion,
    apply_move,
    legal_moves,
    new_game,
    replay,
)


class NoRandomness(random.Random):
    """Fails the test if the computer is asked to move (or to roll a die)."""

    def random(self):
        raise AssertionError("the computer moved when it should not have")

    def getrandbits(self, k):
        raise AssertionError("the computer moved when it should not have")


def position(*cells):
    g = new_game()
    for row, col in cells:
        g = apply_move(g, g.next_player, row, col)
    return g


O_COMPUTER = {d: Opponent("O", d) for d in ai.DIFFICULTIES}


@pytest.mark.parametrize("difficulty", ai.DIFFICULTIES)
def test_a_turn_is_the_human_move_then_one_legal_reply(difficulty):
    opponent = O_COMPUTER[difficulty]
    g = ai.play_turn(new_game(), opponent, "X", 1, 1, 0, random.Random(3))
    assert g.version == 2
    human, reply = g.moves
    assert (human.player, human.row, human.col) == ("X", 1, 1)
    assert reply.player == "O" and (reply.row, reply.col) != (1, 1)
    assert g.next_player == "X"  # the human is to move again
    assert replay(3, 3, 3, g.moves) == g


def test_the_computer_does_not_reply_after_a_human_win():
    g = position((0, 0), (1, 0), (0, 1), (1, 1))  # X to move, (0,2) wins
    after = ai.play_turn(g, O_COMPUTER["hard"], "X", 0, 2, g.version, NoRandomness())
    assert after.status == "won" and after.winner == "X" and after.version == g.version + 1


def test_the_computer_does_not_reply_after_a_human_draw():
    g = position((0, 0), (0, 1), (0, 2), (1, 1), (1, 0), (1, 2), (2, 1), (2, 0))
    after = ai.play_turn(g, O_COMPUTER["hard"], "X", 2, 2, g.version, NoRandomness())
    assert after.status == "draw" and after.version == 9


def test_the_computer_can_win_with_its_reply():
    g = position((0, 0), (1, 0), (0, 1), (1, 1))  # X should win at (0,2) or block (1,2)
    after = ai.play_turn(g, O_COMPUTER["hard"], "X", 2, 2, None, random.Random(0))
    assert after.status == "won" and after.winner == "O"
    assert after.winning_line == ((1, 0), (1, 1), (1, 2))


@pytest.mark.parametrize("difficulty", ai.DIFFICULTIES)
def test_a_stale_human_move_is_rejected_and_the_computer_stays_silent(difficulty):
    g = position((1, 1), (0, 0))
    with pytest.raises(StaleVersion):
        ai.play_turn(g, O_COMPUTER[difficulty], "X", 2, 2, g.version - 1, NoRandomness())


@pytest.mark.parametrize(
    "player,row,col,error",
    [("X", 1, 1, CellTaken), ("X", 5, 5, OutOfBounds), ("O", 2, 2, NotYourTurn)],
    ids=["occupied", "off-board", "the-computers-side"],
)
def test_an_invalid_human_move_changes_nothing_and_triggers_no_reply(player, row, col, error):
    g = ai.play_turn(new_game(), O_COMPUTER["hard"], "X", 1, 1, 0, random.Random(0))
    with pytest.raises(error):
        ai.play_turn(g, O_COMPUTER["hard"], player, row, col, g.version, NoRandomness())


def test_a_move_in_a_finished_game_is_rejected():
    done = position((0, 0), (1, 0), (0, 1), (1, 1), (0, 2))
    with pytest.raises(GameOver):
        ai.play_turn(done, O_COMPUTER["hard"], "O", 2, 2, None, NoRandomness())


def test_the_computer_opens_when_it_plays_x_and_waits_when_it_plays_o():
    opened = ai.open_game(Opponent("X", "hard"), random.Random(1))
    assert opened.version == 1 and opened.moves[0].player == "X"
    assert opened.next_player == "O"
    waiting = ai.open_game(Opponent("O", "hard"), NoRandomness())
    assert waiting.version == 0 and waiting.next_player == "X"


def test_the_human_can_play_o_against_a_computer_x():
    opponent = Opponent("X", "medium")
    g = ai.open_game(opponent, random.Random(2))
    free = legal_moves(g)[0]
    g = ai.play_turn(g, opponent, "O", *free, g.version, random.Random(2))
    assert g.version == 3 and [m.player for m in g.moves] == ["X", "O", "X"]


def test_the_computer_refuses_boards_it_does_not_support():
    with pytest.raises(ai.UnsupportedBoard):
        ai.computer_reply(new_game(4, 4, 4), Opponent("X", "hard"), random.Random(0))


def test_opponent_knows_the_humans_side():
    assert Opponent("O", "easy").human_player == "X"
    assert Opponent("X", "easy").human_player == "O"


@pytest.mark.parametrize("difficulty", ai.DIFFICULTIES)
@pytest.mark.parametrize("computer", ["O", "X"])
def test_two_hundred_seeded_games_against_a_random_human(difficulty, computer):
    opponent = Opponent(computer, difficulty)
    human = opponent.human_player
    rng_ai, rng_human = random.Random(11), random.Random(22)
    results = Counter()
    for _ in range(200):
        g = ai.open_game(opponent, rng_ai)
        while g.status == "in_progress":
            # Invariant: the computer never owes a move between turns.
            assert g.next_player == human
            move = rng_human.choice(legal_moves(g))
            g = ai.play_turn(g, opponent, human, *move, g.version, rng_ai)
        assert replay(3, 3, 3, g.moves) == g
        results[g.winner or "draw"] += 1
    if difficulty == "hard":
        assert results[human] == 0
    assert sum(results.values()) == 200
