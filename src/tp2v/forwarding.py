from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from pathlib import Path

from telethon import utils

from .config import Route

log = logging.getLogger(__name__)
CODE_RE = re.compile(r"(?<!\d)\d{6,7}(?!\d)")


def extract_code(text: str) -> str | None:
    match = CODE_RE.search(text)
    return match[0] if match else None


def contains_code(text: str, code: str) -> bool:
    return re.search(rf"(?<!\d){re.escape(code)}(?!\d)", text) is not None


def message_key(message) -> tuple[str, int]:
    return ("album", message.grouped_id) if message.grouped_id else ("message", message.id)


async def whole_message(client, chat, message) -> list:
    if not message.grouped_id:
        return [message]
    # Telegram albums contain at most ten adjacent messages. Fetch by IDs so
    # unrelated/deleted messages do not change the expansion window.
    nearby = await client.get_messages(chat, ids=list(range(max(1, message.id - 10), message.id + 11)))
    members = {item.id: item for item in nearby if item and item.grouped_id == message.grouped_id}
    members[message.id] = message
    return sorted(members.values(), key=lambda item: item.id)


async def find_match(client, route: Route, messages: list, code: str) -> list | None:
    origin = next(
        (
            item.fwd_from.channel_post
            for item in messages
            if item.fwd_from
            and item.fwd_from.from_id
            and item.fwd_from.channel_post
            and utils.get_peer_id(item.fwd_from.from_id) == route.source
        ),
        None,
    )
    anchor = await client.get_messages(route.source, ids=origin) if origin else None
    if route.mode == "nearby" and anchor is None:
        return None
    original = await whole_message(client, route.source, anchor) if anchor else []
    excluded = {item.id for item in original}
    if route.mode == "search":
        seen = set()
        async for candidate in client.iter_messages(
            route.source, search=f'"{code}"', limit=route.search_limit
        ):
            key = message_key(candidate)
            if key in seen or candidate.id in excluded:
                continue
            seen.add(key)
            logical = await whole_message(client, route.source, candidate)
            if any(contains_code(item.message or "", code) for item in logical):
                return logical
        return None

    for newer in (True, False):
        cursor = max(excluded) if newer else min(excluded)
        seen = {message_key(item) for item in original}
        count = 0
        while count < route.window:
            kwargs = {"min_id": cursor, "reverse": True} if newer else {"max_id": cursor}
            raw = [item async for item in client.iter_messages(route.source, limit=60, **kwargs)]
            if not raw:
                break
            for candidate in raw:
                key = message_key(candidate)
                if key in seen:
                    continue
                seen.add(key)
                count += 1
                logical = await whole_message(client, route.source, candidate)
                if any(contains_code(item.message or "", code) for item in logical):
                    return logical
                if count == route.window:
                    break
            next_cursor = max(item.id for item in raw) if newer else min(item.id for item in raw)
            if len(raw) < 60 or next_cursor == cursor:
                break
            cursor = next_cursor
    return None


class Forwarder:
    def __init__(self, client, routes: tuple[Route, ...], database: str) -> None:
        self.client = client
        self.routes = routes
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS forwarded "
            "(route TEXT, chat INTEGER, kind TEXT, message INTEGER, PRIMARY KEY (route, chat, kind, message))"
        )
        self.db.commit()
        self.lock = asyncio.Lock()

    def close(self) -> None:
        self.db.close()

    async def process(self, messages: list) -> None:
        messages = sorted(messages, key=lambda item: item.id)
        code = extract_code("\n".join(item.message or "" for item in messages))
        if code is None:
            return
        chat = messages[0].chat_id
        async with self.lock:
            for route in self.routes:
                if route.a != chat:
                    continue
                kind, identity = message_key(messages[0])
                key = (route.name, chat, kind, identity)
                if self.db.execute(
                    "SELECT 1 FROM forwarded WHERE route=? AND chat=? AND kind=? AND message=?", key
                ).fetchone():
                    continue
                try:
                    target = await find_match(self.client, route, messages, code)
                    if target is None:
                        log.warning(
                            "route=%s code=%s input=%s/%s unmatched", route.name, code, chat, identity
                        )
                        continue
                    await self.client.forward_messages(
                        route.b, [item.id for item in target], from_peer=route.source
                    )
                    self.db.execute("INSERT INTO forwarded VALUES (?, ?, ?, ?)", key)
                    self.db.commit()
                    log.info(
                        "route=%s mode=%s code=%s source=%s/%s destination=%s forwarded",
                        route.name,
                        route.mode,
                        code,
                        route.source,
                        [item.id for item in target],
                        route.b,
                    )
                except Exception:
                    log.exception("route=%s code=%s input=%s/%s failed", route.name, code, chat, identity)
