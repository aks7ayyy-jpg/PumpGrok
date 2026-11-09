"""Desk reports rendered from the ledger.

Doctor resolves ``pumpgrok.reports``. Daily and weekly markdown are built by
the existing reporting package, so a report over the same ledger bytes stays
the same text the renderer already pins.
"""

from __future__ import annotations

from pathlib import Path

from pumpgrok.reporting.daily import DailyReport
from pumpgrok.reporting.render import render_daily, render_weekly
from pumpgrok.reporting.weekly import WeeklyReport


def render_report_daily(ledger: Path, day: str, *, mode: str) -> str:
    """ASCII daily report for ``day`` (YYYY-MM-DD) from a read-only ledger."""
    return render_daily(DailyReport.build(ledger, day, mode=mode))


def render_report_weekly(ledger: Path, week: str, *, mode: str, root: Path, now: str) -> str:
    """ASCII weekly report. ``now`` is the caller's clock string, never wall time here."""
    return render_weekly(WeeklyReport.build(ledger, week, mode=mode, root=root, now=now))


__all__ = ["render_report_daily", "render_report_weekly"]
