@echo off
REM Starts the live paper-trading runner. Double-click this file, or run it
REM from Task Scheduler, any morning before market open (09:15 IST) --
REM the script itself waits/sleeps outside market hours and only acts
REM during 09:15-15:30 on trading days, so starting it a bit early is fine.
REM
REM Uses the same Python interpreter this project's own venv points to but
REM which is actually broken (see .venv\pyvenv.cfg -- it references a user
REM profile that no longer exists on this machine); pyembed312 is the one
REM that actually works, found and used throughout this project's backtest
REM work for the same reason.
cd /d "D:\Claude DND"
"C:\Users\DELL\pyembed312\python.exe" -m options_bot.live_paper_runner
pause
