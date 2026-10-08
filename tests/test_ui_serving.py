"""The UI is static files served by the API; behaviour is checked in a browser (see README)."""

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.store import InMemoryRepository


@pytest.fixture
def client():
    return TestClient(create_app(InMemoryRepository()))


def test_root_serves_the_game_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<title>Tic-Tac-Toe</title>" in r.text
    assert 'id="board"' in r.text


def test_assets_are_served_with_correct_types(client):
    css, js = client.get("/style.css"), client.get("/app.js")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_api_routes_take_precedence_over_static_mount(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/games").json() == []
    assert client.post("/games").status_code == 201
    assert client.get("/openapi.json").status_code == 200


def test_unknown_path_is_404(client):
    assert client.get("/nope.txt").status_code == 404


def test_ui_contains_no_game_rules():
    # Guard the architecture: rules live in app.engine only.
    from pathlib import Path

    js = (Path(__file__).parent.parent / "app" / "static" / "app.js").read_text()
    for forbidden in ("winLines", "checkWin", "isDraw", "[0, 1, 2]"):
        assert forbidden not in js
