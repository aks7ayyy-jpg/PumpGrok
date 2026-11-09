"""Paper gateway: exec grant, autonomy, risk, and PaperAdapter. No live path."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path

import pytest

from pumpgrok.config.settings import Settings
from pumpgrok.core.adapter import OrderRequest, Side
from pumpgrok.core.errors import OrderRejectedError, PermissionDenied
from pumpgrok.core.ids import IdFactory
from pumpgrok.core.manifests import load_bot_manifests
from pumpgrok.execution.live.live_gate import LiveGate
from pumpgrok.execution.paper_adapter import CurveSnapshot
from pumpgrok.gateway import GatewayError, PaperOrderGateway
from pumpgrok.ledger.autonomy import autonomy_rows, read_autonomy
from pumpgrok.ledger.store import LedgerStore
from pumpgrok.replay.clock import FixtureClock, format_ts_utc
from pumpgrok.replay.rng import make_rng
from pumpgrok.risk.engine import RiskEngine

ROOT = Path(__file__).resolve().parents[2]
ROSTER = ROOT / "contracts" / "tool-rosters"
START = 1_769_900_000.0
MINT = "So11111111111111111111111111111111111111112"
S0 = 42_000_000_000
T0 = 766_428_571_428_571
SIZE = 200_000_000
TICKET = "SOL-20260201-001"


class _Curves:
    def __init__(self, snap: CurveSnapshot) -> None:
        self._snap = snap

    async def curve(self, mint: str) -> CurveSnapshot | None:
        if mint != self._snap.mint:
            return None
        return self._snap

    def reserves(self, mint: str) -> tuple[int, int] | None:
        if mint != self._snap.mint:
            return None
        return self._snap.s_lamports, self._snap.t_units


def _request(clock: FixtureClock, rng: random.Random) -> OrderRequest:
    ids = IdFactory(clock, rng)
    return OrderRequest(
        ticket_id=TICKET,
        mint=MINT,
        side=Side.BUY,
        decision_id=ids.ulid(),
        size_lamports=SIZE,
        max_slippage_bps=100,
        approval_id=f"apr-{ids.ulid()}",
        correlation_id="gateway-test",
    )


def _open(
    tmp_path: Path, clock: FixtureClock, rng: random.Random
) -> tuple[PaperOrderGateway, LedgerStore]:
    store = LedgerStore(str(tmp_path / "ledger.sqlite"))
    curves = _Curves(CurveSnapshot(mint=MINT, s_lamports=S0, t_units=T0, source="fixture"))
    gateway = PaperOrderGateway.open(
        Settings(mode="paper"),
        store=store,
        clock=clock,
        rng=rng,
        price_source=curves,
        get_reserves=curves.reserves,
        manifests=load_bot_manifests(ROSTER),
        p_drop=0.0,
        env={},
    )
    return gateway, store


def _set_level(store: LedgerStore, policy: str, clock: FixtureClock) -> None:
    store.append_batch(
        autonomy_rows(
            policy,
            "human:alice",
            previous=read_autonomy(store),
            ts_utc=format_ts_utc(clock.now()),
            source="paper",
            correlation_id="gateway-test",
        )
    )


async def _place(gateway: PaperOrderGateway, clock: FixtureClock, request: OrderRequest):
    task = asyncio.create_task(
        gateway.place("bot-04-sniper", request, correlation_id="gateway-test")
    )
    for _ in range(400):
        if task.done():
            break
        clock.advance_to(clock.now() + 1.0)
        await clock.yield_to_loop()
    return await task


def test_live_mode_is_refused_and_adapter_stays_unregistered(tmp_path: Path) -> None:
    gate = LiveGate(mode="live", environ={}, root=tmp_path, live_enabled=True)
    rows = {item.id: item for item in gate.evaluate(now="2026-11-09")}
    assert rows["adapter_registered"].met is False
    store = LedgerStore(str(tmp_path / "ledger.sqlite"))
    clock = FixtureClock(START)
    curves = _Curves(CurveSnapshot(mint=MINT, s_lamports=S0, t_units=T0))
    with pytest.raises(GatewayError):
        PaperOrderGateway.open(
            Settings(mode="live"),
            store=store,
            clock=clock,
            rng=make_rng(1),
            price_source=curves,
            get_reserves=curves.reserves,
            manifests=load_bot_manifests(ROSTER),
            env={},
        )
    store.close()


def test_live_engine_cannot_leave_manual_ticket(tmp_path: Path) -> None:
    store = LedgerStore(str(tmp_path / "ledger.sqlite"))
    clock = FixtureClock(START)
    engine = RiskEngine(
        store=store,
        budget=Settings(mode="paper").risk,
        ids=IdFactory(clock, make_rng(1)),
        clock=clock,
        mode="live",
        autonomy="manual-ticket",
        env={},
    )
    engine.set_autonomy("autonomous-paper")
    assert engine._autonomy == "manual-ticket"
    store.close()


async def test_seat_without_exec_grant_places_nothing(tmp_path: Path) -> None:
    clock = FixtureClock(START)
    rng = make_rng(3)
    gateway, store = _open(tmp_path, clock, rng)
    try:
        with pytest.raises(PermissionDenied):
            await gateway.place("bot-03-whale", _request(clock, make_rng(9)))
        kinds = [row["type"] for row in store.read(limit=None)]
        assert "OrderPlaced" not in kinds
        assert "FillRecorded" not in kinds
    finally:
        store.close()


async def test_manual_ticket_buy_is_rejected_on_the_ledger(tmp_path: Path) -> None:
    clock = FixtureClock(START)
    rng = make_rng(4)
    gateway, store = _open(tmp_path, clock, rng)
    try:
        with pytest.raises(OrderRejectedError) as caught:
            await gateway.place("bot-04-sniper", _request(clock, make_rng(8)))
        assert caught.value.reason == "approval_missing"
        kinds = [row["type"] for row in store.read(limit=None)]
        assert "OrderRejected" in kinds
        assert "FillRecorded" not in kinds
    finally:
        store.close()


async def test_autonomous_paper_buy_fills_through_the_adapter(tmp_path: Path) -> None:
    clock = FixtureClock(START)
    rng = make_rng(5)
    gateway, store = _open(tmp_path, clock, rng)
    try:
        _set_level(store, "autonomous-paper", clock)
        fill = await _place(gateway, clock, _request(clock, make_rng(6)))
        assert fill.status.value == "filled"
        kinds = [row["type"] for row in store.read(limit=None)]
        assert "OrderPlaced" in kinds
        assert "FillRecorded" in kinds
        assert read_autonomy(store) == "autonomous-paper"
    finally:
        store.close()
