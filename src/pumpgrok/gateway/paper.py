"""Paper order door for seats that hold an exec grant.

``PaperOrderGateway.place`` refuses a seat with no ``exec.place`` paper grant,
refuses live mode, re-reads autonomy from the ledger, and hands the order to
``PaperAdapter``. The adapter runs ``RiskEngine.check`` and appends the order
rows. Manual-ticket buys need a human approval already on the ledger;
autonomous-paper lets the risk engine mint one. Nothing here signs or sends.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pumpgrok.config.settings import Settings
from pumpgrok.core.adapter import ExitRuleReason, Fill, OrderRequest
from pumpgrok.core.bot import BotManifest
from pumpgrok.core.errors import LiveGateError, PermissionDenied
from pumpgrok.core.ids import Clock, IdFactory
from pumpgrok.core.manifests import load_bot_manifests
from pumpgrok.core.side_effect import SideEffect
from pumpgrok.execution import build_adapter
from pumpgrok.execution.paper_adapter import PaperAdapter
from pumpgrok.execution.paper_adapter.models import PriceSource
from pumpgrok.ledger.autonomy import read_autonomy
from pumpgrok.ledger.store import LedgerStore
from pumpgrok.risk.engine import RiskEngine
from pumpgrok.risk.limits import EngineLimits
from pumpgrok.tools import ensure_paper_genesis

EXEC_PLACE = "exec.place"


class GatewayError(PermissionDenied):
    """The gateway refused to place. Paper mode is the only mode it serves."""

    code = "gateway_refused"


class PaperOrderGateway:
    """One paper ledger, one risk engine, one paper adapter."""

    def __init__(
        self,
        *,
        store: LedgerStore,
        risk: RiskEngine,
        adapter: PaperAdapter,
        manifests: dict[str, BotManifest],
    ) -> None:
        self._store = store
        self._risk = risk
        self._adapter = adapter
        self._manifests = manifests

    @classmethod
    def open(
        cls,
        settings: Settings,
        *,
        store: LedgerStore,
        clock: Clock,
        rng: random.Random,
        price_source: PriceSource,
        get_reserves: Callable[[str], tuple[int, int] | None],
        manifests: dict[str, BotManifest] | None = None,
        roster_dir: Path | None = None,
        p_drop: float = 0.0,
        env: dict[str, str] | None = None,
        limits: EngineLimits | None = None,
    ) -> PaperOrderGateway:
        """Wire a paper-only gateway. Live mode raises before any adapter exists."""
        if settings.effective_mode != "paper":
            raise GatewayError("live mode is refused: the gateway places paper orders only")
        ensure_paper_genesis(store, settings.risk)
        level = read_autonomy(store)
        autonomy: Literal["manual-ticket", "autonomous-paper"] = (
            "autonomous-paper" if level == "autonomous-paper" else "manual-ticket"
        )
        budget = settings.risk
        engine = RiskEngine(
            store=store,
            budget=budget,
            ids=IdFactory(clock, rng),
            clock=clock,
            limits=limits if limits is not None else settings.limits,
            mode="paper",
            autonomy=autonomy,
            env={} if env is None else env,
            get_reserves=get_reserves,
        )
        adapter = build_adapter(
            settings,
            store=store,
            risk=engine,
            ids=IdFactory(clock, rng),
            clock=clock,
            rng=rng,
            price_source=price_source,
            source="paper",
            p_drop=p_drop,
        )
        loaded = manifests
        if loaded is None:
            if roster_dir is None:
                raise GatewayError("gateway needs seat manifests")
            loaded = load_bot_manifests(roster_dir)
        return cls(store=store, risk=engine, adapter=adapter, manifests=loaded)

    async def place(
        self,
        seat: str,
        request: OrderRequest,
        *,
        exit_reason: ExitRuleReason | None = None,
        creator: str | None = None,
        correlation_id: str = "desk",
    ) -> Fill:
        """Place one paper order for ``seat`` or refuse before the adapter runs."""
        self._require_exec_grant(seat)
        level = read_autonomy(self._store)
        if level == "autonomous-paper":
            self._risk.set_autonomy("autonomous-paper")
        else:
            self._risk.set_autonomy("manual-ticket")
        fill = await self._adapter.place(
            request,
            seat=seat,
            exit_reason=exit_reason,
            creator=creator,
            source="paper",
            correlation_id=correlation_id,
        )
        return fill

    def _require_exec_grant(self, seat: str) -> None:
        manifest = self._manifests.get(seat)
        if manifest is None:
            raise PermissionDenied("unknown seat", seat=seat, tool=EXEC_PLACE)
        grant = manifest.grant_for(EXEC_PLACE)
        if grant is None:
            raise PermissionDenied("exec.place is not granted", seat=seat, tool=EXEC_PLACE)
        if grant.side_effect.exceeds(SideEffect.PAPER_WRITE):
            raise LiveGateError("live-write is not granted", seat=seat, tool=EXEC_PLACE)
        if grant.side_effect is not SideEffect.PAPER_WRITE:
            raise PermissionDenied(
                "exec.place grant is not paper-write", seat=seat, tool=EXEC_PLACE
            )
