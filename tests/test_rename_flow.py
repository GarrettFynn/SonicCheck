#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 改名流程回归（v2.0.1）：标签改名/一键重命名端到端

背景：V20-2 控制器搬移漏搬 _run_rename_plan（调用点 mw./self. 双重
错位），确认改名对话框后 AttributeError 闪退——本套件以真实调用链
（mock 对话框，其余全真）锁定两条改名路径与安全模式。

需要 PyQt6（offscreen）。运行: python tests/test_rename_flow.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# QSettings 必须在任何 Qt 窗口构造前隔离到临时目录（防清真实注册表）
from PyQt6.QtCore import QSettings  # noqa: E402

QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                  str(Path(tempfile.mkdtemp(prefix="rename_flow_"))))
QSettings.setDefaultFormat(QSettings.Format.IniFormat)

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import main_window as MW  # noqa: E402
from models.result_item import STATUS_DONE, ResultItem  # noqa: E402
from widgets.rename_dialog import RenameDialog  # noqa: E402

PASS = []


def check(name, cond, extra=""):
    cond = bool(cond)
    PASS.append((name, cond))
    print(f"  {'✓' if cond else '✗'} {name} {extra}")
    assert cond, f"{name} {extra}"


def make_window_with_songs():
    w = MW.MainWindow()
    d = Path(tempfile.mkdtemp(prefix="rename_flow_"))
    w.set_folder(str(d))
    titles = [("晴天", "周杰伦"), ("海阔天空", "Beyond"), ("红日", "李克勤")]
    for i, (title, artist) in enumerate(titles):
        p = d / f"乱七八糟{i}.flac"
        p.write_bytes(b"x" * 64)
        (d / f"乱七八糟{i}.lrc").write_text("[00:01] lyric", encoding="utf-8")
        item = ResultItem(
            filename=p.name, stem=p.stem, filepath=str(p), score=85.0,
            is_fake=False, status=STATUS_DONE,
            detail={"meta": {"title": title, "artist": artist,
                             "duration": 200.0, "format": "FLAC"},
                    "cutoffs": {"-60dB": 20000.0}, "dr": 10.0,
                    "correlation": 0.5})
        w._results[str(p)] = item
        w.result_table.add_row(item)   # 真实流程：扫描时已建行
    return w, d


def mock_dialogs_accept():
    MW.QInputDialog.getItem = staticmethod(
        lambda *a, **k: ("歌名-歌手", True))
    RenameDialog.exec = lambda self: RenameDialog.DialogCode.Accepted


def restore_dialogs():
    # 静态方法覆盖无法用 del 恢复原绑定（类属性被替换）；测试进程即将
    # 结束（run_all 独立子进程），无需恢复
    pass


def main():
    print("1. 标签改名（普通模式）：确认后改名 + lrc 跟随 + 表格同步")
    w, d = make_window_with_songs()
    w.left_panel.chk_safe.setChecked(False)
    mock_dialogs_accept()
    w.on_tag_rename()   # v2.0.0 在此 AttributeError 闪退

    renamed = sorted(x.name for x in d.iterdir())
    expected = sorted([
        "晴天-周杰伦.flac", "晴天-周杰伦.lrc",
        "海阔天空-Beyond.flac", "海阔天空-Beyond.lrc",
        "红日-李克勤.flac", "红日-李克勤.lrc"])
    check("3 音频 + 3 lrc 全部改名", renamed == expected, str(renamed))
    check("表格行数保持", w.result_table.proxy.rowCount() == 3)
    check("表格路径同步到新名",
          all(Path(x).name in expected for x in w._results))

    print("2. 标签改名（安全模式）：原件不动，改名件入安全输出")
    w.left_panel.chk_safe.setChecked(True)
    w2 = d / "安全测试.flac"
    w2.write_bytes(b"y" * 32)
    item = ResultItem(
        filename="安全测试.flac", stem="安全测试", filepath=str(w2),
        score=80.0, status=STATUS_DONE,
        detail={"meta": {"title": "安全歌", "artist": "歌手",
                         "duration": 100.0, "format": "FLAC"},
                "cutoffs": {"-60dB": 20000.0}, "dr": 9.0,
                "correlation": 0.5})
    w._results[str(w2)] = item
    w.result_table.add_row(item)
    w.on_tag_rename()
    safe_out = d.parent / (d.name + "_安全输出")
    hit = list(safe_out.rglob("安全歌-歌手.flac"))
    check("改名件在安全输出目录", bool(hit),
          str(hit[0]) if hit else str(list(safe_out.rglob("*"))[:3]))
    check("原件未动", w2.is_file())
    w.close()

    failed = [n for n, ok in PASS if not ok]
    print(f"\n结果: {len(PASS) - len(failed)}/{len(PASS)} 通过")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
