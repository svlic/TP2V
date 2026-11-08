from pathlib import Path

import pytest

from tp2v.config import load_config


def test_loads_example_with_independent_modes():
    config = load_config(Path(__file__).parents[1] / "config.example.toml")
    assert [route.mode for route in config.routes] == ["nearby", "search"]
    assert config.routes[0].window == 5
    assert config.routes[1].search_limit == 100


@pytest.mark.parametrize(
    "extra",
    [
        'mode="invalid"',
        "window=0",
        "search_limit=-1",
        "window=true",
        'typo="search"',
    ],
)
def test_rejects_invalid_route_options(tmp_path, extra):
    path = tmp_path / "config.toml"
    path.write_text('[[routes]]\nname="one"\na=1\nsource=2\nb=3\n' + extra)
    with pytest.raises((ValueError, TypeError)):
        load_config(path)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "routes=[]",
        "typo=true",
        '[[routes]]\nname="one"\na=1\nsource=2\nb=1',
        '[[routes]]\nname="one"\na=true\nsource=2\nb=3',
        '[[routes]]\nname="one"\na="@"\nsource=2\nb=3',
        '[[routes]]\nname="one"\na=1\nsource=2\nb=3\n[[routes]]\nname="one"\na=4\nsource=5\nb=6',
    ],
)
def test_rejects_empty_duplicate_or_unsafe_config(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body)
    with pytest.raises(ValueError):
        load_config(path)


def test_normalizes_username_and_numeric_string(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[[routes]]\nname="one"\na="-100123"\nsource="@source"\nb="output"')
    route = load_config(path).routes[0]
    assert (route.a, route.source, route.b, route.mode) == (-100123, "source", "output", "nearby")
