@echo off
:: PegasoQuiz - Menu principale
title PegasoQuiz Setup

:MENU
cls
echo.
echo   =============================================
echo     PegasoQuiz ^|  Setup Windows
echo   =============================================
echo.
echo   1.  Setup (prerequisiti + variabili)
echo   2.  Apri cartella dist\  (dopo la build)
echo   3.  Esci
echo.
set /p C=  Scelta [1-3]: 

if "%C%"=="1" goto P1
if "%C%"=="2" goto DIST
if "%C%"=="3" exit /b 0
goto MENU

:P1
set "SCRIPT=%~dp0setup.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
pause & goto MENU

:DIST
if exist "%~dp0sorgenti\dist\PegasoQuiz" (
    explorer "%~dp0sorgenti\dist\PegasoQuiz"
) else (
    echo   [WARN] Cartella dist\ non trovata.
    pause
)
goto MENU
