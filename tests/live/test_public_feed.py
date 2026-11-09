"""Public feed and dataset recording. No network, no endpoint text in logs."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from pumpgrok.live.feed import PublicDataFeed, _tick_from_coin
from pumpgrok.live.record import write_dataset
from pumpgrok.replay.clock import FixtureClock
from pumpgrok.replay.fixtures import load_dataset
from pumpgrok.replay.rng import make_rng
from pumpgrok.tools.onchain.rpc import RpcError

ROOT = Path(__file__).resolve().parents[2]
FEED_SOURCE = ROOT / "src" / "pumpgrok" / "live" / "feed.py"
MINT = "So11111111111111111111111111111111111111112"


class _Rpc:
    def __init__(self, result: object | Exception) -> None:
        self._result = result
        self.clock = FixtureClock(0)

    async def call(self, method: str, params: list[object]) -> object:
        if isinstance(self._result, Exception):
            raise self._result
        assert method == "getSlot"
        assert params == []
        return self._result


def _feed(body: object, rpc: object | Exception) -> PublicDataFeed:
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, json=body)

    rest = httpx.AsyncClient(
        base_url="https://public.example.test",
        transport=httpx.MockTransport(handler),
    )
    return PublicDataFeed(rpc=_Rpc(rpc), rest=rest, clock=FixtureClock(1_700_000_000.0))  # type: ignore[arg-type]


def test_feed_module_embeds_no_endpoint_url() -> None:
    text = FEED_SOURCE.read_text(encoding="utf-8")
    assert "https://" not in text
    assert "wss://" not in text
    assert "api.mainnet" not in text


async def test_poll_reads_rest_reserves_and_rpc_slot(caplog) -> None:
    caplog.set_level(logging.WARNING)
    coin = {
        "mint": MINT,
        "name": "Example <script>",
        "image": "https://cdn.example.test/a.png",
        "created_timestamp": "2026-01-01T00:00:00Z",
        "unique_buyers": 8,
        "vSolInBondingCurve": "1.5",
        "vTokensInBondingCurve": 1000,
        "complete": False,
        "website": "javascript:alert(1)",
    }
    feed = _feed([coin], 42)
    try:
        poll = await feed.poll()
    finally:
        await feed.aclose()
    assert poll.rest == "ok"
    assert poll.rpc == "ok"
    assert poll.slot == 42
    assert len(poll.ticks) == 1
    tick = poll.ticks[0]
    assert tick["mint"] == MINT
    assert tick["source"] == "rest"
    assert tick["S"] == 1_500_000_000
    assert tick["T"] == 1000
    assert tick["unique_buyers"] == 8
    assert "website" not in tick
    assert "public.example.test" not in caplog.text
    assert "cdn.example.test" not in caplog.text


async def test_missing_rest_or_rpc_is_blind(caplog) -> None:
    caplog.set_level(logging.WARNING)
    feed = _feed(httpx.ConnectError("down"), RpcError("rpc error for method=getSlot"))
    try:
        poll = await feed.poll()
    finally:
        await feed.aclose()
    assert poll.rest == "BLIND"
    assert poll.rpc == "BLIND"
    assert poll.ticks == ()
    assert poll.slot is None
    assert "public.example.test" not in caplog.text
    assert "down" not in caplog.text


def test_untrusted_coin_without_mint_is_dropped() -> None:
    assert _tick_from_coin({"name": "no mint"}) is None
    assert _tick_from_coin(["not", "a", "coin"]) is None
    tick = _tick_from_coin({"mint": MINT, "vSolInBondingCurve": "nope"})
    assert tick is not None
    assert "S" not in tick


def test_recording_is_byte_identical_and_replayable(tmp_path: Path) -> None:
    ticks = [
        {
            "ts_utc": "2026-01-01T00:00:01.000000Z",
            "kind": "tick",
            "mint": MINT,
            "name": "Example",
            "S": 15_000_000_000,
            "T": 1000,
            "complete": False,
            "source": "rest",
            "unique_buyers": 8,
            "image_uri": "fixture://example",
            "created_ts_utc": "2025-12-31T23:00:00.000000Z",
        }
    ]
    first = write_dataset(
        tmp_path / "a",
        "sample",
        list(ticks),
        seed=20260101,
        recorded_from_utc=ticks[0]["ts_utc"],
        recorded_to_utc=ticks[0]["ts_utc"],
    )
    second = write_dataset(
        tmp_path / "b",
        "sample",
        list(reversed(ticks)),
        seed=20260101,
        recorded_from_utc=ticks[0]["ts_utc"],
        recorded_to_utc=ticks[0]["ts_utc"],
    )
    assert (first / "events.jsonl").read_bytes() == (second / "events.jsonl").read_bytes()
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    loaded = load_dataset(first)
    assert loaded.manifest.dataset == "sample"
    assert loaded.events[0]["mint"] == MINT
    assert "https://" not in (first / "manifest.json").read_text(encoding="utf-8")


def test_rng_helper_is_importable() -> None:
    assert make_rng(1) is not None
