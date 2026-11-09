"""pumpgrok.reports renders the ledger. Doctor resolves the module."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from pumpgrok.cli import main
from pumpgrok.config.settings import FORBIDDEN_SUBSTRINGS
from pumpgrok.reporting.daily import DailyReport
from pumpgrok.reporting.render import render_daily
from pumpgrok.reports import render_report_daily


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith("PUMPGROK_") or any(
            fragment in upper for fragment in FORBIDDEN_SUBSTRINGS
        ):
            monkeypatch.delenv(name, raising=False)


def test_doctor_can_resolve_reports() -> None:
    assert importlib.util.find_spec("pumpgrok.reports") is not None
    assert importlib.util.find_spec("pumpgrok.gateway") is not None


def test_daily_report_matches_the_renderer_on_an_empty_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "missing.sqlite"
    direct = render_daily(DailyReport.build(ledger, "2026-11-09", mode="paper"))
    wrapped = render_report_daily(ledger, "2026-11-09", mode="paper")
    assert wrapped == direct
    assert wrapped.startswith("# PumpGrok paper report - daily - 2026-11-09 (UTC)\n")
    assert "paper" in wrapped


def test_cli_report_daily_prints_the_ledger_report(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["report", "--daily", "--day", "2026-11-09"]) == 0
    out = capsys.readouterr().out
    assert "# PumpGrok paper report - daily - 2026-11-09 (UTC)" in out
    assert not (tmp_path / "var").exists()
