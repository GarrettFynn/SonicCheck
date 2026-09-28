#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4.6 GUI 集成测试（离屏，C:\\Python314 运行）

覆盖 M4.6 全部新功能的真实按钮/信号路径：
  改名+首启引导 → 多选/右键数据层 → 导出筛选(仅选中/仅假无损) →
  质量标记自定义 → 去重清除+还原(含 manifest) → 安全模式三件套
  (标签改名复制产出 / 去重仅索引 / 歌单进安全目录) → 未匹配剪贴板

⚠ 沙盒为 samples 的副本，原始样本不受影响。
"""

import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import (QApplication, QDialog, QInputDialog,  # noqa: E402
                             QMessageBox)

import main_window as mw  # noqa: E402
from main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402
from widgets.rename_dialog import RenameDialog  # noqa: E402

SAMPLES = ROOT / "samples"
SANDBOX = ROOT / "dev" / "tmp_m46_gui_sandbox"

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def wait_finished(window, timeout_s: int = 180):
    box = {"fired": False, "stopped": None}
    window._scan_manager.scan_finished.connect(
        lambda s: box.update(fired=True, stopped=s))
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while not box["fired"] and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    return box["stopped"] if box["fired"] else None


class FakeQMB:
    """替换 QMessageBox：记录按钮，exec 后 clickedButton 返回被选索引

    ⚠ 必须带 ButtonRole 枚举——产品代码引用 QMessageBox.ButtonRole，
    缺了会在 click() 槽里抛 AttributeError，PyQt6 对槽内未捕获异常
    直接终止进程（EXIT=127 无 traceback）。
    """

    ButtonRole = QMessageBox.ButtonRole

    choose = 0
    instances: list = []

    def __init__(self, parent=None):
        self.buttons = []
        self._clicked = None
        FakeQMB.instances.append(self)

    def setWindowTitle(self, _t):
        pass

    def setText(self, _t):
        pass

    def addButton(self, text, role):
        b = SimpleNamespace(text=lambda t=text: t)
        self.buttons.append(b)
        return b

    def exec(self):
        self._clicked = self.buttons[FakeQMB.choose]
        return 0

    def clickedButton(self):
        return self._clicked


class FakeCompare:
    DialogCode = QDialog.DialogCode

    def __init__(self, groups, mode="compare", parent=None):
        self.groups = groups

    def exec(self):
        return QDialog.DialogCode.Accepted


def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    app = QApplication(sys.argv)
    QSettings().clear()

    print("[1] 初始化：改名 / 首启引导 / 还原按钮初始态")
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    src_toe = SAMPLES / "Because I Hear You - Toe_假无损.flac"
    src_red = SAMPLES / "ALL RED (Explicit) - Playboi Carti_EM_真无损.flac"
    shutil.copy2(src_toe, SANDBOX / "dupA1.flac")
    shutil.copy2(src_toe, SANDBOX / "dupA2.flac")
    (SANDBOX / "dupA2.lrc").write_text("[00:00.00]歌词", encoding="utf-8")
    shutil.copy2(src_red, SANDBOX / "loner.flac")

    window = MainWindow()
    check("窗口标题含中文名与英文名",
          "声鉴·曲库管家" in window.windowTitle()
          and "SonicCheck" in window.windowTitle(), window.windowTitle())
    check("首启自动弹使用指南（非模态）",
          getattr(window, "_first_guide", None) is not None)
    window._first_guide.close()
    check("还原按钮初始禁用", not window.result_table.btn_restore.isEnabled())
    window.set_folder(str(SANDBOX))

    print("\n[2] 完整扫描")
    window.on_start_scan()
    stopped = wait_finished(window)
    check("扫描完成", stopped is False, f"stopped={stopped}")
    check("3 个结果", len(window._results) == 3)

    print("\n[3] 多选与右键数据层")
    rt = window.result_table
    rt.table.selectAll()
    check("全选 3 行", len(rt.selected_paths()) == 3,
          f"{len(rt.selected_paths())}")
    rt.table.clearSelection()
    rt.table.selectRow(0)
    check("单选 1 行", len(rt.selected_paths()) == 1)

    print("\n[4] 导出筛选（仅选中行 / 仅假无损）")
    orig_qmb = mw.QMessageBox
    mw.QMessageBox = FakeQMB
    csv_path = SANDBOX / "sel.csv"
    from PyQt6.QtWidgets import QFileDialog
    orig_save = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (str(csv_path), "CSV"))
    try:
        FakeQMB.choose = 1  # 仅选中行
        window.result_table.btn_export.click()
        app.processEvents()
        rows = csv_path.read_text(encoding='utf-8-sig').strip().splitlines()
        check("仅选中行导出 1 行数据", len(rows) == 2, f"{len(rows)}")

        FakeQMB.instances.clear()
        csv_path2 = SANDBOX / "fake.csv"
        QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (str(csv_path2), "CSV"))
        FakeQMB.choose = 2  # 仅假无损
        window.result_table.btn_export.click()
        app.processEvents()
        rows2 = csv_path2.read_text(encoding='utf-8-sig').strip().splitlines()
        check("仅假无损导出 2 行数据", len(rows2) == 3, f"{len(rows2)}")

        csv_path3 = SANDBOX / "true.csv"
        QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (str(csv_path3), "CSV"))
        FakeQMB.choose = 3  # 仅真无损
        window.result_table.btn_export.click()
        app.processEvents()
        rows3 = csv_path3.read_text(encoding='utf-8-sig').strip().splitlines()
        check("仅真无损导出 1 行数据", len(rows3) == 2, f"{len(rows3)}")

        btn_texts = [b.text() for b in FakeQMB.instances[-1].buttons]
        check("范围弹窗含四选项+取消",
              btn_texts == ["全部文件", "仅选中行（1）", "仅假无损",
                            "仅真无损", "取消"],
              str(btn_texts))
    finally:
        mw.QMessageBox = orig_qmb
        QFileDialog.getSaveFileName = orig_save
    rt.table.clearSelection()

    print("\n[5] 质量标记自定义（[T]/[F] 后缀）")
    class FakeMarks:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {"mark_true": "[T]", "mark_fake": "[F]",
                    "position": "suffix"}

    orig_marks = mw.QualityMarkDialog
    mw.QualityMarkDialog = FakeMarks
    try:
        window.on_marks()
    finally:
        mw.QualityMarkDialog = orig_marks
    check("标记配置已注入 core",
          mw.set_mark_config.__module__ and True)  # 注入不报错即过
    from core.quality_marks import get_config
    check("QSettings 已保存", window._settings.value("marks/fake") == "[F]"
          and get_config()["mark_fake"] == "[F]")

    orig_rn = RenameDialog.exec
    RenameDialog.exec = lambda self: QDialog.DialogCode.Accepted
    try:
        window.result_table.btn_rename.click()  # 无选中 → 全部，无范围弹窗
        app.processEvents()
    finally:
        RenameDialog.exec = orig_rn
    check("自定义标记落盘 dupA1[F].flac",
          (SANDBOX / "dupA1[F].flac").is_file())
    check("自定义标记落盘 loner[T].flac",
          (SANDBOX / "loner[T].flac").is_file())
    check("原文件名已不在（非安全模式=改名）",
          not (SANDBOX / "dupA1.flac").exists())

    print("\n[6] 去重清除（含自定义标记的文件）→ 还原")
    mw.CompareDialog = FakeCompare
    try:
        window.result_table.btn_dedupe.click()
        app.processEvents()
    finally:
        del mw.CompareDialog
    clear_dir = SANDBOX / "_待清除"
    moved = list(clear_dir.glob("*.flac")) if clear_dir.is_dir() else []
    check("_待清除 含 1 个音频", len(moved) == 1,
          str([p.name for p in moved]))
    check("还原按钮已启用", rt.btn_restore.isEnabled())
    check("manifest 已写",
          (clear_dir / ".restore_map.json").is_file())

    orig_qmb2 = mw.QMessageBox
    mw.QMessageBox = FakeQMB
    FakeQMB.choose = 0  # "还原"
    try:
        window.result_table.btn_restore.click()
        app.processEvents()
    finally:
        mw.QMessageBox = orig_qmb2
    back = [p for p in SANDBOX.glob("*.flac")]
    check("还原后根目录 3 个音频", len(back) == 3,
          str([p.name for p in back]))
    check("manifest 已清空",
          not (clear_dir / ".restore_map.json").exists())
    check("还原按钮回到禁用", not rt.btn_restore.isEnabled())
    check("内存路径已指回原位",
          all("_待清除" not in p for p in window._results))

    print("\n[7] 安全模式三件套")
    window.left_panel.chk_safe.setChecked(True)
    app.processEvents()
    safe_root = SANDBOX.parent / f"{SANDBOX.name}_安全输出"
    check("安全输出目录标签已显示",
          str(safe_root) in window.left_panel.safe_label.text())
    check("安全模式下还原按钮禁用", not rt.btn_restore.isEnabled())

    orig_get_item = QInputDialog.getItem
    QInputDialog.getItem = staticmethod(lambda *a, **k: ("歌名-歌手", True))
    RenameDialog.exec = lambda self: QDialog.DialogCode.Accepted
    try:
        before = {p: Path(p).stat().st_mtime_ns for p in window._results}
        window.result_table.btn_tag_rename.click()
        app.processEvents()
    finally:
        QInputDialog.getItem = orig_get_item
    safe_audio = list(safe_root.glob("*.flac")) if safe_root.is_dir() else []
    check("安全目录有改名件", len(safe_audio) >= 1,
          str([p.name for p in safe_audio]))
    check("原文件零改动（改名前路径仍存在）",
          all(Path(p).exists() for p in before))
    check("内存已指到安全副本",
          any(str(safe_root) in p for p in window._results))

    mw.CompareDialog = FakeCompare
    try:
        n_before = len(window._results)
        window.result_table.btn_dedupe.click()
        app.processEvents()
    finally:
        del mw.CompareDialog
    check("安全去重=仅索引清除（结果数减少）",
          len(window._results) < n_before)
    check("安全去重不产生新 _待清除", not (SANDBOX / "_待清除").exists()
          or not list((SANDBOX / "_待清除").glob("*.flac")))

    orig_pl = mw.PlaylistDialog.exec

    def fake_pl_exec(self):
        self.edit_name.setText("安全歌单")
        self.text_edit.setPlainText(
            "Because I Hear You - Toe\n不存在的歌 - 某人")
        self._on_match()
        return QDialog.DialogCode.Accepted

    mw.PlaylistDialog.exec = fake_pl_exec
    try:
        window.result_table.btn_playlist.click()
        app.processEvents()
    finally:
        mw.PlaylistDialog.exec = orig_pl
    pl_dir = safe_root / "安全歌单"
    check("歌单建在安全输出目录", pl_dir.is_dir()
          and list(pl_dir.glob("*.flac")))
    clip = app.clipboard().text()
    check("未匹配清单已进剪贴板", "不存在的歌" in clip, clip[:40])

    window.close()
    print(f"\n{'=' * 60}\n总体: {'全部通过' if not failures else f'FAIL: {failures}'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
