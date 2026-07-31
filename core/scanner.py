#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音频文件扫描：递归遍历文件夹（③-1 已确认递归）

M4 边界打磨：无权限/损坏的子目录不再让整个扫描崩掉——os.walk 的
onerror 捕获并计数，由上层在日志提示"跳过 N 个不可访问文件夹"。
"""

import os
from pathlib import Path

from core.analyzer import AUDIO_EXTS

# 去重清除的隔离目录：扫描时排除，避免移进去的文件被反复扫出
EXCLUDE_DIR_NAMES = {"_待清除"}
# 安全模式输出目录（<文件夹名>_安全输出）同样排除，避免产出副本被反复扫出
EXCLUDE_DIR_SUFFIXES = ("_安全输出",)


def _excluded(dirname: str) -> bool:
    return dirname in EXCLUDE_DIR_NAMES or \
        any(dirname.endswith(s) for s in EXCLUDE_DIR_SUFFIXES)


def find_audio_files(folder: str) -> tuple:
    """递归查找音频文件。

    返回 (files, skipped_dirs)：
    - files：绝对路径列表（按路径排序）
    - skipped_dirs：遍历中不可访问的目录次数（权限不足/损坏链接等）
    """
    root = Path(folder)
    if not root.is_dir():
        return [], 0

    files = []
    skipped = [0]  # onerror 闭包计数

    def _onerror(_err):
        skipped[0] += 1

    for dirpath, dirnames, filenames in os.walk(root, onerror=_onerror):
        dirnames[:] = [d for d in dirnames if not _excluded(d)]
        for name in filenames:
            if Path(name).suffix.lower() in AUDIO_EXTS:
                files.append(Path(dirpath) / name)

    files.sort(key=lambda p: str(p).lower())
    return [str(p.resolve()) for p in files], skipped[0]
