"""#6 VWAP Breakout with Volume Confirmation -- NOT RUN.

This strategy requires real (non-zero) volume: the long entry needs "volume
>= 2x 20-period average" and a consolidation filter ("within ~0.1% of VWAP
for >=5 consecutive candles") that in practice is judged on relative volume
too. The NIFTY index series in data_ingest/data/nifty_forward.sqlite3 has
volume IS NULL on 100% of rows -- a price index has no traded quantity.
This has already been confirmed by prior work in this same repo (see
clean_room/backtest_index_scalps.py's module docstring, which reports the
same check across all 936,271 rows).

Unlike VWAP (which this project substitutes with session TWAP when clearly
labelled TWAP-PROXY, since TWAP is at least a defined, honestly-computed
number from the same OHLC data), volume itself has no honest substitute --
fabricating or proxying it would misrepresent what the archive actually
contains. So this strategy is not implemented; this script only records
that fact so the master report has a results/s06_vwap_volume_breakout.json
file to read for this strategy's status, per the project's convention that
every strategy in scope gets an entry.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402

NAME = "s06_vwap_volume_breakout"


def main(argv=None):
    print(f"{NAME}: NOT RUN -- no volume data in archive "
          "(index series, volume IS NULL on all rows)")
    out, _ = E.save_results(NAME, [], extra={
        "status": "NOT RUN - no volume data in archive (index series, volume IS NULL on all rows)",
    })
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
