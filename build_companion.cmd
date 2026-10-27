@echo off
rem Builds OutlawsCompanion.exe (next to this file) from companion.py: the small app you give your friends.
rem Send them just OutlawsCompanion.exe. Needs Python 3 with tkinter on THIS PC only (friends need nothing to join).
setlocal
cd /d "%~dp0"
taskkill /IM OutlawsCompanion.exe /F >nul 2>&1
set "TCL_LIBRARY="
set "TK_LIBRARY="
set "B=%~dp0.build"
if not exist "%B%\Scripts\python.exe" (
    py -3 -m venv "%B%" 2>nul || python -m venv "%B%" || (echo Python 3 not found; install it from python.org & pause & exit /b 1)
)
"%B%\Scripts\python.exe" -c "import tkinter" || (echo This Python has no tkinter; reinstall Python from python.org with "tcl/tk" ticked & pause & exit /b 1)
"%B%\Scripts\python.exe" -m pip install -q --disable-pip-version-check pyinstaller || (echo could not install pyinstaller & pause & exit /b 1)
"%B%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --icon "%~dp0launcher.ico" --name OutlawsCompanion --distpath "%~dp0." --workpath "%B%\work_c" --specpath "%B%" companion.py || (pause & exit /b 1)
echo.
rem Publish folder: the Update Launcher button in the companion reads companion\version.txt + companion\OutlawsCompanion.exe from GitHub.
if not exist "%~dp0companion" mkdir "%~dp0companion"
copy /y "%~dp0OutlawsCompanion.exe" "%~dp0companion\OutlawsCompanion.exe" >nul
powershell -NoProfile -Command "$v=(Select-String -Path '%~dp0companion.py' -Pattern '^VERSION = (\d+)').Matches[0].Groups[1].Value; $h=(Get-FileHash -Algorithm SHA256 '%~dp0companion\OutlawsCompanion.exe').Hash.ToLower(); Set-Content -NoNewline -Path '%~dp0companion\version.txt' -Value \"$v $h\""
echo Done: %~dp0OutlawsCompanion.exe  (send this file to your friends)
echo Update files written to %~dp0companion  (GitHub Actions now builds and publishes these by itself)
pause
