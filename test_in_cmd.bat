@echo off
chcp 65001 >nul
title OmniCaption AI - CMD Live Transcription Test
echo ======================================================
echo   OmniCaption AI - Console Live Transcription Test
echo ======================================================
echo.
echo Select Language:
echo   1. French (fr)
echo   2. Tunisian Darija (ar-tn)
echo   3. English (en)
set /p lang_choice="Enter choice (1, 2, or 3) [Default: 1]: "

set LANG_CODE=fr
if "%lang_choice%"=="2" set LANG_CODE=ar-tn
if "%lang_choice%"=="3" set LANG_CODE=en

echo.
echo Select Audio Source:
echo   1. System Audio (YouTube, Meetings, PC sound)
echo   2. Microphone
echo   3. Both (Calls Mode)
set /p src_choice="Enter choice (1, 2, or 3) [Default: 1]: "

set SRC_MODE=system
if "%src_choice%"=="2" set SRC_MODE=mic
if "%src_choice%"=="3" set SRC_MODE=both

echo.
echo Starting live transcription [%LANG_CODE%] on [%SRC_MODE%]...
echo.
cd /d "c:\Users\tayar\OneDrive\Bureau\sprint\tools\windows_live_transcriber"
"C:\Users\tayar\AppData\Local\Programs\Python\Python312\python.exe" cli_test.py --lang %LANG_CODE% --source %SRC_MODE%
pause
