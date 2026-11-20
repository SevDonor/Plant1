@echo off
cd /d "%~dp0"
python mpu6050_ui.py
if errorlevel 1 (
    echo.
    echo [LOI] Khong chay duoc UI. Hay kiem tra Python va chay: pip install -r requirements.txt
    pause
)
