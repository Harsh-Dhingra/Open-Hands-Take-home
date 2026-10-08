import sqlite3
import threading

import pytest

from app.engine import CellTaken, apply_move, replay
from app.store import CorruptGame, GameNotFound, SqliteRepository


@pytest.fixture
def path(tmp_path):
    return tmp_path / "games.db"


@pytest.fixture
def repo(path):
    return SqliteRepository(path)


def raw(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def rows(path, sql, *args):
    conn = raw(path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def play_x(repo, gid, row, col):
    return repo.update(gid, lambda g: apply_move(g, g.next_player, row, col))


# --- schema ---------------------------------------------------------------


def test_new_file_gets_schema_and_parent_dirs(tmp_path):
    db = tmp_path / "nested" / "dir" / "games.db"
    SqliteRepository(db)
    tables = {r[0] for r in rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"games", "moves"} <= tables


def test_reopening_is_idempotent_and_keeps_data(path):
    gid, _ = SqliteRepository(path).create()
    again = SqliteRepository(path)
    assert [i for i, _ in again.list()] == [gid]


def test_wal_mode_and_foreign_keys_enabled(path, repo):
    assert rows(path, "PRAGMA journal_mode") == [("wal",)]
    with repo._connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)


def test_no_derived_state_is_stored(path, repo):
    cols = {r[1] for r in rows(path, "PRAGMA table_info(games)")}
    assert cols.isdisjoint({"status", "winner", "board"})


# --- DB-level invariants --------------------------------------------------


def _insert_move(path, gid, n, player, r, c):
    conn = raw(path)
    try:
        conn.execute("INSERT INTO moves VALUES (?, ?, ?, ?, ?)", (gid, n, player, r, c))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "args",
    [
        (1, "O", 2, 2),  # duplicate (game_id, n)
        (2, "O", 0, 0),  # same cell played twice
        (2, "Z", 1, 1),  # bad player
    ],
    ids=["duplicate-n", "duplicate-cell", "bad-player"],
)
def test_constraints_reject_bad_rows(path, repo, args):
    gid, _ = repo.create()
    play_x(repo, gid, 0, 0)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_move(path, gid, *args)


def test_orphan_move_rejected(path, repo):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_move(path, "no-such-game", 1, "X", 0, 0)


# --- update semantics -----------------------------------------------------


def test_update_appends_one_row_per_move_and_keeps_earlier_rows(path, repo):
    gid, _ = repo.create()
    play_x(repo, gid, 0, 0)
    first = rows(path, "SELECT * FROM moves ORDER BY n")
    play_x(repo, gid, 1, 1)
    play_x(repo, gid, 2, 2)
    after = rows(path, "SELECT * FROM moves ORDER BY n")
    assert after[:1] == first
    assert after == [(gid, 1, "X", 0, 0), (gid, 2, "O", 1, 1), (gid, 3, "X", 2, 2)]


def test_rejected_update_leaves_no_partial_rows(path, repo):
    gid, _ = repo.create()
    play_x(repo, gid, 1, 1)

    def two_moves_then_fail(g):
        g = apply_move(g, "O", 0, 0)
        return apply_move(g, "X", 1, 1)  # cell taken -> rollback of the whole update

    with pytest.raises(CellTaken):
        repo.update(gid, two_moves_then_fail)
    assert rows(path, "SELECT COUNT(*) FROM moves") == [(1,)]
    assert repo.get(gid).version == 1


def test_state_is_derived_by_replay_of_stored_rows(path, repo):
    gid, _ = repo.create()
    for r, c in [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)]:
        returned = play_x(repo, gid, r, c)
    stored = repo.get(gid)
    assert stored == returned
    assert stored.status == "won" and stored.winner == "X"
    assert stored == replay(3, 3, 3, stored.moves)


def test_get_unknown_and_update_unknown(repo):
    with pytest.raises(GameNotFound):
        repo.get("missing")
    with pytest.raises(GameNotFound):
        repo.update("missing", lambda g: g)


# --- corrupt logs ----------------------------------------------------------

ILLEGAL_LOGS = {
    "wrong-turn": [(1, "X", 0, 0), (2, "X", 1, 1)],
    "out-of-bounds": [(1, "X", 5, 5)],
    "move-after-win": [
        (1, "X", 0, 0),
        (2, "O", 1, 0),
        (3, "X", 0, 1),
        (4, "O", 1, 1),
        (5, "X", 0, 2),
        (6, "O", 2, 2),
    ],  # fmt: skip
    "gap-in-numbering": [(1, "X", 0, 0), (3, "O", 1, 1)],
}


@pytest.mark.parametrize("name", ILLEGAL_LOGS)
def test_illegal_stored_log_raises_corrupt_game(path, repo, name):
    gid, _ = repo.create()
    for move in ILLEGAL_LOGS[name]:
        _insert_move(path, gid, *move)
    with pytest.raises(CorruptGame):
        repo.get(gid)
    with pytest.raises(CorruptGame):
        repo.update(gid, lambda g: g)


# --- multi-connection race (stands in for multiple processes) ----------------


def test_independent_repositories_racing_on_one_file(path):
    gid, _ = SqliteRepository(path).create()
    repos = [SqliteRepository(path) for _ in range(30)]
    results = []

    def attempt(r):
        try:
            r.update(gid, lambda g: apply_move(g, g.next_player, 1, 1))
            results.append("ok")
        except CellTaken:
            results.append("taken")

    threads = [threading.Thread(target=attempt, args=(r,)) for r in repos]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("taken") == 29
    assert rows(path, "SELECT n FROM moves") == [(1,)]


def test_racing_distinct_cells_serialises_without_gaps(path):
    gid, _ = SqliteRepository(path).create()
    repos = [SqliteRepository(path) for _ in range(9)]
    cells = [(r, c) for r in range(3) for c in range(3)]
    outcomes = []

    def attempt(repo, cell):
        try:
            repo.update(gid, lambda g: apply_move(g, g.next_player, *cell))
            outcomes.append("ok")
        except Exception as exc:  # game_over once someone wins
            outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=attempt, args=a) for a in zip(repos, cells, strict=True)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    ns = [r[0] for r in rows(path, "SELECT n FROM moves ORDER BY n")]
    assert ns == list(range(1, len(ns) + 1))
    assert outcomes.count("ok") == len(ns)
    SqliteRepository(path).get(gid)  # still a legal game
