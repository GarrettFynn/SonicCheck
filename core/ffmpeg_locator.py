#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ffmpeg / ffprobe 定位与子进程辅助

查找顺序：
1. 内置目录（打包后随包分发，解压即用，不依赖用户环境）
2. 系统 PATH
3. Windows 常见安装位置（chocolatey / C:\\ffmpeg）
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

_EXE = ".exe" if os.name == "nt" else ""

# Windows 常见安装位置兜底
_FALLBACK_DIRS = [
    r"C:\ProgramData\chocolatey\bin",
    r"C:\ffmpeg\bin",
]


def _app_dir() -> Path:
    """应用根目录：打包后为 exe 所在目录，源码运行时为项目根目录"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundled_candidates(name: str):
    base = _app_dir()
    exe = name + _EXE
    # onedir 打包布局：<app>/resources/ffmpeg/ffmpeg.exe
    yield base / "resources" / "ffmpeg" / exe
    yield base / "resources" / "ffmpeg" / "bin" / exe
    # PyInstaller _MEIPASS（onefile 兼容，本项目用 onedir，此处仅兜底）
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        yield Path(mei) / "resources" / "ffmpeg" / exe
        yield Path(mei) / "resources" / "ffmpeg" / "bin" / exe


def find_tool(name: str) -> str:
    """定位 ffmpeg/ffprobe，返回可执行文件绝对路径；找不到返回空字符串"""
    for cand in _bundled_candidates(name):
        if cand.is_file():
            return str(cand)

    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())

    if os.name == "nt":
        for d in _FALLBACK_DIRS:
            cand = Path(d) / (name + _EXE)
            if cand.is_file():
                return str(cand)
    return ""


def find_ffmpeg() -> str:
    return find_tool("ffmpeg")


def find_ffprobe() -> str:
    return find_tool("ffprobe")


def hidden_subprocess_kwargs() -> dict:
    """Windows 下隐藏子进程控制台窗口（--windowed 打包后防黑窗闪烁，⑦-6）"""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }
