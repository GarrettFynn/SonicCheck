#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离屏渲染 M3 重命名预览对话框实拍图（用 samples 扫描结果建真实计划）"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtGui import QFont, QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.renamer import build_rename_plan  # noqa: E402
from main import load_stylesheet  # noqa: E402
from main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402
from widgets.rename_dialog import RenameDialog  # noqa: E402


def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    app = QApplication(sys.argv)
    QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
    app.setFont(QFont("Microsoft YaHei", 9))
    load_stylesheet(app)
    QSettings().clear()

    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    window.set_folder(str(ROOT / "samples"))
    window.on_start_scan()

    done = {"ok": False}
    window._scan_manager.scan_finished.connect(lambda s: done.update(ok=True))
    deadline = time.monotonic() + 120
    while not done["ok"] and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)

    plan, skipped = build_rename_plan(list(window._results.values()))
    dialog = RenameDialog(plan, skipped, window)
    dialog.show()
    app.processEvents()
    time.sleep(0.3)
    app.processEvents()

    out = ROOT / "dev" / "m3_rename_dialog.png"
    dialog.grab().save(str(out))
    print(f"截图已保存: {out}")
    dialog.close()
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
