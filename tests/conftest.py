import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.store import InMemoryRepository


@pytest.fixture
def client():
    return TestClient(create_app(InMemoryRepository()))
