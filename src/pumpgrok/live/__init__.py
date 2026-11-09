"""Read-only public market data for paper runs.

The feed polls pump.fun's public REST coin list and a Solana JSON-RPC
``getSlot`` probe through the injected clients. It does not decode bonding-curve
account bytes and it does not sign or send. Recorded datasets are fixture
shaped so a later replay stays offline.
"""

from pumpgrok.live.feed import FeedPoll, PublicDataFeed, build_public_feed
from pumpgrok.live.record import write_dataset
from pumpgrok.live.session import PublicSessionResult, run_public_session

__all__ = [
    "FeedPoll",
    "PublicDataFeed",
    "PublicSessionResult",
    "build_public_feed",
    "run_public_session",
    "write_dataset",
]
