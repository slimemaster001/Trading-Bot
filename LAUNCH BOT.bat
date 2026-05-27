@echo off
chcp 65001 >nul
title Day Trading Bot - Launcher
color 0A
cd /d "%~dp0"

:MENU
cls
echo.
echo  =============================================================
echo   DAY TRADING BOT  --  LAUNCHER
echo  =============================================================
echo.

REM Check if .env exists
if not exist ".env" (
    echo  WARNING: No .env file found!
    echo.
    echo  You need to create a .env file with your Alpaca API keys.
    echo  A template is available: .env.example
    echo.
    echo  Steps:
    echo    1. Open this folder in Explorer
    echo    2. Copy .env.example to .env
    echo    3. Open .env in Notepad and fill in your Alpaca keys
    echo    4. Come back and run the bot
    echo.
    echo  Get paper trading keys at: https://alpaca.markets
    echo.
    pause
    exit /b
)

echo  Today: %date%    Time: %time:~0,5%
echo.
echo  -------------------------------------------------------------
echo   What do you want to do today?
echo  -------------------------------------------------------------
echo.
echo    1  Full day    (scan, approve picks, trade, monitor all day)
echo    2  Scan only  (see AI picks, no trades placed)
echo    3  Backtest   (test strategy on historical data)
echo    4  Report     (read backtest results in plain English)
echo    5  Stats      (win rate, profit/loss history)
echo    6  Learn      (analyse results, update AI weights)
echo    7  Swing      (backtest / optimise / robust -- hold 2-5 days)
echo    8  Swing Live  (morning routine -- approve picks, Alpaca watches)
echo    9  Rec Report  (how accurate were the AI picks? by confidence tier)
echo   10  Exit
echo.
echo  -------------------------------------------------------------
echo.
set /p CHOICE="  Enter your choice (1-10): "

if "%CHOICE%"=="1"  goto FULLDAY
if "%CHOICE%"=="2"  goto SCANONLY
if "%CHOICE%"=="3"  goto BACKTEST
if "%CHOICE%"=="4"  goto REPORT
if "%CHOICE%"=="5"  goto STATS
if "%CHOICE%"=="6"  goto LEARN
if "%CHOICE%"=="7"  goto SWING
if "%CHOICE%"=="8"  goto SWINGLIVE
if "%CHOICE%"=="9"  goto RECREPORT
if "%CHOICE%"=="10" goto EXIT
echo.
echo  Invalid choice - please enter a number from 1 to 10.
timeout /t 2 /nobreak >nul
goto MENU


REM ============================================================
:FULLDAY
cls
echo.
echo  =============================================================
echo   FULL DAY MODE - Scan, Approve, Trade, Monitor
echo  =============================================================
echo.
echo  The bot will:
echo    1. Scan ~200 stocks and score them with your local Ollama AI
echo    2. Show you the top picks -- YOU choose which ones to trade
echo    3. Wait until 9:45 AM ET then place bracket orders on Alpaca
echo    4. Monitor all day, auto-close everything at 3:30 PM ET
echo    5. Log results and update AI scoring weights overnight
echo.
echo  Press Ctrl+C at any time to abort before trades are placed.
echo.
pause
python run_day.py
echo.
echo  =============================================================
echo   Session complete. Press any key to return to menu.
echo  =============================================================
pause >nul
goto MENU


REM ============================================================
:SCANONLY
cls
echo.
echo  =============================================================
echo   SCAN ONLY - No trades will be placed
echo  =============================================================
echo.
python run_day.py --scan-only
echo.
echo  -------------------------------------------------------------
echo   Scan complete. Press any key to return to menu.
echo  -------------------------------------------------------------
pause >nul
goto MENU


REM ============================================================
:BACKTEST
cls
echo.
echo  =============================================================
echo   BACKTEST - Test strategy on historical data
echo  =============================================================
echo.
echo  IMPORTANT: Use dashes in the date, NOT spaces.
echo.
echo    Correct:   2026-03-12
echo    Wrong:     2026 03 12
echo.
set /p BT_START="  Start date (YYYY-MM-DD, e.g. 2026-01-01): "
set /p BT_END="  End date   (YYYY-MM-DD, e.g. 2026-03-01): "
set /p BT_PICKS="  Picks per day (1-5, press Enter for default 3): "
if "%BT_PICKS%"=="" set BT_PICKS=3
echo.
echo  What would you like to do?
echo.
echo    1  Single strategy backtest
echo    2  Compare ALL 4 strategies (same date range - recommended!)
echo    3  AUTO-OPTIMISE - find the best stop/TP settings to hit 20pct+ profit
echo.
echo  Option 3 is the most powerful: it tests 100 parameter combinations
echo  and tells you exactly which settings make the most money.
echo.
set /p BT_MODE="  Choice (1-3, press Enter for Compare All): "
if "%BT_MODE%"=="" set BT_MODE=2
echo.
if "%BT_MODE%"=="3" goto BT_OPTIMIZE
if "%BT_MODE%"=="2" goto BT_COMPARE
goto BT_SINGLE
echo.

:BT_SINGLE
echo  Strategy options:
echo    1  momentum       (strong RSI, positive premarket)
echo    2  mean_reversion (buy yesterday big losers expecting a bounce)
echo    3  gap_and_go     (open gap over 2.5pct with high volume)
echo    4  breakout       (near 52-week high with above-average volume)
echo.
set /p BT_STRAT_NUM="  Strategy (1-4, press Enter for momentum): "
if "%BT_STRAT_NUM%"==""  set BT_STRAT=momentum
if "%BT_STRAT_NUM%"=="1" set BT_STRAT=momentum
if "%BT_STRAT_NUM%"=="2" set BT_STRAT=mean_reversion
if "%BT_STRAT_NUM%"=="3" set BT_STRAT=gap_and_go
if "%BT_STRAT_NUM%"=="4" set BT_STRAT=breakout
if not defined BT_STRAT  set BT_STRAT=momentum
echo.
echo  Running: %BT_STRAT%  --  %BT_START% to %BT_END%  (%BT_PICKS% picks/day)
echo  Downloading data...
echo.
python run_day.py --backtest "%BT_START%" "%BT_END%" --picks %BT_PICKS% --strategy %BT_STRAT%
goto BT_DONE

:BT_COMPARE
echo  Running ALL 4 strategies on same data: %BT_START% to %BT_END%
echo  Downloading data once...
echo.
python run_day.py --backtest "%BT_START%" "%BT_END%" --picks %BT_PICKS% --compare
goto BT_DONE

:BT_OPTIMIZE
echo.
echo  AUTO-OPTIMISER - finds the best stop/TP/score settings
echo  -------------------------------------------------------
echo  This tests 100 parameter combinations and uses walk-forward
echo  validation to avoid overfitting. Takes 2-5 minutes.
echo.
echo  Strategy to optimise:
echo    1  momentum  (recommended to start)
echo    2  mean_reversion
echo    3  gap_and_go
echo    4  breakout
echo.
set /p OPT_STRAT_NUM="  Strategy (1-4, press Enter for momentum): "
if "%OPT_STRAT_NUM%"==""  set OPT_STRAT=momentum
if "%OPT_STRAT_NUM%"=="1" set OPT_STRAT=momentum
if "%OPT_STRAT_NUM%"=="2" set OPT_STRAT=mean_reversion
if "%OPT_STRAT_NUM%"=="3" set OPT_STRAT=gap_and_go
if "%OPT_STRAT_NUM%"=="4" set OPT_STRAT=breakout
if not defined OPT_STRAT  set OPT_STRAT=momentum
echo.
set /p APPLY_BEST="  Apply best settings automatically? (y/N): "
set OPT_APPLY=
if /i "%APPLY_BEST%"=="y" set OPT_APPLY=--apply-best
echo.
echo  Optimising %OPT_STRAT% from %BT_START% to %BT_END%...
echo.
python run_day.py --optimize "%BT_START%" "%BT_END%" --strategy %OPT_STRAT% --picks %BT_PICKS% %OPT_APPLY%
goto BT_DONE

:BT_DONE
echo.
echo  -------------------------------------------------------------
echo   Done! Results saved to the memory folder.
echo   NEXT STEPS:
echo     - Run option 6 (Learn) so the bot improves from results
echo     - Run option 4 (Report) to read results in plain English
echo   Press any key to return to menu.
echo  -------------------------------------------------------------
pause >nul
goto MENU


REM ============================================================
:REPORT
cls
echo.
echo  =============================================================
echo   BACKTEST REPORT - Results explained in plain English
echo  =============================================================
echo.
echo  Reading all saved backtest results and generating report...
echo.
python run_day.py --report
echo.
echo  -------------------------------------------------------------
echo   Press any key to return to menu.
echo  -------------------------------------------------------------
pause >nul
goto MENU


REM ============================================================
:STATS
cls
echo.
echo  =============================================================
echo   PERFORMANCE STATS - How the bot has done so far
echo  =============================================================
echo.
python run_day.py --stats
echo.
echo  -------------------------------------------------------------
echo   Press any key to return to menu.
echo  -------------------------------------------------------------
pause >nul
goto MENU


REM ============================================================
:LEARN
cls
echo.
echo  =============================================================
echo   LEARNING CYCLE - Analyse results, update scoring weights
echo  =============================================================
echo.
echo  This reads all past trade results and updates the AI scoring
echo  weights based on which signals actually predicted wins.
echo  Requires at least 20 closed trades before weights change.
echo.
python run_day.py --learn
echo.
echo  -------------------------------------------------------------
echo   Learning cycle complete. Press any key to return to menu.
echo  -------------------------------------------------------------
pause >nul
goto MENU


REM ============================================================
:SWING
cls
echo.
echo  =============================================================
echo   SWING BACKTEST  -  Hold 2-5 days, daily bars, bigger targets
echo  =============================================================
echo.
echo  Why swing trading beats intraday:
echo    - Targets 5-15%% per trade instead of 1-2%% (commission irrelevant)
echo    - No opening-hour noise -- enter at open, ride the trend for days
echo    - No 5-minute Alpaca data needed -- runs on existing daily bars
echo    - Breakeven win rate drops from 46%% (intraday) to ~40%% (swing)
echo    - Runs in under 30 seconds even for 6-year date ranges
echo.
echo  What do you want to do?
echo.
echo    1  Backtest         (run one strategy, see results)
echo    2  Optimise         (find best stop/TP/trail/hold -- same stocks, fast)
echo    3  ROBUST Optimise  (proves params work on stocks optimizer NEVER saw)
echo.
echo  Option 2: ~140 combos, under 30 seconds.
echo  Option 3: ~960 combos x 5 stock splits. Strongest proof of real edge.
echo            Folds run in parallel. Typical runtime: 2-4 minutes.
echo            Includes a robustness map: plateau = safe, spike = overfit.
echo.
set /p SW_MODE="  Choice (1-3, press Enter for Optimise): "
if "%SW_MODE%"=="" set SW_MODE=2
echo.
set /p SW_START="  Start date (YYYY-MM-DD, e.g. 2020-01-01): "
set /p SW_END="  End date   (YYYY-MM-DD, e.g. 2026-01-01): "
set /p SW_PICKS="  Picks per day (1-5, press Enter for default 3): "
if "%SW_PICKS%"=="" set SW_PICKS=3
echo.
echo  Strategy:
echo    1  momentum       (strong upward momentum -- best for multi-day hold)
echo    2  breakout       (near 52-week high -- often runs 5-15%% over 3-7 days)
echo    3  gap_and_go     (large premarket gaps that continue trending)
echo    4  mean_reversion (oversold bounces -- 2-3 day recovery plays)
echo    5  ALL strategies (compare all 4 -- shows the winner)
echo.
set /p SW_STRAT_NUM="  Strategy (1-5, press Enter for ALL): "
if "%SW_STRAT_NUM%"==""  set SW_STRAT=all
if "%SW_STRAT_NUM%"=="1" set SW_STRAT=momentum
if "%SW_STRAT_NUM%"=="2" set SW_STRAT=breakout
if "%SW_STRAT_NUM%"=="3" set SW_STRAT=gap_and_go
if "%SW_STRAT_NUM%"=="4" set SW_STRAT=mean_reversion
if "%SW_STRAT_NUM%"=="5" set SW_STRAT=all
if not defined SW_STRAT  set SW_STRAT=all
echo.
if "%SW_MODE%"=="3" goto SW_ROBUST
if "%SW_MODE%"=="2" goto SW_OPTIMISE

REM -- Backtest mode --
if "%SW_STRAT%"=="all" (
    echo  NOTE: Compare-all only available in Optimise mode. Using momentum.
    set SW_STRAT=momentum
)
echo  Running swing backtest: %SW_STRAT%  %SW_START% to %SW_END%  (%SW_PICKS% picks/day)
echo.
python run_day.py --swing-backtest "%SW_START%" "%SW_END%" --strategy %SW_STRAT% --picks %SW_PICKS%
goto SW_DONE

:SW_OPTIMISE
if "%SW_STRAT%"=="all" (
    echo  COMPARE ALL: ~140 combos x 4 strategies -- usually under 2 minutes.
    echo  Results saved to a text file so they survive window close.
) else (
    echo  AUTO-OPTIMISER: ~140 stop/TP/trail/hold combinations.
    echo  Runs entirely on daily bars -- typically under 30 seconds.
)
echo.
set /p SW_APPLY="  Apply best settings automatically after? (y/N): "
set SW_APPLY_FLAG=
if /i "%SW_APPLY%"=="y" set SW_APPLY_FLAG=--apply-best
echo.
python run_day.py --swing-optimize "%SW_START%" "%SW_END%" --strategy %SW_STRAT% --picks %SW_PICKS% %SW_APPLY_FLAG%
if ERRORLEVEL 1 (
    echo.
    echo  ============================================================
    echo   SOMETHING WENT WRONG -- see the error message above.
    echo   Open a Command Prompt and run the command manually to debug.
    echo  ============================================================
    echo.
    pause
)
goto SW_DONE

:SW_ROBUST
cls
echo.
echo  =============================================================
echo   ROBUST OPTIMISER  --  Stock cross-validation
echo  =============================================================
echo.
echo  Dates    : %SW_START%  to  %SW_END%
echo  Strategy : %SW_STRAT%
echo.
echo  What this does:
echo    - Splits ~300 stocks randomly: 60%% train / 40%% test  (5 times)
echo    - Finds best params on train stocks only
echo    - Checks those params STILL work on the test stocks (never seen)
echo    - Consensus = params that won across most splits
echo    - Robustness map = shows if result is a plateau (safe) or spike (overfit)
echo.
echo  Typical runtime: 2-4 minutes  (folds run in parallel).
echo.
if "%SW_STRAT%"=="all" set SW_STRAT=momentum
set /p RO_APPLY="  Apply consensus params automatically? (y/N): "
set RO_APPLY_FLAG=
if /i "%RO_APPLY%"=="y" set RO_APPLY_FLAG=--apply-best
echo.
echo  Running robust optimizer: %SW_STRAT%  %SW_START% to %SW_END%
echo  Please wait -- folds are running in parallel...
echo.
python run_day.py --robust-optimize "%SW_START%" "%SW_END%" --strategy %SW_STRAT% %RO_APPLY_FLAG%
if ERRORLEVEL 1 (
    echo.
    echo  ============================================================
    echo   SOMETHING WENT WRONG -- see the error message above.
    echo.
    echo   To see the full error, open a Command Prompt and run:
    echo     cd /d "%~dp0"
    echo     python run_day.py --robust-optimize %SW_START% %SW_END% --strategy %SW_STRAT%
    echo  ============================================================
    echo.
    pause
)
goto RO_DONE

:RO_DONE
echo.
echo  -------------------------------------------------------------
echo   Done! Results in memory/swing_results/robust_STRAT_TIME.txt
echo.
echo   READ THE VERDICT LINE:
echo     PLATEAU = params are robust. Use these for live trading.
echo     SPIKE   = params may be overfit. Use standard optimizer.
echo.
echo   To use the found params: re-run with "Apply = y"
echo   Press any key to return to menu.
echo  -------------------------------------------------------------
pause >nul
goto MENU


:SW_DONE
echo.
echo  -------------------------------------------------------------
echo   Done! Results saved to memory/swing_results/
echo   TIP: The .txt file in that folder has the full comparison
echo        table -- open it any time to re-read the results.
echo   Press any key to return to menu.
echo  -------------------------------------------------------------
pause >nul
goto MENU


REM ============================================================
:SWINGLIVE
cls
echo.
echo  =============================================================
echo   SWING LIVE  -  Your 15-minute morning routine
echo  =============================================================
echo.
echo  What this does:
echo    1. Shows all your open swing positions with current P&L
echo    2. Flags any positions held 3+ days (asks if you want to close)
echo    3. Scans ~200 stocks for today's best momentum setups
echo    4. You approve picks (or skip if you already have 3 open)
echo    5. Places bracket orders on Alpaca (entry + stop + take profit)
echo.
echo  After this runs:
echo    - Alpaca's servers watch your stops and take-profits 24/7
echo    - Your PC can be turned off
echo    - Come back tomorrow morning and run option 9 again
echo.
echo  Requirements: Alpaca API keys in .env (paper trading is fine)
echo.
echo  Stop loss: 3%%   Take profit: 8%%   Max hold: 3 days
echo.
pause
python run_day.py --swing-live
echo.
echo  =============================================================
echo   Done! Alpaca is watching your positions. PC can be off.
echo   Come back tomorrow morning and run option 9 again.
echo  =============================================================
pause >nul
goto MENU


REM ============================================================
:RECREPORT
cls
echo.
echo  =============================================================
echo   REC REPORT  -  How accurate are the AI pick recommendations?
echo  =============================================================
echo.
echo  This shows you:
echo    - Win rate and average P&L for trades you actually placed
echo    - Breakdown by confidence tier: [HIGH] [GOOD] [HIGH?] [GOOD?] [OK]
echo    - Picks you SKIPPED at [HIGH] confidence -- what did you miss?
echo    - Worst losses and what the AI said the risk was at scan time
echo.
echo  Default: last 30 days.  Change with --days N (e.g. --days 90).
echo.
set /p RC_DAYS="  Days to look back (press Enter for 30): "
if "%RC_DAYS%"=="" set RC_DAYS=30
echo.
python run_day.py --rec-report --days %RC_DAYS%
echo.
echo  -------------------------------------------------------------
echo   Press any key to return to menu.
echo  -------------------------------------------------------------
pause >nul
goto MENU


REM ============================================================
:EXIT
echo.
echo  Good luck today. See you tomorrow!  (Option 10 tomorrow morning)
echo.
timeout /t 2 /nobreak >nul
exit /b
