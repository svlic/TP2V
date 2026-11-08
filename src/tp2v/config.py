from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Route:
    name: str
    a: int | str
    source: int | str
    b: int | str
    mode: str = "nearby"
    window: int = 5
    search_limit: int = 100


@dataclass(frozen=True)
class Config:
    routes: tuple[Route, ...]
    session: str = "data/tp2v"
    database: str = "data/state.sqlite3"


def load_config(path: str | Path) -> Config:
    with Path(path).open("rb") as stream:
        data = tomllib.load(stream)
    if unknown := data.keys() - {"routes", "session", "database"}:
        raise ValueError(f"unknown configuration keys: {sorted(unknown)}")
    entries = data.get("routes")
    if not isinstance(entries, list) or not entries:
        raise ValueError("at least one [[routes]] entry is required")
    routes = []
    names = set()
    for entry in entries:
        route = Route(**entry)
        if not isinstance(route.name, str) or not route.name.strip() or route.name in names:
            raise ValueError("route names must be nonempty and unique")
        names.add(route.name)
        if route.mode not in {"nearby", "search"}:
            raise ValueError(f"{route.name}: mode must be nearby or search")
        for field in ("window", "search_limit"):
            value = getattr(route, field)
            if type(value) is not int or value < 1:
                raise ValueError(f"{route.name}: {field} must be a positive integer")
        channels = {}
        for field in ("a", "source", "b"):
            value = getattr(route, field)
            if isinstance(value, str):
                value = value.strip()
                value = int(value) if value.lstrip("-").isdigit() else value.removeprefix("@")
            if (type(value) is not int and not isinstance(value, str)) or not value:
                raise ValueError(f"{route.name}: {field} must be a channel id or username")
            channels[field] = value
        if len(set(channels.values())) != 3:
            raise ValueError(f"{route.name}: a, source and b must be different channels")
        routes.append(Route(**{**entry, **channels}))
    for field in ("session", "database"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            raise ValueError(f"{field} must be a nonempty path")
    return Config(tuple(routes), **{key: data[key] for key in ("session", "database") if key in data})
