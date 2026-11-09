"""Read-only top-holder concentration. Never signs or sends.

WHALE runs this as ``python tools/holder_check.py --mint <MINT> --limit 20``.
The check lives in ``pumpgrok.tools.holder_check`` and returns BLIND when the
public RPC sample is missing. This script does not sign or send.
"""

from __future__ import annotations

import sys

from pumpgrok.tools.holder_check import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
