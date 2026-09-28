#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4 边界测试（离屏，C:\\Python314 运行）

覆盖：
1. scanner 容错：无权限子目录跳过并计数，可访问部分照常返回
2. ffmpeg 缺失前置防护：拒绝扫描且日志说明（不整批进 error 行）
3. 扫描中整窗拖拽被锁定（dragEnter 不接受）
4. 扫描中线程数/分析时长 spin 锁定
5. 空文件夹扫描：仅提示，不进入运行态
6. 日志面板行数上限 5000
7. 扫描完成汇总日志含真/假/失败明细
"""

import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QMimeData, QPoint, QSettings, Qt, QUrl  # noqa: E402
from PyQt6.QtGui import QDragEnterEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import main_window as mw_module  # noqa: E402
from core.scanner import find_audio_files  # noqa: E402
from main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402

SAMPLES = ROOT / "samples"
SANDBOX = ROOT / "dev" / "tmp_m4_sandbox"

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def last_logs(window, n: int = 5) -> str:
    return window.log_panel.view.toPlainText().strip().splitlines()[-n:]


def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    app = QApplication(sys.argv)
    QSettings().clear()

    print("[1] scanner 容错：无权限子目录跳过并计数")
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    (SANDBOX / "good").mkdir(parents=True)
    (SANDBOX / "blocked").mkdir()
    shutil.copy2(SAMPLES / "music-sample-44100hz-16bit.wav",
                 SANDBOX / "good" / "ok.wav")
    shutil.copy2(SAMPLES / "music-sample-44100hz-16bit.wav",
                 SANDBOX / "blocked" / "hidden.wav")

    orig_scandir = os.scandir

    def fake_scandir(path):
        if "blocked" in str(path):
            raise PermissionError("模拟无权限")
        return orig_scandir(path)

    os.scandir = fake_scandir
    try:
        files, skipped = find_audio_files(str(SANDBOX))
    finally:
        os.scandir = orig_scandir
    check("可访问目录文件照常返回", len(files) == 1
          and files[0].endswith("ok.wav"), str(files))
    check("无权限目录计数 1", skipped == 1, f"skipped={skipped}")

    window = MainWindow()

    print("\n[2] ffmpeg 缺失前置防护")
    orig_ff, orig_fp = mw_module.find_ffmpeg, mw_module.find_ffprobe
    mw_module.find_ffmpeg = lambda: ""
    mw_module.find_ffprobe = lambda: ""
    try:
        window.set_folder(str(SAMPLES))
        window.on_start_scan()
    finally:
        mw_module.find_ffmpeg, mw_module.find_ffprobe = orig_ff, orig_fp
    check("未进入扫描运行态", not window._scan_manager.is_running)
    check("日志说明 ffmpeg 缺失",
          any("ffmpeg" in line for line in last_logs(window)))

    print("\n[3] 扫描中整窗拖拽锁定")
    window._scan_manager._running = True  # 模拟扫描中（不真跑）
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(SAMPLES))])
    event = QDragEnterEvent(QPoint(100, 100), Qt.DropAction.CopyAction,
                            mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(event)
    check("扫描中 dragEnter 被拒绝", not event.isAccepted())
    window._scan_manager._running = False
    event2 = QDragEnterEvent(QPoint(100, 100), Qt.DropAction.CopyAction,
                             mime, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(event2)
    check("非扫描中 dragEnter 正常接受", event2.isAccepted())

    print("\n[4] 扫描中参数控件锁定")
    window.left_panel.set_scanning(True)
    check("线程数 spin 锁定", not window.left_panel.spin_threads.isEnabled())
    check("分析时长 spin 锁定",
          not window.left_panel.spin_seconds.isEnabled())
    window.left_panel.set_scanning(False)
    check("扫描结束后恢复", window.left_panel.spin_threads.isEnabled())

    print("\n[5] 空文件夹扫描")
    empty_dir = SANDBOX / "empty"
    empty_dir.mkdir(exist_ok=True)
    window.set_folder(str(empty_dir))
    window.on_start_scan()
    check("未进入扫描运行态", not window._scan_manager.is_running)
    check("日志提示无音频文件",
          any("没有找到音频文件" in line for line in last_logs(window)))

    print("\n[6] 日志面板行数上限")
    check("maximumBlockCount = 5000",
          window.log_panel.view.document().maximumBlockCount() == 5000)

    print("\n[7] 完成汇总日志明细（真扫 samples）")
    window.set_folder(str(SAMPLES))
    window.on_start_scan()
    box = {"done": False}
    window._scan_manager.scan_finished.connect(lambda s: box.update(done=True))
    deadline = time.monotonic() + 120
    while not box["done"] and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    logs = last_logs(window, 3)
    check("汇总含真/假/失败明细",
          any("真无损 1 首" in line and "假无损 3 首" in line
              and "失败 0 首" in line for line in logs), str(logs))

    window.close()
    shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{'=' * 60}\n总体: {'全部通过' if not failures else f'FAIL: {failures}'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
