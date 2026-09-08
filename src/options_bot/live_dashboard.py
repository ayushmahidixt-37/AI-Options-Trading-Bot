"""Read-only localhost viewer for live_paper_runner.py's trades.

Separate process, separate port (8010, not the existing web.py's 8000) --
this only ever reads live_paper_trading/ledger.jsonl and status.json, never
writes anything, so it's safe to leave running or restart independently of
the runner itself. Answers "where do I check what trades are getting
placed" with an actual localhost page, since nothing did that before this.

Run: python -m options_bot.live_dashboard
Then open: http://localhost:8010
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "live_paper_trading" / "ledger.jsonl"
STATUS_PATH = REPO_ROOT / "live_paper_trading" / "status.json"

app = FastAPI()


def _load_trades() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    out = []
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    out.sort(key=lambda t: t["entry_at"], reverse=True)
    return out


def _load_status() -> dict | None:
    if not STATUS_PATH.exists():
        return None
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def _row(t: dict) -> str:
    net = t.get("net_rupees", 0.0)
    color = "#0ca30c" if net > 0 else ("#d03b3b" if net < 0 else "#666")
    legs = ", ".join(f"{l.get('type','')}{l.get('strike','')}" for l in t.get("legs", []))
    return (f"<tr><td>{t['strategy']}</td><td>{t['day']}</td>"
            f"<td>{t['entry_at'][11:19]}</td><td>{(t.get('exit_at') or '')[11:19]}</td>"
            f"<td>{t['direction']}</td><td>{legs}</td><td>{t['qty_lots']}</td>"
            f"<td>{t['status']}</td><td>{t.get('reason','')}</td>"
            f"<td style='color:{color};text-align:right'>Rs{net:,.2f}</td></tr>")


@app.get("/", response_class=HTMLResponse)
def index():
    trades = _load_trades()
    status = _load_status()
    closed = [t for t in trades if t["status"] == "closed"]
    open_ = [t for t in trades if t["status"] == "open"]
    total_pnl = sum(t["net_rupees"] for t in closed)

    if status:
        last_run = datetime.fromisoformat(status["last_run_at"])
        age_s = (datetime.now(last_run.tzinfo) - last_run).total_seconds()
        alive = age_s < 180
        status_html = (
            f"<div class='card'><b>Runner status:</b> "
            f"<span style='color:{'#0ca30c' if alive else '#d03b3b'}'>"
            f"{'RUNNING' if alive else 'NOT RESPONDING'}</span> "
            f"(last cycle {int(age_s)}s ago) &middot; "
            f"NIFTY spot: {status.get('nifty_price')} &middot; "
            f"Active: {', '.join(status.get('active_strategies', []))} &middot; "
            f"Open positions: {status.get('open_positions')} &middot; "
            f"Today's realized P&amp;L: Rs{status.get('today_realized_pnl', 0):,.2f}</div>"
        )
    else:
        status_html = "<div class='card' style='color:#d03b3b'><b>No status file yet -- runner hasn't started, or hasn't completed a cycle.</b></div>"

    rows = "".join(_row(t) for t in trades)
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="20">
<title>Live Paper Trading</title>
<style>
body {{ font-family: system-ui, sans-serif; background:#fafafa; color:#111; padding:20px; }}
.card {{ background:#fff; border:1px solid #ddd; border-radius:8px; padding:12px 16px; margin-bottom:14px; }}
table {{ width:100%; border-collapse:collapse; background:#fff; }}
th, td {{ padding:6px 10px; border-bottom:1px solid #eee; font-size:13px; text-align:left; }}
th {{ background:#f2f2f2; }}
h1 {{ font-size:20px; }}
.stat {{ display:inline-block; margin-right:24px; }}
</style></head><body>
<h1>Live Paper Trading</h1>
{status_html}
<div class='card'>
  <span class='stat'><b>{len(closed)}</b> closed trades</span>
  <span class='stat'><b>{len(open_)}</b> open now</span>
  <span class='stat'>Total realized: <b style='color:{"#0ca30c" if total_pnl>=0 else "#d03b3b"}'>Rs{total_pnl:,.2f}</b></span>
</div>
<table>
<tr><th>Strategy</th><th>Day</th><th>Entry</th><th>Exit</th><th>Dir</th><th>Legs</th>
<th>Qty</th><th>Status</th><th>Reason</th><th>Net Rs</th></tr>
{rows}
</table>
<p style="color:#888;font-size:12px">Auto-refreshes every 20s. Reads live_paper_trading/ledger.jsonl and status.json only -- read-only, safe to leave open.</p>
</body></html>"""
    return html


def main():
    uvicorn.run(app, host="127.0.0.1", port=8010)


if __name__ == "__main__":
    main()
