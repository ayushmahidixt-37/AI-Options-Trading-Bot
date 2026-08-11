# Project Status and Session Handoff

> **Read this file first in every new development session.** Keep it updated in
> the same commit whenever scope, safety decisions, completed work, current
> priorities, operating instructions, or known limitations change.

**Last updated:** 2026-08-11
**Current phase:** Forward paper evidence collection and validation review;
Upstox historical-backtesting feature complete and merged to `main`. Two
real-device (Termux) findings have been fixed: a Cloudflare bot-block (HTTP
403 error 1010, missing User-Agent) and a contract field-name mismatch
(`KeyError: 'expired_instrument_key'`) caused by Upstox's own inconsistent
documentation. Ingestion now tracks archive coverage and skips already-
fetched date ranges automatically, and the deep-analysis view now surfaces
plain-language best/worst highlights (even from a small sample, clearly
marked preliminary) alongside the existing gated Suggestions. The Highlights
card now links to a plain-text "Copy analysis for Claude" export so the full
breakdown can be pasted directly into a chat for tuning discussion. A real
multi-month data pull is still pending the user's own Upstox Plus
subscription/access token.
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
- Enabling automatic paper entries requires an explicit toggle confirmation;
  disabling the toggle prevents new entries while preserving exit monitoring.
- Never add a live-order call as an incidental part of another phase.
- Never commit or print Angel, Telegram, password, PIN, or TOTP secrets.
- Upstox is used only as a read-only historical-data source for offline
  backtesting; it must never place orders and is not a second trading
  broker. Never print or log an Upstox access token.

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
- The automation toggle starts a paper-monitor cycle immediately; periodic page
  refreshes always return to the GET dashboard instead of reloading POST routes.
- Browser refreshes and history navigation that revisit any `/actions/...` URL
  are redirected to the matching dashboard workspace instead of returning 405.
- Forward-paper collection defaults to two different concurrent contracts and
  no daily trade-count cap (`0`); fresh-signal deduplication, one-lot sizing,
  duplicate-contract, entry-window, daily-loss, capital, and quote gates remain.
- Paper capital and position tables expose premium committed, total entry cost,
  available capital, estimated equity, fresh LTP/P&L, and quote time, with a
  fixed 15-second monitor and dashboard refresh cadence.
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
- Termux safely migrates untouched legacy `1`-position/`3`-trades defaults to
  the two-position/no-count-cap forward-paper collection profile.
- CI pins Ruff below the next breaking rule expansion so local and GitHub lint
  checks evaluate the same documented rule set.

### Strategy validation workspace

- Explicit non-overlapping development, validation, and untouched test ranges.
- Side-by-side baseline, RSI, ATR, time-of-day, expiry-day, stop, maximum-hold,
  target, and trailing-stop variants without changing forward-paper settings.
- A candidate is selected from validation data; only that candidate is evaluated
  on the untouched test range, with CSV comparison export.

### Upstox historical backtesting — complete (Batch 3 of 3)

A second, strictly read-only data source is being added to speed up strategy
validation beyond what forward-paper collection alone can provide. Upstox is
data-only: it must never place orders and is not a second trading broker.

- `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_ACCESS_TOKEN` are recognized
  credential names (`credentials.py`); `UPSTOX_BACKTEST_ENABLED` (default
  `false`), `UPSTOX_TIMEOUT_SECONDS`, and `UPSTOX_MAX_LOOKBACK_DAYS` (default
  `180`) are new non-secret settings (`config.py`).
- `upstox_data.py`: a thin, mockable read-only client (instrument search,
  expiries, expired option contracts, expired historical candles, and the
  free non-expired Historical Candle Data V3 endpoint for underlying spot
  candles). Raises a clear error on an expired/invalid token or when an
  endpoint requires the paid Upstox Plus tier.
- `market_archive.py`: an idempotent migration adds a nullable
  `open_interest` column to `market_candles`; a new `save_upstox_candles()`
  method writes Upstox rows under `source="upstox"`, kept separate from the
  always-running Angel `save_candles()` path.
- `upstox_ingest.py`: discovers expired NIFTY option contracts for a
  requested date range (near-ATM strike selection, chunked per-contract
  requests, rate-limit pacing), pulls both option and underlying candles, and
  records each run via the existing `collection_runs` table.
- **Hard platform limit, not a budget/rate issue**: Upstox's expiry-discovery
  endpoint only returns expiries from roughly the last 6 months, so this
  feature cannot reach further back than that regardless of subscription
  tier or request volume. `UPSTOX_MAX_LOOKBACK_DAYS` mirrors this ceiling so
  requests fail fast with a clear message instead of a doomed round trip.
- **Operational note**: Upstox access tokens are short-lived (typically
  daily) with no long-lived refresh grant for this flow, unlike Angel's TOTP
  login — expect to re-authorize manually on a recurring basis.
- `upstox_backtest.py`: a walk-forward, no-lookahead replay
  (`generate_signals_from_candles`) that generates signals directly from raw
  Upstox underlying candles — no `strategy_observations` writes, kept fully
  separate from the in-production Angel-observation-based backtest.
  `run_upstox_backtest()` reuses the same conservative entry/exit/fee logic
  and restricts every `market_candles`/`instruments` lookup to
  `source='upstox'` so Angel and Upstox data can never be cross-matched in
  one run (verified by a dedicated test). Returns the existing
  `BacktestResult`/`OptionBacktestTrade` types unchanged.
- `upstox_analysis.py`: explainable, aggregate-only breakdowns (time-of-day,
  day-of-week, expiry-day, volatility regime, per-variant comparison using
  the existing `validation.py` strategy variants) and `generate_suggestions()`,
  which emits plain comparative statements only when both compared buckets
  have at least 20 supporting trades and the win-rate gap exceeds a 10
  percentage-point noise floor. No model, no fitting — every suggestion is a
  hypothesis mined from historical data, not a proven edge, and is meant to
  be manually retested through the existing development/validation/test
  split discipline, never tuned against the same data it was mined from.
- **New "Historical backtest" dashboard tab.** Two forms: pull Upstox data
  for a date range (`/actions/upstox-ingest`), then run a backtest over the
  archived data (`/actions/upstox-backtest`), which also computes the deep
  analysis breakdowns and suggestions in the same action. A CSV export
  (`/upstox/trades.csv`) mirrors the existing Research tab's pattern. Every
  route fails with a clear on-page message (never a stack trace) when the
  feature is disabled, credentials are missing, or Upstox itself is
  unreachable — network-level failures (DNS, blocked/refused connections,
  timeouts) are caught explicitly, not just HTTP error codes.
- `run_strategy_validation()` now accepts an injectable `runner` parameter
  (defaults to the existing Angel-observation-based `run_momentum_backtest`,
  unchanged), so Upstox-sourced strategy variants can go through the
  identical development/validation/untouched-test selection discipline
  instead of a shortcut.
- Manually verified end-to-end in development: booted the dashboard with
  `UPSTOX_BACKTEST_ENABLED=true` and a placeholder token, confirmed the new
  tab renders, confirmed the disabled/missing-credential/network-failure
  paths all show friendly messages instead of crashing (a real bug — an
  unhandled network exception causing a 500 — was found and fixed this way,
  with a regression test added), and confirmed the backtest action correctly
  reports `INSUFFICIENT DATA` against an empty archive.
- **Confirmed on a real Termux device**: the "Historical backtest" tab
  renders and the ingest form submits correctly. That run surfaced a real
  production issue not reachable from development testing: Upstox's API
  sits behind Cloudflare, which returned `HTTP 403` (Cloudflare error 1010,
  "blocked access based on your browser's signature") because the client
  sent no `User-Agent` header, so Python's default (`Python-urllib/...`)
  was flagged as a bot. Fixed by sending a standard browser-style
  `User-Agent`/`Accept-Language`, covered by a regression test.
- **Second real-device finding**: past the Cloudflare block, ingestion
  crashed with `KeyError: 'expired_instrument_key'`. Upstox's own docs are
  internally inconsistent about this field's name — the "Backtesting" guide
  calls it `expired_instrument_key`, but the confirmed Expired Future
  Contracts example response names it `instrument_key`, and real Expired
  Option Contracts responses use `instrument_key` too. `plan_ingestion` now
  accepts either name (and similarly for `strike_price`/`strike` and
  `instrument_type`/`option_type`), and raises a clear diagnostic error
  (listing the actual fields present) instead of a raw `KeyError` if a
  future response shape doesn't match either name — covered by regression
  tests for both the fallback and the diagnostic-error path.
- A real multi-month pull against live Upstox data has still not been
  completed — that requires the user's own Upstox Plus subscription and
  enough real testing time; the connection and discovery steps are now
  confirmed working end-to-end on a real device, though.
- **Ingestion tracks archive coverage and skips already-fetched data.**
  `MarketArchive.has_upstox_candles()`/`upstox_coverage_ranges()` let
  `pull_range()` skip any date-chunk already archived for a token instead of
  re-calling Upstox for it (an explicit `force_refetch` checkbox on the tab
  bypasses this when needed). The "Pull historical data" card now shows
  which date ranges are already available before you pull anything, and the
  ingestion result message reports how many chunks were skipped as
  already-cached.
- **Deep analysis now leads with plain-language highlights.** A new
  "Highlights" card shows the best/worst-performing group per dimension
  (time-of-day, day-of-week, expiry-day, volatility) from *any* sample
  size, clearly labeled "Preliminary" when either side is below the same
  20-trade threshold the formal Suggestions section requires. This answers
  "what's going well/badly" even from a single small backtest, while the
  existing Suggestions section stays statistically gated and unchanged.
- **"Copy analysis for Claude" plain-text export.** A new
  `GET /upstox/analysis-summary.txt` route (linked from the Highlights card)
  renders the full deep-analysis report — overall stats, every breakdown
  bucket, variant comparison, and highlights, plus the same small-sample
  caution — as plain text meant to be copy-pasted directly into a chat.
  `format_analysis_summary()` in `upstox_analysis.py` does the rendering; it
  is a pure formatter over already-computed data, not a new analysis path.
  404s until a backtest has been run.
- **New "No stop-loss cap" variant, and why it exists.** A real month-long
  Upstox backtest (56 trades, July 2026) showed a 7.1% win rate with a
  suspiciously uniform ~-300 average loss in almost every breakdown bucket —
  the fingerprint of trades being mechanically stopped out by a fixed-rupee
  stop distance (`MAX_LOSS_PER_TRADE × stop_risk_fraction ÷ lot size`, a few
  rupees of option premium by default) rather than a genuine directional
  read. `BacktestParameters.stop_risk_fraction` is now `float | None`;
  setting it to `None` skips the price-based stop/target/trailing-stop
  block entirely in both `backtest.py` and `upstox_backtest.py`, so a trade
  only exits on a signal reversal, a max-hold cap, or the session's
  force-exit time. `STRATEGY_VARIANTS` in `validation.py` includes this as
  `"No stop-loss cap"` so it always appears in the deep-analysis variant
  comparison and the "Copy analysis for Claude" export, for diagnosing
  whether a strategy's real signal quality is being masked by an
  over-tight stop before drawing conclusions from win rate alone.
- **Capital deployed and return on capital now shown alongside P&L and
  drawdown.** `BacktestResult` gained three read-only properties —
  `capital_deployed_total` (sum of entry premium across all trades),
  `capital_deployed_average` (per-trade mean), and `return_on_capital_pct`
  (net P&L as a percentage of total capital deployed) — computed purely
  from already-recorded `trade_details`, no new stored fields or analysis
  path. Shown on both the Angel-sourced offline-backtest card and the
  Upstox-backtest card, and in the "Copy analysis for Claude" export. Added
  in response to a question about what "drawdown" means and a request to
  see total capital used, since a raw rupee P&L number is hard to judge
  without knowing how much capital produced it. Note this is *turnover*
  capital, not simultaneous margin — positions are opened one at a time,
  never overlapping, so this is the sum of money moved across the whole
  period, not a peak concurrent-exposure figure.

### Strategy research backlog — not active

The professional strategy assessment is a research roadmap, not active forward
paper logic. After the current baseline has enough complete evidence, evaluate
fresh EMA crossover/separation and slope, trend pullbacks, opening-range
breakouts, VWAP/ADX confirmation, normalized ATR regimes, and minimum sample
requirements. Bid/ask liquidity, volume/open interest, implied volatility,
Greeks, calibrated confidence, and debit spreads require additional reliable
option-chain fields and conservative multi-leg cost modelling before they can be
implemented. Do not activate these ideas together or select them from the same
period used for final testing.

### Paper-readiness review gate

- Evidence checklist for archive coverage/gaps/integrity, paper-trade count and
  drawdown, force exits, heartbeat/failures, backups, credential permissions,
  dashboard password, and the paper-only boundary.
- Persisted manual acknowledgements for broker restrictions, recovery drills,
  and operator acceptance, plus a downloadable review CSV.
- A completed review explicitly records `live_trading_approved=false`; it never
  changes configuration or exposes broker order submission.

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

## Completed review phase

### Phase C — review gate, not automatic live deployment

Only after extended forward-paper evidence, perform a documented readiness
review covering reliability, drawdown, data quality, broker restrictions,
security, operational recovery, and user acceptance. Live trading is not an
approved phase and must require a separate explicit decision and design review.

The evidence gate is implemented, but it will remain blocked until the tablet
has collected the required forward-paper evidence and manual reviews. Passing
the gate still does not approve or enable live trading.

## Known limitations and cautions

- Termux must remain alive; Android can suspend it despite the web page working.
- `12345` is acceptable only for localhost. Use a strong password before any
  tunnel or LAN binding.
- Signals and option history are useful only when archive coverage is adequate.
- Market-closed behavior cannot validate live-session quote timing.
- SmartAPI availability, permissions, rate limits, and contract data remain
  external dependencies.
- A successful backtest or paper period does not guarantee profitability.
- Upstox's expiry-discovery endpoint only covers roughly the last 6 months;
  historical backtesting through Upstox cannot reach further back than that
  regardless of subscription tier. Upstox access tokens expire (typically
  daily) and require manual re-authorization; there is no long-lived refresh
  grant for this flow.

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
- The paper-readiness evidence gate and CSV review export are complete; live
  trading remains unapproved and unreachable.
- The strategy assessment backlog is documented but intentionally not active;
  the EMA/RSI ATM-option baseline remains the forward-paper strategy.
- The most valuable next activity is collecting complete forward paper sessions,
  preserving the SQLite archive, and reviewing Phase B only when every split has
  adequate low-gap option history.
- All three Upstox historical-backtesting batches (credentials/settings/
  client; storage/ingestion; backtest engine and deep analysis/suggestions;
  dashboard tab and validation-loop integration) are merged to `main`.
  `UPSTOX_BACKTEST_ENABLED` still defaults to `false`, so none of this
  changes runtime behavior for existing forward-paper operation unless
  explicitly turned on.
- Confirmed working on a real Termux device: the dashboard tab renders and
  the ingest action reaches Upstox. Two real-device-only issues were found
  and fixed, each merged separately: a Cloudflare bot-block (HTTP 403, error
  1010) caused by a missing `User-Agent` header, and a `KeyError` from
  Upstox's expired-option-contract responses using field name
  `instrument_key` where their own docs inconsistently say
  `expired_instrument_key` — the client now accepts either. The Upstox
  historical-backtesting feature is feature-complete end-to-end and its
  connection/discovery steps are confirmed live; a real multi-month data
  pull is still pending the user's own Upstox Plus subscription/access
  token and testing time.
- Added coverage-aware ingestion (skip already-fetched date ranges unless
  explicitly forced) and plain-language "Highlights" alongside the existing
  gated Suggestions, in response to direct user feedback that repeat pulls
  wasted API calls and that the analysis view gave no visible feedback
  until 20+ trades accumulated.
- Added a "Copy analysis for Claude" plain-text export
  (`GET /upstox/analysis-summary.txt`) so the full deep-analysis breakdown
  can be pasted directly into a chat for tuning discussion, in response to
  direct user feedback that they wanted an easy way to hand over analysis
  results for parameter-change suggestions.
