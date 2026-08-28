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

## The 3 steps

1. **Generate a consent ID.**
   `GET https://api.dhan.co/v2/app/generate-consent?client_id=<DHAN_CLIENT_ID>`
   with header `app_id: <DHAN_APP_ID>`, `app_secret: <DHAN_APP_SECRET>`. Returns
   a `consentId`.

2. **Log in and approve.**
   Open `https://auth.dhan.co/consent-login?consentId=<consentId>` in a browser,
   sign into your Dhan account, approve the request. This is the step that must
   be a human — it's your account credentials.

3. **Consume the consent for a token.**
   `GET https://api.dhan.co/v2/app/consumeApp-consent?tokenId=<consentId>` with
   the same `app_id`/`app_secret` headers. Returns `accessToken` — this is the
   new `DHAN_ACCESS_TOKEN`.

Paste it into `credentials.env`:

```
DHAN_ACCESS_TOKEN=<the new accessToken>
```

## Do this in a browser or curl, not by asking Claude to automate it

Step 2 needs your login inside a real browser session. Nothing in
`options_bot` wraps steps 1 or 3 either — there's no existing helper for this
flow in the codebase, so doing it manually via `curl`/Postman/browser is the
straightforward path today. (A small script wrapping steps 1 and 3 would be
easy to add if this becomes routine — say so if you want it.)

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
