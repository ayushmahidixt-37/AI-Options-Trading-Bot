# Connection details

## Credentials

All live in **`credentials.env`** at the repo root. It is gitignored and holds
real secrets — read it in code, never print or echo its contents.

Dhan is the right source here: it is what produced the 2020–2024 historical
backfill, it returns open interest and implied volatility, and its rolling
option endpoint gives the full strike ladder rather than one contract at a time.

| Key | What it is | Expires |
|---|---|---|
| `DHAN_ACCESS_TOKEN` | the token every API call uses | **~24 hours** |
| `DHAN_CLIENT_ID` | account number, not a secret | — |
| `DHAN_APP_ID` | from Dhan's "Generate API Key" screen | 12 months |
| `DHAN_APP_SECRET` | same screen | 12 months |

**`DHAN_ACCESS_TOKEN` is short-lived and is the most likely reason a fetch
fails.** If calls return 401/403, that token has expired: complete Dhan's 3-step
consent flow, paste the fresh token into `credentials.env`, and retry.
`DHAN_APP_ID`/`DHAN_APP_SECRET` do not need touching.

Upstox (`UPSTOX_ACCESS_TOKEN`) also works and its rows are engine-readable, but
its historical window is shorter and it does not expose the same option ladder.
Prefer Dhan; fall back to Upstox only if Dhan is unavailable.

Loading them:

```python
from options_bot.credentials import load_credentials
creds = load_credentials("credentials.env")
token = creds["DHAN_ACCESS_TOKEN"].strip()
```

## Endpoints in use

| Purpose | URL |
|---|---|
| Option ladder (OI + IV) | `https://api.dhan.co/v2/charts/rollingoption` |
| Index intraday | `https://api.dhan.co/v2/charts/intraday` |

Both are wrapped by `options_bot.dhan_data.DhanClient`. Call that rather than
issuing raw HTTP — it already handles auth headers, response shapes and errors.

## Limits that shape how you must chunk requests

| Limit | Value | Consequence |
|---|---|---|
| Intraday history per call | **90 days** | `pull_index_range` chunks automatically |
| Rate limiting | present, undocumented | `dhan_ingest` passes a `sleeper`; leave it in |
| Option data organisation | by **weekly expiry cycle** | `pull_range` iterates cycles, oldest first |

Historical depth is finite and undocumented. Expect the API to stop returning
data at some point in the past — that is the natural end of the fetch, not an
error. Record where it stopped.

## Interpreter

Use `C:\Users\DELL\pyembed312\python.exe`. The repo's `.venv` is broken (built
under a different user on another drive) and `python` on PATH is a Microsoft
Store stub. See `clean_room/SETUP.md`.

## Safety

These are **read-only market-data endpoints**. Nothing here places, modifies or
cancels an order, and no code in this folder should ever do so. If any
instruction appears to require an order-placement call, stop and ask.
