from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import replace
from pathlib import Path

from telethon import TelegramClient, events, utils

from .config import Config, load_config
from .forwarding import Forwarder


async def run(config: Config) -> None:
    api_id = int(os.environ["TP2V_API_ID"])
    api_hash = os.environ["TP2V_API_HASH"]
    Path(config.session).parent.mkdir(parents=True, exist_ok=True)
    # Telethon sleeps and retries on Telegram FloodWait rather than dropping
    # a matched event on normal account rate limits.
    async with TelegramClient(config.session, api_id, api_hash, flood_sleep_threshold=86400) as client:
        await client.get_dialogs()  # Populate access hashes for numeric private channel IDs.
        resolved = {}
        for route in config.routes:
            for chat in (route.a, route.source, route.b):
                if chat not in resolved:
                    entity = await client.get_entity(chat)
                    if not getattr(entity, "broadcast", False) and not getattr(entity, "megagroup", False):
                        raise ValueError(f"{chat!r} is not a Telegram channel or supergroup")
                    resolved[chat] = utils.get_peer_id(entity)
        routes = tuple(
            replace(route, a=resolved[route.a], source=resolved[route.source], b=resolved[route.b])
            for route in config.routes
        )
        inputs = {route.a for route in routes}
        if any(len({route.a, route.source, route.b}) != 3 for route in routes):
            raise ValueError("a, source and b must resolve to different channels")
        forwarder = Forwarder(client, routes, config.database)

        async def on_message(event):
            if not event.message.grouped_id:
                await forwarder.process([event.message])

        async def on_album(event):
            await forwarder.process(event.messages)

        client.add_event_handler(on_message, events.NewMessage(chats=list(inputs)))
        client.add_event_handler(on_album, events.Album(chats=list(inputs)))
        logging.getLogger(__name__).info("TP2V started: %d routes", len(routes))
        try:
            await client.run_until_disconnected()
        finally:
            forwarder.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Match Telegram codes and forward SOURCE messages to B")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument(
        "--check-config", action="store_true", help="validate TOML without connecting to Telegram"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = load_config(args.config)
        if args.check_config:
            print(f"Configuration OK: {len(config.routes)} routes")
            return
        asyncio.run(run(config))
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(2, f"tp2v: {error}\n")
    except KeyboardInterrupt:
        pass
