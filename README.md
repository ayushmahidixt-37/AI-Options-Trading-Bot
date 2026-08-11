# AI Options Trading Bot

> **Session handoff:** Read [`PROJECT_STATUS.md`](PROJECT_STATUS.md) first for
> the current implementation, safety boundary, validation plan, and next phase.

Tablet-friendly foundations for an options bot deliberately limited to
**paper trading**, with Angel One read-only market data, durable archives,
backtesting, and guarded simulated entries. This release contains no reachable
broker order-submission implementation.

> Automated trading cannot guarantee profits or passive income. Keep the bot in
> paper mode until its data, strategy, risk controls, and operations have been
> evaluated over an extended period.

## Current safety boundary

- `TRADING_MODE=paper` is the only accepted mode.
- `LIVE_TRADING_ENABLED` and `AUTO_START` must both remain false.
- The paper broker models adverse slippage and per-order fees.
- Risk checks reject stale quotes, excessive lots/risk, duplicate positions,
  insufficient capital, entries outside the configured window, and breached
  daily limits.
- The monolithic `bot30.ipynb.txt` prototype has been removed. Its maintained
  responsibilities now live in focused package modules.
- The preserved live adapter is isolated in `execution/live_angel.py`; the
  application configuration rejects live mode before it can be constructed.

## Server layout

```text
/opt/ai-options-bot/                 application checkout
/etc/ai-options-bot/bot.env          non-secret settings
/etc/ai-options-bot/credentials.env  private values
/var/lib/ai-options-bot/              SQLite database and process lock
/var/log/ai-options-bot/              operational logs
```

This Codex workspace is for development and tests; it is not a persistent
server. For continuous forward paper trading, use an always-on Linux VPS or a
reliable local Linux machine.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e . pytest
cp bot.env.example /tmp/options-bot.env
sed -i "s|/var/lib/ai-options-bot|$PWD/.local-data|g" /tmp/options-bot.env
options-bot --config /tmp/options-bot.env validate-config
options-bot --config /tmp/options-bot.env init-db
options-bot --config /tmp/options-bot.env healthcheck
options-bot --config /tmp/options-bot.env status
```

Run the idle, paper-only service skeleton with:

```bash
options-bot --config /tmp/options-bot.env serve
```

The service emits health heartbeats but does **not** start entries automatically.

## Linux server installation

Place the repository at `/opt/ai-options-bot`, then inspect and run:

```bash
sudo bash deploy/install.sh
sudo editor /etc/ai-options-bot/credentials.env
sudo editor /etc/ai-options-bot/bot.env
sudo -u ai-options-bot /opt/ai-options-bot/.venv/bin/options-bot \
  --config /etc/ai-options-bot/bot.env validate-config
sudo systemctl enable --now ai-options-bot
sudo systemctl status ai-options-bot
```

Do not paste server, GitHub, Angel One, Telegram, or TOTP secrets into chat.

## Credentials

The credential loader accepts only the recognized names in
`credentials.env.example`. The populated file must stay outside Git and should
be readable only by root and the service group. `UPSTOX_API_KEY`,
`UPSTOX_API_SECRET`, and `UPSTOX_ACCESS_TOKEN` are recognized for the
read-only historical-backtesting feature; they carry no order-placement
capability.

Previously committed credentials must be revoked and rotated. Removing secrets
from the current revision does not erase them from Git history; clean the history
before making the repository public.

## Commands

```text
options-bot validate-config  validate fail-closed settings
options-bot init-db          initialize/migrate the paper ledger
options-bot healthcheck      check mode, SQLite, and free disk
options-bot status           show paper account and open positions
options-bot serve            run the signal-aware service skeleton
options-bot web              run the local password-protected paper UI
```

## Local web UI

The web UI is designed for local laptop/server control in paper mode. It uses
HTTP Basic auth with username `admin` and a password from
`OPTIONS_BOT_WEB_PASSWORD`. It shows safety status, health, paper account state,
open paper positions, and paper-safe action buttons. Telegram remains alert-only
and does not receive commands.

```bash
export OPTIONS_BOT_WEB_PASSWORD='change-this-local-password'
options-bot --config /tmp/options-bot.env web --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` and sign in as `admin`. Keep the UI bound to
`127.0.0.1` unless you add a trusted reverse proxy with HTTPS.


## Android tablet / Termux quick start

If you only have an Android tablet, install Termux from F-Droid or GitHub, then run this in Termux:

```bash
pkg update -y
pkg install -y git
git clone https://github.com/ayushmahidixt-37/AI-Options-Trading-Bot.git
cd AI-Options-Trading-Bot
scripts/termux_web.sh
```

The script installs only the runtime packages needed by the dashboard (including the timezone database required by Android), creates a tablet-local config under `.termux-data`, checks that the application imports, and starts the password-protected UI on `http://127.0.0.1:8000`. It intentionally does not install developer tools such as Ruff because compiling them can exceed a tablet's available memory. Open that URL in Chrome on the same tablet and sign in as `admin` with the initial password `12345`.

The Termux launcher saves that password in `.termux-data/web-password` with owner-only permissions and reuses it on every start, so no repeated editing is needed. This simple default is suitable only while the dashboard remains bound to `127.0.0.1`; set `OPTIONS_BOT_WEB_PASSWORD` to a stronger value before exposing the UI through a tunnel or network interface. The supplied value is then saved for future starts.

Keep the Termux session running while using the dashboard. If an earlier setup stopped before converting the Linux `/var/lib/ai-options-bot` paths, rerun `scripts/termux_web.sh`; it repairs those defaults automatically before starting the server.

Before the first connected-data run, put the rotated Angel One and Telegram values in the private Termux file:

```bash
cd ~/AI-Options-Trading-Bot
nano credentials.env
```

Fill `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PASSWORD`, `ANGEL_TOTP_SECRET`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`. Do not paste credentials into GitHub, Colab, or chat. In Nano, save with **Ctrl+O**, press **Enter**, and exit with **Ctrl+X**. The Termux script creates this ignored file when needed, restricts it to the current user, and stops with editing instructions if a required value is empty.

In the dashboard, use **Test Angel connection** before **Refresh NIFTY**. A connected dashboard refreshes the market-data-only NIFTY quote every 15 seconds. Use **Send test alert** to verify one-way Telegram notifications; Telegram commands and live order execution remain disabled.

If Angel One rejects the connection, the panel shows Angel's error code and a credential-redacted reason. Share that displayed reason when troubleshooting, never the contents of `credentials.env`.

The NIFTY panel similarly displays Angel's credential-redacted quote error when login succeeds but quote retrieval fails. Share only that **Reason** text to diagnose symbol, token, permission, or API errors.

NIFTY refresh uses Angel's token-based Market Data API first and automatically falls back to the legacy LTP endpoint for SDK compatibility.

The Termux installer includes the logging and WebSocket modules imported by Angel One's SDK, even though this milestone uses only REST market data. Rerun the installer after every `git pull` so newly required runtime packages are installed.

SmartAPI's SDK can log complete request headers on API failures. The application disables that SDK logger and the Termux installer removes older `logs/` files that may contain an API key. If a terminal output or screenshot ever reveals `X-PrivateKey`, rotate that Angel API key immediately.

### Free public Angel redirect URL

This repository includes a credential-free GitHub Pages callback at `docs/angel-callback/index.html`. After enabling GitHub Pages for this repository from the `/docs` folder, register this exact redirect URL with Angel One:

```text
https://ayushmahidixt-37.github.io/AI-Options-Trading-Bot/angel-callback/
```

The callback page does not run the bot and is not a substitute for any static outbound IP Angel requires. The bot continues to authenticate with SmartAPI's direct session API from Termux.

### Read-only five-minute intelligence

After **Test Angel connection** succeeds, use **Refresh NIFTY** or **Refresh 5-minute analysis** to load up to 100 closed NIFTY candles from Angel One. Spot quotes continue every 15 seconds, while historical analysis is rate-limited to once per five-minute bucket. The dashboard calculates EMA 9, EMA 21, RSI 14, ATR 14, candle freshness, and a plain-language signal. The currently forming candle is excluded, malformed or out-of-order data fails closed, and no order is placed. Telegram sends an alert only when an actionable `BULLISH`, `BEARISH`, or `NO TRADE` state changes.

The web process also starts a read-only background monitor. It reconnects to Angel One when needed, refreshes spot data every 15 seconds, and evaluates at most once per five-minute bucket even when Chrome is closed. The dashboard reload only displays the latest in-memory snapshot; it does not drive the monitor. Keep Termux and the `options-bot web` process running, and disable Android battery optimization for Termux if Android suspends it. This monitor has no order-placement call and does not change the `AUTO_START=false` or `LIVE_TRADING_ENABLED=false` safety requirements.

### Local historical archive

The monitor permanently stores validated closed NIFTY candles in `.termux-data/market-data.sqlite3`, separate from the paper ledger. Repeated API responses are duplicate-safe. The daily instrument-master refresh archives current NIFTY option metadata—including token, strike, expiry, type, and lot size—without deleting expired contracts already in the database. The dashboard shows archive coverage, database size, possible intraday gaps, nearest expiry, and ATM strike. It also provides an Excel-compatible candle CSV and a downloadable SQLite backup. SQLite remains the master copy; exports do not modify it, and the archive cannot place an order.

During the open session, the next archive phase collects closed five-minute candles for the nearest-expiry NIFTY CE and PE contracts at ATM ±5 strikes. Collection is limited to once per five-minute bucket and retains expired-contract history. The offline backtest button uses only local strategy observations, instrument metadata, and option candles: an entry uses the next available option candle open and an exit uses the last candle before the next opposing directional signal. Results include configured lot size, fees, slippage, net paper P&L, drawdown, and profit factor; they are simulations, not guaranteed returns. It reports insufficient data until enough matching option history has accumulated.

The confirmed paper-entry panel is deliberately two-step. **Create fresh paper proposal** reads the current signal, selects the matching nearest-expiry ATM CE or PE, fetches a current option quote, and calculates a one-lot stop below the configured maximum-loss limit. It does not open a position. **Confirm one-lot paper entry** fetches and validates the proposal again, rejects a changed signal or ATM contract, runs every existing risk check, and only then writes a simulated paper position. No SmartAPI order method is called and live execution remains disabled.

Confirmed paper positions are monitored by the same 15-second background worker even when Chrome is closed. The paper monitor fetches a fresh option quote and automatically closes the simulated position if its stop is reached, the NIFTY directional signal reverses, or the configured force-exit time arrives. Quote failures leave the position open and are shown in the dashboard rather than guessing a fill. Manual entries remain separately confirmed; when guarded automation is explicitly enabled, the monitor may create paper-ledger entries from new closed signals, but it cannot place a broker order.

Every confirmed proposal is also written to a durable paper journal with its signal candle, NIFTY spot, EMA, RSI, ATR, confidence, selected contract, estimated risk, favorable/adverse option movement, exit reason, fees, and net result. The dashboard summarizes all closed journaled trades with win rate, average win/loss, and profit factor. Journal data remains in the paper SQLite database across restarts.

Automatic **paper-only** entries are available but disabled by default. Use the Automation toggle and accept the browser confirmation to start monitoring immediately. The forward-paper collection profile permits two different open contracts and uses `MAX_TRADES_PER_DAY=0` to remove the daily count cap; every entry still requires a new closed actionable five-minute signal and must pass quote freshness, entry-window, one-lot, duplicate-contract, position-capacity, per-trade loss, daily-loss, and available-capital checks. Turn the toggle off to prevent new entries; existing paper positions continue to be monitored for exits. This feature writes simulated ledger rows only and has no SmartAPI order call.

The toggle state is stored in the paper database. After Termux restarts the application, the background worker reconnects Angel One, resumes the 15-second archive/position cycle, and restores enabled paper automation without requiring another code change. Android must still be allowed to keep Termux running; a stopped or suspended process cannot collect data.

The Paper trading workspace refreshes every 15 seconds and shows each open contract's lots/units, entry fill, premium committed, total entry cost including its simulated entry fee, stop, fresh option LTP, estimated net open P&L, and latest quote time. The Paper capital summary shows aggregate premium committed, capital used, capital available, and estimated equity. These are simulated values, not funds held by Angel One.

### Reliability and detailed backtests

Set `NSE_HOLIDAYS` in `local-bot.env` to the official comma-separated `YYYY-MM-DD` holiday dates for the current year; those dates are treated as closed sessions rather than failures. The monitor now attempts one fresh Angel login when a quote refresh indicates an expired/broken session, performs a bounded NIFTY candle catch-up after downtime, and displays reconnect, catch-up, and SQLite integrity status. The **Verify database** action runs SQLite's quick integrity check without modifying data.

Archived backtests support optional start/end dates, conservative historical stop handling (opening gaps use the candle open; intrabar stop touches use the stop), configured force exit, lot size, fees, and slippage. Detailed rows show contract, entry, stop, exit, reason, and net P&L, and can be downloaded as CSV. These reports remain simulations and should be considered preliminary until multiple complete sessions with low gap counts have been collected.

### Operational hardening and daily report

The Data & Operations workspace shows persistent monitor heartbeat, last Angel success, last archive write, last paper cycle, consecutive failures, free storage, and any stale-data/storage entry lock. Telegram sends one warning when the configured consecutive-failure threshold is reached and one recovery message after service returns. After force-exit time, the paper monitor sends one daily summary per trading date. Automatic backups are rotated according to `BACKUP_RETENTION_COUNT`.

For optional startup after an Android reboot, install **Termux:Boot** from the same source as Termux, open it once, and create `~/.termux/boot/start-options-bot` containing:

```bash
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
cd "$HOME/AI-Options-Trading-Bot"
scripts/termux_web.sh >> "$HOME/options-bot-startup.log" 2>&1
```

Then run `chmod 700 ~/.termux/boot/start-options-bot`. Automatic Android startup does not remove the need to disable battery optimization, monitor the dashboard, and keep the system in paper mode.

### Strategy validation workspace

The Research workspace compares documented variants over three required chronological, non-overlapping ranges. Development and validation results are visible for every variant; one candidate is selected using validation results and evaluated once against the untouched test range. Other variants never see test results. Comparison CSV exports include trades, net P&L, and drawdown. Gaps or missing option history keep the report preliminary.

### Paper-readiness review

The Readiness review workspace turns the documented review gate into an evidence checklist. It assesses archive sessions, gaps and integrity; closed paper trades and drawdown; overdue positions; monitor heartbeat and failures; backups; credential-file permissions; dashboard password strength; and the enforced paper-only configuration. Broker restrictions, recovery drills, and operator acceptance require explicit persisted acknowledgements using `SAVE PAPER REVIEW`.

The downloadable readiness CSV always contains `live_trading_approved=false`. Even a fully passing paper review does not change configuration, unlock the quarantined live adapter, or authorize broker orders.

## Functional modules

```text
config.py                  validated server and risk settings
market_archive.py          durable NIFTY candles, option metadata, CSV, and backups
credentials.py             strict external secret-file parsing
instruments.py             Angel instrument-master normalization and ATM universe
market_data.py             Angel One quote/candle data only
candles.py                 thread-safe closed-candle aggregation
indicators.py              SMA, EMA, RSI, ATR
strategy.py                deterministic underlying direction and CE/PE selection
risk.py                    centralized paper pre-trade checks
paper_broker.py            conservative simulated fills
ledger.py                  transactional SQLite state
backtest.py                chronological, no-same-bar-look-ahead replay
validation.py              split-safe offline strategy comparison workspace
readiness.py               evidence-based paper review with no live-trading switch
reporting.py               paper-account reports
notifications.py           alert-only Telegram sender
actions.py                 UI-safe paper action helpers
web.py                     local password-protected paper dashboard
service.py                 process lifecycle and single-instance lock
execution/live_angel.py    preserved, independently gated future live adapter
upstox_data.py             read-only Upstox historical/expired-option data client
upstox_ingest.py           discovers and pulls Upstox candles into the archive
upstox_backtest.py         no-lookahead replay of Upstox candles, no strategy_observations
upstox_analysis.py         explainable breakdowns and traceable suggestions, no model
```

## Upstox historical backtesting (read-only, in progress)

A second, strictly read-only data source is being added to speed up strategy
validation: Upstox's expired-instruments API can supply months of historical
NIFTY option candles for offline replay, rather than waiting solely on
forward-paper collection. Upstox is used only for historical data — it has no
order-placement capability and is not a second trading broker.

The feature is now complete end-to-end, gated behind `UPSTOX_BACKTEST_ENABLED`
(default `false`): a new **Historical backtest** dashboard tab lets you pull
Upstox data for a date range, run a backtest over it, and see the deep
analysis breakdowns and suggestions — all from the browser. Two things worth
knowing before relying on this feature: Upstox's expiry-discovery endpoint
only covers roughly the last 6 months, a hard platform limit independent of
subscription tier; and Upstox access tokens are short-lived with no
long-lived refresh grant, so `UPSTOX_ACCESS_TOKEN` needs periodic manual
renewal.

`upstox_backtest.py` never touches `strategy_observations` — it walks raw
Upstox candles forward one at a time (no look-ahead) and generates signals
in memory, then reuses the same conservative entry/exit/fee logic as the
existing offline backtest. Every query is restricted to `source='upstox'`
rows, so Angel- and Upstox-sourced data in the same archive can never be
cross-matched. `upstox_analysis.py` adds explainable, aggregate-only
breakdowns (time-of-day, day-of-week, expiry-day, volatility regime, and
per-strategy-variant comparison) and a `generate_suggestions()` function that
emits plain comparative statements — never a fitted model — and only when
both compared groups have at least 20 trades and a real (not noise-level)
win-rate gap. A suggestion is a hypothesis to manually retest through the
existing development/validation/test split, not a conclusion to trust
outright.

Every dashboard action fails with a clear on-page message — never a stack
trace — when the feature is disabled, credentials are missing, or Upstox
itself is unreachable (including network-level failures like a blocked or
refused connection, not just HTTP error responses). `run_strategy_validation()`
also accepts an injectable `runner` parameter so Upstox-sourced strategy
variants can go through the same development/validation/untouched-test
selection discipline as the existing Angel-sourced ones.

Ingestion is coverage-aware: before pulling, the tab shows which date
ranges are already archived, and `pull_range()` skips any date-chunk
already saved for a token instead of re-calling Upstox for it — a
"Re-fetch even if already cached" checkbox bypasses this when you
deliberately want to. The deep-analysis view also shows a "Highlights"
card ahead of the raw breakdown tables: a plain-language best/worst group
per dimension, generated from any sample size (unlike Suggestions) and
clearly labeled "Preliminary" whenever either side is below the same
20-trade threshold Suggestions requires — so there's always visible
feedback on what's going well or badly, not just once 20+ trades exist.
The Highlights card also links to `GET /upstox/analysis-summary.txt`, a
plain-text export of the full report (overall stats, every breakdown
bucket, variant comparison, highlights, and the small-sample caution) meant
to be copy-pasted into a chat when asking what to tune.

`BacktestParameters.stop_risk_fraction` accepts `None` to disable the
price-based stop/target/trailing-stop exit entirely, so a trade only closes
on a signal reversal, a max-hold cap, or the session force-exit time. This
exists because the default fixed-rupee stop distance
(`MAX_LOSS_PER_TRADE × stop_risk_fraction ÷ lot size`) can be only a few
rupees of option premium — tight enough that ordinary intraday noise
triggers it on nearly every trade, making win rate look far worse than the
underlying signal quality actually is. `STRATEGY_VARIANTS` includes a
`"No stop-loss cap"` entry so this is always visible in the deep-analysis
variant comparison.

`BacktestResult` also exposes `capital_deployed_total`,
`capital_deployed_average`, and `return_on_capital_pct` — read-only
properties derived purely from `trade_details` (sum/mean of entry premium,
and net P&L as a percentage of total capital deployed). Shown next to Net
P&L and Drawdown on both the offline-backtest and Upstox-backtest cards,
and in the "Copy analysis for Claude" export, so a raw rupee P&L figure can
be judged against how much capital produced it. This is turnover capital,
not simultaneous margin: positions never overlap (one at a time), so it's
the sum of money moved across the whole period, not a peak concurrent
exposure.

## Tests

```bash
python -m pytest -q
python -m compileall -q src tests
```

Pull requests run the same checks on Python 3.11 and 3.12 through GitHub
Actions. Repository owners can enable native auto-merge by following
[`docs/GITHUB_AUTOMATION.md`](docs/GITHUB_AUTOMATION.md). The recommended solo
setup requires CI but no artificial bot approval; team repositories should keep
an independent human approval requirement.

## Next milestones

1. Connect the Angel One market-data adapter to authenticated sessions and add
   WebSocket reconnection/health handling.
2. Connect the deterministic strategy to live market data and the paper broker.
3. Add end-of-day paper exits and SQLite backup/restore commands.
4. Extend chronological replay to option-contract fills through `PaperBroker`.
