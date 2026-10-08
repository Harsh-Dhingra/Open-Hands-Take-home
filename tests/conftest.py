import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.store import InMemoryRepository, SqliteRepository


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    """Every repository implementation must pass the same contract tests."""
    if request.param == "memory":
        return InMemoryRepository()
    return SqliteRepository(tmp_path / "games.db")


@pytest.fixture
def client(repo):
    return TestClient(create_app(repo))
