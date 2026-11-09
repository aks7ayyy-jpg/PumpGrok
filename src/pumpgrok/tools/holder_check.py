"""Read-only top-holder concentration. Never signs or sends.

WHALE's holder skill asks for a concentration snapshot. This module is that
check: ``getTokenLargestAccounts`` through the injected ``RpcClient``. When
the sample is missing or the call fails, the result is ``BLIND`` rather than
a zero concentration or a raised error. Endpoint URLs are not read or logged
here; the client already holds them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from pumpgrok.config.settings import Settings, load_settings
from pumpgrok.core.errors import DataStale
from pumpgrok.replay.clock import Clock, WallClock
from pumpgrok.replay.rng import make_rng
from pumpgrok.tools.onchain.holders import holder_snapshot
from pumpgrok.tools.onchain.rpc import RpcClient, RpcError

_MAX_LIMIT = 20
_BLIND_NOTE = "No holder sample was returned. Concentration is BLIND, not zero."


def blind(mint: str) -> dict[str, Any]:
    """Concentration with no data. Shares are omitted so they cannot be read as zero."""
    return {
        "ok": False,
        "status": "BLIND",
        "mint": mint,
        "reason": "no holder data",
        "holders": [],
        "note": _BLIND_NOTE,
    }


async def concentration(mint: str, rpc: RpcClient, limit: int = _MAX_LIMIT) -> dict[str, Any]:
    """Largest-account concentration, or ``BLIND`` when the sample is empty."""
    bounded = min(max(int(limit), 1), _MAX_LIMIT)
    try:
        snap = await holder_snapshot(mint, rpc, limit=bounded)
    except (RpcError, DataStale, OSError, TypeError, ValueError, KeyError):
        return blind(mint)
    if not isinstance(snap, dict) or not snap.get("ok"):
        return blind(mint)
    holders = snap.get("holders")
    if not isinstance(holders, list) or not holders:
        return blind(mint)
    out = dict(snap)
    out["status"] = "OK"
    return out


async def _from_settings(settings: Settings, mint: str, limit: int, clock: Clock) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        rpc = RpcClient(
            list(settings.rpc.endpoints),
            client,
            clock,
            make_rng(settings.replay.seed),
            api_key=settings.rpc.api_key,
        )
        return await concentration(mint, rpc, limit)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--mint`` and optional ``--limit``. Prints one JSON object."""
    parser = argparse.ArgumentParser(
        prog="holder_check",
        description="Read-only top-holder concentration. Never signs or sends.",
    )
    parser.add_argument("--mint", required=True, help="token mint (base58 public key)")
    parser.add_argument("--limit", type=int, default=_MAX_LIMIT, help="largest accounts, 1-20")
    args = parser.parse_args(argv)
    mint = str(args.mint).strip()
    if not mint or args.limit < 1 or args.limit > _MAX_LIMIT:
        sys.stdout.write(json.dumps(blind(mint or ""), sort_keys=True) + "\n")
        return 1
    try:
        settings = load_settings()
        payload = asyncio.run(_from_settings(settings, mint, args.limit, WallClock()))
    except (OSError, RuntimeError, ValueError, TypeError):
        payload = blind(mint)
    json.dump(payload, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0
