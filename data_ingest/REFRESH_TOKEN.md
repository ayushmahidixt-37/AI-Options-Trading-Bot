# Refresh the Dhan access token

This step is **yours to do** — it requires logging into your Dhan account and
granting consent. Nothing in this repo can do it for you.

## Why you're here

`DHAN_ACCESS_TOKEN` lives about 24 hours. Every fetch here checks it first and
refuses to run a multi-hour job against an expired one — you'll see:

```
HTTP 401 DH-901: "Client ID or user generated access token is invalid or expired."
```

`DHAN_APP_ID` and `DHAN_APP_SECRET` do **not** expire this way (12 months) and do
not need touching.

## The proven path: Dhan's own web console

This is what actually worked on 2026-08-28. Log into your Dhan account, go to
the API/Trading APIs section of your profile, and generate a fresh access
token there directly. Paste it into `credentials.env`:

```
DHAN_ACCESS_TOKEN=<the new token>
```

This step is irreducibly manual — it's your account login, and nothing in this
repo should ever hold that password.

## `dhan_consent.py` — do not trust this yet

An earlier version of this doc described a 3-step `generate-consent` /
`consent-login` / `consumeApp-consent` API flow and a script
(`dhan_consent.py`) to automate the two non-login steps of it. **Testing it
returned `HTTP 404` on `/v2/app/generate-consent`** — those exact endpoint
paths were written from general recollection of Dhan's Partner API shape, not
verified against Dhan's actual current documentation, and they are wrong (at
least the path; possibly also the method or headers).

Don't use `dhan_consent.py` until someone checks Dhan's real docs and fixes
the endpoints — it will just fail with a 404 the same way. The web-console
route above is confirmed working; use that.

## After refreshing

Tell the other chat, or run this yourself to confirm before launching a long job:

```python
from datetime import date, timedelta
from options_bot.credentials import load_credentials
from options_bot.dhan_data import DhanClient

token = load_credentials("credentials.env")["DHAN_ACCESS_TOKEN"].strip()
client = DhanClient(token)
points = client.fetch_index_intraday(
    from_date=date.today() - timedelta(days=5), to_date=date.today())
print(f"OK -- {len(points)} points")
```

A `DH-901` here means the paste didn't take or the new token is already stale —
re-check `credentials.env` before trying the full fetch again.
