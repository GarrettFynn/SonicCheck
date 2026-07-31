#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SonicCheck 入口：创建 QApplication 与主窗口"""

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from main_window import (APP_NAME, APP_VERSION, ORG_NAME, MainWindow,
                         resource_path)


def load_stylesheet(app: QApplication) -> None:
    qss = Path(resource_path("resources/style.qss"))
    if qss.exists():
        app.setStyleSheet(qss.read_text(encoding="utf-8"))


def main() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    QApplication.setApplicationVersion(APP_VERSION)

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("resources/icon.png")))
    load_stylesheet(app)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
