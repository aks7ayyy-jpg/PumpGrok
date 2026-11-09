"""One paper session over the public feed.

Polls the feed, optionally writes a dataset, and places paper buys only when
the ledger autonomy level is ``autonomous-paper`` and the sniper holds
``exec.place``. ``manual-ticket`` records the ticks and places nothing.
Replay and backtest do not call this module.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pumpgrok.config.settings import Settings
from pumpgrok.core.adapter import OrderRequest, Side
from pumpgrok.core.errors import OrderRejectedError, OrderUnknownError
from pumpgrok.core.ids import Clock, IdFactory
from pumpgrok.core.manifests import load_bot_manifests
from pumpgrok.engine.pipeline import decide_replay, evaluate_candidates
from pumpgrok.execution.paper_adapter.models import CurveSnapshot
from pumpgrok.gateway.paper import PaperOrderGateway
from pumpgrok.ledger.autonomy import read_autonomy
from pumpgrok.ledger.paths import PAPER_LEDGER_PATH
from pumpgrok.ledger.store import LedgerStore
from pumpgrok.ledger.tickets import allocate_ticket
from pumpgrok.live.feed import FeedPoll
from pumpgrok.live.record import write_dataset
from pumpgrok.replay.clock import FixtureClock, format_ts_utc
from pumpgrok.replay.rng import make_rng
from pumpgrok.risk.engine.constants import BUY_SEAT

POLL_S = 10.0
PUBLIC_BUY_LAMPORTS = 10_000_000
PUBLIC_SLIPPAGE_BPS = 100
_ROSTER = Path(__file__).resolve().parents[3] / "contracts" / "tool-rosters"


class PublicFeed(Protocol):
    """Anything that returns one :class:`FeedPoll`."""

    async def poll(self) -> FeedPoll: ...


@dataclass(frozen=True)
class PublicSessionResult:
    """What one public paper session observed and, when allowed, filled."""

    rest: str
    rpc: str
    slot: int | None
    ticks: int
    dataset_name: str | None
    decision: str
    reason: str
    filled: int
    rejected: int
    skipped: str

    def lines(self) -> list[str]:
        slot = "none" if self.slot is None else str(self.slot)
        rows = [
            f"public feed rest={self.rest} rpc={self.rpc} slot={slot} ticks={self.ticks}",
        ]
        if self.dataset_name is not None:
            rows.append(f"recorded dataset={self.dataset_name} events={self.ticks}")
        rows.append(f"engine decision={self.decision} reason={self.reason}")
        rows.append(f"trades filled={self.filled} rejected={self.rejected}")
        if self.skipped:
            rows.append(f"orders skipped={self.skipped}")
        return rows


class _Book:
    """Latest REST reserves. ``source`` stays ``rest``, never ``rpc``."""

    def __init__(self) -> None:
        self.snaps: dict[str, CurveSnapshot] = {}

    async def curve(self, mint: str) -> CurveSnapshot | None:
        return self.snaps.get(mint)

    def reserves(self, mint: str) -> tuple[int, int] | None:
        snap = self.snaps.get(mint)
        if snap is None:
            return None
        return snap.s_lamports, snap.t_units


async def run_public_session(
    settings: Settings,
    feed: PublicFeed,
    clock: Clock,
    *,
    once: bool,
    duration_s: int | None,
    record: str | None,
    rng: random.Random | None = None,
    ledger_path: Path | None = None,
    record_root: Path | None = None,
    p_drop: float = 0.0,
    roster_dir: Path | None = None,
) -> PublicSessionResult:
    """Poll, record, and paper-trade. ``duration_s`` of 0 is a single poll."""
    started = clock.now()
    ticks: list[dict[str, Any]] = []
    rest = "BLIND"
    rpc = "BLIND"
    slot: int | None = None
    while True:
        poll = await feed.poll()
        rest, rpc, slot = poll.rest, poll.rpc, poll.slot
        stamped = [_stamp(row, format_ts_utc(clock.now())) for row in poll.ticks]
        ticks.extend(stamped)
        if once or _window_done(clock, started, duration_s):
            break
        await _pace(clock, POLL_S)
        if _window_done(clock, started, duration_s):
            break
    decision = decide_replay(ticks, settings.filter)
    filled, rejected, skipped = await _maybe_trade(
        settings,
        ticks,
        clock=clock,
        rng=rng if rng is not None else make_rng(settings.replay.seed),
        ledger_path=ledger_path if ledger_path is not None else Path.cwd() / PAPER_LEDGER_PATH,
        p_drop=p_drop,
        roster_dir=roster_dir if roster_dir is not None else _ROSTER,
    )
    dataset_name = None
    if record is not None:
        root = record_root if record_root is not None else Path.cwd() / "var" / "recordings"
        begin = ticks[0]["ts_utc"] if ticks else format_ts_utc(started)
        end = ticks[-1]["ts_utc"] if ticks else format_ts_utc(clock.now())
        write_dataset(
            root / record,
            record,
            ticks,
            seed=settings.replay.seed,
            recorded_from_utc=str(begin),
            recorded_to_utc=str(end),
        )
        dataset_name = record
    return PublicSessionResult(
        rest=rest,
        rpc=rpc,
        slot=slot,
        ticks=len(ticks),
        dataset_name=dataset_name,
        decision=decision.decision,
        reason=decision.reason,
        filled=filled,
        rejected=rejected,
        skipped=skipped,
    )


def _window_done(clock: Clock, started: float, duration_s: int | None) -> bool:
    if duration_s is None:
        return False
    return clock.now() - started >= float(duration_s)


def _stamp(row: dict[str, Any], ts_utc: str) -> dict[str, Any]:
    stamped = dict(row)
    stamped["ts_utc"] = ts_utc
    stamped["kind"] = "tick"
    return stamped


async def _pace(clock: Clock, seconds: float) -> None:
    if isinstance(clock, FixtureClock):
        clock.advance_to(clock.now() + seconds)
        await clock.yield_to_loop()
        return
    await clock.sleep(seconds)


async def _maybe_trade(
    settings: Settings,
    ticks: list[dict[str, Any]],
    *,
    clock: Clock,
    rng: random.Random,
    ledger_path: Path,
    p_drop: float,
    roster_dir: Path,
) -> tuple[int, int, str]:
    buys = _buy_ticks(ticks, settings)
    if not buys:
        return 0, 0, ""
    if _autonomy(ledger_path) != "autonomous-paper":
        return 0, 0, "manual-ticket"
    return await _place_buys(
        settings,
        buys,
        clock=clock,
        rng=rng,
        ledger_path=ledger_path,
        p_drop=p_drop,
        roster_dir=roster_dir,
    )


def _buy_ticks(ticks: list[dict[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in evaluate_candidates(ticks, settings.filter):
        if candidate.verdict.decision != "buy":
            continue
        mint = candidate.verdict.mint
        if mint in seen:
            continue
        match = next((row for row in ticks if row.get("mint") == mint), None)
        if match is None or "S" not in match or "T" not in match:
            continue
        if match.get("source") == "rpc":
            continue
        seen.add(mint)
        chosen.append(match)
    return chosen


def _autonomy(ledger_path: Path) -> str:
    if not ledger_path.exists():
        return "manual-ticket"
    store = LedgerStore(str(ledger_path))
    try:
        return read_autonomy(store)
    finally:
        store.close()


async def _place_buys(
    settings: Settings,
    buys: list[dict[str, Any]],
    *,
    clock: Clock,
    rng: random.Random,
    ledger_path: Path,
    p_drop: float,
    roster_dir: Path,
) -> tuple[int, int, str]:
    store = LedgerStore(str(ledger_path))
    filled = 0
    rejected = 0
    try:
        book = _Book()
        gateway = PaperOrderGateway.open(
            settings,
            store=store,
            clock=clock,
            rng=rng,
            price_source=book,
            get_reserves=book.reserves,
            manifests=load_bot_manifests(roster_dir),
            p_drop=p_drop,
            env={},
        )
        ids = IdFactory(clock, rng)
        for row in buys:
            outcome = await _place_one(gateway, book, ids=ids, clock=clock, store=store, row=row)
            if outcome == "filled":
                filled += 1
            else:
                rejected += 1
    finally:
        store.close()
    return filled, rejected, ""


async def _place_one(
    gateway: PaperOrderGateway,
    book: _Book,
    *,
    ids: IdFactory,
    clock: Clock,
    store: LedgerStore,
    row: dict[str, Any],
) -> str:
    mint = str(row["mint"])
    book.snaps[mint] = CurveSnapshot(
        mint=mint,
        s_lamports=int(row["S"]),
        t_units=int(row["T"]),
        complete=bool(row.get("complete", False)),
        source="rest",
        ts=clock.now(),
    )
    now = format_ts_utc(clock.now())
    ticket = allocate_ticket(
        store,
        ts_utc=now,
        source="paper",
        correlation_id="public-feed",
        seat=BUY_SEAT,
        title=f"public feed {mint}",
    )
    request = OrderRequest(
        ticket_id=ticket,
        mint=mint,
        side=Side.BUY,
        decision_id=ids.ulid(),
        size_lamports=PUBLIC_BUY_LAMPORTS,
        max_slippage_bps=PUBLIC_SLIPPAGE_BPS,
        approval_id=f"apr-{ids.ulid()}",
        correlation_id="public-feed",
    )
    try:
        fill = await _await_fill(
            clock, gateway.place(BUY_SEAT, request, correlation_id="public-feed")
        )
    except (OrderRejectedError, OrderUnknownError):
        return "rejected"
    if fill.status.value != "filled":
        return "rejected"
    return "filled"


async def _await_fill(clock: Clock, place: Any) -> Any:
    if not isinstance(clock, FixtureClock):
        return await place
    task = asyncio.create_task(place)
    for _ in range(400):
        if task.done():
            break
        clock.advance_to(clock.now() + 1.0)
        await clock.yield_to_loop()
    return await task
