#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4.5 GUI 集成测试（离屏，C:\\Python314 运行）

端到端覆盖二期功能批 A~F：
  A 双击行 → 详情对话框（DetailDialog 构造参数正确）
  B 同名对比 → CompareDialog(MODE_COMPARE) 收到分组
  E 歌单导入 → 粘贴文本 → 解析匹配 → 复制落盘 + 未匹配清单.txt
  D 标签改名 → QInputDialog 选"歌名-歌手" → 预览确认 → 落盘
  C 去重清除 → CompareDialog(MODE_DEDUPE) 确认 → 移入 _待清除/ + lrc 同步

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

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog  # noqa: E402

import main_window as mw  # noqa: E402
from main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402
from widgets.rename_dialog import RenameDialog  # noqa: E402

SAMPLES = ROOT / "samples"
SANDBOX = ROOT / "dev" / "tmp_m45_gui_sandbox"

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


def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    app = QApplication(sys.argv)
    QSettings().clear()

    print("[1] 搭沙盒（2 个同标签重复 + 1 个单曲）")
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    src_toe = SAMPLES / "Because I Hear You - Toe_假无损.flac"
    src_red = SAMPLES / "ALL RED (Explicit) - Playboi Carti_EM_真无损.flac"
    shutil.copy2(src_toe, SANDBOX / "dupA1.flac")
    shutil.copy2(src_toe, SANDBOX / "完全不同的名字.flac")
    (SANDBOX / "完全不同的名字.lrc").write_text(
        "[00:00.00]歌词", encoding="utf-8")
    shutil.copy2(src_red, SANDBOX / "loner.flac")

    window = MainWindow()
    window.set_folder(str(SANDBOX))

    print("\n[2] 完整扫描")
    window.on_start_scan()
    stopped = wait_finished(window)
    check("扫描完成", stopped is False, f"stopped={stopped}")
    check("3 个结果", len(window._results) == 3,
          f"{len(window._results)}")
    rt = window.result_table
    check("第二排 4 按钮已启用",
          rt.btn_compare.isEnabled() and rt.btn_dedupe.isEnabled()
          and rt.btn_tag_rename.isEnabled() and rt.btn_playlist.isEnabled())

    print("\n[3] A 双击行 → 详情对话框")
    captured = {}

    class FakeDetail:
        def __init__(self, item, parent=None):
            captured["item"] = item

        def exec(self):
            return QDialog.DialogCode.Rejected

    orig_detail = mw.DetailDialog
    mw.DetailDialog = FakeDetail
    try:
        target = next(p for p in window._results if p.endswith("loner.flac"))
        rt.detail_requested.emit(target)  # 双击行的信号路径
        app.processEvents()
    finally:
        mw.DetailDialog = orig_detail
    check("DetailDialog 收到正确 item",
          captured.get("item") is not None
          and captured["item"].filename == "loner.flac")

    print("\n[4] B 同名对比（仅查看，不处置）")
    cmp_cap = {}

    class FakeCompare:
        DialogCode = QDialog.DialogCode  # on_dedupe 里要做 DialogCode 比较

        def __init__(self, groups, mode="compare", parent=None):
            cmp_cap["groups"] = groups
            cmp_cap["mode"] = mode

        def exec(self):
            return cmp_cap.get("ret", QDialog.DialogCode.Rejected)

    orig_compare = mw.CompareDialog
    mw.CompareDialog = FakeCompare
    try:
        rt.btn_compare.click()
        app.processEvents()
    finally:
        mw.CompareDialog = orig_compare
    groups = cmp_cap.get("groups") or []
    check("CompareDialog 收到 1 个重复组", len(groups) == 1,
          f"{len(groups)}")
    check("对比模式 MODE_COMPARE", cmp_cap.get("mode") == "compare")
    if groups:
        names = {Path(m.filepath).name for m in groups[0].items}
        check("组成员正确", names == {"dupA1.flac", "完全不同的名字.flac"},
              str(names))

    print("\n[5] E 歌单导入（粘贴文本 → 匹配 → 复制落盘）")
    orig_pl_exec = mw.PlaylistDialog.exec

    def fake_pl_exec(self):
        self.edit_name.setText("我的歌单")
        self.text_edit.setPlainText(
            "Because I Hear You - Toe\n不存在的歌 - 某人")
        self._on_match()
        return QDialog.DialogCode.Accepted

    mw.PlaylistDialog.exec = fake_pl_exec
    try:
        rt.btn_playlist.click()
        app.processEvents()
    finally:
        mw.PlaylistDialog.exec = orig_pl_exec

    pl_dir = SANDBOX / "我的歌单"
    pl_audio = list(pl_dir.glob("*.flac")) if pl_dir.is_dir() else []
    miss_file = pl_dir / "未匹配清单.txt"
    check("歌单文件夹已建且有音频", len(pl_audio) >= 1,
          f"{len(pl_audio)} 个")
    check("原件未动（复制非移动）",
          (SANDBOX / "dupA1.flac").is_file())
    check("未匹配清单落盘且含缺失条目",
          miss_file.is_file()
          and "不存在的歌" in miss_file.read_text(encoding="utf-8"))

    print("\n[6] D 标签改名（歌名-歌手，预览自动确认）")
    orig_get_item = QInputDialog.getItem
    orig_rn_exec = RenameDialog.exec
    QInputDialog.getItem = staticmethod(
        lambda *a, **k: ("歌名-歌手", True))
    RenameDialog.exec = lambda self: QDialog.DialogCode.Accepted
    try:
        rt.btn_tag_rename.click()
        app.processEvents()
    finally:
        QInputDialog.getItem = orig_get_item
        RenameDialog.exec = orig_rn_exec

    check("落盘: Because I Hear You-Toe.flac",
          (SANDBOX / "Because I Hear You-Toe.flac").is_file())
    check("落盘: ALL RED-Playboi Carti.flac",
          (SANDBOX / "ALL RED-Playboi Carti.flac").is_file())
    check("撞名跳过: 完全不同的名字.flac 仍在",
          (SANDBOX / "完全不同的名字.flac").is_file())
    check("内存结果路径已同步",
          any(p.endswith("Because I Hear You-Toe.flac")
              for p in window._results))

    print("\n[7] C 去重清除（确认 → 移入 _待清除/）")
    mw.CompareDialog = FakeCompare
    cmp_cap["ret"] = QDialog.DialogCode.Accepted
    try:
        rt.btn_dedupe.click()
        app.processEvents()
    finally:
        mw.CompareDialog = orig_compare

    clear_dir = SANDBOX / "_待清除"
    moved_audio = list(clear_dir.glob("*.flac")) if clear_dir.is_dir() else []
    check("_待清除 含 1 个音频", len(moved_audio) == 1,
          str([p.name for p in moved_audio]))
    root_flacs = [p for p in SANDBOX.glob("*.flac")]
    check("无文件丢失（根 2 + 清除 1 = 3）",
          len(root_flacs) == 2 and len(moved_audio) == 1,
          f"root={len(root_flacs)} moved={len(moved_audio)}")
    if moved_audio and moved_audio[0].name == "完全不同的名字.flac":
        check("lrc 同步移入 _待清除",
              (clear_dir / "完全不同的名字.lrc").is_file())
    else:
        check("被跳过的文件 lrc 不被误改（留在原处）",
              (SANDBOX / "完全不同的名字.lrc").is_file())
    check("dupA1/loner 未被误移",
          not (clear_dir / "ALL RED-Playboi Carti.flac").exists()
          if clear_dir.is_dir() else False)

    print("\n[8] 清除后再扫描 → _待清除 不进结果；歌单副本会被扫到（预期行为）")
    window.on_start_scan()
    stopped = wait_finished(window)
    check("再扫描完成", stopped is False, f"stopped={stopped}")
    bad = [p for p in window._results if "_待清除" in p]
    check("结果不含 _待清除", not bad, str(bad))
    in_playlist = [p for p in window._results if "我的歌单" in p]
    check("歌单副本被扫到（复制非隐藏，符合扫描语义）",
          len(in_playlist) == 1, str(len(in_playlist)))
    check("再扫描 3 个结果", len(window._results) == 3,
          f"{len(window._results)}")

    window.close()
    print(f"\n{'=' * 60}\n总体: {'全部通过' if not failures else f'FAIL: {failures}'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
