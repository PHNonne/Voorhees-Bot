@echo off
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --console --name Voorhees ^
  --collect-data discord ^
  --collect-submodules yt_dlp ^
  --collect-all google.genai ^
  --collect-data gtts ^
  --collect-data tzdata ^
  Voorhees.py
if errorlevel 1 (
  echo Build that bai.
  pause
  exit /b 1
)
echo.
echo File exe: %~dp0dist\Voorhees.exe
pause
