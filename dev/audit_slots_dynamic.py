#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户入口动态巡检（v2.0.1）：mock 全部对话框后逐个真实调用

静态审计（test_slot_refs）拦"引用不存在的方法"；本巡检拦"运行到一半
才炸"的路径——每个用户可触发入口（按钮槽/回调）都以取消语义真实执行
一遍，任何异常即失败。OFFSCREEN + QSettings 隔离。

运行: python dev/audit_slots_dynamic.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402

QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                  str(Path(tempfile.mkdtemp(prefix="audit_slots_"))))
QSettings.setDefaultFormat(QSettings.Format.IniFormat)

from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog,  # noqa: E402
                             QInputDialog, QMessageBox)

app = QApplication([])  # noqa: E402

# ── 对话框全部改为"取消"语义 ──
QDialog.exec = lambda self: QDialog.DialogCode.Rejected
QMessageBox.exec = lambda self: QMessageBox.DialogCode.Rejected
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
QMessageBox.about = staticmethod(lambda *a, **k: None)
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))


def main():
    import main_window as MW
    from models.result_item import STATUS_ERROR, ResultItem

    w = MW.MainWindow()
    d = Path(tempfile.mkdtemp(prefix="audit_slots_dir_"))
    w.set_folder(str(d))

    # 一行已完成 + 一行失败（供 pick_scope/dedupe 等取数）
    ok_item = ResultItem(
        filename="ok.flac", stem="ok", filepath=str(d / "ok.flac"),
        score=85.0, status="done",
        detail={"meta": {"duration": 60.0, "title": "歌", "artist": "歌手"},
                "cutoffs": {"-60dB": 20000.0}, "dr": 9.0,
                "correlation": 0.5})
    (d / "ok.flac").write_bytes(b"x" * 64)
    err_item = ResultItem(filename="bad.flac", stem="bad",
                          filepath=str(d / "bad.flac"), status=STATUS_ERROR,
                          error_message="模拟失败")
    for it in (ok_item, err_item):
        w._results[it.filepath] = it
        w.result_table.add_row(it)

    entry_points = [
        ("窗口", w.on_select_folder),
        ("窗口", w.on_start_scan),
        ("窗口", w.on_stop_scan),
        ("窗口", w.on_settings),
        ("窗口", w.on_guide),
        ("窗口", w.on_unlock),
        ("窗口", w.on_export_csv),
        ("窗口", w.on_report),
        ("窗口", w.on_rename),
        ("窗口", w.on_tag_rename),
        ("窗口", w.on_compare),
        ("窗口", w.on_dedupe),
        ("窗口", w.on_restore),
        ("窗口", w.on_playlist),
        ("窗口", lambda: w.on_show_detail("不存在的路径")),
        ("窗口", lambda: w.on_file_started(str(d / "ok.flac"))),
        ("窗口", lambda: w.on_file_done(ok_item)),
        ("窗口", lambda: w.on_progress(1, 1)),
        ("窗口", lambda: w.on_scan_finished(False)),
        ("窗口", lambda: w.on_scan_finished(True)),
        ("窗口", w._refresh_summary),
        ("窗口", w._refresh_restore_enabled),
        ("窗口", w._update_safe_label),
        ("窗口", lambda: w._on_safe_toggled(True)),
        ("窗口", lambda: w._on_safe_toggled(False)),
        ("窗口", lambda: w._check_updates_bg()),
        ("控制器", lambda: w.ops._on_fp_progress(0, 0)),
        ("表格", lambda: rt_action(w)),
        ("左栏", lambda: w.left_panel.set_scanning(True)),
        ("左栏", lambda: w.left_panel.set_scanning(False)),
        ("日志", lambda: w.log_panel.log("巡检 <test> & escaping")),
        ("日志", lambda: w.log_panel.log_error("巡检错误")),
    ]

    def rt_action(w):
        """表格内部处理器：右键无选中返回 / 双击信号 / 单点更新"""
        rt = w.result_table
        rt._on_context_menu(type("P", (), {"x": 0, "y": 0})())
        rt._on_double_clicked(rt.proxy.index(0, 0))
        rt.update_row_by_path(str(d / "ok.flac"), ok_item)
        rt.apply_filter("只看真无损")
        rt.apply_filter("全部")
        rt.mark_unfinished_cancelled()
        rt.begin_update()
        rt.end_update()

    failures = []
    for owner, fn in entry_points:
        name = getattr(fn, "__name__", repr(fn)[:40])
        try:
            fn()
            app.processEvents()
            print(f"  ✓ [{owner}] {name}")
        except Exception as exc:
            import traceback
            failures.append((owner, name, exc))
            print(f"  ✗ [{owner}] {name}: {exc!r}")
            traceback.print_exc()

    w.close()
    if failures:
        print(f"\n✗ 巡检发现 {len(failures)} 个入口异常")
        sys.exit(1)
    print(f"\n✓ 动态巡检通过：{len(entry_points)} 个用户入口全部无异常")


if __name__ == "__main__":
    main()
