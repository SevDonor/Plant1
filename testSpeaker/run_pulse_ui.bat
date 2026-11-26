@echo off
cd /d "%~dp0"
python speaker_pulse_ui.py
if errorlevel 1 (
    echo.
    echo [LOI] Khong chay duoc giao dien. Hay kiem tra Python va chay: pip install pyserial
    pause
)
