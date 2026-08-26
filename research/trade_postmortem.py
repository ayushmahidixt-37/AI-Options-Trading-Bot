"""Per-trade post-mortem: what happens *after* we exit, and what that says about our exits.

Every study so far measured whether a configuration made money. None asked the
diagnostic question: when a trade goes wrong, what does the option do next?
The specific worry motivating this — a position stopped out on a 5% dip that
then runs +10% minutes later — is testable directly, and if it is happening at
scale it is a *trade-management* defect rather than a signal defect. Those need
different fixes, so it is worth knowing which one we have.

For each trade this walks the selected option contract's own candles from entry
to the session's force-exit time and computes:

- **MAE** (maximum adverse excursion): the worst the position got, as a % of the
  entry fill, between entry and exit.
- **MFE** (maximum favourable excursion): the best it got over the same window.
- **Post-exit MFE**: the best it reached *after* we were already out, still as a
  % of the original entry fill. For a stopped-out trade this is the "regret" —
  how much was available to a position that simply held on.
- **Hold-to-close P&L**: what the trade would have returned exiting at the
  session's last available candle instead of when it actually exited.

Aggregated by exit reason, these answer concrete questions: are stops firing on
noise that immediately reverses? Is the profit target leaving material money on
the table? Is there an MAE level beyond which a trade is genuinely dead, which
would justify a *tighter* stop rather than a looser one?

Open interest is included because a change in OI while price dips is a common
folk explanation for "it dipped then ran". Trades are bucketed by whether OI
rose or fell over their life and the post-exit behaviour compared. Treat that
as a screening look, not a finding: OI here is per-contract on 5-minute candles
and the buckets are small.

Nothing here changes any strategy. It is measurement only.

Usage:
    python research/trade_postmortem.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import statistics
from datetime import date, datetime

from options_bot.backtest import BacktestParameters
from options_bot.backtest_cli import _settings_for_archive
from options_bot.market_archive import MarketArchive
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy
from options_bot.upstox_backtest import run_upstox_backtest
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

CANDIDATE_B = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
PARAMS = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)


def pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def summarize(label: str, values: list[float]) -> str:
    if not values:
        return f"{label:<22} (none)"
    values = sorted(values)
    median = statistics.median(values)
    return (f"{label:<22} n={len(values):>4}  median={pct(median):>7}  "
            f"p25={pct(values[len(values) // 4]):>7}  p75={pct(values[3 * len(values) // 4]):>7}  "
            f"best={pct(values[-1]):>8}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2021-12-31")
    parser.add_argument("--timeframe", default="FIVE_MINUTE")
    parser.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)
    force_exit = settings.force_exit

    result = run_upstox_backtest(
        archive, strategy=CANDIDATE_B,
        start=date.fromisoformat(args.start), end=date.fromisoformat(args.end),
        settings=settings, parameters=PARAMS,
        underlying_key=args.underlying_key, timeframe=args.timeframe,
        include_dhan=True, include_derived=True,
    )
    print(f"Post-mortem of {result.trades} trades  ({args.start}..{args.end}, {args.timeframe})")
    print(f"Strategy: Candidate B.  Net P&L {result.net_pnl:,.2f}, "
          f"return on capital {result.return_on_capital_pct}%")
    print("=" * 104)

    rows = []
    with archive.connect() as con:
        for trade in result.trade_details:
            session_end = datetime.combine(
                trade.entry_at.date(), force_exit, tzinfo=trade.entry_at.tzinfo,
            ).isoformat()
            candles = con.execute(
                """SELECT started_at, high, low, close, open_interest FROM market_candles
                   WHERE instrument_token=? AND timeframe=?
                     AND started_at>=? AND started_at<=?
                   ORDER BY started_at""",
                (trade.token, args.timeframe, trade.entry_at.isoformat(), session_end),
            ).fetchall()
            if not candles:
                continue
            entry = trade.entry_price
            exit_iso = trade.exit_at.isoformat()
            during = [c for c in candles if c[0] <= exit_iso]
            after = [c for c in candles if c[0] > exit_iso]
            if not during:
                continue

            mae = min(float(c[2]) for c in during) / entry - 1.0
            mfe = max(float(c[1]) for c in during) / entry - 1.0
            post_mfe = (max(float(c[1]) for c in after) / entry - 1.0) if after else None
            hold_close = float(candles[-1][3]) / entry - 1.0

            ois = [float(c[4]) for c in candles if c[4] is not None]
            oi_delta = (ois[-1] - ois[0]) / ois[0] if len(ois) >= 2 and ois[0] else None

            rows.append({
                "reason": trade.exit_reason, "mae": mae, "mfe": mfe,
                "post_mfe": post_mfe, "hold_close": hold_close,
                "realized": trade.net_pnl > 0, "oi_delta": oi_delta,
            })

    print(f"\nAnalysed {len(rows)} trades with usable candle paths.\n")

    by_reason: dict[str, list[dict]] = {}
    for row in rows:
        by_reason.setdefault(row["reason"], []).append(row)

    print("--- EXCURSION BEFORE EXIT, by exit reason ---")
    for reason, group in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print(f"\n[{reason}]  {len(group)} trades")
        print("  " + summarize("MAE (worst dip)", [r["mae"] for r in group]))
        print("  " + summarize("MFE (best rise)", [r["mfe"] for r in group]))

    print("\n\n--- WHAT HAPPENED AFTER WE EXITED ---")
    print("Post-exit MFE is measured against the ORIGINAL entry fill, so +10% means a")
    print("holder would have been up 10% on entry at some point after we were already out.\n")
    for reason, group in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        post = [r["post_mfe"] for r in group if r["post_mfe"] is not None]
        if not post:
            print(f"[{reason}] no post-exit window (exited at session end)")
            continue
        recovered = sum(1 for p in post if p > 0)
        big = sum(1 for p in post if p >= 0.10)
        holds = [r["hold_close"] for r in group]
        hold_wins = sum(1 for h in holds if h > 0)
        print(f"[{reason}]  {len(post)} trades with a post-exit window")
        print("  " + summarize("post-exit MFE", post))
        print(f"  reached ANY profit vs entry after we exited : {recovered}/{len(post)} "
              f"({recovered / len(post) * 100:.0f}%)")
        print(f"  reached >= +10% vs entry after we exited    : {big}/{len(post)} "
              f"({big / len(post) * 100:.0f}%)")
        print(f"  would have ended the session profitable     : {hold_wins}/{len(holds)} "
              f"({hold_wins / len(holds) * 100:.0f}%)  "
              f"median hold-to-close {pct(statistics.median(holds))}")

    print("\n\n--- IS THERE AN MAE LEVEL THAT SEPARATES WINNERS FROM LOSERS? ---")
    print("If losers dip much deeper than winners ever do, a TIGHTER stop cuts losses without")
    print("touching winners. If the distributions overlap, no stop level can separate them.\n")
    win_mae = sorted(r["mae"] for r in rows if r["realized"])
    lose_mae = sorted(r["mae"] for r in rows if not r["realized"])
    print("  " + summarize("winners' MAE", win_mae))
    print("  " + summarize("losers' MAE", lose_mae))
    if win_mae:
        for threshold in (-0.05, -0.10, -0.15, -0.20):
            w = sum(1 for m in win_mae if m <= threshold)
            lost = sum(1 for m in lose_mae if m <= threshold)
            print(f"  dipped past {threshold * 100:>4.0f}%:  winners {w:>3}/{len(win_mae)} "
                  f"({w / len(win_mae) * 100:>4.0f}%)   losers {lost:>3}/{len(lose_mae)} "
                  f"({lost / len(lose_mae) * 100:>4.0f}%)")

    print("\n\n--- OPEN-INTEREST SCREEN (directional look only, small buckets) ---")
    with_oi = [r for r in rows if r["oi_delta"] is not None and r["post_mfe"] is not None]
    if not with_oi:
        print("  no usable open-interest data on these contracts")
    else:
        rising = [r for r in with_oi if r["oi_delta"] > 0]
        falling = [r for r in with_oi if r["oi_delta"] <= 0]
        for name, group in (("OI rising", rising), ("OI falling/flat", falling)):
            if not group:
                continue
            post = [r["post_mfe"] for r in group]
            wins = sum(1 for r in group if r["realized"])
            print(f"  {name:<18} n={len(group):>4}  win rate {wins / len(group) * 100:>4.0f}%  "
                  f"median post-exit MFE {pct(statistics.median(post))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
