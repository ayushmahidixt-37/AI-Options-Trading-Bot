"""Automates the 2 of 3 Dhan token-refresh steps that don't need a human login.

Step 2 of the consent flow (auth.dhan.co/consent-login) is your own Dhan
brokerage login -- username, password, OTP. Nothing in this repo has that, and
it never should: credentials.env holds APP_ID/APP_SECRET/CLIENT_ID (an app
identity), not your broker account password, and logging into a brokerage
account on your behalf is not something this tool does.

What this DOES do: generate the consent request (step 1) and, after you've
approved it in a browser, exchange it for a fresh access token (step 3) --
the two steps that only need the app identity already in credentials.env.

Usage:
    python data_ingest/dhan_consent.py --start
        -> prints the URL to open and approve in your own browser

    python data_ingest/dhan_consent.py --finish <consent_id>
        -> exchanges the approved consent for a token and writes it into
           credentials.env automatically
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from options_bot.credentials import load_credentials  # noqa: E402

CREDENTIALS_PATH = _REPO / "credentials.env"

try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = None  # falls back to the interpreter's default (may fail on this build)


def _call(url: str, app_id: str, app_secret: str) -> dict:
    request = Request(url, headers={"app_id": app_id, "app_secret": app_secret})
    try:
        with urlopen(request, timeout=20, context=_SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc


def _write_token(token: str) -> None:
    text = CREDENTIALS_PATH.read_text(encoding="utf-8")
    if re.search(r"^DHAN_ACCESS_TOKEN=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^DHAN_ACCESS_TOKEN=.*$", f"DHAN_ACCESS_TOKEN={token}",
                      text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\nDHAN_ACCESS_TOKEN={token}\n"
    CREDENTIALS_PATH.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", action="store_true",
                       help="Step 1: request a consent ID and print the approval URL.")
    group.add_argument("--finish", metavar="CONSENT_ID",
                       help="Step 3: exchange an approved consent ID for a token.")
    args = parser.parse_args(argv)

    creds = load_credentials(str(CREDENTIALS_PATH))
    app_id, app_secret = creds["DHAN_APP_ID"].strip(), creds["DHAN_APP_SECRET"].strip()
    client_id = creds["DHAN_CLIENT_ID"].strip()

    if args.start:
        result = _call(
            f"https://api.dhan.co/v2/app/generate-consent?client_id={client_id}",
            app_id, app_secret,
        )
        consent_id = result.get("consentId")
        if not consent_id:
            print(f"No consentId in response: {result}")
            return 1
        print(f"consentId: {consent_id}\n")
        print("Open this in YOUR OWN browser and log in to approve it (this step is")
        print("yours to do -- it's your Dhan account login, not something this script has):\n")
        print(f"  https://auth.dhan.co/consent-login?consentId={consent_id}\n")
        print("Once approved, run:")
        print(f"  python data_ingest/dhan_consent.py --finish {consent_id}")
        return 0

    result = _call(
        f"https://api.dhan.co/v2/app/consumeApp-consent?tokenId={args.finish}",
        app_id, app_secret,
    )
    token = result.get("accessToken")
    if not token:
        print(f"No accessToken in response (did you approve it in the browser first?): {result}")
        return 1
    _write_token(token)
    print("DHAN_ACCESS_TOKEN refreshed and written to credentials.env.")
    print("Verify before launching the full fetch:")
    print("  python -c \"from datetime import date, timedelta; "
          "from options_bot.credentials import load_credentials; "
          "from options_bot.dhan_data import DhanClient; "
          "c=DhanClient(load_credentials('credentials.env')['DHAN_ACCESS_TOKEN'].strip()); "
          "print(len(c.fetch_index_intraday(from_date=date.today()-timedelta(days=5), to_date=date.today())))\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
