#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审查修复回归（v2.0.2 批）：更新提示/取消按钮/排序指示器/换文件夹/表格映射

对应审查报告修复项 ①③④⑤⑥ 的运行时锁定。需要 PyQt6（offscreen）。
运行: python tests/test_review_fixes.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402

QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                  str(Path(tempfile.mkdtemp(prefix="review_fix_"))))
QSettings.setDefaultFormat(QSettings.Format.IniFormat)

from PyQt6.QtCore import Qt, QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

PASS = []


def check(name, cond, extra=""):
    cond = bool(cond)
    PASS.append((name, cond))
    print(f"  {'✓' if cond else '✗'} {name} {extra}")
    assert cond, f"{name} {extra}"


def main():
    import main_window as MW
    import core.update_check as UC
    from models.result_item import STATUS_DONE, ResultItem
    from widgets.unlock_dialog import UnlockDialog

    print("1. 修复① 更新提示经信号回主线程（曾是静默死功能）")
    UC.check = lambda v: 'v99.9.9'
    w = MW.MainWindow()
    w._check_updates_bg()   # 后台线程 emit → queued → 主线程槽
    loop = QEventLoop()

    def poll():
        if '新版本' in w.log_panel.view.toPlainText():
            loop.quit()
    t = QTimer(); t.timeout.connect(poll); t.start(20)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    check('日志出现"发现新版本 v99.9.9"',
          '发现新版本 v99.9.9' in w.log_panel.view.toPlainText())
    check('状态栏提示', 'v99.9.9' in w.statusBar().currentMessage())

    print("2. 修复③ 解锁运行中点按钮=取消（曾是死按钮）")
    dlg = UnlockDialog('')
    dlg._running = True
    dlg.btn_start.setText('取消')
    dlg._on_start()
    check('点击后 _cancel 置位', dlg._cancel.is_set())
    check('按钮进入禁用态', not dlg.btn_start.isEnabled())
    dlg._running = False   # 复位供下一断言
    dlg2 = UnlockDialog('')
    dlg2._on_start()       # 空列表守卫仍生效
    check('空列表守卫不受影响', not dlg2._running)
    dlg.close(); dlg2.close()

    print("3. 修复④ 改名行映射（rename_row_path）")
    w2 = MW.MainWindow()
    d = Path(tempfile.mkdtemp(prefix='fix4_'))
    p = d / 'old.flac'
    p.write_bytes(b'x' * 32)
    it = ResultItem(filename='old.flac', stem='old', filepath=str(p),
                    score=80.0, status=STATUS_DONE,
                    detail={'meta': {'artist': '张三'},
                            'cutoffs': {'-60dB': 20000.0}, 'dr': 9.0})
    w2._results[str(p)] = it
    w2.result_table.add_row(it)
    # 控制器时序：先原地改 item，再 rename_row_path(old, new)
    it.filepath = str(d / 'new.flac')
    it.filename = 'new.flac'
    ok = w2.result_table.rename_row_path(str(p), it.filepath)
    check('rename_row_path 成功', ok)
    check('新路径可定位行',
          w2.result_table.model.row_of_path(str(d / 'new.flac')) == 0)
    check('旧路径已出映射',
          w2.result_table.model.row_of_path(str(p)) == -1)
    check('_item_of(新) 可用（右键筛选依赖）',
          w2.result_table._item_of(str(d / 'new.flac')) is not None)

    print("4. 修复⑤ 扫描结束按指示器重排")
    w2.result_table.clear_rows()
    for name, score in (('a.flac', 50), ('b.flac', 90), ('c.flac', 70)):
        it = ResultItem(filename=name, stem=name, filepath='C:/x/' + name,
                        score=score, status=STATUS_DONE,
                        detail={'meta': {}, 'cutoffs': {'-60dB': 20000.0},
                                'dr': 9.0})
        w2._results[it.filepath] = it
        w2.result_table.add_row(it)
    w2.result_table.table.horizontalHeader().setSortIndicator(
        1, Qt.SortOrder.DescendingOrder)
    w2.result_table.clear_rows()
    for name, score in (('a.flac', 50), ('b.flac', 90), ('c.flac', 70)):
        w2.result_table.add_row(ResultItem(
            filename=name, stem=name, filepath='C:/x/' + name,
            score=score, status=STATUS_DONE,
            detail={'meta': {}, 'cutoffs': {'-60dB': 20000.0}, 'dr': 9.0}))
    w2.scan.on_scan_finished(False)   # 内部含 apply_current_sort
    first = w2.result_table.proxy.index(0, 0).data(0)
    check('重排后首行=最高分', first == 'b.flac', first)

    print("5. 修复⑥ 换文件夹清空结果")
    w2._results.clear()
    p2 = d / 'keep.flac'
    p2.write_bytes(b'y' * 32)
    it2 = ResultItem(filename='keep.flac', stem='keep', filepath=str(p2),
                     score=80.0, status=STATUS_DONE,
                     detail={'meta': {}, 'cutoffs': {'-60dB': 1}, 'dr': 1})
    w2._results[str(p2)] = it2
    w2.result_table.add_row(it2)
    w2.set_folder(tempfile.mkdtemp(prefix='other_'))
    check('结果已清空', not w2._results)
    check('表格已清空', w2.result_table.proxy.rowCount() == 0)
    w.close(); w2.close()

    failed = [n for n, ok in PASS if not ok]
    print(f"\n结果: {len(PASS) - len(failed)}/{len(PASS)} 通过")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
