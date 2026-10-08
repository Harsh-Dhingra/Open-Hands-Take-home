import sqlite3
import threading

import pytest

from app.ai import Opponent
from app.engine import CellTaken, Move, apply_move, replay
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


# --- configurable boards: stored dimensions drive replay -------------------


def test_custom_board_survives_reopen_and_can_be_finished(path):
    first = SqliteRepository(path)
    gid, _ = first.create(5, 5, 4)
    for r, c in [(2, 0), (4, 0), (2, 1), (4, 1), (2, 2)]:
        play_x(first, gid, r, c)
    before = first.get(gid)
    del first

    second = SqliteRepository(path)
    assert second.get(gid) == before
    assert (before.rows, before.cols, before.k) == (5, 5, 4)
    assert [(i, g.version) for i, g in second.list()] == [(gid, 5)]
    play_x(second, gid, 4, 2)  # O
    won = play_x(second, gid, 2, 3)  # X completes four in row 2
    assert won.status == "won" and won.winning_line == ((2, 0), (2, 1), (2, 2), (2, 3))
    assert SqliteRepository(path).get(gid) == won


def test_replay_bounds_come_from_the_stored_board_size(path, repo):
    small, _ = repo.create()  # 3x3
    big, _ = repo.create(5, 5, 4)
    _insert_move(path, small, 1, "X", 4, 4)  # outside 3x3
    _insert_move(path, big, 1, "X", 4, 4)  # corner of 5x5
    with pytest.raises(CorruptGame):
        repo.get(small)
    assert repo.get(big).board[4][4] == "X"


# --- the opponent columns: migration and constraints -------------------------------

# The schema exactly as the first releases created it (no opponent columns).
LEGACY_DDL = """
CREATE TABLE games (
    id     TEXT PRIMARY KEY,
    n_rows INTEGER NOT NULL,
    n_cols INTEGER NOT NULL,
    k      INTEGER NOT NULL
);
CREATE TABLE moves (
    game_id TEXT    NOT NULL REFERENCES games(id),
    n       INTEGER NOT NULL,
    player  TEXT    NOT NULL CHECK (player IN ('X', 'O')),
    row_idx INTEGER NOT NULL,
    col_idx INTEGER NOT NULL,
    PRIMARY KEY (game_id, n),
    UNIQUE (game_id, row_idx, col_idx)
);
"""


def make_legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_DDL)
    conn.execute("INSERT INTO games VALUES ('won1', 3, 3, 3)")
    conn.execute("INSERT INTO games VALUES ('live1', 3, 3, 3)")
    won = [("X", 0, 0), ("O", 1, 0), ("X", 0, 1), ("O", 1, 1), ("X", 0, 2)]
    for n, (player, r, c) in enumerate(won, 1):
        conn.execute("INSERT INTO moves VALUES ('won1', ?, ?, ?, ?)", (n, player, r, c))
    conn.execute("INSERT INTO moves VALUES ('live1', 1, 'X', 1, 1)")
    conn.commit()
    conn.close()


def columns(path):
    return [r[1] for r in rows(path, "PRAGMA table_info(games)")]


def test_a_database_from_an_older_version_is_migrated_and_keeps_its_games(path):
    make_legacy_db(path)
    assert "difficulty" not in columns(path)

    repo = SqliteRepository(path)

    assert columns(path)[-2:] == ["computer_player", "difficulty"]
    won, live = repo.get("won1"), repo.get("live1")
    assert won.status == "won" and won.winner == "X" and won.version == 5
    assert live.version == 1 and live.next_player == "O"
    assert repo.opponent_of("won1") is None and repo.opponent_of("live1") is None
    assert [i for i, _ in repo.list()] == ["live1", "won1"]  # rowid order is preserved
    # An old game is still playable, and a new computer game works beside it.
    assert repo.update("live1", lambda g: apply_move(g, "O", 0, 0)).version == 2
    gid, _ = repo.create(opponent=Opponent("O", "hard"))
    assert repo.opponent_of(gid) == Opponent("O", "hard")


def test_migration_is_idempotent_and_data_survives_reopening(path):
    make_legacy_db(path)
    SqliteRepository(path)
    first = columns(path)
    repo = SqliteRepository(path)  # second open: nothing left to migrate
    gid, _ = repo.create(opponent=Opponent("X", "easy"))
    assert columns(path) == first
    assert SqliteRepository(path).opponent_of(gid) == Opponent("X", "easy")


def test_several_processes_migrating_at_once_do_not_fail(path):
    make_legacy_db(path)
    errors = []

    def open_repo():
        try:
            SqliteRepository(path)
        except Exception as exc:  # noqa: BLE001 - any failure is a test failure
            errors.append(exc)

    threads = [threading.Thread(target=open_repo) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert columns(path).count("difficulty") == 1


@pytest.mark.parametrize("column,bad", [("computer_player", "Z"), ("difficulty", "impossible")])
def test_opponent_columns_reject_values_outside_their_domain(path, repo, column, bad):
    sql = f"INSERT INTO games (id, n_rows, n_cols, k, {column}) VALUES ('g', 3, 3, 3, ?)"
    conn = raw(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, (bad,))
    finally:
        conn.close()


def test_migrated_databases_enforce_the_same_domains(path):
    make_legacy_db(path)
    SqliteRepository(path)
    conn = raw(path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE games SET difficulty = 'impossible' WHERE id = 'won1'")
    conn.close()


def test_opening_moves_are_saved_with_the_game_in_order(path, repo):
    gid, _ = repo.create(opponent=Opponent("X", "hard"), moves=(Move(1, "X", 2, 2),))
    assert rows(path, "SELECT n, player, row_idx, col_idx FROM moves WHERE game_id=?", gid) == [
        (1, "X", 2, 2)
    ]
    assert rows(path, "SELECT computer_player, difficulty FROM games WHERE id=?", gid) == [
        ("X", "hard")
    ]


def test_a_failure_while_saving_the_opening_leaves_no_game_behind(path, repo, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(SqliteRepository, "_insert_moves", staticmethod(explode))
    with pytest.raises(RuntimeError):
        repo.create(opponent=Opponent("X", "hard"), moves=(Move(1, "X", 1, 1),))
    assert rows(path, "SELECT COUNT(*) FROM games") == [(0,)]
    assert rows(path, "SELECT COUNT(*) FROM moves") == [(0,)]


def test_losing_the_migration_race_is_not_an_error(path):
    # Simulate another process adding the columns between our check and our ALTER:
    # the column list we see is stale (empty) although the columns already exist.
    SqliteRepository(path)
    real = raw(path)

    class StaleView:
        def execute(self, sql, *args):
            if sql.startswith("PRAGMA table_info"):
                return []
            return real.execute(sql, *args)

    SqliteRepository._migrate(StaleView())  # must not raise "duplicate column name"
    assert columns(path).count("computer_player") == 1
    real.close()


def test_a_migration_failure_other_than_the_race_is_not_swallowed(path):
    make_legacy_db(path)
    conn = raw(path)
    conn.execute("PRAGMA query_only = ON")  # ALTER will fail: attempt to write a readonly database
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        SqliteRepository._migrate(conn)
    conn.close()
    assert "difficulty" not in columns(path)
