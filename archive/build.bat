@echo off
chcp 65001 >NUL
rem ============================================================
rem  声鉴·曲库管家 SonicCheck —— 一键打包脚本（Windows）
rem  用法：双击 build.bat，或在项目根目录执行 build.bat
rem  产物：dist\SonicCheck\SonicCheck.exe（整个目录即绿色版）
rem ============================================================
setlocal
cd /d "%~dp0"

echo [1/4] 检查 Python ...
where python >NUL 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)

echo [2/4] 安装依赖（PyQt6 / numpy / PyInstaller）...
python -m pip install --upgrade -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试
    pause
    exit /b 1
)

echo [3/4] 检查内置 ffmpeg ...
if not exist "resources\ffmpeg\ffmpeg.exe" (
    echo [警告] resources\ffmpeg\ffmpeg.exe 不存在
    echo        打包将继续，但 exe 运行时会去系统 PATH 找 ffmpeg。
    echo        建议先按 BUILDING.md 把 ffmpeg.exe / ffprobe.exe 放进 resources\ffmpeg\
    echo        再重新打包（或打包后手动复制到 dist\SonicCheck\resources\ffmpeg\）
    pause
)
if not exist "resources\ffmpeg\ffprobe.exe" (
    echo [警告] resources\ffmpeg\ffprobe.exe 不存在（ffprobe 缺失会导致无法分析）
    pause
)

echo [4/4] 执行 PyInstaller ...
python -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 (
    echo [错误] 打包失败，请把上方报错信息反馈给开发者
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  打包完成：dist\SonicCheck\SonicCheck.exe
echo  整个 dist\SonicCheck 目录就是绿色版，可整体拷贝分发。
echo  若 ffmpeg 未内置，请手动复制到 dist\SonicCheck\resources\ffmpeg\
echo ============================================================
pause
