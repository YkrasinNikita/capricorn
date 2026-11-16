@echo off
cd /d "%~dp0"
set "PY="
for %%v in (3.13 3.12 3.11 3.14) do if not defined PY py -%%v -c "" >nul 2>nul && set "PY=py -%%v"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY set "PY=python"

%PY% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo [Capri] Installing PyInstaller...
  %PY% -m pip install --upgrade pyinstaller
  if errorlevel 1 goto fail
)

%PY% -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 goto fail

echo.
echo [Capri] Done: dist\Capri\Capri.exe
echo [Capri] The speech model is not included - the app will offer to download it on first run.
pause
exit /b 0

:fail
echo.
echo [Capri] Build failed. See the messages above.
pause
exit /b 1
