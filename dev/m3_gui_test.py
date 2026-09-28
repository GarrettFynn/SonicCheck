#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3 GUI 集成测试（离屏，C:\\Python314 运行）

端到端：扫描沙盒 → 按钮启用 → 导出 CSV（打补丁存盘对话框）
→ 一键重命名（打补丁预览对话框自动确认）→ 落盘/表格/内存三方一致
→ 二次重命名无操作（防重复）。

⚠ 沙盒为 samples 的副本，原始样本不受影响。
"""

import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog  # noqa: E402

from core.renamer import build_rename_plan  # noqa: E402
from main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402
from widgets.rename_dialog import RenameDialog  # noqa: E402
from widgets.result_table import PATH_ROLE  # noqa: E402

SAMPLES = ROOT / "samples"
SANDBOX = ROOT / "dev" / "tmp_m3_gui_sandbox"

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def wait_finished(window, timeout_s: int = 120):
    box = {"fired": False, "stopped": None}
    window._scan_manager.scan_finished.connect(
        lambda s: box.update(fired=True, stopped=s))
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while not box["fired"] and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    return box["stopped"] if box["fired"] else None


def table_paths(window):
    t = window.result_table.table
    return [t.item(r, 0).data(PATH_ROLE) for r in range(t.rowCount())]


def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    app = QApplication(sys.argv)
    QSettings().clear()

    print("[1] 搭沙盒（2 个 WAV 副本：1 无后缀、1 带错后缀）")
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    src = SAMPLES / "music-sample-44100hz-16bit.wav"
    shutil.copy2(src, SANDBOX / "gui_song.wav")
    (SANDBOX / "gui_song.lrc").write_text("[00:00.00]歌词", encoding="utf-8")
    shutil.copy2(src, SANDBOX / "gui_song2_真无损.wav")  # 改判场景

    window = MainWindow()
    window.set_folder(str(SANDBOX))
    # M4.6 起导出/重命名有范围弹窗：测试中固定走"全部文件"
    # ⚠ 必须 **_kwargs：槽内 TypeError 会让 PyQt6 直接终止进程（EXIT=127）
    window._pick_scope = lambda title, **_kwargs: \
        list(window._results.values())

    print("\n[2] 完整扫描")
    window.on_start_scan()
    stopped = wait_finished(window)
    check("扫描完成", stopped is False, f"stopped={stopped}")
    check("导出/重命名按钮已启用",
          window.result_table.btn_export.isEnabled()
          and window.result_table.btn_rename.isEnabled())

    print("\n[3] 导出 CSV（真实点击按钮 + 打补丁存盘对话框）")
    csv_path = SANDBOX / "gui_export.csv"
    orig_get_save = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (str(csv_path), "CSV 文件 (*.csv)"))
    try:
        window.result_table.btn_export.click()  # 回归：按钮必须真正触发
        app.processEvents()
    finally:
        QFileDialog.getSaveFileName = orig_get_save
    check("CSV 落盘且带 BOM",
          csv_path.read_bytes().startswith(b'\xef\xbb\xbf'))
    check("CSV 2 行数据",
          len(csv_path.read_text(encoding='utf-8-sig').strip()
              .splitlines()) == 3)

    print("\n[4] 一键重命名（真实点击按钮 + 打补丁预览对话框自动确认）")
    orig_exec = RenameDialog.exec
    RenameDialog.exec = lambda self: QDialog.DialogCode.Accepted
    try:
        window.result_table.btn_rename.click()  # 回归：按钮必须真正触发
        app.processEvents()
    finally:
        RenameDialog.exec = orig_exec

    check("落盘: gui_song_假无损.wav",
          (SANDBOX / "gui_song_假无损.wav").is_file())
    check("落盘: gui_song_假无损.lrc",
          (SANDBOX / "gui_song_假无损.lrc").is_file()
          and not (SANDBOX / "gui_song.lrc").exists())
    check("改判落盘: gui_song2_假无损.wav（旧名已剥）",
          (SANDBOX / "gui_song2_假无损.wav").is_file()
          and not (SANDBOX / "gui_song2_真无损.wav").exists())

    paths = table_paths(window)
    check("表格行路径已更新为新名",
          any(p.endswith("gui_song_假无损.wav") for p in paths)
          and any(p.endswith("gui_song2_假无损.wav") for p in paths),
          str([Path(p).name for p in paths]))
    check("内存结果键与新路径一致",
          all(p in window._results for p in paths))
    consistent = all(
        window.result_table.table.item(r, 0).text()
        in (paths[r] or "") for r in range(len(paths)))
    check("重命名后行内数据一致", consistent)

    print("\n[5] 二次重命名 → 应全部无操作")
    plan2, skipped2 = build_rename_plan(list(window._results.values()))
    check("二次计划为空", len(plan2) == 0, f"{len(plan2)}")

    print("\n[6] 重命名后再扫描（⑦-8 清空重扫仍正常）")
    window.on_start_scan()
    stopped = wait_finished(window)
    check("再扫描完成", stopped is False, f"stopped={stopped}")
    check("再扫描判定仍正确（2 假无损）",
          all("假无损" in window.result_table.table.item(r, 4).text()
              for r in range(window.result_table.table.rowCount())))

    window.close()
    print(f"\n{'=' * 60}\n总体: {'全部通过' if not failures else f'FAIL: {failures}'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
