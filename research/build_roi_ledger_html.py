# -*- coding: utf-8 -*-
"""Regenerate research/roi_ledger.html from research/roi_all_runs.json
(the durable ROI ledger -- see roi_ledger.py). Run this any time after
new rows have been recorded to refresh the local table:

    python research/build_roi_ledger_html.py

Then, to publish/update the shareable version, ask Claude to re-publish
research/roi_ledger.html as an Artifact (pass the existing artifact's URL
so it updates in place instead of creating a new link).
"""
import json
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
DATA_PATH = HERE / "roi_all_runs.json"
OUTPUT_PATH = HERE / "roi_ledger.html"

with open(DATA_PATH, "r", encoding="utf-8") as f:
    rows = json.load(f)

# Known groups get a curated title/description; anything new recorded
# later (a group name not in this list) still renders correctly, just
# with a generic subtitle -- add a proper entry here when convenient.
GROUP_META = [
    ("M. Full-range continuous runs (final recommended configs)", "Adopted configs, full history",
     "Both surviving candidates' actual recommended configuration, run once as a single continuous pass over the entire archive (Oct 2024 - May 2026) rather than chunked by quarter -- this is the real, final picture."),
    ("A. Screening pass (Jan-Mar 2026)", "First look, single range",
     "The four candidate signal shapes compared on one range with default parameters, before any dev/val discipline -- exploratory only."),
    ("B. Baseline / trend-confirmed dev-val", "Proper dev/val split",
     "Baseline momentum and default-parameter trend-confirmed momentum, given the project's standard Development (Jan-Mar 2026) / Validation (Apr-May 2026) split."),
    ("C. Trend-confirmed param sweep", "Entry parameter search",
     "Varying trend-confirmed momentum's macro EMA period and fast/slow EMA pair to find the winning region."),
    ("D. Trend-confirmed combo (fast5/slow13/rsi21)", "Combining two winning levers",
     "Combining the two individually-best levers found in the sweep above."),
    ("E1. Trend-confirmed full sweep - entry refine", "Fine entry-logic search",
     "A finer search around the winning region -- fast/slow EMA pairs, macro period, RSI period -- that found fast=5/slow=10/macro=60/rsi=21, later named “candidate B”."),
    ("E2. Trend-confirmed full sweep - exit sweep", "Exit-shell search",
     "Exit-parameter sweep (stop distance, target, trailing stop) run against the winning entry combo from E1."),
    ("F. Candidate B premium-floor sweep", "minimum_option_premium sweep",
     "Testing a floor on the selected option's entry premium -- became part of candidate B's final recommendation at ≥₹20."),
    ("G. Candidate B 7-quarter check (unfiltered)", "Robustness check, no filters",
     "Candidate B's fixed entry+exit parameters (no premium/OI filters) run across every quarter since Oct 2024, chunked quarter by quarter -- not a single continuous run (see note below on why this differs slightly from the continuous total in group M)."),
    ("H. Known-event calendar filter test", "Macro-event filter test",
     "Testing whether excluding trades on/after known RBI MPC / FOMC / Union Budget dates helps -- it does not; excluded here for reference, not adopted."),
    ("I. Mean-reversion exit-shell sweep (REJECTED)", "Rejected strategy",
     "Nine exit-shell variants tried to rescue mean-reversion after its original rejection. Every variant still lost money -- shown in full for completeness, not because any of it survived."),
    ("J. Candidate B confidence/OI sweep", "Confidence & open-interest filters",
     "Testing two previously-unused data fields as entry filters. Open interest ≥100,000 became a free addition to candidate B; confidence filtering was tested and not adopted (real quality/quantity trade-off, no free tier)."),
    ("K. Opening-range breakout bars/exit sweep", "Second candidate's search",
     "Opening-range breakout's own parameter search -- opening-range width and exit shell -- that found bars=6 + candidate B's exit shell."),
    ("L. Opening-range breakout 7-quarter check", "Robustness check, no filters",
     "The winning opening-range breakout config run across every quarter since Oct 2024, chunked quarter by quarter (see note below)."),
    ("N. ML-filtered baseline models (v1 engine)", "ML signal-quality filters",
     "Every saved ML model scored at its own chosen threshold (no retraining) -- v4 remains the best plain-baseline filter; v3/v6/v7 are marginal variants; trend-confirmed+ML stacks the filter on candidate B's own signal."),
]
GROUP_ORDER = [g[0] for g in GROUP_META]
GROUP_SUBTITLE = {g[0]: g[1] for g in GROUP_META}
GROUP_DESC = {g[0]: g[2] for g in GROUP_META}

by_group = {}
group_first_seen = []
for r in rows:
    if r["group"] not in by_group:
        group_first_seen.append(r["group"])
    by_group.setdefault(r["group"], []).append(r)

# Known groups in curated order first, then any new/unseen group (from a
# future recorded run) appended in the order it first appears in the data.
final_order = [g for g in GROUP_ORDER if g in by_group]
final_order += [g for g in group_first_seen if g not in final_order]

ordered_rows = []
for g in final_order:
    ordered_rows.extend(by_group[g])

def days_between(start_iso, end_iso):
    s = date.fromisoformat(start_iso)
    e = date.fromisoformat(end_iso)
    return (e - s).days + 1

for r in ordered_rows:
    r["period_days"] = days_between(r["period_start"], r["period_end"])
    trading_days = r.get("trading_days")
    if trading_days:
        r["daily_roi_pct"] = round(r["roi_pct"] / trading_days, 4) if r["roi_pct"] is not None else None
    else:
        r["daily_roi_pct"] = None

data_json = json.dumps(ordered_rows, ensure_ascii=False)
group_meta_json = json.dumps(
    [{"id": g, "subtitle": GROUP_SUBTITLE.get(g, "Recorded run"), "desc": GROUP_DESC.get(g, "")} for g in final_order],
    ensure_ascii=False,
)

total_runs = len(rows)
profitable = sum(1 for r in rows if r["net_pnl"] > 0)
unprofitable = total_runs - profitable
roi_vals = [r["roi_pct"] for r in rows if r["roi_pct"] is not None]
win_vals = [r["win_rate"] for r in rows if r["win_rate"] is not None]
roi_min, roi_max = min(roi_vals), max(roi_vals)
win_min, win_max = min(win_vals) * 100, max(win_vals) * 100
daily_vals = [r["daily_roi_pct"] for r in rows if r.get("daily_roi_pct") is not None]
daily_min, daily_max = (min(daily_vals), max(daily_vals)) if daily_vals else (0.0, 0.0)

html = """<title>Backtest Run Ledger</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Spectral:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #f6f4ef;
  --surface: #ffffff;
  --surface-2: #eeece4;
  --border: #ddd8cc;
  --text: #22262e;
  --text-dim: #5c6270;
  --accent: #2f7c8c;
  --accent-soft: #e4eff0;
  --profit: #2f8a5c;
  --profit-soft: #e6f2ea;
  --loss: #c14a3a;
  --loss-soft: #fbeae7;
  --rejected: #b5791d;
  --rejected-soft: #f8eedc;
  --shadow: 0 1px 2px rgba(30,25,15,0.06), 0 4px 14px rgba(30,25,15,0.05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f1420;
    --surface: #161c2c;
    --surface-2: #1b2338;
    --border: #2a3350;
    --text: #e8ecf5;
    --text-dim: #8892ab;
    --accent: #5fb8c9;
    --accent-soft: #1b2f34;
    --profit: #4fae7e;
    --profit-soft: #16281f;
    --loss: #e0715f;
    --loss-soft: #2c1a18;
    --rejected: #d9a441;
    --rejected-soft: #2a2213;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
  }
}
:root[data-theme="dark"] {
  --bg: #0f1420;
  --surface: #161c2c;
  --surface-2: #1b2338;
  --border: #2a3350;
  --text: #e8ecf5;
  --text-dim: #8892ab;
  --accent: #5fb8c9;
  --accent-soft: #1b2f34;
  --profit: #4fae7e;
  --profit-soft: #16281f;
  --loss: #e0715f;
  --loss-soft: #2c1a18;
  --rejected: #d9a441;
  --rejected-soft: #2a2213;
  --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  line-height: 1.5;
}
.wrap { max-width: 1240px; margin: 0 auto; padding: 2.5rem 1.5rem 5rem; }

.eyebrow {
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.72rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--accent);
  margin: 0 0 0.6rem;
}
h1 {
  font-family: "Spectral", Georgia, serif;
  font-weight: 600;
  font-size: clamp(1.8rem, 3.2vw, 2.5rem);
  margin: 0 0 0.5rem;
  text-wrap: balance;
}
.lede {
  color: var(--text-dim);
  max-width: 66ch;
  font-size: 1rem;
  margin: 0 0 1.5rem;
}

.disclaimer {
  border: 1px solid var(--border);
  background: var(--surface-2);
  border-radius: 8px;
  padding: 0.85rem 1.1rem;
  font-size: 0.85rem;
  color: var(--text-dim);
  margin-bottom: 2rem;
}
.disclaimer strong { color: var(--text); }

.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 0.9rem;
  margin-bottom: 2rem;
}
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem 1.1rem;
  box-shadow: var(--shadow);
}
.kpi .label {
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-dim);
  margin-bottom: 0.35rem;
}
.kpi .value {
  font-family: "IBM Plex Mono", monospace;
  font-size: 1.35rem;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.kpi .value.split span:first-child { color: var(--profit); }
.kpi .value.split span:last-child { color: var(--loss); }
.kpi .sub { font-size: 0.75rem; color: var(--text-dim); margin-top: 0.2rem; }

.callout {
  border-left: 3px solid var(--accent);
  background: var(--accent-soft);
  border-radius: 0 8px 8px 0;
  padding: 0.9rem 1.1rem;
  font-size: 0.88rem;
  margin-bottom: 2rem;
}
.callout strong { color: var(--accent); }

.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: center;
  margin-bottom: 1rem;
  position: sticky;
  top: 0;
  background: var(--bg);
  padding: 0.75rem 0;
  z-index: 5;
}
.controls input[type="search"] {
  flex: 1 1 220px;
  padding: 0.55rem 0.8rem;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-family: "IBM Plex Sans", sans-serif;
  font-size: 0.88rem;
}
.controls select {
  padding: 0.55rem 0.7rem;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-family: "IBM Plex Sans", sans-serif;
  font-size: 0.85rem;
}
.controls label {
  font-size: 0.8rem;
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.count-note {
  font-size: 0.78rem;
  color: var(--text-dim);
  margin-bottom: 0.9rem;
  font-family: "IBM Plex Mono", monospace;
}

.table-scroll {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow);
  background: var(--surface);
}
table { border-collapse: collapse; width: 100%; min-width: 1020px; }
thead th {
  position: sticky;
  top: 0;
  background: var(--surface-2);
  text-align: left;
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.7rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-dim);
  padding: 0.7rem 0.9rem;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
thead th:hover { color: var(--accent); }
thead th.sorted { color: var(--accent); }
th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody td {
  padding: 0.55rem 0.9rem;
  border-bottom: 1px solid var(--border);
  font-size: 0.86rem;
  vertical-align: top;
}
tbody tr:last-child td { border-bottom: none; }
td.run-label { font-family: "IBM Plex Mono", monospace; font-size: 0.8rem; max-width: 380px; }
td.period { font-family: "IBM Plex Mono", monospace; font-size: 0.76rem; color: var(--text-dim); white-space: nowrap; }
td.num { font-family: "IBM Plex Mono", monospace; }
tbody tr:hover td { background: var(--surface-2); }

.group-row td {
  background: var(--surface-2);
  padding: 0.8rem 0.9rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.group-row .g-title {
  font-family: "Spectral", serif;
  font-weight: 600;
  font-size: 1rem;
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.group-row .g-subtitle {
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.68rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--accent);
  font-weight: 500;
}
.group-row .g-count {
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.72rem;
  color: var(--text-dim);
  font-weight: 400;
}
.group-row .g-desc {
  font-size: 0.82rem;
  color: var(--text-dim);
  margin-top: 0.3rem;
  max-width: 90ch;
}

.pill {
  display: inline-block;
  padding: 0.12rem 0.5rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-family: "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums;
  background: var(--surface-2);
  color: var(--text-dim);
}
.roi { font-weight: 600; }
.roi.pos { color: var(--profit); }
.roi.neg { color: var(--loss); }
.pnl.pos { color: var(--profit); }
.pnl.neg { color: var(--loss); }
.tag-rejected {
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.65rem;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  background: var(--rejected-soft);
  color: var(--rejected);
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  margin-left: 0.4rem;
}

footer {
  margin-top: 2.5rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--border);
  font-size: 0.82rem;
  color: var(--text-dim);
}
footer h2 {
  font-family: "Spectral", serif;
  font-size: 1rem;
  color: var(--text);
  margin: 0 0 0.6rem;
}
footer ul { padding-left: 1.1rem; margin: 0 0 1.2rem; }
footer li { margin-bottom: 0.45rem; }
footer code {
  font-family: "IBM Plex Mono", monospace;
  background: var(--surface-2);
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  font-size: 0.82em;
}
</style>

<div class="wrap">
  <p class="eyebrow">NIFTY Options Research -- Paper Trading Only</p>
  <h1>Backtest Run Ledger</h1>
  <p class="lede">Every backtest configuration recorded to <code>research/roi_all_runs.json</code>, with the exact
  period it covers, invested capital, and true return-on-capital percentage -- kept separate from win rate, which is
  a different number. Regenerate this page any time with <code>python research/build_roi_ledger_html.py</code> after
  new runs are recorded.</p>

  <div class="disclaimer">
    <strong>Historical backtest research on paper-simulated data, not live trading and not investment advice.</strong>
    Past performance does not indicate future results. Figures are per-run, over that run's own date range --
    "invested" amounts across different rows overlap in time and strategy variant, so they cannot be summed into one
    portfolio total. See the notes at the bottom for what this table does and doesn't cover.
  </div>

  <div class="kpis">
    <div class="kpi">
      <div class="label">Total runs</div>
      <div class="value">__TOTAL_RUNS__</div>
      <div class="sub">recorded to the ledger so far</div>
    </div>
    <div class="kpi">
      <div class="label">Profitable / not</div>
      <div class="value split"><span>__PROFITABLE__</span> / <span>__UNPROFITABLE__</span></div>
      <div class="sub">net P&amp;L &gt; 0 vs. &le; 0</div>
    </div>
    <div class="kpi">
      <div class="label">Profit % (ROI) range</div>
      <div class="value">__ROI_MIN__% - __ROI_MAX__%</div>
      <div class="sub">return on capital deployed, per run</div>
    </div>
    <div class="kpi">
      <div class="label">Win % range (different metric)</div>
      <div class="value">__WIN_MIN__% - __WIN_MAX__%</div>
      <div class="sub">fraction of winning trades, per run</div>
    </div>
    <div class="kpi">
      <div class="label">Daily profit % range</div>
      <div class="value">__DAILY_MIN__% - __DAILY_MAX__%</div>
      <div class="sub">period ROI ÷ trading days -- see note below</div>
    </div>
  </div>

  <div class="callout">
    <strong>Profit % &ne; win %.</strong> Win rate is the share of trades that closed positive. Profit % (ROI) is
    net profit or loss divided by the total rupees actually turned over on entries that period. A strategy can have a
    low win rate and still be strongly profitable (small frequent losses, occasional large wins), or a high win rate
    and a mediocre return (many small wins, one large loss) -- the two numbers answer different questions, so this
    table always shows both, never one standing in for the other.
  </div>

  <div class="callout">
    <strong>Daily Profit % is a simple average, not a compounding rate.</strong> It is Profit % (ROI) divided by the
    number of actual NSE trading days the run's date range spans (not calendar days, and not just days a trade
    happened to fire) -- e.g. a run showing 4.55% ROI over 39 trading days shows roughly 0.117% per day. This is
    <em>not</em> the same as saying "and it compounds daily" (like a CAGR figure would imply) -- these backtests use
    a fixed lot size per trade, not full reinvestment of prior profits into larger positions, so a flat average is
    the honest way to read "roughly how much per trading day," while a compounded figure would overstate what
    actually happens. Use it to compare pace across runs of very different lengths (a 3-month sweep vs. a 19-month
    full-history run), not as a growth-rate prediction.
  </div>

  <div class="controls">
    <input type="search" id="search" placeholder="Search run label or group..." />
    <select id="groupFilter"><option value="">All groups</option></select>
    <label><input type="checkbox" id="onlyProfitable" /> Profitable only</label>
  </div>
  <div class="count-note" id="countNote"></div>

  <div class="table-scroll">
    <table id="ledger">
      <thead>
        <tr>
          <th data-key="label">Run</th>
          <th data-key="period_start">Period</th>
          <th data-key="period_days" class="num">Days</th>
          <th data-key="trades" class="num">Trades</th>
          <th data-key="win_rate" class="num">Win %</th>
          <th data-key="invested" class="num">Invested (Rs)</th>
          <th data-key="net_pnl" class="num">Profit / Loss (Rs)</th>
          <th data-key="roi_pct" class="num">Profit % (ROI)</th>
          <th data-key="daily_roi_pct" class="num">Daily Profit %</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>

  <footer>
    <h2>Notes on this table</h2>
    <ul>
      <li><strong>"Period"</strong> is the run's own backtest date range (its Development window, Validation window,
      a single quarter, or a full continuous range) -- not how long any individual trade was held. Individual trades
      close same-day or roll to force-exit; "Days" here is the calendar span of the backtest itself.
      <strong>"Daily Profit %"</strong> instead divides by actual NSE trading days in that range (weekends/holidays
      excluded, from the underlying's own candle calendar) -- see the callout above for why it's a simple average,
      not a compounding rate.</li>
      <li><strong>Chunked vs. continuous ranges:</strong> some groups run one strategy's fixed parameters across
      several separate quarter-long calls; the "full-range continuous runs" group runs the same final configs as
      <em>one continuous pass</em> instead. The two don't sum to identical totals -- a fresh quarter-chunked call
      restarts each indicator's warm-up window at the quarter boundary, while a continuous run carries that warm-up
      through. The continuous version is the more realistic of the two.</li>
      <li><strong>"Invested" is capital turned over, not capital at risk simultaneously:</strong> each strategy holds
      one position at a time, so this is the sum of entry premium &times; lot size across all trades in that run --
      total rupees put to work over the period, not peak simultaneous exposure.</li>
      <li>Rows tagged <span class="tag-rejected">rejected</span> are strategies this project's own research explicitly
      rejected -- shown here for completeness, not as a recommendation.</li>
      <li><strong>Keeping this table current:</strong> see <code>research/roi_ledger.py</code> -- call
      <code>record_run(group, label, start, end, result)</code> right after any backtest worth keeping, then re-run
      this script. New group names not yet in this script's curated list still render correctly with a generic label.</li>
    </ul>
  </footer>
</div>

<script>
const ROWS = __DATA_JSON__;
const GROUP_META = __GROUP_META_JSON__;
const groupMetaById = Object.fromEntries(GROUP_META.map(g => [g.id, g]));

const groupFilterEl = document.getElementById('groupFilter');
GROUP_META.forEach(g => {
  const opt = document.createElement('option');
  opt.value = g.id;
  opt.textContent = g.id;
  groupFilterEl.appendChild(opt);
});

const fmtMoney = n => n == null ? '-' : n.toLocaleString('en-IN', {maximumFractionDigits: 0});
const fmtPct = n => n == null ? '-' : (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
const fmtWin = n => n == null ? '-' : (n * 100).toFixed(1) + '%';
const fmtDailyPct = n => n == null ? '-' : (n >= 0 ? '+' : '') + n.toFixed(3) + '%';
const fmtPeriod = r => `${r.period_start} → ${r.period_end}`;

let sortKey = null;
let sortDir = 1;

function currentFilters() {
  return {
    q: document.getElementById('search').value.trim().toLowerCase(),
    group: groupFilterEl.value,
    onlyProfitable: document.getElementById('onlyProfitable').checked,
  };
}

function render() {
  const { q, group, onlyProfitable } = currentFilters();
  let filtered = ROWS.filter(r => {
    if (group && r.group !== group) return false;
    if (onlyProfitable && !(r.net_pnl > 0)) return false;
    if (q && !(r.label.toLowerCase().includes(q) || r.group.toLowerCase().includes(q))) return false;
    return true;
  });

  if (sortKey) {
    filtered = filtered.slice().sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return av.localeCompare(bv) * sortDir;
      return (av - bv) * sortDir;
    });
  }

  document.getElementById('countNote').textContent =
    `Showing ${filtered.length} of ${ROWS.length} runs`;

  const tbody = document.getElementById('tbody');
  tbody.innerHTML = '';

  if (sortKey) {
    for (const r of filtered) tbody.appendChild(renderRow(r, true));
    return;
  }

  let lastGroup = null;
  for (const r of filtered) {
    if (r.group !== lastGroup) {
      lastGroup = r.group;
      const meta = groupMetaById[r.group] || { subtitle: 'Recorded run', desc: '' };
      const count = filtered.filter(x => x.group === r.group).length;
      const tr = document.createElement('tr');
      tr.className = 'group-row';
      tr.innerHTML = `<td colspan="9">
        <div class="g-title">${r.group} <span class="g-subtitle">${meta.subtitle}</span>
          <span class="g-count">${count} run${count === 1 ? '' : 's'}</span></div>
        <div class="g-desc">${meta.desc}</div>
      </td>`;
      tbody.appendChild(tr);
    }
    tbody.appendChild(renderRow(r, false));
  }
}

function renderRow(r, showGroup) {
  const tr = document.createElement('tr');
  const roiClass = r.roi_pct == null ? '' : (r.roi_pct >= 0 ? 'pos' : 'neg');
  const pnlClass = r.net_pnl >= 0 ? 'pos' : 'neg';
  const rejected = r.group.includes('REJECTED');
  tr.innerHTML = `
    <td class="run-label">${showGroup ? `<span class="pill">${r.group.split('.')[0]}</span> ` : ''}${r.label}${rejected ? '<span class="tag-rejected">rejected</span>' : ''}</td>
    <td class="period">${fmtPeriod(r)}</td>
    <td class="num">${r.period_days}</td>
    <td class="num">${r.trades}</td>
    <td class="num">${fmtWin(r.win_rate)}</td>
    <td class="num">${fmtMoney(r.invested)}</td>
    <td class="num pnl ${pnlClass}">${r.net_pnl >= 0 ? '+' : ''}${fmtMoney(r.net_pnl)}</td>
    <td class="num roi ${roiClass}">${fmtPct(r.roi_pct)}</td>
    <td class="num pnl ${r.daily_roi_pct == null ? '' : (r.daily_roi_pct >= 0 ? 'pos' : 'neg')}">${fmtDailyPct(r.daily_roi_pct)}</td>
  `;
  return tr;
}

document.querySelectorAll('thead th').forEach(th => {
  th.addEventListener('click', () => {
    const key = th.dataset.key;
    if (sortKey === key) { sortDir *= -1; } else { sortKey = key; sortDir = key === 'label' || key === 'period_start' ? 1 : -1; }
    document.querySelectorAll('thead th').forEach(x => x.classList.remove('sorted'));
    th.classList.add('sorted');
    render();
  });
});

document.getElementById('search').addEventListener('input', render);
groupFilterEl.addEventListener('change', render);
document.getElementById('onlyProfitable').addEventListener('change', render);

render();
</script>
"""

html = html.replace("__TOTAL_RUNS__", str(total_runs))
html = html.replace("__PROFITABLE__", str(profitable))
html = html.replace("__UNPROFITABLE__", str(unprofitable))
html = html.replace("__ROI_MIN__", f"{roi_min:.1f}")
html = html.replace("__ROI_MAX__", f"{roi_max:.1f}")
html = html.replace("__WIN_MIN__", f"{win_min:.0f}")
html = html.replace("__WIN_MAX__", f"{win_max:.0f}")
html = html.replace("__DAILY_MIN__", f"{daily_min:.3f}")
html = html.replace("__DAILY_MAX__", f"{daily_max:.3f}")
html = html.replace("__DATA_JSON__", data_json)
html = html.replace("__GROUP_META_JSON__", group_meta_json)

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    f.write(html)

print(f"written {OUTPUT_PATH} ({len(html)} bytes, {total_runs} runs)")
