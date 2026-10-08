import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.engine import (
    CellTaken,
    Game,
    GameOver,
    InvalidConfig,
    NotYourTurn,
    OutOfBounds,
    apply_move,
    new_game,
    replay,
)


def play(game: Game, cells: list[tuple[int, int]]) -> Game:
    """Play cells in order; X moves first and players alternate."""
    for row, col in cells:
        game = apply_move(game, game.next_player, row, col)
    return game


# --- new_game ------------------------------------------------------------


def test_new_game_defaults():
    g = new_game()
    assert (g.rows, g.cols, g.k) == (3, 3, 3)
    assert g.status == "in_progress"
    assert g.next_player == "X"
    assert g.winner is None and g.winning_line == ()
    assert g.version == 0
    assert all(v is None for line in g.board for v in line)


@pytest.mark.parametrize(
    "rows,cols,k", [(0, 3, 3), (3, 0, 3), (3, 3, 0), (3, 3, 4), (-1, 3, 3), (2, 2, 3)]
)
def test_new_game_rejects_invalid_config(rows, cols, k):
    with pytest.raises(InvalidConfig):
        new_game(rows, cols, k)


# --- turns ----------------------------------------------------------------


def test_x_moves_first_and_turns_alternate():
    g = new_game()
    assert g.next_player == "X"
    g = apply_move(g, "X", 0, 0)
    assert g.next_player == "O"
    g = apply_move(g, "O", 1, 1)
    assert g.next_player == "X"
    assert g.board[0][0] == "X" and g.board[1][1] == "O"
    assert [m.n for m in g.moves] == [1, 2]


def test_o_cannot_move_first():
    with pytest.raises(NotYourTurn):
        apply_move(new_game(), "O", 0, 0)


def test_same_player_cannot_move_twice():
    g = apply_move(new_game(), "X", 0, 0)
    with pytest.raises(NotYourTurn):
        apply_move(g, "X", 1, 1)


def test_unknown_player_rejected():
    with pytest.raises(NotYourTurn):
        apply_move(new_game(), "Z", 0, 0)  # type: ignore[arg-type]


# --- invalid moves --------------------------------------------------------


@pytest.mark.parametrize("row,col", [(-1, 0), (0, -1), (3, 0), (0, 3), (99, 99)])
def test_out_of_bounds(row, col):
    with pytest.raises(OutOfBounds):
        apply_move(new_game(), "X", row, col)


def test_occupied_cell_rejected_and_state_unchanged():
    g = apply_move(new_game(), "X", 1, 1)
    with pytest.raises(CellTaken):
        apply_move(g, "O", 1, 1)
    assert g.next_player == "O" and g.version == 1


def test_apply_move_does_not_mutate_input():
    g = new_game()
    apply_move(g, "X", 0, 0)
    assert g.version == 0 and g.board[0][0] is None


# --- winning --------------------------------------------------------------

# X wins using `line`; O's filler moves are placed so O never wins first.
X_WINS = {
    "row0": ([(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)], [(0, 0), (0, 1), (0, 2)]),
    "row2": ([(2, 0), (0, 0), (2, 1), (0, 1), (2, 2)], [(2, 0), (2, 1), (2, 2)]),
    "col0": ([(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)], [(0, 0), (1, 0), (2, 0)]),
    "col2": ([(0, 2), (0, 1), (1, 2), (1, 1), (2, 2)], [(0, 2), (1, 2), (2, 2)]),
    "diag": ([(0, 0), (0, 1), (1, 1), (0, 2), (2, 2)], [(0, 0), (1, 1), (2, 2)]),
    "anti": ([(0, 2), (0, 0), (1, 1), (0, 1), (2, 0)], [(0, 2), (1, 1), (2, 0)]),
}


@pytest.mark.parametrize("name", X_WINS)
def test_x_wins_each_line(name):
    cells, line = X_WINS[name]
    g = play(new_game(), cells)
    assert g.status == "won" and g.winner == "X"
    assert sorted(g.winning_line) == sorted(line)
    assert g.next_player is None


# O wins the same lines: X opens on `filler`, then O takes the line while X
# plays the throwaway cells from X_WINS (never three in a row).
O_FILLER = {
    "row0": (2, 2),
    "row2": (1, 1),
    "col0": (2, 2),
    "col2": (0, 0),
    "diag": (2, 0),
    "anti": (2, 2),
}


@pytest.mark.parametrize("name", X_WINS)
def test_o_wins_each_line(name):
    cells, line = X_WINS[name]
    g = play(new_game(), [O_FILLER[name], *cells])
    assert g.status == "won" and g.winner == "O"
    assert sorted(g.winning_line) == sorted(line)


def test_moves_rejected_after_win():
    cells, _ = X_WINS["row0"]
    g = play(new_game(), cells)
    with pytest.raises(GameOver):
        apply_move(g, "O", 2, 2)


# --- draw -----------------------------------------------------------------

DRAW = [(0, 0), (0, 1), (0, 2), (1, 1), (1, 0), (1, 2), (2, 1), (2, 0), (2, 2)]


def test_draw():
    g = play(new_game(), DRAW)
    assert g.status == "draw" and g.winner is None and g.winning_line == ()
    assert g.next_player is None
    with pytest.raises(GameOver):
        apply_move(g, "X", 0, 0)


def test_win_on_final_cell_beats_draw():
    # The 9th move fills the board AND completes X's main diagonal.
    seq = [(0, 0), (0, 1), (0, 2), (1, 0), (2, 1), (1, 2), (1, 1), (2, 0), (2, 2)]
    g = play(new_game(), seq)
    assert g.version == 9
    assert all(v is not None for line in g.board for v in line)
    assert g.status == "won" and g.winner == "X"
    assert g.winning_line == ((0, 0), (1, 1), (2, 2))


# --- generalised boards (k-aware) ----------------------------------------


def test_k_less_than_board_size():
    g = new_game(5, 5, 4)
    # X plays row 2 cols 0..3, O plays row 4.
    g = play(g, [(2, 0), (4, 0), (2, 1), (4, 1), (2, 2), (4, 2), (2, 3)])
    assert g.status == "won" and g.winner == "X"
    assert g.winning_line == ((2, 0), (2, 1), (2, 2), (2, 3))


def test_three_in_a_row_is_not_enough_when_k_is_four():
    g = new_game(5, 5, 4)
    g = play(g, [(2, 0), (4, 0), (2, 1), (4, 1), (2, 2)])
    assert g.status == "in_progress"


def test_non_square_board_vertical_and_diagonal():
    g = play(new_game(3, 5, 3), [(0, 4), (0, 0), (1, 4), (0, 1), (2, 4)])
    assert g.winner == "X" and g.winning_line == ((0, 4), (1, 4), (2, 4))
    g = play(new_game(4, 3, 3), [(0, 0), (0, 1), (1, 1), (0, 2), (2, 2)])
    assert g.winner == "X"


def test_win_found_when_last_move_fills_the_gap_in_the_middle():
    g = play(new_game(), [(0, 0), (1, 0), (0, 2), (1, 1), (0, 1)])
    assert g.winner == "X" and g.winning_line == ((0, 0), (0, 1), (0, 2))


def test_one_by_one_board_k1_wins_immediately():
    g = play(new_game(1, 1, 1), [(0, 0)])
    assert g.status == "won" and g.winner == "X"


# --- replay ---------------------------------------------------------------


def test_replay_reproduces_state():
    g = play(new_game(), DRAW[:6])
    assert replay(3, 3, 3, g.moves) == g


def test_replay_rejects_corrupt_log():
    g = play(new_game(), [(0, 0), (1, 1)])
    bad = (*g.moves, g.moves[0])  # replays X onto an occupied cell
    with pytest.raises(CellTaken):
        replay(3, 3, 3, bad)


# --- properties -----------------------------------------------------------


@given(
    st.integers(1, 5),
    st.integers(1, 5),
    st.data(),
)
def test_random_legal_games_keep_invariants(rows, cols, data):
    k = data.draw(st.integers(1, max(rows, cols)))
    g = new_game(rows, cols, k)
    cells = [(r, c) for r in range(rows) for c in range(cols)]
    order = data.draw(st.permutations(cells))

    for row, col in order:
        if g.status != "in_progress":
            break
        g = apply_move(g, g.next_player, row, col)
        marks = sum(v is not None for line in g.board for v in line)
        assert marks == g.version
        xs = sum(v == "X" for line in g.board for v in line)
        os_ = sum(v == "O" for line in g.board for v in line)
        assert xs - os_ in (0, 1)
        if g.status == "won":
            assert g.winner == g.moves[-1].player
            assert len(g.winning_line) >= k
            assert (row, col) in g.winning_line
            assert all(g.board[r][c] == g.winner for r, c in g.winning_line)
        else:
            assert g.winner is None and g.winning_line == ()
        if g.status == "draw":
            assert g.version == rows * cols

    assert g.status in ("won", "draw") or g.version < rows * cols
    assert replay(rows, cols, k, g.moves) == g
