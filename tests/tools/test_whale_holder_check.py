"""WHALE holder check: concentration or BLIND. Read-only, no endpoint text."""

from __future__ import annotations

import json
from pathlib import Path

from pumpgrok.replay.clock import FixtureClock
from pumpgrok.tools.holder_check import blind, concentration, main
from pumpgrok.tools.onchain.rpc import RpcError

ROOT = Path(__file__).resolve().parents[2]
MINT = "So11111111111111111111111111111111111111112"


class _Rpc:
    def __init__(self, result: object) -> None:
        self._result = result
        self.clock = FixtureClock(0)

    async def call(self, method: str, params: list[object]) -> object:
        if isinstance(self._result, Exception):
            raise self._result
        assert method == "getTokenLargestAccounts"
        return self._result


def test_module_source_has_no_endpoint_url() -> None:
    text = (ROOT / "src" / "pumpgrok" / "tools" / "holder_check.py").read_text(encoding="utf-8")
    assert "https://" not in text
    assert "api.mainnet" not in text
    script = (ROOT / "tools" / "holder_check.py").read_text(encoding="utf-8")
    assert "Never signs or sends" in script


async def test_empty_sample_is_blind() -> None:
    rpc = _Rpc({"value": []})
    payload = await concentration(MINT, rpc)  # type: ignore[arg-type]
    assert payload["status"] == "BLIND"
    assert payload["ok"] is False
    assert payload["holders"] == []
    assert "top1Pct" not in payload


async def test_rpc_failure_is_blind() -> None:
    rpc = _Rpc(RpcError("rpc error for method=getTokenLargestAccounts"))
    payload = await concentration(MINT, rpc)  # type: ignore[arg-type]
    assert payload == blind(MINT)


async def test_sample_returns_concentration() -> None:
    rpc = _Rpc(
        {
            "value": [
                {
                    "address": "Holder111111111111111111111111111111111111",
                    "uiAmount": 75,
                    "decimals": 6,
                },
                {
                    "address": "Holder222222222222222222222222222222222222",
                    "uiAmount": 25,
                    "decimals": 6,
                },
            ]
        }
    )
    payload = await concentration(MINT, rpc, limit=20)  # type: ignore[arg-type]
    assert payload["status"] == "OK"
    assert payload["ok"] is True
    assert payload["top1Pct"] == 75.0
    assert len(payload["holders"]) == 2


def test_cli_bad_limit_is_blind_without_network(capsys) -> None:
    assert main(["--mint", MINT, "--limit", "0"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "BLIND"
