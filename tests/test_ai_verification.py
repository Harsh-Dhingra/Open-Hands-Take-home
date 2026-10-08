"""Exhaustive verification of the computer policies.

Nothing here reuses app.ai's search: positions are enumerated through the
engine, and an independent minimax oracle (its own line table, no engine, no
imports from app.ai) gives the true value of every position.
"""

import random
import time
from collections import Counter
from functools import cache

import pytest

from app import ai
from app.engine import GameOver, apply_move, legal_moves, new_game

# --- independent oracle ---------------------------------------------------------

LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
WIN = 10  # same convention as the spec: a win on move n is worth WIN - n to its maker


def flat(board):
    return tuple(c or "." for line in board for c in line)


def opposite(p):
    return "O" if p == "X" else "X"


def line_winner(cells):
    for a, b, c in LINES:
        if cells[a] != "." and cells[a] == cells[b] == cells[c]:
            return cells[a]
    return None


@cache
def oracle_score(cells, mover):
    """Best achievable score for `mover` (win sooner / lose later is better)."""
    marks = sum(c != "." for c in cells)
    if line_winner(cells):  # the other side just completed a line
        return -(WIN - marks)
    if marks == 9:
        return 0
    best = None
    for i, c in enumerate(cells):
        if c == ".":
            after = cells[:i] + (mover,) + cells[i + 1 :]
            score = -oracle_score(after, opposite(mover))
            best = score if best is None or score > best else best
    return best


def score_of_move(game, move):
    mover = game.next_player
    cells = list(flat(game.board))
    cells[move[0] * 3 + move[1]] = mover
    return -oracle_score(tuple(cells), opposite(mover))


def best_score(game):
    return oracle_score(flat(game.board), game.next_player)


def threats(cells, player):
    """Empty cells where `player` would complete a line, by the oracle's own table."""
    out = set()
    for a, b, c in LINES:
        trio = [cells[a], cells[b], cells[c]]
        if trio.count(player) == 2 and trio.count(".") == 1:
            out.add((a, b, c)[trio.index(".")])
    return {(i // 3, i % 3) for i in out}


# --- every position reachable through the engine ----------------------------------


@pytest.fixture(scope="module")
def positions():
    seen = {}
    frontier = [new_game()]
    while frontier:
        nxt = []
        for g in frontier:
            if g.board in seen:
                continue
            seen[g.board] = g
            for m in legal_moves(g):
                nxt.append(apply_move(g, g.next_player, *m))
        frontier = nxt
    return list(seen.values())


@pytest.fixture(scope="module")
def live(positions):
    return [g for g in positions if g.status == "in_progress"]


def test_enumeration_matches_the_known_tic_tac_toe_counts(positions):
    # 5,478 reachable positions, 958 of them finished: 626 X wins, 316 O wins, 16 draws.
    assert len(positions) == 5478
    outcomes = Counter(g.winner or g.status for g in positions if g.status != "in_progress")
    assert outcomes == {"X": 626, "O": 316, "draw": 16}


# --- immediate wins ---------------------------------------------------------------


def winning_cells(game):
    mover = game.next_player
    return {m for m in legal_moves(game) if apply_move(game, mover, *m).status == "won"}


@pytest.mark.parametrize("difficulty", ["medium", "hard"])
def test_always_takes_an_available_win(live, difficulty):
    checked = 0
    for g in live:
        wins = winning_cells(g)
        if not wins:
            continue
        checked += 1
        for seed in range(4):
            assert ai.choose_move(g, difficulty, random.Random(seed)) in wins, g.board
    assert checked > 1000  # a large share of positions offer a win


def test_medium_prefers_winning_over_blocking(live):
    both = [g for g in live if winning_cells(g) and threats(flat(g.board), opposite(g.next_player))]
    assert both
    for g in both:
        assert ai.choose_move(g, "medium", random.Random(0)) in winning_cells(g)


# --- forced blocks ----------------------------------------------------------------


def blocking_positions(live):
    for g in live:
        cells = flat(g.board)
        enemy = threats(cells, opposite(g.next_player))
        if enemy and not winning_cells(g):
            yield g, enemy


def test_medium_blocks_every_immediate_threat(live):
    single = multi = 0
    for g, enemy in blocking_positions(live):
        single += len(enemy) == 1
        multi += len(enemy) > 1
        for seed in range(4):
            assert ai.choose_move(g, "medium", random.Random(seed)) in enemy, g.board
    assert single > 100 and multi > 10


def test_hard_blocks_a_single_threat_and_a_double_threat_is_really_lost(live):
    for g, enemy in blocking_positions(live):
        if len(enemy) == 1:
            move = ai.choose_move(g, "hard", random.Random(0))
            after = apply_move(g, g.next_player, *move)
            assert not threats(flat(after.board), after.next_player or "-") or after.status != (
                "in_progress"
            ), g.board
            assert move in enemy  # the only way to stop the loss
        else:
            assert best_score(g) < 0  # nothing can save it: the oracle agrees it is lost


def test_medium_chooses_randomly_among_several_wins_or_several_blocks(live):
    two_wins = next(g for g in live if len(winning_cells(g)) >= 2)
    picks = {ai.choose_move(two_wins, "medium", random.Random(s)) for s in range(60)}
    assert picks == winning_cells(two_wins)
    two_threats = next(g for g, enemy in blocking_positions(live) if len(enemy) >= 2)
    enemy = threats(flat(two_threats.board), opposite(two_threats.next_player))
    picks = {ai.choose_move(two_threats, "medium", random.Random(s)) for s in range(60)}
    assert picks == enemy


# --- hard is optimal ----------------------------------------------------------------


def test_hard_always_plays_an_oracle_optimal_move(live):
    for g in live:
        best = best_score(g)
        for seed in range(6):
            move = ai.choose_move(g, "hard", random.Random(seed))
            assert score_of_move(g, move) == best, (g.board, move)


def test_hard_varies_among_equally_good_moves():
    # On an empty board every opening draws, so all nine cells are optimal.
    seen = {ai.choose_move(new_game(), "hard", random.Random(s)) for s in range(200)}
    assert seen == {(r, c) for r in range(3) for c in range(3)}


def test_real_game_position_hard_answers_on_an_edge_never_a_corner():
    # From live play (game c7f8d87c): X on two opposite corners, O in the centre, O to move.
    g = new_game()
    for row, col in [(0, 0), (1, 1), (2, 2)]:
        g = apply_move(g, g.next_player, row, col)
    assert g.next_player == "O" and g.version == 3
    edges = {(0, 1), (1, 0), (1, 2), (2, 1)}
    corners = {(0, 2), (2, 0)}
    losing = {m for m in legal_moves(g) if score_of_move(g, m) < 0}
    assert losing == corners  # a corner reply loses by force
    assert {m for m in legal_moves(g) if score_of_move(g, m) == 0} == edges
    assert {ai.choose_move(g, "hard", random.Random(s)) for s in range(60)} == edges


# --- hard never loses -----------------------------------------------------------------


def play_every_opponent_line(hard_side, seed):
    """Hard answers each position; the opponent tries every legal move at every turn."""
    rng = random.Random(seed)
    outcomes, openings = Counter(), set()

    def walk(game):
        if game.status != "in_progress":
            outcomes[game.winner or "draw"] += 1
            return
        if game.next_player == hard_side:
            walk(apply_move(game, hard_side, *ai.choose_move(game, "hard", rng)))
        else:
            for move in legal_moves(game):
                if game.version <= 1:
                    openings.add(move)
                walk(apply_move(game, game.next_player, *move))

    walk(new_game())
    return outcomes, openings


@pytest.mark.parametrize("hard_side", ["X", "O"])
def test_hard_never_loses_against_every_possible_opponent(hard_side):
    foe = opposite(hard_side)
    total_wins = total_draws = 0
    for seed in range(25):
        outcomes, openings = play_every_opponent_line(hard_side, seed)
        assert outcomes[foe] == 0, (hard_side, seed, outcomes)
        # The enumeration really branched (observed 73-167 lines as X, 513-657 as O).
        assert sum(outcomes.values()) > {"X": 60, "O": 400}[hard_side]
        total_wins += outcomes[hard_side]
        total_draws += outcomes["draw"]
        # the opponent's first move was tried in every cell it could use
        assert len(openings) == (9 if hard_side == "O" else 8)
    assert total_wins > 0 and total_draws > 0  # it wins when given the chance, draws otherwise


def test_hard_against_hard_always_draws():
    for seed in range(20):
        rng = random.Random(seed)
        g = new_game()
        while g.status == "in_progress":
            g = apply_move(g, g.next_player, *ai.choose_move(g, "hard", rng))
        assert g.status == "draw"


@pytest.mark.parametrize("opponent,min_wins", [("easy", 100), ("medium", 20)])
@pytest.mark.parametrize("hard_side", ["X", "O"])
def test_hard_never_loses_to_the_weaker_levels_over_many_games(opponent, min_wins, hard_side):
    rng = random.Random(2024)
    results = Counter()
    for _ in range(300):
        g = new_game()
        while g.status == "in_progress":
            level = "hard" if g.next_player == hard_side else opponent
            g = apply_move(g, g.next_player, *ai.choose_move(g, level, rng))
        results[g.winner or "draw"] += 1
    assert results[opposite(hard_side)] == 0
    assert results[hard_side] > min_wins  # it punishes mistakes, it does not just draw


# --- every generated move is legal -------------------------------------------------------


@pytest.mark.parametrize("difficulty", ai.DIFFICULTIES)
def test_every_move_in_every_position_is_legal_and_finished_games_have_none(positions, difficulty):
    for g in positions:
        if g.status != "in_progress":
            with pytest.raises(GameOver):
                ai.choose_move(g, difficulty, random.Random(0))
            continue
        for seed in range(4):
            move = ai.choose_move(g, difficulty, random.Random(seed))
            assert move in legal_moves(g)
            apply_move(g, g.next_player, *move)  # the engine accepts it


# --- easy and medium behaviour (seeded statistics) ------------------------------------------


def test_easy_is_roughly_uniform_over_the_empty_board():
    rng = random.Random(12345)
    counts = Counter(ai.choose_move(new_game(), "easy", rng) for _ in range(4500))
    assert set(counts) == {(r, c) for r in range(3) for c in range(3)}
    assert all(400 <= n <= 600 for n in counts.values()), counts  # expected 500, sd about 21


def test_easy_does_not_look_for_wins_or_blocks():
    g = new_game()
    for row, col in [(0, 0), (1, 0), (0, 1), (1, 1)]:  # X to move; (0,2) wins
        g = apply_move(g, g.next_player, row, col)
    rng = random.Random(99)
    picks = Counter(ai.choose_move(g, "easy", rng) for _ in range(900))
    assert 80 <= picks[(0, 2)] <= 180  # about 1/5 of the time (5 empty cells), not always
    assert len(picks) == len(legal_moves(g))  # and every legal cell shows up


def test_medium_without_a_win_or_threat_plays_varied_legal_moves():
    g = apply_move(new_game(), "X", 1, 1)  # O to move; nobody threatens anything
    assert not winning_cells(g) and not threats(flat(g.board), "X")
    picks = {ai.choose_move(g, "medium", random.Random(s)) for s in range(100)}
    assert picks == set(legal_moves(g))


# --- unsupported boards and cost -----------------------------------------------------------------


def test_cold_solve_and_warm_decisions_stay_within_budget(monkeypatch):
    monkeypatch.setattr(ai, "_VALUES", {})  # a genuinely cold table
    start = time.perf_counter()
    assert ai.warm_up() == 5478
    assert time.perf_counter() - start < 2.0  # about 0.06 s on a laptop; generous for CI
    rng = random.Random(0)
    slowest = 0.0
    for _ in range(300):
        g = new_game()
        while g.status == "in_progress":
            t = time.perf_counter()
            move = ai.choose_move(g, "hard", rng)
            slowest = max(slowest, time.perf_counter() - t)
            g = apply_move(g, g.next_player, *move)
    assert slowest < 0.005
