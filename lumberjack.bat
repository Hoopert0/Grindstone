@echo off
rem Grindstone shortcuts - works from any folder, on any PC set up with setup_pc.ps1.
rem   lumberjack          start everything: server, game, control panel (same as the desktop icon)
rem   lumberjack client   start the bot-ready 2009scape client (downloads your latest save first)
rem   lumberjack panel    start the control panel and open it in your browser
rem   lumberjack woodcut  run the woodcutting bot in this window (F12 stops)
rem   lumberjack sync     upload your save now (normally automatic when the game closes)
rem   lumberjack status   which PC is using the shared save
cd /d "%~dp0"
set PY=%USERPROFILE%\.venvs\lumberjack\Scripts\python.exe
if not exist "%PY%" (
  echo Python environment not found - run setup_pc.ps1 first.
  goto :eof
)
if "%1"==""          ( "%PY%" -m lumberjack.start & goto :eof )
if /i "%1"=="start"   ( "%PY%" -m lumberjack.start %2 & goto :eof )
if /i "%1"=="client"  ( "%PY%" -m lumberjack.launch_client %2 & goto :eof )
if /i "%1"=="woodcut" ( "%PY%" -m lumberjack.main woodcut %2 %3 %4 %5 & goto :eof )
if /i "%1"=="sync"    ( "%PY%" -m lumberjack.savesync push & goto :eof )
if /i "%1"=="status"  ( "%PY%" -m lumberjack.savesync status & goto :eof )
if /i "%1"=="panel" (
  start "" http://127.0.0.1:8765
  "%PY%" -m lumberjack.web.server
  goto :eof
)
echo Usage: lumberjack [start ^| client ^| panel ^| woodcut ^| sync ^| status]
