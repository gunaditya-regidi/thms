@echo off
title Treasure Hunt Server - CRASH PROOF RUNNER
color 0A

:loop
echo =======================================================
echo [TREASURE HUNT SERVER] Starting at %date% %time%
echo =======================================================
python app.py

echo.
echo =======================================================
echo [WARNING] Server crashed or stopped unexpectedly!
echo Restarting server automatically in 3 seconds...
echo Press Ctrl+C to stop the loop.
echo =======================================================
echo.
timeout /t 3
goto loop
