"""expected_version over HTTP (both storage backends via the client fixture)."""

import pytest


def new_game(client):
    return client.post("/games").json()["id"]


def move(client, gid, player, row, col, **extra):
    body = {"player": player, "row": row, "col": col, **extra}
    return client.post(f"/games/{gid}/moves", json=body)


def assert_stale(resp, current_version):
    assert resp.status_code == 409, resp.text
    err = resp.json()["error"]
    assert err["code"] == "stale_version" and err["message"]
    assert err["current_version"] == current_version


# --- compatibility: the field is optional -----------------------------------


@pytest.mark.parametrize("extra", [{}, {"expected_version": None}], ids=["omitted", "null"])
def test_move_without_expected_version_behaves_as_before(client, extra):
    gid = new_game(client)
    r = move(client, gid, "X", 0, 0, **extra)
    assert r.status_code == 200 and r.json()["version"] == 1
    # Rule errors are unchanged when no version is supplied.
    assert move(client, gid, "X", 1, 1, **extra).json()["error"]["code"] == "not_your_turn"
    assert move(client, gid, "O", 0, 0, **extra).json()["error"]["code"] == "cell_taken"


def test_error_bodies_without_staleness_carry_no_current_version(client):
    gid = new_game(client)
    err = move(client, gid, "O", 0, 0).json()["error"]
    assert "current_version" not in err


# --- the check -------------------------------------------------------------


def test_matching_version_is_applied_and_advances_by_one(client):
    gid = new_game(client)
    r = move(client, gid, "X", 0, 0, expected_version=0)
    assert r.status_code == 200 and r.json()["version"] == 1


@pytest.mark.parametrize("expected", [0, 2, 99])  # behind and ahead of version 1
def test_mismatched_version_is_409_with_current_version_and_changes_nothing(client, expected):
    gid = new_game(client)
    move(client, gid, "X", 0, 0)
    before = client.get(f"/games/{gid}").json()
    assert_stale(move(client, gid, "O", 1, 1, expected_version=expected), current_version=1)
    assert client.get(f"/games/{gid}").json() == before
    assert len(client.get(f"/games/{gid}/moves").json()) == 1


def test_retrying_an_applied_move_reports_stale_not_a_rule_error(client):
    gid = new_game(client)
    assert move(client, gid, "X", 0, 0, expected_version=0).status_code == 200
    # Same request again (a client retry after a lost response).
    assert_stale(move(client, gid, "X", 0, 0, expected_version=0), current_version=1)


def test_a_stale_client_can_recover_using_current_version(client):
    gid = new_game(client)
    move(client, gid, "X", 0, 0)
    stale = move(client, gid, "O", 1, 1, expected_version=0)
    retry = move(client, gid, "O", 1, 1, expected_version=stale.json()["error"]["current_version"])
    assert retry.status_code == 200 and retry.json()["version"] == 2


@pytest.mark.parametrize("bad", [-1, 1.5, "1", True, [1], {"v": 1}])
def test_invalid_expected_version_is_422_validation_error(client, bad):
    gid = new_game(client)
    r = move(client, gid, "X", 0, 0, expected_version=bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.get(f"/games/{gid}").json()["version"] == 0


def test_whole_game_with_chained_versions(client):
    gid = new_game(client)
    plays = [("X", 0, 0), ("O", 1, 0), ("X", 0, 1), ("O", 1, 1), ("X", 0, 2)]
    version = 0
    for player, row, col in plays:
        r = move(client, gid, player, row, col, expected_version=version)
        assert r.status_code == 200, r.text
        assert r.json()["version"] == version + 1
        version = r.json()["version"]
    assert r.json()["status"] == "won"
