#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 GUI 集成冒烟测试（离屏）

覆盖：
1. 完整扫描 samples 目录：4 行进表、状态/判定正确、进度与汇总正确
2. ⑦-8 重复开始 = 清空重扫；扫描中立即停止 → 协作式取消不挂死
3. 回归：排序开启状态下行数据不错位（文件名与状态列始终同曲）
4. 行内路径与状态一致（按 PATH_ROLE 校验）

运行（需带 PyQt6 的解释器）：
  C:\\Python314\\python.exe dev/gui_smoke_test.py
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402
from widgets.result_table import PATH_ROLE  # noqa: E402

SAMPLES_DIR = ROOT / "samples"

# 期望判定（以新引擎为准，WAV 为 bug 修复后的正确结果）
EXPECTED = {
    "ALL RED (Explicit) - Playboi Carti_EM_真无损.flac": "真无损",
    "Because I Hear You - Toe_假无损.flac": "假无损",
    "music-sample-44100hz-16bit.wav": "假无损",
    "music-sample-96000hz-24bit.flac": "假无损",
}

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def wait_scan_finished(window: MainWindow, timeout_ms: int = 120000) -> bool:
    """驱动事件循环直到 scan_finished，返回 stopped 参数"""
    box = {"fired": False, "stopped": None}

    def on_finished(stopped: bool):
        box["fired"] = True
        box["stopped"] = stopped

    window._scan_manager.scan_finished.connect(on_finished)
    app = QApplication.instance()
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    while not box["fired"] and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    return box["stopped"] if box["fired"] else None


def row_snapshot(window: MainWindow):
    """返回 [(filename, status_text, path)]，并校验行内一致性"""
    table = window.result_table.table
    rows = []
    for r in range(table.rowCount()):
        name_cell = table.item(r, 0)
        status_cell = table.item(r, 4)
        rows.append((name_cell.text(), status_cell.text(),
                     name_cell.data(PATH_ROLE)))
    return rows


def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    app = QApplication(sys.argv)

    # 隔离测试对真实 QSettings 的影响
    QSettings().clear()

    window = MainWindow()

    print("\n[1] 完整扫描 samples 目录")
    window.set_folder(str(SAMPLES_DIR))
    window.on_start_scan()
    check("扫描管理器进入运行态", window._scan_manager.is_running)
    stopped = wait_scan_finished(window)
    check("scan_finished 触发且为自然完成", stopped is False,
          f"stopped={stopped}")

    rows = row_snapshot(window)
    check("表格 4 行", len(rows) == 4, f"实际 {len(rows)} 行")
    by_name = {name: status for name, status, _ in rows}
    for fname, expect in EXPECTED.items():
        status = by_name.get(fname, "<缺行>")
        check(f"判定 {fname}", expect in status, f"状态='{status}'")

    # 行内一致性：文件名必须出现在其路径中（无错位）
    consistent = all(name in (path or "") for name, _, path in rows)
    check("行内数据一致（文件名∈路径）", consistent)

    summary = window.summary_bar.label.text()
    check("汇总含总数 4", "共 4 首" in summary, summary)
    check("汇总含假无损 3", "假无损 3 首" in summary, summary)

    print("\n[2] 排序回归：按评分排序后行数据仍不错位")
    window.result_table.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    app.processEvents()
    rows_sorted = row_snapshot(window)
    consistent = all(name in (path or "") for name, _, path in rows_sorted)
    check("排序后行内数据一致", consistent)
    scores = [window.result_table.table.item(r, 1).data(
        Qt.ItemDataRole.DisplayRole) for r in range(len(rows_sorted))]
    check("评分列确实降序", scores == sorted(scores, reverse=True),
          f"{scores}")

    print("\n[3] ⑦-8 重复开始=清空重扫 + 立即停止（协作式取消）")
    window.on_start_scan()  # 不清空旧结果直接重扫
    check("重扫后表格重置为 4 行等待",
          window.result_table.table.rowCount() == 4)
    window.on_stop_scan()   # 立即停止
    stopped = wait_scan_finished(window, timeout_ms=30000)
    check("停止后 scan_finished 触发且 stopped=True", stopped is True,
          f"stopped={stopped}")
    check("停止后管理器退出运行态", not window._scan_manager.is_running)
    check("停止按钮已禁用（离开扫描态）",
          not window.left_panel.btn_stop.isEnabled())
    check("开始按钮恢复可用", window.left_panel.btn_start.isEnabled())

    print("\n[4] 停止后再次完整扫描（确认状态机可复用）")
    stopped = wait_scan_finished(window)  # 上一轮残留信号（若有）排空
    window.on_start_scan()
    stopped = wait_scan_finished(window)
    check("二次完整扫描自然完成", stopped is False, f"stopped={stopped}")
    rows = row_snapshot(window)
    by_name = {name: status for name, status, _ in rows}
    all_done = all(EXPECTED[n] in by_name.get(n, "") for n in EXPECTED)
    check("二次扫描判定仍全部正确", all_done)

    window.close()
    print(f"\n{'=' * 60}\n总体: {'全部通过' if not failures else f'FAIL: {failures}'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
