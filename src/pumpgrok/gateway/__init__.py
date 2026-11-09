"""In-process paper order gateway.

Seats place paper orders here only when their roster grants ``exec.place``.
The gateway reads autonomy from the ledger, runs ``RiskEngine``, and fills
through ``PaperAdapter``. It never constructs a live adapter, signer, or
broadcast path.
"""

from pumpgrok.gateway.paper import GatewayError, PaperOrderGateway

__all__ = ["GatewayError", "PaperOrderGateway"]
