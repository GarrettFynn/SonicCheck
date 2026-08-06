# 打包指南（BUILDING）

SonicCheck 使用 PyInstaller 打包为 Windows onedir 绿色版：`dist\SonicCheck\` 整个目录可解压即用，目标机器无需安装 Python。

## 前置条件

```bash
pip install -r requirements.txt
pip install pyinstaller
```

- Python ≥ 3.10（开发用 3.11/3.12 均验证过）
- `resources/ffmpeg/` 下已放置 `ffmpeg.exe` 与 `ffprobe.exe`（随包分发）

## 一键打包

```bat
build.bat
```

等价于：

```bat
pyinstaller --noconfirm --clean --windowed --name SonicCheck ^
    --icon resources\icon.ico ^
    --add-data "resources;resources" ^
    --exclude-module tests ^
    main.py
```

产物：`dist\SonicCheck\`，入口 `SonicCheck.exe`。

## 发版前检查清单

1. **跑全量测试**：`python tests\run_all.py`（自动发现 `tests\test_*.py`，任一失败退出码非 0）
2. **确认版本号**：`main_window.py` 顶部 `APP_VERSION`（窗口标题、QSettings、发布说明共用）
3. **确认 ffmpeg 已内置**：`resources\ffmpeg\ffmpeg.exe` / `ffprobe.exe` 存在
4. 打包后**实机冒烟**：双击 exe → 扫描一个文件夹 → 解锁一个 ncm/mflac 文件

## 目录布局说明（PyInstaller 6+）

打包后数据资源位于 `dist\SonicCheck\_internal\resources\`，代码通过
`sys._MEIPASS` 定位（`main_window.resource_path()` 与
`core/ffmpeg_locator._bundled_candidates()` 均已兼容），无需手工搬运。

## 分发注意（GPL v3）

本项目以 GPL v3 发布（依赖 PyQt6）。分发 exe 时必须同时提供源码获取方式，
并在发布页声明许可证。ffmpeg 官方静态构建的许可见其发布页说明。
