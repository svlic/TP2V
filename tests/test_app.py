from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon import events
from telethon.tl.types import Channel, ChatPhotoEmpty
from test_forwarding import SOURCE, A, B, FakeTelegram, message

from tp2v import app
from tp2v.config import Config, Route


class EventClient(FakeTelegram):
    def __init__(self):
        super().__init__([message(100, "0123456"), message(101, "7654321")])
        self.handlers = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get_dialogs(self):
        return []

    async def get_entity(self, chat):
        if chat == "input":
            chat = A
        return Channel(
            id=-chat - 10**12,
            title="test",
            photo=ChatPhotoEmpty(),
            date=datetime(2026, 11, 8, tzinfo=UTC),
            broadcast=True,
        )

    def add_event_handler(self, callback, builder):
        self.handlers.append((callback, builder))

    async def run_until_disconnected(self):
        for callback, builder in self.handlers:
            if isinstance(builder, events.NewMessage):
                await callback(SimpleNamespace(message=message(1, "0123456", chat=A)))
                # A media group's individual events must NOT trigger a forward.
                await callback(SimpleNamespace(message=message(2, "7654321", chat=A, album=9)))
            elif isinstance(builder, events.Album):
                await callback(
                    SimpleNamespace(
                        messages=[
                            message(2, "7654321", chat=A, album=9),
                            message(3, chat=A, album=9),
                        ]
                    )
                )


async def test_application_resolves_channels_and_handles_single_and_album_once(tmp_path, monkeypatch):
    client = EventClient()
    monkeypatch.setenv("TP2V_API_ID", "12345")
    monkeypatch.setenv("TP2V_API_HASH", "test-only")
    monkeypatch.setattr(app, "TelegramClient", lambda *args, **kwargs: client)
    await app.run(
        Config(
            (Route("s", "input", SOURCE, B, mode="search"),),
            str(tmp_path / "session"),
            str(tmp_path / "state.db"),
        )
    )
    assert client.sent == [(B, SOURCE, [100]), (B, SOURCE, [101])]


async def test_startup_rejects_username_and_id_resolving_to_same_channel(tmp_path, monkeypatch):
    client = EventClient()
    monkeypatch.setenv("TP2V_API_ID", "12345")
    monkeypatch.setenv("TP2V_API_HASH", "test-only")
    monkeypatch.setattr(app, "TelegramClient", lambda *args, **kwargs: client)
    with pytest.raises(ValueError, match="different channels"):
        await app.run(
            Config(
                (Route("s", "input", A, B),),
                str(tmp_path / "session"),
                str(tmp_path / "state.db"),
            )
        )
    assert not client.sent
