import pytest
from hypothesis import given
from hypothesis import strategies as st

from app import limits
from app.engine import InvalidConfig, new_game
from app.limits import validate_config


@pytest.mark.parametrize(
    "rows,cols,k",
    [(3, 3, 3), (20, 20, 20), (3, 20, 20), (20, 3, 20), (10, 4, 10), (5, 5, 3)],
)
def test_supported_configs_pass_and_the_engine_accepts_them(rows, cols, k):
    validate_config(rows, cols, k)
    assert new_game(rows, cols, k).version == 0


@pytest.mark.parametrize(
    "rows,cols,k,fragment",
    [
        (2, 3, 3, "rows must be between 3 and 20"),
        (21, 3, 3, "rows must be between 3 and 20"),
        (3, 2, 3, "cols must be between 3 and 20"),
        (3, 21, 3, "cols must be between 3 and 20"),
        (0, 3, 3, "rows"),
        (-5, 3, 3, "rows"),
        (10**9, 3, 3, "rows"),
        (3, 3, 2, "k must be between 3 and 3 for a 3x3 board"),
        (3, 3, 4, "k must be between 3 and 3 for a 3x3 board"),
        (10, 4, 11, "k must be between 3 and 10 for a 10x4 board"),
        (3, 3, 0, "k must be"),
        (3, 3, -1, "k must be"),
    ],
)
def test_unsupported_configs_name_the_problem(rows, cols, k, fragment):
    with pytest.raises(InvalidConfig, match=fragment) as exc:
        validate_config(rows, cols, k)
    assert exc.value.code == "invalid_config"


def test_describe_matches_the_constants():
    d = limits.describe()
    assert d["defaults"] == {"rows": 3, "cols": 3, "k": 3}
    assert d["limits"] == {"min_size": 3, "max_size": 20, "min_k": 3}
    validate_config(**d["defaults"])


_any_int = st.integers(-(10**18), 10**18)


@given(_any_int, _any_int, _any_int)
def test_validate_config_accepts_exactly_the_documented_space(rows, cols, k):
    """Random integers across the whole range, including huge and negative values."""
    allowed = 3 <= rows <= 20 and 3 <= cols <= 20 and 3 <= k <= max(rows, cols)
    try:
        validate_config(rows, cols, k)
        accepted = True
    except InvalidConfig:
        accepted = False  # any other exception type would fail the test
    assert accepted == allowed


@given(st.integers(3, 20), st.integers(3, 20), st.data())
def test_every_supported_config_builds_a_game(rows, cols, data):
    k = data.draw(st.integers(3, max(rows, cols)))
    validate_config(rows, cols, k)
    g = new_game(rows, cols, k)
    assert len(g.board) == rows and len(g.board[0]) == cols
