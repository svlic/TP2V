import asyncio
from datetime import UTC, datetime

import pytest
from telethon.tl.types import Message, MessageFwdHeader, PeerChannel

from tp2v.config import Route
from tp2v.forwarding import Forwarder, extract_code, find_match

A, SOURCE, B = -1000000000010, -1000000000020, -1000000000030


def message(identifier, text="", *, chat=SOURCE, album=None, origin=None, origin_chat=SOURCE):
    return Message(
        id=identifier,
        peer_id=PeerChannel(-chat - 10**12),
        date=datetime(2026, 11, 8, tzinfo=UTC),
        message=text,
        grouped_id=album,
        fwd_from=(
            MessageFwdHeader(
                date=datetime(2026, 11, 8, tzinfo=UTC),
                from_id=PeerChannel(-origin_chat - 10**12),
                channel_post=origin,
            )
            if origin is not None
            else None
        ),
    )


class FakeTelegram:
    """In-memory history with Telegram's exclusive ID boundaries and ordering."""

    def __init__(self, messages=()):
        self.messages = {(item.chat_id, item.id): item for item in messages}
        self.sent = []
        self.fail_destinations = set()

    async def get_messages(self, chat, *, ids):
        if isinstance(ids, int):
            return self.messages.get((chat, ids))
        return [self.messages.get((chat, identifier)) for identifier in ids]

    async def iter_messages(self, chat, *, limit, min_id=0, max_id=None, reverse=False, search=None):
        # Search deliberately includes fuzzy/non-matching results: local exact
        # validation, not this fake server, must prevent false positives.
        values = sorted(
            (item for (channel, _), item in self.messages.items() if channel == chat),
            key=lambda item: item.id,
            reverse=not reverse,
        )
        values = [item for item in values if item.id > min_id and (max_id is None or item.id < max_id)]
        for item in values[:limit]:
            yield item

    async def forward_messages(self, destination, ids, *, from_peer):
        await asyncio.sleep(0)  # Let concurrent deliveries overlap as a real RPC would.
        if destination in self.fail_destinations:
            raise OSError("simulated Telegram failure")
        self.sent.append((destination, from_peer, ids))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("编号： 2636937 （261014）", "2636937"),
        ("编号 0123456", "0123456"),
        ("123456 / 7654321", "123456"),
        ("12345 12345678 9123456789", None),
        ("", None),
    ],
)
def test_extracts_first_complete_code(text, expected):
    assert extract_code(text) == expected


async def test_nearby_prioritizes_newer_and_forwards_whole_album():
    client = FakeTelegram(
        [
            message(100, "123456", album=1),
            message(101, album=1),
            message(99, "123456"),
            message(102, "not a match"),
            message(103, album=2),
            message(104, "编号 123456", album=2),
        ]
    )
    result = await find_match(client, Route("n", A, SOURCE, B), [message(1, chat=A, origin=100)], "123456")
    assert [item.id for item in result] == [103, 104]


@pytest.mark.parametrize("offset", [5, 6])
async def test_nearby_counts_logical_messages_and_obeys_both_window_boundaries(offset):
    history = [message(100), message(99, "123456")]
    # Four ten-item albums consume four logical positions, not forty.
    history += [message(i, album=(i - 101) // 10 + 1) for i in range(101, 141)]
    history += [message(141, "123456" if offset == 5 else "miss"), message(142, "123456")]
    client = FakeTelegram(history)
    result = await find_match(client, Route("n", A, SOURCE, B), [message(1, chat=A, origin=100)], "123456")
    assert [item.id for item in result] == ([141] if offset == 5 else [99])


async def test_nearby_paginates_without_counting_split_album_twice():
    history = [message(100), message(101)]
    # The page ends at 160, splitting the album at 152..161.
    history += [message(i, album=(i - 102) // 10 + 1) for i in range(102, 172)]
    history += [message(172, album=8), message(173, "0123456", album=8)]
    result = await find_match(
        FakeTelegram(history),
        Route("n", A, SOURCE, B, window=9),
        [message(1, chat=A, origin=100)],
        "0123456",
    )
    assert [item.id for item in result] == [172, 173]


@pytest.mark.parametrize("origin,origin_chat", [(None, SOURCE), (100, B), (999, SOURCE)])
async def test_nearby_requires_visible_existing_origin_in_correct_source(origin, origin_chat):
    result = await find_match(
        FakeTelegram([message(100), message(101, "123456")]),
        Route("n", A, SOURCE, B),
        [message(1, chat=A, origin=origin, origin_chat=origin_chat)],
        "123456",
    )
    assert result is None


async def test_nearby_older_fifth_is_included_but_sixth_is_not():
    client = FakeTelegram([message(i, "123456" if i in (94, 95) else "") for i in range(94, 101)])
    route = Route("n", A, SOURCE, B)
    incoming = [message(1, chat=A, origin=100)]
    assert [item.id for item in await find_match(client, route, incoming, "123456")] == [95]
    client.messages.pop((SOURCE, 95))
    # Preserve the fifth logical position, but without a matching caption.
    client.messages[SOURCE, 95] = message(95)
    assert await find_match(client, route, incoming, "123456") is None


@pytest.mark.parametrize("code", ["123456", "0123456"])
async def test_search_works_without_origin_and_rejects_longer_numbers(code):
    client = FakeTelegram(
        [
            message(300, f"编号 9{code}"),
            message(299, f"编号 {code}8"),
            message(200, f"编号 {code}"),
            message(201, album=8),
            message(202, f"编号 {code}", album=8),
        ]
    )
    result = await find_match(
        client,
        Route("s", A, SOURCE, B, mode="search"),
        [message(1, chat=A)],
        code,
    )
    # For six digits, a seven-digit number is a valid OTHER code, not this code.
    assert [item.id for item in result] == [201, 202]


async def test_search_excludes_origin_album_and_respects_result_limit():
    client = FakeTelegram(
        [
            message(300, "123456", album=1),
            message(301, album=1),
            message(100, "123456"),
        ]
    )
    incoming = [message(1, chat=A, origin=300)]
    assert (
        await find_match(
            client,
            Route("s", A, SOURCE, B, mode="search", search_limit=2),
            incoming,
            "123456",
        )
        is None
    )
    result = await find_match(client, Route("s", A, SOURCE, B, mode="search"), incoming, "123456")
    assert [item.id for item in result] == [100]


async def test_multiple_routes_are_isolated_and_successes_deduplicate_after_restart(tmp_path):
    source2, input2, output2 = SOURCE - 1, A - 1, B - 1
    client = FakeTelegram(
        [
            message(100),
            message(101, "123456", album=9),
            message(102, album=9),
            message(20, "123456", chat=source2),
        ]
    )
    routes = (
        Route("near", A, SOURCE, B),
        Route("search", A, source2, output2, mode="search"),
        Route("other", input2, SOURCE, B, mode="search"),
    )
    incoming = [message(1, "编号 123456", chat=A, origin=100, album=7), message(2, chat=A, album=7)]
    database = str(tmp_path / "state.db")
    forwarder = Forwarder(client, routes, database)
    try:
        await asyncio.gather(
            forwarder.process(list(reversed(incoming))),
            forwarder.process(incoming),
        )
        assert client.sent == [(B, SOURCE, [101, 102]), (output2, source2, [20])]
    finally:
        forwarder.close()
    forwarder = Forwarder(client, routes, database)
    try:
        await forwarder.process(incoming)
        assert len(client.sent) == 2
        await forwarder.process([message(3, "123456", chat=input2)])
        assert client.sent[-1] == (B, SOURCE, [101, 102])
        assert len(client.sent) == 3
        await forwarder.process([message(4, "123456", chat=A, origin=100)])
        assert client.sent[3:] == [(B, SOURCE, [101, 102]), (output2, source2, [20])]
    finally:
        forwarder.close()


async def test_failed_route_does_not_block_others_and_can_be_redelivered(tmp_path):
    client = FakeTelegram([message(100, "123456")])
    client.fail_destinations.add(B)
    forwarder = Forwarder(
        client,
        (
            Route("fail", A, SOURCE, B, mode="search"),
            Route("ok", A, SOURCE, B - 1, mode="search"),
        ),
        str(tmp_path / "state.db"),
    )
    incoming = [message(1, "123456", chat=A)]
    try:
        await forwarder.process(incoming)
        assert client.sent == [(B - 1, SOURCE, [100])]
        client.fail_destinations.clear()
        await forwarder.process(incoming)
        assert client.sent == [(B - 1, SOURCE, [100]), (B, SOURCE, [100])]
        await forwarder.process([message(2, "12345678", chat=A)])
        assert len(client.sent) == 2
    finally:
        forwarder.close()
