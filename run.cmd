@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.ps1 first.
  pause
  popd
  exit /b 1
)
".venv\Scripts\python.exe" -m data_xray.launcher %*
set "launcher_result=%errorlevel%"
popd
exit /b %launcher_result%
