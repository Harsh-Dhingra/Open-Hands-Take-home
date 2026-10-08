import pytest

WIN_X = [("X", 0, 0), ("O", 1, 0), ("X", 0, 1), ("O", 1, 1), ("X", 0, 2)]
DRAW = [
    ("X", 0, 0), ("O", 0, 1), ("X", 0, 2), ("O", 1, 1), ("X", 1, 0),
    ("O", 1, 2), ("X", 2, 1), ("O", 2, 0), ("X", 2, 2),
]  # fmt: skip


def new_game(client) -> str:
    return client.post("/games").json()["id"]


def move(client, gid, player, row, col):
    return client.post(f"/games/{gid}/moves", json={"player": player, "row": row, "col": col})


def play(client, gid, moves):
    r = None
    for player, row, col in moves:
        r = move(client, gid, player, row, col)
        assert r.status_code == 200, r.text
    return r.json()


def assert_error(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]


# --- basics ---------------------------------------------------------------


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_game(client):
    r = client.post("/games")
    assert r.status_code == 201
    body = r.json()
    assert r.headers["location"] == f"/games/{body['id']}"
    assert body["board"] == [[None] * 3 for _ in range(3)]
    assert body["next_player"] == "X"
    assert body["status"] == "in_progress"
    assert body["winner"] is None and body["winning_line"] == []
    assert (body["rows"], body["cols"], body["k"], body["version"]) == (3, 3, 3, 0)


def test_games_have_distinct_ids_and_independent_state(client):
    a, b = new_game(client), new_game(client)
    assert a != b
    move(client, a, "X", 0, 0)
    assert client.get(f"/games/{b}").json()["version"] == 0


def test_get_game_matches_move_response(client):
    gid = new_game(client)
    after = move(client, gid, "X", 1, 1).json()
    assert client.get(f"/games/{gid}").json() == after
    assert after["board"][1][1] == "X"
    assert after["next_player"] == "O" and after["version"] == 1


def test_list_games_newest_first(client):
    a, b = new_game(client), new_game(client)
    move(client, a, "X", 0, 0)
    assert client.get("/games").json() == [
        {"id": b, "status": "in_progress", "version": 0},
        {"id": a, "status": "in_progress", "version": 1},
    ]


# --- full games -----------------------------------------------------------


def test_play_to_a_win(client):
    gid = new_game(client)
    final = play(client, gid, WIN_X)
    assert final["status"] == "won" and final["winner"] == "X"
    assert final["winning_line"] == [[0, 0], [0, 1], [0, 2]]
    assert final["next_player"] is None


def test_play_to_a_draw(client):
    gid = new_game(client)
    final = play(client, gid, DRAW)
    assert final["status"] == "draw" and final["winner"] is None
    assert final["winning_line"] == [] and final["version"] == 9


def test_move_history_in_order(client):
    gid = new_game(client)
    play(client, gid, WIN_X)
    assert client.get(f"/games/{gid}/moves").json() == [
        {"n": i, "player": p, "row": r, "col": c} for i, (p, r, c) in enumerate(WIN_X, 1)
    ]


# --- rule violations (409 / 422) -----------------------------------------


def test_cell_taken_409_and_state_unchanged(client):
    gid = new_game(client)
    move(client, gid, "X", 0, 0)
    assert_error(move(client, gid, "O", 0, 0), 409, "cell_taken")
    state = client.get(f"/games/{gid}").json()
    assert state["version"] == 1 and state["next_player"] == "O"


def test_not_your_turn_409(client):
    gid = new_game(client)
    assert_error(move(client, gid, "O", 0, 0), 409, "not_your_turn")
    move(client, gid, "X", 0, 0)
    assert_error(move(client, gid, "X", 1, 1), 409, "not_your_turn")


def test_move_after_game_over_409(client):
    gid = new_game(client)
    play(client, gid, WIN_X)
    assert_error(move(client, gid, "O", 2, 2), 409, "game_over")


@pytest.mark.parametrize("row,col", [(-1, 0), (0, -1), (3, 0), (0, 3)])
def test_out_of_bounds_422(client, row, col):
    gid = new_game(client)
    assert_error(move(client, gid, "X", row, col), 422, "out_of_bounds")


# --- not found ------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [("get", "/games/nope"), ("get", "/games/nope/moves"), ("post", "/games/nope/moves")],
)
def test_unknown_game_404(client, method, path):
    kwargs = {"json": {"player": "X", "row": 0, "col": 0}} if method == "post" else {}
    assert_error(getattr(client, method)(path, **kwargs), 404, "not_found")


# --- malformed requests ---------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"player": "X", "row": 0},
        {"player": "Z", "row": 0, "col": 0},
        {"player": "x", "row": 0, "col": 0},
        {"player": "X", "row": "0", "col": 0},
        {"player": "X", "row": 0.5, "col": 0},
        {"player": "X", "row": None, "col": 0},
        [],
    ],
)
def test_malformed_move_body_422(client, payload):
    gid = new_game(client)
    assert_error(client.post(f"/games/{gid}/moves", json=payload), 422, "validation_error")
    assert client.get(f"/games/{gid}").json()["version"] == 0


def test_non_json_body_422(client):
    gid = new_game(client)
    r = client.post(
        f"/games/{gid}/moves", content="not json", headers={"content-type": "application/json"}
    )
    assert_error(r, 422, "validation_error")
