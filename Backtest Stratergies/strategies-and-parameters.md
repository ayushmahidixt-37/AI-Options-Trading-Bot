# Strategies & Parameters — Backtest Input File

Single file, strategies + exact parameters only. Copy this file alone into any folder to hand off for backtesting — no other file is required to implement and test what's below.

**Capital:** ₹1,00,000 | **Risk per trade:** 1–2%
**Position sizing (applies to every strategy):**
```
risk_amount = capital × risk_%
quantity = risk_amount ÷ |entry_price − stop_price|   → round down to nearest tradable lot size
```

---

# Top 10 — Strategies With Published Backtest Evidence

Unlike the strategies below (which are documented setups without attached proof), every entry here has an actual backtest result published somewhere — win rate, return, drawdown, sample size — not just a theoretical rationale. Mix of long-running US equity research (decades of data, very well studied) and India-specific F&O backtests (shorter history, but directly relevant to Nifty/BankNifty trading). Numbers are as reported by the cited source — none of these have been independently re-verified by re-running the backtest myself, and none include full realistic Indian transaction costs unless stated. Treat the numbers as evidence of "worth prototyping," not as a guarantee.

**Important — US numbers ≠ Indian numbers.** Strategies #1–7 were backtested on US markets (S&P 500, QQQ, SPY). Their win rate/CAGR/Sharpe figures do **not** transfer to Nifty/BankNifty as-is — Indian F&O has structurally higher intraday volatility, different cost structure (STT, stamp duty), and a different participant mix (retail option buyers vs. algo option sellers). Only #8–10 have backtest evidence that is *already* Indian-market data (AlgoTest Bank Nifty, intradaylab.com Nifty 8-year) — those numbers are native, not translated. Of #1–7, momentum (#3) is the one with the strongest cross-market evidence (tested across 31 countries); the calendar-effect strategies (#5 Day-of-Week, #6 Turnaround Tuesday) are the least likely to hold up outside the exact US sample they were found on. Any of #1–7 would need a fresh backtest on actual Nifty/BankNifty data before their numbers can be trusted here.

## 1. Golden Cross — 50/200 SMA Crossover
- **Market:** S&P 500 index
- **Parameters:** Buy when 50-day SMA crosses above 200-day SMA; exit/sell when it crosses back below.
- **Backtest evidence:** 66-year backtest (1960–2026) on S&P 500 daily data — 33 total trades, **79% win rate**, average gain **+15.8% per trade**. Sharpe ratio 0.70 vs. 0.48 for buy-and-hold; max drawdown −20.3% vs. −51.7% for buy-and-hold. Very low trade frequency (~1 trade every 2 years).
- **Source:** [Golden Cross Strategy: 20-Year S&P 500 Backtest — TOS Indicators](https://tosindicators.com/research/golden-cross-trading-strategy-20-year-backtest-results), [Golden Cross Trading Strategy — QuantifiedStrategies.com](https://www.quantifiedstrategies.com/golden-cross-trading-strategy/)

## 2. RSI(2) Mean Reversion (Larry Connors)
- **Market:** US equities/ETFs (QQQ and S&P 500 constituents)
- **Parameters:** Price above 200 EMA (trend filter); buy when RSI(2) drops below 10 (some variants below 5); sell when RSI(2) rises above 70 or price closes above prior high.
- **Backtest evidence:** Original Connors research — **75–79% win rate** sustained over 10+ years. An enhanced QQQ variant (added filter): **CAGR 12.75%** vs. 6.4% buy-and-hold, profit factor 3.15, Sharpe ratio 2.85, 75% winners. A broader ~150-name S&P 500 test over 12 months: 68.7% win rate, profit factor 1.57, +0.82% net/trade.
- **Source:** [RSI 2 Strategy: Larry Connors' 2-Period RSI Rules — QuantifiedStrategies.com](https://www.quantifiedstrategies.com/rsi-2-strategy/), [Backtested a mean-reversion RSI(2) pullback strategy on ~150 S&P500 names — Elite Trader](https://www.elitetrader.com/et/threads/backtested-a-mean-reversion-rsi-2-pullback-strategy-on-150-s-p500-names.390710/)

## 3. Time-Series / Cross-Sectional Momentum
- **Market:** Global equities (US + 30 other countries), also applied via ETFs
- **Parameters:** Rank assets by 3–12 month trailing return; go long the top decile/quintile; rebalance monthly or quarterly. ETF variants apply the same ranking logic across a small basket of sector/country ETFs.
- **Backtest evidence:** **9.24%/year** in the US (1927–2024), **7.57%/year** internationally, validated across all 31 countries tested — one of the most extensively re-tested anomalies in finance (100+ years of supporting data cited). An ETF-based momentum variant: **13.09%/year over 54 years** vs. 10.34%/year for MSCI World.
- **Source:** [Momentum Investing Strategy Backtested Over 150 Years — Quant Investing](https://www.quant-investing.com/blog/momentum-investing-strategy-backtested-over-150-years)

## 4. Turtle Trading — Donchian Channel Breakout
- **Market:** Futures/commodities (original), also re-tested on crypto
- **Parameters:** Enter on a 20-day high/low breakout (or 55-day for the slower "System 2"); exit on a 10-day opposite-direction breakout; position size scaled by ATR ("N" in the original Turtle rules), not a fixed lot.
- **Backtest evidence:** Highly implementation-dependent — a literal/unoptimized replication scored a weak **Sharpe of 0.09** and 6.14% annualized return; a parameter-optimized version (grid search) reached **62.71% annualized return with max drawdown under 15%**. Crypto re-tests show improved variants beating the original on Sharpe and drawdown.
- **Source:** [Gate Research Institute: Turtle Trading Rules reproduced, annualized up to 62.71%](https://www.odaily.news/en/post/5205696), [SPL Quantitative Trading: Turtle Trading Strategy](https://github.com/SPLWare/esProc/wiki/SPL-Quantitative-Trading-Practice-Series%EF%BC%9ATurtle-Trading-Strategy)
- **Caveat:** the single most parameter-fragile strategy on this list — the same rule family swings from Sharpe 0.09 to Sharpe-strong depending on tuning and asset class. Backtest any variant of this one extremely carefully before trusting it.

## 5. QQQ Day-of-Week Effect
- **Market:** QQQ (Nasdaq-100 ETF)
- **Parameters:** Combines price action with a specific day-of-week entry/exit pattern (full exact rule set is behind QuantifiedStrategies' paywall; headline stats below are publicly published).
- **Backtest evidence:** 458 trades, **76% win rate**, +0.85% average gain/trade, **15% CAGR**, profit factor 2.7, max drawdown −27%.
- **Source:** [Best Algo Trading Strategies 2026 — QuantifiedStrategies.com](https://www.quantifiedstrategies.com/algo-trading-strategies/)

## 6. Turnaround Tuesday (SPY)
- **Market:** SPY (S&P 500 ETF)
- **Parameters:** Short-term swing entry following a weak Monday session, exit on Tuesday strength (exact numeric trigger behind paywall).
- **Backtest evidence:** 400 trades, **75% win rate**, +0.65% average gain/trade, **7.9% CAGR**, profit factor 2.7, max drawdown −18%.
- **Source:** [Best Algo Trading Strategies 2026 — QuantifiedStrategies.com](https://www.quantifiedstrategies.com/algo-trading-strategies/)

## 7. Bollinger Bands Mean Reversion (Connors & Alvarez)
- **Market:** US stocks/ETFs
- **Parameters:** 20-period SMA ± 2 standard deviation bands; buy near/below the lower band on a confirming reversal signal, scale out near the mid-band/upper band.
- **Backtest evidence:** In-sample (2006–2012, original publication): most variants **>75% win rate**, some above 90%. Out-of-sample re-test on 45 stocks/ETFs (9/2012–9/2017): win rate and profit-per-trade came down somewhat from the original but the system held up with low drawdown — i.e. it survived out-of-sample testing, which most curve-fit strategies don't.
- **Source:** [Bollinger Bands Mean Reversion Trading Strategy — FMZQuant/Medium](https://medium.com/@FMZQuant/bollinger-bands-mean-reversion-trading-strategy-dc80a7ff7a4f)

## 8. Nifty/BankNifty 9:20 Short Straddle
- **Market:** Indian F&O — Nifty/BankNifty weekly options
- **Parameters:** Sell ATM Call + ATM Put at a fixed time (9:20 or 9:45 AM variants both documented), fixed percentage stop-loss per leg (25% commonly used), square off by ~3:00–3:06 PM.
- **Backtest evidence:** AlgoTest 5-year Bank Nifty backtest — total profit **593% of capital**, **69% win rate**. A separate Jan–Sep 2024 Bank Nifty run: **+12% annualized return, 7% max drawdown**. A Nifty monthly straddle entered ahead of Budget day: outlier-adjusted profit factor 2.06, average profit 16 points, **79% win rate**.
- **Source:** search-aggregated from AlgoTest/StockMock-style Indian options-backtesting studies (see [strategy-reference-catalog.md](strategy-reference-catalog.md) sources)
- **Unverified claim to flag:** an independent Medium post claims a further-optimized version of this same setup nets 80%+ annualized returns. I could not directly access/verify that article (blocked to automated fetching) — don't treat that specific number as confirmed until read firsthand.

## 9. Iron Condor — Actively Managed vs. Mechanical
- **Market:** Bank Nifty / broad index options
- **Parameters:** Short strikes ~0.15–0.20 delta OTM with long hedge legs further out; the version that actually performs exits at **45–60% of max profit** with **30–45 days to expiry**, and adjusts (rolls the tested leg 200–300 points further out, or closes and re-enters) when the underlying moves 15–20% against a strike.
- **Backtest evidence:** Mechanical version (no adjustments, hold to expiry) on Bank Nifty 2017–2020: only ₹4,538 profit over 3.5 years after transaction costs entering at expiry-week start; improved to ₹27,600 entering 2 days before expiry — still thin. The actively-managed version reaches **70–80% win rate**.
- **Source:** [Iron Condor Success Rate — OptionsTradingIQ](https://optionstradingiq.com/iron-condor-success-rate/), [Iron Condor Strategy — ApexVol](https://apexvol.com/strategies/iron-condor)
- **Key lesson (applies beyond just this strategy):** the published evidence says the edge in iron condors lives almost entirely in the adjustment/exit discipline, not the entry — a mechanically-run condor without active management is close to a coin flip after costs.

## 10. Opening Range Breakout — Nifty, 8-Year Real Backtest
- **Market:** Nifty 50 spot index
- **Parameters:** Range = first two 15-min candles (9:15–9:45 AM); entry on a break of range high/low; stop-loss = opposite end of range; target = 2× risk (2:1 R:R); exit by 2:30 PM if neither hit; max 1 trade/day; skip days where the range is under 40 Nifty points.
- **Backtest evidence:** 8+ years tested (July 2017 – March 2026), **2,122 trades**, **48.7% win rate**, **+91.6% total return**, profit factor 1.23, max drawdown −11.2%, Sharpe ratio 1.16. **8 of 9 years profitable** — only 2023 was a losing year (−1.1%). Max 10 consecutive losses.
- **Source:** [Best Intraday Breakout Strategy for Nifty 50 (8+ Year Backtest Results) — Intraday Lab](https://intradaylab.com/blog/nifty-orb-breakout-strategy-backtest)
- **Caveat:** transaction costs were explicitly **not included** in this backtest — real returns would be lower after brokerage/STT/slippage, and this is a lower-win-rate/higher-frequency style strategy (profit factor 1.23 is thin) where costs matter proportionally more than in the high-win-rate strategies above.

---

## 1. VWAP + Bollinger Band Mean-Reversion Scalp
- **Timeframe:** 1-min
- **Indicators:** VWAP (session-anchored from 9:15 AM) · Bollinger Bands (20-SMA, ±2 std dev) · RSI(14)
- **Long entry:** candle closes below lower BB + RSI(14) < 30 + price below VWAP → wait for next candle to close back inside band → enter at that close
- **Short entry:** mirror (close above upper BB + RSI > 70 + price above VWAP + confirmation candle closes back inside band)
- **Stop-loss:** 1–2 points beyond the low/high of the confirmation candle
- **Take-profit:** VWAP (primary target); optional partial exit at 20-SMA mid-band
- **Best regime:** range-bound/choppy sessions; avoid strong trend days

## 2. EMA 9/21 Crossover + RSI Momentum Filter
- **Timeframe:** 5-min
- **Indicators:** EMA(9) · EMA(21) · RSI(14, filter only)
- **Long entry:** EMA9 crosses above EMA21 on candle close + RSI(14) > 50
- **Short entry:** EMA9 crosses below EMA21 + RSI(14) < 50
- **Stop-loss:** low (long) / high (short) of the crossover candle ± 1 tick
- **Take-profit:** trailing — exit when price closes back across EMA21 against the position
- **Best regime:** trending sessions; weak in sideways/chop

## 3. Opening Range Breakout (ORB)
- **Timeframe:** 5-min
- **Indicators:** none (pure price action); optional 20-period volume MA if real volume data available
- **Range definition:** high/low of first three 5-min candles, 9:15–9:30 AM
- **Filter:** skip the day if (range high − range low) < ~40 index points
- **Long entry:** first 5-min candle after 9:30 AM that closes above range high
- **Short entry:** first 5-min candle that closes below range low
- **Max attempts/day:** 2 breakout attempts; stand aside after both stop out
- **Stop-loss:** range low (long) / range high (short)
- **Take-profit:** entry ± 1.75 × range size
- **Best regime:** gap-up/gap-down, high-volume/news-driven opens

## 4. Supertrend(10,3) + VWAP Trend-Following
- **Timeframe:** 5-min
- **Indicators:** Supertrend (ATR period 10, multiplier 3) · VWAP (session-anchored)
- **Long entry:** Supertrend flips bullish on candle close + price above VWAP
- **Short entry:** Supertrend flips bearish + price below VWAP
- **Stop-loss:** Supertrend line value at entry, trailing as it updates each candle
- **Take-profit:** none fixed — hold until Supertrend flips against position; cap 3 trades/day
- **Best regime:** sustained trend days, especially 10:00 AM onward

## 5. RSI(2) Pullback-in-Trend Scalp
- **Timeframe:** 1-min
- **Indicators:** EMA(200, trend filter) · RSI(2)
- **Long entry:** price above EMA200 + RSI(2) drops below 10, then crosses back above 10 → enter at that close
- **Short entry:** price below EMA200 + RSI(2) above 90, then crosses back below 90
- **Stop-loss:** low of the pullback swing (long) / high (short)
- **Take-profit:** RSI(2) crosses back above 70 (long)/below 30 (short), or fixed 2:1 reward-to-risk, whichever first
- **Best regime:** established trend with regular shallow pullbacks; fails in range-bound markets

## 6. VWAP Breakout with Volume Confirmation
- **Timeframe:** 1-min
- **Indicators:** VWAP (session-anchored) · Relative Volume (current candle vs 20-period average)
- **Setup:** price consolidates within ~0.1% of VWAP for ≥5 consecutive 1-min candles
- **Long entry:** candle closes above VWAP with volume ≥ 2× 20-period average, and 5-min trend agrees
- **Short entry:** mirror (close below VWAP, volume ≥ 2×, 5-min trend agrees)
- **Stop-loss:** just below VWAP (long) / above VWAP (short)
- **Take-profit:** fixed 2R; move stop to breakeven once 1R captured
- **Best regime:** news/event-driven sessions, first trading hour
- **Data note:** requires real (non-zero) volume data — index data from free sources like yfinance has no usable volume; needs a futures/ETF proxy with real volume

## 7. Iron Condor — Nifty Weekly Expiry
- **Type:** Options-selling, defined-risk, non-directional
- **Instrument:** NIFTY weekly index options
- **Entry:** Monday of expiry week (Tuesday if Monday is a holiday), 9:30–9:45 AM
- **Structure:** short call/put at ~0.15 delta OTM each; long call/put hedges 200 points further OTM each
- **Stop-loss:** exit full structure if combined MTM loss ≥ 1.5× net credit received
- **Take-profit:** exit at 70% of max profit (net credit) captured
- **Time exit:** force close by 3:00 PM on expiry day
- **Data needed:** weekly options chain (strike, OI, IV, LTP) at 5-min granularity + spot price

## 8. Short Strangle with Hard Stop — BankNifty Weekly
- **Type:** Options-selling, semi-defined-risk (SL-protected, not hedged)
- **Instrument:** BANKNIFTY weekly index options
- **Entry:** Monday of expiry week, 9:20 AM
- **Structure:** short call/put at ~0.20 delta OTM each, no hedge legs
- **Stop-loss:** per-leg — exit if premium rises 100% above entry; combined — exit both if MTM loss ≥ ₹15,000/lot
- **Take-profit:** exit at 60% of total premium collected
- **Time exit:** force close by 3:00 PM on expiry day
- **Data needed:** weekly options chain (strike, OI, IV, LTP) at 5-min granularity + spot price

## 9. Bull Put Credit Spread — Nifty Monthly, Support-Based
- **Type:** Options-selling, defined-risk, directional (mildly bullish)
- **Instrument:** NIFTY monthly index options
- **Entry trigger:** spot closes above 20-day SMA on daily candle + within 1% of a prior swing-low support
- **Entry:** next trading day, 9:30 AM
- **Structure:** short put at nearest strike below support level; long put hedge 100 points further OTM
- **Stop-loss:** spot closes below the long put strike on a daily candle
- **Take-profit:** 50% of max profit (net credit)
- **Time exit:** close 3 trading days before monthly expiry
- **Data needed:** daily spot OHLC + monthly options chain (strike, LTP)

## Community-verified variants (sourced from real open-source code)

These three come from an actual open-source Zerodha algo-trading repo used by retail Indian traders — [buzzsubash/algo_trading_strategies_india](https://github.com/buzzsubash/algo_trading_strategies_india) — not estimated deltas but the exact fixed values used in live code. Treat these as alternate, more battle-tested parameter sets for the "9:20 strategy" style trades already in this file (#7/#8), worth backtesting side-by-side against the delta-based versions above.

## 10. Short Straddle — 9:20, NIFTY50 (community code)
- **Type:** Options-selling, non-directional, unhedged (SL-protected only)
- **Instrument:** NIFTY weekly options
- **Entry:** 9:20 AM — sell ATM Call + ATM Put (ATM = spot LTP rounded to nearest 50 points)
- **Stop-loss:** 25% above sell price on each leg (CE and PE independently), + 5-point slippage buffer added to the SL trigger price
- **Re-entry:** if both legs get stopped out, re-enter a fresh ATM straddle at 12:30 PM (one re-entry only)
- **Take-profit:** none fixed — SL-only strategy, monitored via live MTM
- **Time exit:** square off at 3:06 PM
- **Quantity:** 1 lot (lot size 75) as coded default — scale via the position-sizing formula above instead of copying this raw

## 11. Short Strangle — 9:20, NIFTY50 (community code)
- **Type:** Options-selling, non-directional, unhedged (SL-protected only)
- **Instrument:** NIFTY weekly options
- **Entry:** 9:20 AM — sell Call at (ATM + 50) and Put at (ATM − 50), i.e. a fixed 50-point strangle width, not a delta-based offset
- **Stop-loss:** 25% above sell price on each leg + 5-point slippage buffer
- **Re-entry:** same rule — both legs stopped out → fresh strangle at 12:30 PM
- **Take-profit:** none fixed — SL-only
- **Time exit:** square off at 3:06 PM
- **Quantity:** 1 lot (lot size 75) as coded default

## 12. Short Straddle with Breakeven Adjustment — BankNifty (community code)
- **Type:** Options-selling, non-directional, dynamically risk-reduced
- **Instrument:** BANKNIFTY weekly options
- **Entry:** 9:59 AM — sell ATM Call + ATM Put (ATM = spot LTP rounded to nearest 100 points)
- **Stop-loss:** asymmetric — Call SL at 20% above sell price, Put SL at 23% above sell price
- **Adjustment technique (the notable part):** the moment either leg's SL is hit, the *surviving* leg's stop-loss is immediately moved to its own breakeven (entry) price — locking in a "can't lose further on this leg" state instead of leaving it running at the original wide SL. A second adjustment/re-entry check happens at 12:30 PM.
- **Time exit:** square off at 3:06 PM
- **Quantity:** 10 lots (lot size 25) as coded default — again, replace with the position-sizing formula for your own capital

**Worth carrying into every straddle/strangle backtest above, not just this one:** the breakeven-on-first-leg-SL adjustment is a genuinely useful risk technique independent of which strategy it's attached to — cuts tail risk on the classic "one side gets run over" failure mode for unhedged option selling.

## 13–14. Reference-only variants (lower priority, from wider catalog)
- **Iron Butterfly** (short ATM straddle + OTM wings 100–300 pts out) and **Bollinger+VWAP breakout** (15-min signal / 5-min entry, volume > average) are documented in `strategy-reference-catalog.md` if broader coverage is wanted beyond what's above.

---

## Shared backtest requirements

- **Costs to model:** brokerage, STT, exchange charges, GST, stamp duty, and ≥1 tick slippage per leg on entry and exit. Skipping these makes every result look better than reality.
- **Backtest window:** 3 years minimum where data allows, covering both trending and range-bound periods. For free sources (e.g. yfinance ^NSEI), intraday history is typically limited to ~60 days — state this limitation explicitly rather than presenting a small sample as robust.
- **Volume data:** index sources typically report 0 volume — strategies #6 and the volume filter in #3 need a real-volume instrument (futures/ETF) to be properly tested.
- **Options data:** strategies #7–12 need a historical options chain (strike/IV/LTP), not just spot OHLC — flag if unavailable rather than approximating with spot-only data.

## Sources for the community-verified section
- [buzzsubash/algo_trading_strategies_india — GitHub](https://github.com/buzzsubash/algo_trading_strategies_india) (short-straddle/0920_short_straddle, short-strangle/0920_short_strangle, short-straddle/trailing_stop_loss folders — code read directly, not summarized from a blog)
