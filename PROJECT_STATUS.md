# Project Status and Session Handoff

> **Read this file first in every new development session.** Keep it updated in
> the same commit whenever scope, safety decisions, completed work, current
> priorities, operating instructions, or known limitations change.

**Last updated:** 2026-08-07
**Current phase:** Forward paper evidence collection and validation review
**Production status:** Not approved for live trading

## Objective

Build a tablet-operated NIFTY options research and paper-trading system that:

- obtains NIFTY and option market data from Angel One;
- stores validated five-minute data locally for future analysis;
- generates deterministic signals and simulates conservative fills;
- reports through a localhost dashboard and outbound-only Telegram alerts; and
- proves reliability and strategy behavior before any live-trading discussion.

This project does not promise passive income or profitability. Historical and
paper results are simulations and may not represent future results.

## Non-negotiable safety boundary

- `TRADING_MODE=paper` is the only accepted application mode.
- `LIVE_TRADING_ENABLED=false` and `AUTO_START=false` remain mandatory.
- The currently reachable Angel integration is market-data only.
- Telegram is outbound-only; it does not accept trading commands.
- Automatic entries create paper-ledger rows only and are disabled by default.
- Enabling automatic paper entries requires the exact text
  `ENABLE AUTO PAPER`; disabling requires `DISABLE AUTO PAPER`.
- Never add a live-order call as an incidental part of another phase.
- Never commit or print Angel, Telegram, password, PIN, or TOTP secrets.

## What is implemented

### Tablet operation and UI

- FastAPI/Jinja2 dashboard protected with HTTP Basic authentication.
- Termux installer/launcher and localhost operation at `127.0.0.1:8000`.
- Initial local-only login is `admin` / `12345`; the launcher stores the
  password in `.termux-data/web-password` with owner-only permissions.
- Health, connection, archive, signal, paper position, journal, and safety
  controls are visible in one dashboard.

### Market data and archive

- Angel One session login, NIFTY LTP, and closed five-minute candles.
- EMA 9, EMA 21, RSI 14, ATR 14, freshness checks, and deterministic
  bullish/bearish/no-trade intelligence.
- Background refresh with reconnect and bounded catch-up behavior.
- Durable SQLite master archive at `.termux-data/market-data.sqlite3`.
- Duplicate-safe NIFTY candles, option candles, and retained instrument history.
- Nearest-expiry NIFTY option collection around ATM, CSV export, database
  backup, coverage/gap reporting, and SQLite integrity verification.

### Backtesting

- Offline-only replay from archived observations and option candles.
- Date filters, conservative slippage/fees, stop handling, force exit,
  per-trade details, drawdown, profit factor, and CSV export.
- Backtests report insufficient data rather than inventing missing prices.

### Paper trading and risk

- Two-step manually confirmed one-lot paper proposals.
- Optional automatic paper-only entries, explicitly enabled and persisted.
- One attempt per closed signal candle prevents restart duplicates.
- Central checks for entry time, quote freshness, lots, maximum trade loss,
  open positions, duplicate symbols, daily trades, daily loss, and capital.
- Automatic stop, signal-reversal, and force-exit monitoring.
- Fresh position quotes, estimated unrealized P&L, restart recovery visibility,
  Telegram lifecycle alerts, and typed paper kill switch.

### Journal and reporting

- Durable trade context: signal time/direction, NIFTY spot, indicators,
  confidence, contract, expiry, strike, risk, and option excursion.
- Today, 7-day, 30-day, and all-time paper analytics.
- Win rate, average win/loss, reward-to-risk, profit factor, net P&L, and
  maximum drawdown.
- Durable safety/audit events.

### Operational hardening

- Persistent heartbeat, Angel/archive/paper-cycle timestamps, failure count,
  recovery time, storage headroom, stale-data entry lock, and dashboard status.
- Deduplicated repeated-failure and recovery Telegram notifications.
- One deduplicated post-force-exit Telegram daily report.
- Automatic archive backup rotation using the configured retention count.
- Termux startup now fails clearly on tracked local changes and requires a
  fast-forward to the latest `origin/main`, preventing silent use of old code.

### Strategy validation workspace

- Explicit non-overlapping development, validation, and untouched test ranges.
- Side-by-side baseline, RSI, ATR, time-of-day, expiry-day, stop, maximum-hold,
  target, and trailing-stop variants without changing forward-paper settings.
- A candidate is selected from validation data; only that candidate is evaluated
  on the untouched test range, with CSV comparison export.

## Important local files

| Purpose | Tablet path relative to repository |
| --- | --- |
| Non-secret runtime configuration | `local-bot.env` |
| Private Angel/Telegram credentials | `credentials.env` |
| Saved dashboard password | `.termux-data/web-password` |
| Paper account, orders, journal, state | `.termux-data/paper.sqlite3` |
| Market-data master archive | `.termux-data/market-data.sqlite3` |
| Automatic archive backups | `.termux-data/backups/` |

SQLite files are the master records. CSV downloads are exports, not databases.
Do not delete `.termux-data` during updates.

## How to resume on the tablet

```bash
cd ~/AI-Options-Trading-Bot
git checkout main
git pull origin main
chmod +x scripts/termux_web.sh
scripts/termux_web.sh
```

Then open `http://127.0.0.1:8000` on the same tablet. Keep Termux running,
disable Android battery optimization for Termux, and keep the tablet powered
during a full-session test.

## Current validation plan

Before adding another trading feature, collect forward paper evidence:

1. Run at least 20 complete NSE sessions, preferably producing 100+ paper
   trades before drawing strategy conclusions.
2. Check archive gap count, reconnects, monitor errors, duplicate entries,
   force exits, journal completeness, fees, and drawdown each day.
3. Confirm no paper position remains open after the configured force-exit time.
4. Download periodic SQLite backups without replacing the tablet master copy.
5. Keep automatic paper entry disabled whenever behavior is being diagnosed.

## Completed implementation phases

### Phase A — operational hardening and daily Telegram report

- Persists monitor heartbeat, consecutive failure count, last successful Angel
  response, last archive write, and last paper cycle.
- Adds storage-space and stale-data lockout indicators.
- Alerts Telegram after repeated failures and again after recovery.
- Sends one force-exit-time daily paper report with trades, P&L, fees, open
  positions, archive gaps, reconnects, failures, and database integrity.
- Adds backup retention/rotation and Termux:Boot guidance.
- Includes deterministic tests for alert deduplication and restart behavior.

### Phase B — strategy validation workspace

- Compares strategy variants without changing the forward-paper strategy.
- Adds development, validation, and untouched test date ranges.
- Compares RSI thresholds, ATR filter, time-of-day filters, stop styles,
  trailing stops, targets, maximum hold, weekdays, and expiry-day behavior.
- Exports a comparison table and clearly flags insufficient/gapped datasets.
- Avoids choosing parameters solely because they maximize historical profit.

Both phases are implemented. Results remain preliminary until the archive has
enough complete, low-gap sessions in every split.

## Next review phase

### Phase C — review gate, not automatic live deployment

Only after extended forward-paper evidence, perform a documented readiness
review covering reliability, drawdown, data quality, broker restrictions,
security, operational recovery, and user acceptance. Live trading is not an
approved phase and must require a separate explicit decision and design review.

## Known limitations and cautions

- Termux must remain alive; Android can suspend it despite the web page working.
- `12345` is acceptable only for localhost. Use a strong password before any
  tunnel or LAN binding.
- Signals and option history are useful only when archive coverage is adequate.
- Market-closed behavior cannot validate live-session quote timing.
- SmartAPI availability, permissions, rate limits, and contract data remain
  external dependencies.
- A successful backtest or paper period does not guarantee profitability.

## Standard checks before committing

```bash
git diff --check
bash -n scripts/termux_web.sh
ruff check src tests
python -m compileall -q src tests
python -m pytest -q
python -m pip wheel --no-deps . -w /tmp/options-wheels
```

For a visible dashboard change, capture a screenshot when a browser is
available. If the environment prevents it, record the exact limitation.

## Session update checklist

At the end of every development session, update this file when applicable:

- [ ] Change **Last updated** and **Current phase**.
- [ ] Move completed work into **What is implemented**.
- [ ] Update **Next implementation phases** and known blockers.
- [ ] Record new operating commands, paths, and safety decisions.
- [ ] Ensure no credentials or personal secrets were added.
- [ ] Run the standard checks and include results in the final response.
- [ ] Commit, push, and update/create the pull request as required.

## Latest handoff

- Durable paper journal and period analytics are complete.
- Guarded automatic paper entries are complete and disabled by default.
- Operational hardening, daily Telegram reporting, backup rotation, and the
  strategy validation workspace are complete.
- The most valuable next activity is collecting complete forward paper sessions,
  preserving the SQLite archive, and reviewing Phase B only when every split has
  adequate low-gap option history.
