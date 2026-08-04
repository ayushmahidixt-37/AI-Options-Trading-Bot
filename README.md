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
```

## Functional modules

```text
config.py                  validated server and risk settings
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
