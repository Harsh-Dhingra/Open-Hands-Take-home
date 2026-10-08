import threading

import pytest

from app.engine import CellTaken, InvalidConfig, apply_move
from app.store import GameNotFound


def test_create_and_get_round_trip(repo):
    gid, game = repo.create()
    assert repo.get(gid) == game


def test_get_unknown_raises(repo):
    with pytest.raises(GameNotFound):
        repo.get("missing")


def test_update_saves_and_returns_result(repo):
    gid, _ = repo.create()
    updated = repo.update(gid, lambda g: apply_move(g, "X", 0, 0))
    assert repo.get(gid) == updated and updated.version == 1


def test_update_unknown_raises(repo):
    with pytest.raises(GameNotFound):
        repo.update("missing", lambda g: g)


def test_failed_update_saves_nothing(repo):
    gid, _ = repo.create()
    repo.update(gid, lambda g: apply_move(g, "X", 0, 0))
    with pytest.raises(CellTaken):
        repo.update(gid, lambda g: apply_move(g, "O", 0, 0))
    assert repo.get(gid).version == 1


def test_list_newest_first(repo):
    a, _ = repo.create()
    b, _ = repo.create()
    assert [i for i, _ in repo.list()] == [b, a]


def test_update_is_atomic_under_threads(repo):
    # 30 threads race to play the same cell: the lock makes exactly one win.
    gid, _ = repo.create()
    results = []

    def attempt():
        try:
            repo.update(gid, lambda g: apply_move(g, g.next_player, 1, 1))
            results.append("ok")
        except CellTaken:
            results.append("taken")

    threads = [threading.Thread(target=attempt) for _ in range(30)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("taken") == 29
    assert repo.get(gid).version == 1


def test_create_defaults_to_3x3(repo):
    _, game = repo.create()
    assert (game.rows, game.cols, game.k) == (3, 3, 3)


def test_create_with_custom_size_round_trips(repo):
    gid, game = repo.create(3, 7, 4)
    assert (game.rows, game.cols, game.k) == (3, 7, 4)
    assert len(game.board) == 3 and len(game.board[0]) == 7
    assert repo.get(gid) == game


def test_invalid_config_creates_nothing(repo):
    with pytest.raises(InvalidConfig):
        repo.create(3, 3, 9)
    assert repo.list() == []


def test_non_square_game_plays_to_a_win_and_reads_back(repo):
    gid, _ = repo.create(3, 7, 4)
    for r, c in [(0, 6), (2, 0), (0, 5), (2, 1), (0, 4), (2, 2), (0, 3)]:
        returned = repo.update(gid, lambda g, r=r, c=c: apply_move(g, g.next_player, r, c))
    stored = repo.get(gid)
    assert stored == returned
    assert stored.status == "won" and stored.winner == "X"
    assert stored.winning_line == ((0, 3), (0, 4), (0, 5), (0, 6))
    assert stored.version == 7
