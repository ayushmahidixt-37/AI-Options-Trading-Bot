# AI Options Trading Bot

Server-ready foundations for an options bot that is deliberately limited to
**paper trading**. Angel One market-data support is the next milestone; this
release contains no reachable broker order-submission implementation.

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
be readable only by root and the service group.

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

The script installs only the runtime packages needed by the dashboard (including the timezone database required by Android), creates a tablet-local config under `.termux-data`, checks that the application imports, and starts the password-protected UI on `http://127.0.0.1:8000`. It intentionally does not install developer tools such as Ruff because compiling them can exceed a tablet's available memory. Open that URL in Chrome on the same tablet and sign in as `admin`.

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

Confirmed paper positions are monitored by the same 15-second background worker even when Chrome is closed. The exit-only monitor fetches a fresh option quote and automatically closes the simulated position if its stop is reached, the NIFTY directional signal reverses, or the configured force-exit time arrives. Quote failures leave the position open and are shown in the dashboard rather than guessing a fill. Entries remain manual and separately confirmed; this monitor cannot create a position or place a broker order.

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
reporting.py               paper-account reports
notifications.py           alert-only Telegram sender
actions.py                 UI-safe paper action helpers
web.py                     local password-protected paper dashboard
service.py                 process lifecycle and single-instance lock
execution/live_angel.py    preserved, independently gated future live adapter
```

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
