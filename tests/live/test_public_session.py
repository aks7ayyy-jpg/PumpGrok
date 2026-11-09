"""Paper session over a scripted public feed. Replay fixtures are not involved."""

from __future__ import annotations

from pathlib import Path

from pumpgrok.config.settings import Settings
from pumpgrok.ledger.autonomy import autonomy_rows, read_autonomy
from pumpgrok.ledger.store import LedgerStore
from pumpgrok.live.feed import FeedPoll
from pumpgrok.live.session import run_public_session
from pumpgrok.replay.clock import FixtureClock, format_ts_utc
from pumpgrok.replay.fixtures import load_dataset
from pumpgrok.replay.rng import make_rng

MINT = "So11111111111111111111111111111111111111112"
START = 1_769_900_000.0
ROOT = Path(__file__).resolve().parents[2]


class _Feed:
    def __init__(self, polls: list[FeedPoll]) -> None:
        self._polls = list(polls)
        self.calls = 0

    async def poll(self) -> FeedPoll:
        self.calls += 1
        if len(self._polls) > 1:
            return self._polls.pop(0)
        return self._polls[0]


def _coin(**extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": "tick",
        "mint": MINT,
        "name": "Example",
        "image_uri": "fixture://example",
        "created_ts_utc": format_ts_utc(START - 1000),
        "unique_buyers": 12,
        "S": 10_000_000_000,
        "T": 700_000_000_000_000,
        "complete": False,
        "source": "rest",
    }
    row.update(extra)
    return row


def _settings() -> Settings:
    return Settings(mode="paper")


async def test_duration_polls_until_the_window_and_records(tmp_path: Path) -> None:
    clock = FixtureClock(START)
    feed = _Feed(
        [
            FeedPoll(ticks=(_coin(),), rest="ok", rpc="ok", slot=7),
            FeedPoll(ticks=(_coin(),), rest="ok", rpc="ok", slot=8),
        ]
    )
    result = await run_public_session(
        _settings(),
        feed,
        clock,
        once=False,
        duration_s=11,
        record="window",
        rng=make_rng(1),
        ledger_path=tmp_path / "ledger.sqlite",
        record_root=tmp_path / "recordings",
        roster_dir=ROOT / "contracts" / "tool-rosters",
    )
    assert feed.calls == 2
    assert result.ticks == 2
    assert result.skipped == "manual-ticket"
    assert result.filled == 0
    loaded = load_dataset(tmp_path / "recordings" / "window")
    assert len(loaded.events) == 2
    assert not (tmp_path / "ledger.sqlite").exists()


async def test_once_blind_feed_skips(tmp_path: Path) -> None:
    result = await run_public_session(
        _settings(),
        _Feed([FeedPoll(ticks=(), rest="BLIND", rpc="BLIND", slot=None)]),
        FixtureClock(START),
        once=True,
        duration_s=None,
        record=None,
        ledger_path=tmp_path / "ledger.sqlite",
        roster_dir=ROOT / "contracts" / "tool-rosters",
    )
    assert result.decision == "skip"
    assert result.reason == "no_events"
    assert result.filled == 0
    assert result.skipped == ""
    assert "public feed rest=BLIND" in result.lines()[0]


async def test_autonomous_paper_fills_a_public_tick(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.sqlite"
    clock = FixtureClock(START)
    store = LedgerStore(str(ledger))
    try:
        store.set_meta("initial_equity_lamports", "10000000000")
        store.set_meta("source", "paper")
        store.append_batch(
            autonomy_rows(
                "autonomous-paper",
                "human:alice",
                previous=read_autonomy(store),
                ts_utc=format_ts_utc(clock.now()),
                source="paper",
                correlation_id="session",
            )
        )
    finally:
        store.close()
    result = await run_public_session(
        _settings(),
        _Feed([FeedPoll(ticks=(_coin(),), rest="ok", rpc="ok", slot=3)]),
        clock,
        once=True,
        duration_s=None,
        record=None,
        rng=make_rng(11),
        ledger_path=ledger,
        p_drop=0.0,
        roster_dir=ROOT / "contracts" / "tool-rosters",
    )
    assert result.decision == "buy"
    assert result.filled == 1
    assert result.rejected == 0
    check = LedgerStore(str(ledger))
    try:
        kinds = [row["type"] for row in check.read(limit=None)]
    finally:
        check.close()
    assert "FillRecorded" in kinds
    assert "OrderPlaced" in kinds
