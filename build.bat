@echo off
REM ══════════════════════════════════════════════════════════════
REM SonicCheck 一键打包（onedir 绿色版）
REM 用法：在项目根目录执行 build.bat，产物在 dist\SonicCheck\
REM 前置：pip install -r requirements.txt 且 pip install pyinstaller
REM ══════════════════════════════════════════════════════════════
setlocal
cd /d %~dp0

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo [X] 未找到 PyInstaller，请先执行: pip install pyinstaller
    exit /b 1
)

REM 建议打包前先跑全量测试：python tests\run_all.py

pyinstaller --noconfirm --clean ^
    --windowed ^
    --name SonicCheck ^
    --icon resources\icon.ico ^
    --add-data "resources;resources" ^
    --exclude-module tests ^
    main.py

if errorlevel 1 (
    echo [X] 打包失败，请检查上方 PyInstaller 输出
    exit /b 1
)

echo.
echo [OK] 打包完成：dist\SonicCheck\
echo      整个目录即为绿色版（resources\ffmpeg 已内置，解压即用）
echo      入口：dist\SonicCheck\SonicCheck.exe
