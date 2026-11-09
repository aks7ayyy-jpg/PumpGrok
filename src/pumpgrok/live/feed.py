"""Public pump.fun REST ticks plus a read-only RPC slot probe.

REST bodies are untrusted: only a mint, sanitized text, and numeric reserves
with the field names the monitor already reads become a tick. Missing reserves
are omitted, so they cannot price a fill. RPC account bytes are not decoded;
the curve layout stays ASSUMED and is not a price source. No endpoint URL is
written in this module or in its logs.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from pumpgrok.config.settings import Settings
from pumpgrok.core.errors import DataStale
from pumpgrok.core.units import sol_to_lamports
from pumpgrok.replay.clock import Clock, format_ts_utc, parse_ts_utc
from pumpgrok.replay.rng import make_rng
from pumpgrok.tools.onchain.rpc import RpcClient, RpcError

log = logging.getLogger("pumpgrok.live")

_COIN_PATH = "/coins"
_COIN_LIMIT = 20
_TEXT_LIMIT = 200
_BASE58 = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
_LIST_KEYS = ("coins", "items", "data")


@dataclass(frozen=True)
class FeedPoll:
    """One poll. ``rest`` and ``rpc`` are ``ok`` or ``BLIND``. Ticks carry no URLs."""

    ticks: tuple[dict[str, Any], ...]
    rest: str
    rpc: str
    slot: int | None


class PublicDataFeed:
    """Polls one REST client and one RpcClient. Callers own or close both."""

    def __init__(
        self,
        *,
        rpc: RpcClient,
        rest: httpx.AsyncClient,
        clock: Clock,
        rpc_http: httpx.AsyncClient | None = None,
    ) -> None:
        self._rpc = rpc
        self._rest = rest
        self._clock = clock
        self._rpc_http = rpc_http

    async def aclose(self) -> None:
        await self._rest.aclose()
        if self._rpc_http is not None:
            await self._rpc_http.aclose()

    async def poll(self) -> FeedPoll:
        rpc_status, slot = await self._slot()
        rest_status, ticks = await self._coins()
        return FeedPoll(ticks=tuple(ticks), rest=rest_status, rpc=rpc_status, slot=slot)

    async def _slot(self) -> tuple[str, int | None]:
        try:
            result = await self._rpc.call("getSlot", [])
        except (RpcError, DataStale):
            log.warning("public feed rpc probe failed")
            return "BLIND", None
        if isinstance(result, bool) or not isinstance(result, int) or result < 0:
            log.warning("public feed rpc probe returned no slot")
            return "BLIND", None
        return "ok", result

    async def _coins(self) -> tuple[str, list[dict[str, Any]]]:
        try:
            response = await self._rest.get(_COIN_PATH, params={"offset": 0, "limit": _COIN_LIMIT})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning("public feed rest poll failed: %s", type(exc).__name__)
            return "BLIND", []
        coins = _coin_list(body)
        if coins is None:
            log.warning("public feed rest body was not a coin list")
            return "BLIND", []
        ticks = [tick for coin in coins[:_COIN_LIMIT] if (tick := _tick_from_coin(coin))]
        return "ok", ticks


def build_public_feed(
    settings: Settings, clock: Clock, rng: random.Random | None = None
) -> PublicDataFeed:
    """Clients from settings. This function embeds no endpoint literal."""
    rest = httpx.AsyncClient(
        base_url=settings.data.rest_url,
        timeout=settings.data.request_timeout,
        headers={"Accept": "application/json"},
    )
    rpc_http = httpx.AsyncClient(timeout=settings.data.request_timeout)
    rpc = RpcClient(
        list(settings.rpc.endpoints),
        rpc_http,
        clock,
        rng if rng is not None else make_rng(settings.replay.seed),
        api_key=settings.rpc.api_key,
    )
    return PublicDataFeed(rpc=rpc, rest=rest, clock=clock, rpc_http=rpc_http)


def _coin_list(body: Any) -> list[Any] | None:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in _LIST_KEYS:
            value = body.get(key)
            if isinstance(value, list):
                return value
    return None


def _tick_from_coin(coin: Any) -> dict[str, Any] | None:
    """One untrusted coin object. None when it has no mint. Reserves stay optional."""
    if not isinstance(coin, dict):
        return None
    mint = _mint(coin.get("mint"))
    if mint is None:
        return None
    tick: dict[str, Any] = {
        "kind": "tick",
        "mint": mint,
        "complete": coin.get("complete") is True,
        "source": "rest",
    }
    name = _text(coin.get("name"))
    image = _text(coin.get("image") or coin.get("image_uri"))
    created = _created(coin.get("created_timestamp") or coin.get("createdTimestamp"))
    buyers = _buyers(coin.get("unique_buyers"))
    sol = _sol_lamports(coin.get("vSolInBondingCurve"))
    tokens = _base_units(coin.get("vTokensInBondingCurve"))
    if name is not None:
        tick["name"] = name
    if image is not None:
        tick["image_uri"] = image
    if created is not None:
        tick["created_ts_utc"] = created
    if buyers is not None:
        tick["unique_buyers"] = buyers
    if sol is not None:
        tick["S"] = sol
    if tokens is not None:
        tick["T"] = tokens
    return tick


def _mint(value: object) -> str | None:
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        return None
    if any(char not in _BASE58 for char in value):
        return None
    return value


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(char for char in value if char.isprintable()).strip()
    if not cleaned:
        return None
    return cleaned[:_TEXT_LIMIT]


def _buyers(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _base_units(value: object) -> int | None:
    parsed = _whole_number(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _whole_number(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        return _whole_text(value)
    return None


def _whole_text(value: str) -> int | None:
    try:
        number = Decimal(value)
    except (ArithmeticError, ValueError):
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


def _sol_lamports(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    lamports = sol_to_lamports(number)
    return lamports if lamports > 0 else None


def _created(value: object) -> str | None:
    if isinstance(value, str):
        try:
            parse_ts_utc(value)
        except ValueError:
            return None
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    stamp = float(value)
    if stamp > 1e11:
        stamp = stamp / 1000.0
    if stamp <= 0:
        return None
    try:
        datetime.fromtimestamp(stamp, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return format_ts_utc(stamp)
