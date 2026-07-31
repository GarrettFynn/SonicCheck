#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进度区：进度条 + 当前分析文件名"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget


class ProgressWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        row = QHBoxLayout()
        row.addWidget(QLabel("进度:"))
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("待命")
        row.addWidget(self.bar, 1)
        lay.addLayout(row)

        self.current_label = QLabel("当前: —", self)
        self.current_label.setObjectName("CurrentFileLabel")
        lay.addWidget(self.current_label)

    def set_progress(self, current: int, total: int) -> None:
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(current)
        self.bar.setFormat("%p% (%v/%m)")

    def set_current_file(self, name: str) -> None:
        fm = self.current_label.fontMetrics()
        text = fm.elidedText(f"当前: {name}", Qt.TextElideMode.ElideMiddle,
                             self.current_label.width() or 600)
        self.current_label.setText(text)
        self.current_label.setToolTip(name)

    def reset(self) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("待命")
        self.current_label.setText("当前: —")
        self.current_label.setToolTip("")
