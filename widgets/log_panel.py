#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可折叠日志面板（P1）"""

from datetime import datetime

from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 8)
        lay.setSpacing(4)

        bar = QHBoxLayout()
        self.btn_toggle = QPushButton("▾ 日志", self)
        self.btn_toggle.setObjectName("LogToggle")
        self.btn_toggle.setFlat(True)
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setChecked(True)
        bar.addWidget(self.btn_toggle)
        bar.addStretch(1)
        self.btn_clear = QPushButton("清空", self)
        self.btn_clear.setObjectName("LogClear")
        self.btn_clear.setFlat(True)
        bar.addWidget(self.btn_clear)
        lay.addLayout(bar)

        self.view = QTextEdit(self)
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setMaximumHeight(160)
        # M4：防超大曲库日志撑爆内存（maximumBlockCount 在 document 上）
        self.view.document().setMaximumBlockCount(5000)
        lay.addWidget(self.view)

        self.btn_toggle.toggled.connect(self._on_toggled)
        self.btn_clear.clicked.connect(self.view.clear)

    def _on_toggled(self, checked: bool) -> None:
        self.view.setVisible(checked)
        self.btn_toggle.setText("▾ 日志" if checked else "▸ 日志")

    def log(self, message: str) -> None:
        self.view.append(f"[{datetime.now():%H:%M:%S}] {message}")

    def log_error(self, message: str) -> None:
        """标红日志（重命名失败、目标冲突等，③-3）"""
        import html
        self.view.append(
            f"[{datetime.now():%H:%M:%S}] "
            f'<span style="color:#F48771">{html.escape(message)}</span>')
