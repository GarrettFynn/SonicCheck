#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离屏渲染 M2 真实扫描结果界面截图"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from main import load_stylesheet  # noqa: E402
from main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402


def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    app = QApplication(sys.argv)
    # 离屏环境无系统字体回退，手动加载雅黑保证中文可渲染（仅测试用）
    from PyQt6.QtGui import QFontDatabase, QFont
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

    # 按评分降序展示，视觉效果更好
    window.result_table.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    app.processEvents()
    time.sleep(0.3)
    app.processEvents()

    out = ROOT / "dev" / "m2_scan_screenshot.png"
    window.grab().save(str(out))
    print(f"截图已保存: {out}")
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
