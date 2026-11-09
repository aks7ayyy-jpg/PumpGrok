"""CLI paper public-data path. The feed is injected; nothing dials the network."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pumpgrok.cli import main, verbs_state
from pumpgrok.config.settings import FORBIDDEN_SUBSTRINGS
from pumpgrok.live.feed import FeedPoll
from pumpgrok.replay.fixtures import load_dataset


class _Feed:
    def __init__(self, payload: FeedPoll) -> None:
        self.payload = payload
        self.closed = False

    async def poll(self) -> FeedPoll:
        return self.payload

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith("PUMPGROK_") or any(
            fragment in upper for fragment in FORBIDDEN_SUBSTRINGS
        ):
            monkeypatch.delenv(name, raising=False)


def test_live_run_still_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--mode", "live", "--once"]) == 3
    err = capsys.readouterr().err
    assert "adapter_registered" in err
    assert not (tmp_path / "var").exists()


def test_paper_once_uses_the_public_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    feed = _Feed(FeedPoll(ticks=(), rest="BLIND", rpc="BLIND", slot=None))
    monkeypatch.setattr(verbs_state, "build_public_feed", lambda *_args, **_kwargs: feed)
    assert main(["run", "--mode", "paper", "--once"]) == 0
    out = capsys.readouterr().out
    assert "public feed rest=BLIND rpc=BLIND slot=none ticks=0" in out
    assert "engine decision=skip reason=no_events" in out
    assert feed.closed is True


def test_record_once_writes_a_replayable_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    mint = "So11111111111111111111111111111111111111112"
    tick = {
        "kind": "tick",
        "mint": mint,
        "name": "Example",
        "source": "rest",
        "complete": False,
        "S": 1_000_000_000,
        "T": 10,
    }
    feed = _Feed(FeedPoll(ticks=(tick,), rest="ok", rpc="ok", slot=4))
    monkeypatch.setattr(verbs_state, "build_public_feed", lambda *_args, **_kwargs: feed)
    assert main(["run", "--mode", "paper", "--once", "--record", "sample"]) == 0
    out = capsys.readouterr().out
    assert "recorded dataset=sample events=1" in out
    loaded = load_dataset(tmp_path / "var" / "recordings" / "sample")
    assert loaded.events[0]["mint"] == mint
    assert "slot=4" in out


def test_replay_once_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    smoke = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "smoke"
    assert main(["run", "--mode", "paper", "--once", "--replay", str(smoke)]) == 0
    out = capsys.readouterr().out
    assert "engine decision=buy" in out
    assert "trades filled=2" in out
