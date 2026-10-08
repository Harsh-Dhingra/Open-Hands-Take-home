"""Board configuration over HTTP (runs on both storage backends via the client fixture)."""

import pytest


def move(client, gid, player, row, col):
    return client.post(f"/games/{gid}/moves", json={"player": player, "row": row, "col": col})


def assert_error(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["error"]["code"] == code
    assert resp.json()["error"]["message"]


# --- defaults ---------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"json": {}}, {"json": {"rows": 3, "cols": 3, "k": 3}}],
    ids=["no-body", "empty-object", "explicit-defaults"],
)
def test_default_board_is_3x3_k3(client, kwargs):
    g = client.post("/games", **kwargs).json()
    assert (g["rows"], g["cols"], g["k"]) == (3, 3, 3)
    assert len(g["board"]) == 3 and all(len(line) == 3 for line in g["board"])


def test_partial_config_fills_in_defaults(client):
    g = client.post("/games", json={"rows": 5}).json()
    assert (g["rows"], g["cols"], g["k"]) == (5, 3, 3)


# --- custom boards ----------------------------------------------------------


@pytest.mark.parametrize(
    "rows,cols,k", [(5, 5, 4), (3, 7, 3), (7, 3, 3), (3, 20, 20), (20, 20, 5), (20, 3, 20)]
)
def test_custom_board_is_created_with_the_requested_shape(client, rows, cols, k):
    r = client.post("/games", json={"rows": rows, "cols": cols, "k": k})
    assert r.status_code == 201
    g = r.json()
    assert (g["rows"], g["cols"], g["k"]) == (rows, cols, k)
    assert len(g["board"]) == rows and all(len(line) == cols for line in g["board"])
    assert client.get(f"/games/{g['id']}").json() == g


def test_moves_are_bounds_checked_against_the_games_own_size(client):
    gid = client.post("/games", json={"rows": 3, "cols": 7, "k": 4}).json()["id"]
    assert_error(move(client, gid, "X", 3, 0), 422, "out_of_bounds")  # only rows 0-2
    assert_error(move(client, gid, "X", 0, 7), 422, "out_of_bounds")  # only cols 0-6
    assert move(client, gid, "X", 2, 6).status_code == 200  # far corner is legal


def test_wide_board_game_is_won_and_reported_over_http(client):
    gid = client.post("/games", json={"rows": 3, "cols": 7, "k": 4}).json()["id"]
    plays = [
        ("X", 1, 2), ("O", 0, 0), ("X", 1, 3), ("O", 0, 1),
        ("X", 1, 4), ("O", 0, 2), ("X", 1, 5),
    ]  # fmt: skip
    for p in plays:
        last = move(client, gid, *p)
        assert last.status_code == 200
    g = last.json()
    assert g["status"] == "won" and g["winner"] == "X"
    assert g["winning_line"] == [[1, 2], [1, 3], [1, 4], [1, 5]]
    assert len(client.get(f"/games/{gid}/moves").json()) == 7


def test_summaries_report_each_games_size(client):
    a = client.post("/games", json={"rows": 5, "cols": 5, "k": 4}).json()["id"]
    b = client.post("/games").json()["id"]
    assert [(g["id"], g["rows"], g["cols"], g["k"]) for g in client.get("/games").json()] == [
        (b, 3, 3, 3),
        (a, 5, 5, 4),
    ]


# --- invalid config ---------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"rows": 2},
        {"rows": 21},
        {"cols": 2},
        {"cols": 99},
        {"rows": 0},
        {"rows": -1},
        {"rows": 10**12},
        {"k": 2},
        {"k": 4},  # default 3x3 board
        {"rows": 10, "cols": 4, "k": 11},
        {"rows": 3, "cols": 3, "k": 0},
    ],
)
def test_out_of_range_config_is_422_invalid_config_and_creates_nothing(client, payload):
    assert_error(client.post("/games", json=payload), 422, "invalid_config")
    assert client.get("/games").json() == []


@pytest.mark.parametrize(
    "payload",
    [
        {"rows": "5"},
        {"rows": 5.5},
        {"rows": True},
        {"rows": None},
        {"rows": [5]},
        {"row": 5},  # typo for "rows": unknown fields are rejected
        {"rows": 5, "extra": 1},
        [],
        "5x5",
    ],
)
def test_wrong_types_or_unknown_fields_are_422_validation_error(client, payload):
    assert_error(client.post("/games", json=payload), 422, "validation_error")
    assert client.get("/games").json() == []


def test_malformed_json_is_422_validation_error(client):
    r = client.post("/games", content="{rows:", headers={"content-type": "application/json"})
    assert_error(r, 422, "validation_error")
    assert client.get("/games").json() == []


def test_error_message_names_the_field_and_range(client):
    msg = client.post("/games", json={"rows": 10, "cols": 4, "k": 11}).json()["error"]["message"]
    assert "k must be between 3 and 10 for a 10x4 board" in msg


# --- discoverable limits ----------------------------------------------------


def test_config_endpoint_publishes_defaults_and_limits(client):
    assert client.get("/config").json() == {
        "defaults": {"rows": 3, "cols": 3, "k": 3},
        "limits": {"min_size": 3, "max_size": 20, "min_k": 3},
    }
