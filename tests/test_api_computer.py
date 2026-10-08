"""Playing against the computer over HTTP (both storage backends via the repo fixture)."""

import random

import pytest
from fastapi.testclient import TestClient

from app.ai import DIFFICULTIES, Opponent
from app.api import create_app
from app.engine import Move

HUMAN_X = Opponent("O", "hard")  # the computer plays O


@pytest.fixture
def make_client(repo):
    def build(seed=1):
        return TestClient(create_app(repo, rng=random.Random(seed)))

    return build


@pytest.fixture
def client(make_client):
    return make_client()


def create(client, **body):
    return client.post("/games", json={"opponent": "computer", **body})


def move(client, gid, player, row, col, **extra):
    body = {"player": player, "row": row, "col": col, **extra}
    return client.post(f"/games/{gid}/moves", json=body)


def free_cells(game):
    return [(r, c) for r, line in enumerate(game["board"]) for c, v in enumerate(line) if v is None]


def error_code(resp):
    return resp.json()["error"]["code"]


def seed_game(repo, opponent, cells):
    """A computer game already in progress: `cells` are played in order (X first)."""
    moves = [Move(i + 1, "XO"[i % 2], r, c) for i, (r, c) in enumerate(cells)]
    return repo.create(opponent=opponent, moves=moves)[0]


# --- creating a game --------------------------------------------------------------


def test_defaults_are_medium_with_the_human_as_x(client):
    r = create(client)
    assert r.status_code == 201
    g = r.json()
    assert g["opponent"] == {"type": "computer", "difficulty": "medium", "computer_player": "O"}
    assert g["version"] == 0 and g["next_player"] == "X"
    assert all(v is None for line in g["board"] for v in line)


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_each_difficulty_is_echoed(client, difficulty):
    assert create(client, difficulty=difficulty).json()["opponent"]["difficulty"] == difficulty


def test_when_the_human_plays_o_the_computer_opens_as_x(client):
    g = create(client, human_plays="O", difficulty="hard").json()
    assert g["opponent"]["computer_player"] == "X"
    assert g["version"] == 1 and g["next_player"] == "O"
    assert sum(v == "X" for line in g["board"] for v in line) == 1
    history = client.get(f"/games/{g['id']}/moves").json()
    assert [(m["n"], m["player"]) for m in history] == [(1, "X")]


def test_the_opponent_is_part_of_get_list_and_human_games_have_none(client):
    vs_computer = create(client, difficulty="easy").json()["id"]
    vs_human = client.post("/games").json()
    assert vs_human["opponent"] is None
    assert client.get(f"/games/{vs_computer}").json()["opponent"]["difficulty"] == "easy"
    listed = {g["id"]: g["opponent"] for g in client.get("/games").json()}
    assert listed[vs_human["id"]] is None
    assert listed[vs_computer]["computer_player"] == "O"


@pytest.mark.parametrize(
    "body,code",
    [
        ({"opponent": "alien"}, "validation_error"),
        ({"opponent": "computer", "difficulty": "impossible"}, "validation_error"),
        ({"opponent": "computer", "human_plays": "Z"}, "validation_error"),
        ({"opponent": "computer", "level": "hard"}, "validation_error"),
        ({"opponent": "computer", "rows": 4, "cols": 4, "k": 4}, "invalid_config"),
        ({"opponent": "computer", "rows": 3, "cols": 4}, "invalid_config"),
        ({"opponent": "computer", "rows": 5}, "invalid_config"),
        ({"difficulty": "hard"}, "invalid_config"),  # a difficulty needs a computer
        ({"human_plays": "O"}, "invalid_config"),
        ({"opponent": "human", "difficulty": "easy"}, "invalid_config"),
    ],
)
def test_invalid_computer_games_are_rejected_and_create_nothing(client, body, code):
    r = client.post("/games", json=body)
    assert r.status_code == 422 and error_code(r) == code
    assert client.get("/games").json() == []


def test_computer_games_name_the_problem_for_other_boards(client):
    r = client.post("/games", json={"opponent": "computer", "rows": 4, "cols": 4, "k": 4})
    assert "3x3" in r.json()["error"]["message"]


# --- a turn ---------------------------------------------------------------------------


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_the_response_to_a_move_contains_the_computers_reply(client, difficulty):
    g = create(client, difficulty=difficulty).json()
    r = move(client, g["id"], "X", 1, 1, expected_version=0)
    assert r.status_code == 200
    after = r.json()
    assert after["version"] == 2 and after["next_player"] == "X"
    assert after["opponent"]["difficulty"] == difficulty
    history = client.get(f"/games/{g['id']}/moves").json()
    assert [(m["n"], m["player"]) for m in history] == [(1, "X"), (2, "O")]
    assert (history[0]["row"], history[0]["col"]) == (1, 1)
    reply = (history[1]["row"], history[1]["col"])
    assert reply != (1, 1) and after["board"][reply[0]][reply[1]] == "O"
    assert client.get(f"/games/{g['id']}").json() == after


def test_playing_as_o_against_a_computer_x(client):
    g = create(client, human_plays="O", difficulty="medium").json()
    r = move(client, g["id"], "O", *free_cells(g)[0], expected_version=g["version"])
    assert r.json()["version"] == 3
    history = client.get(f"/games/{g['id']}/moves").json()
    assert [m["player"] for m in history] == ["X", "O", "X"]


def test_a_human_win_ends_the_game_without_a_reply(repo, client):
    gid = seed_game(repo, HUMAN_X, [(0, 0), (1, 0), (0, 1), (1, 1)])  # X wins at (0,2)
    after = move(client, gid, "X", 0, 2, expected_version=4).json()
    assert after["status"] == "won" and after["winner"] == "X" and after["version"] == 5
    assert after["winning_line"] == [[0, 0], [0, 1], [0, 2]]


def test_a_human_draw_ends_the_game_without_a_reply(repo, client):
    gid = seed_game(repo, HUMAN_X, [(0, 0), (0, 1), (0, 2), (1, 1), (1, 0), (1, 2), (2, 1), (2, 0)])
    after = move(client, gid, "X", 2, 2, expected_version=8).json()
    assert after["status"] == "draw" and after["version"] == 9 and after["next_player"] is None


def test_the_computer_wins_when_the_human_does_not_stop_it(repo, client):
    gid = seed_game(repo, HUMAN_X, [(0, 0), (1, 0), (0, 1), (1, 1)])  # O threatens (1,2)
    after = move(client, gid, "X", 2, 2, expected_version=4).json()  # X ignores both threats
    assert after["status"] == "won" and after["winner"] == "O" and after["version"] == 6
    assert after["winning_line"] == [[1, 0], [1, 1], [1, 2]]


def test_the_same_seed_gives_the_same_computer_replies(repo, make_client):
    plays = []
    for seed in (4, 4):
        client = make_client(seed)
        gid = create(client, difficulty="easy").json()["id"]
        move(client, gid, "X", 1, 1, expected_version=0)
        plays.append(client.get(f"/games/{gid}/moves").json()[1])
    assert plays[0] == plays[1]


# --- rejected requests make no computer move --------------------------------------------


def snapshot(client, gid):
    return client.get(f"/games/{gid}").json(), client.get(f"/games/{gid}/moves").json()


def test_a_request_for_the_computers_side_is_not_your_turn(client):
    gid = create(client).json()["id"]
    before = snapshot(client, gid)
    r = move(client, gid, "O", 1, 1, expected_version=0)
    assert r.status_code == 409 and error_code(r) == "not_your_turn"
    assert snapshot(client, gid) == before


def test_a_stale_request_is_rejected_and_the_computer_stays_silent(client):
    gid = create(client).json()["id"]
    move(client, gid, "X", 1, 1, expected_version=0)  # version is now 2
    before = snapshot(client, gid)
    free = free_cells(before[0])[0]
    r = move(client, gid, "X", *free, expected_version=0)  # a retry of the first request
    assert r.status_code == 409 and error_code(r) == "stale_version"
    assert r.json()["error"]["current_version"] == 2
    assert snapshot(client, gid) == before


@pytest.mark.parametrize("extra", [{}, {"expected_version": None}], ids=["omitted", "null"])
def test_expected_version_is_required_against_the_computer(client, extra):
    gid = create(client).json()["id"]
    before = snapshot(client, gid)
    r = move(client, gid, "X", 1, 1, **extra)
    assert r.status_code == 422 and error_code(r) == "validation_error"
    assert "expected_version" in r.json()["error"]["message"]
    assert snapshot(client, gid) == before


def test_human_games_still_do_not_need_expected_version(client):
    gid = client.post("/games").json()["id"]
    assert move(client, gid, "X", 0, 0).status_code == 200


@pytest.mark.parametrize(
    "row,col,status,code",
    [(1, 1, 409, "cell_taken"), (7, 0, 422, "out_of_bounds")],
)
def test_invalid_human_moves_change_nothing(client, row, col, status, code):
    gid = create(client, human_plays="O").json()["id"]
    g = client.get(f"/games/{gid}").json()
    taken = next((r, c) for r, line in enumerate(g["board"]) for c, v in enumerate(line) if v)
    cell = taken if code == "cell_taken" else (row, col)
    before = snapshot(client, gid)
    r = move(client, gid, "O", *cell, expected_version=g["version"])
    assert r.status_code == status and error_code(r) == code
    assert snapshot(client, gid) == before


def test_moves_after_the_game_is_over_are_rejected(repo, client):
    gid = seed_game(repo, HUMAN_X, [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)])
    r = move(client, gid, "X", 2, 2, expected_version=5)
    assert r.status_code == 409 and error_code(r) == "game_over"
