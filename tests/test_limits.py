import pytest

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
