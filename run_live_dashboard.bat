@echo off
REM Opens the read-only trade viewer at http://localhost:8010 -- run this
REM alongside run_live_paper_trading.bat (separate window), any time, in
REM any order. It only reads live_paper_trading\ledger.jsonl and
REM status.json, never writes anything, so it's safe to start/stop freely
REM without affecting the trading process itself.
cd /d "D:\Claude DND"
start "" http://localhost:8010
"C:\Users\DELL\pyembed312\python.exe" -m options_bot.live_dashboard
pause
