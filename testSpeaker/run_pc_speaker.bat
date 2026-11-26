@echo off
cd /d "%~dp0"
python esp32_pc_speaker.py
if errorlevel 1 (
    echo.
    echo [LOI] Khong chay duoc ung dung. Hay kiem tra Python va chay: pip install -r requirements.txt
    pause
)
