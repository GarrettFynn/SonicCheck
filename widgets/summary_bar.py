#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""底部汇总统计栏"""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel


class SummaryBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SummaryBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        self.label = QLabel("汇总: 尚未开始扫描", self)
        lay.addWidget(self.label)
        lay.addStretch(1)

    def update_stats(self, total: int, fake_count: int, avg_score: float) -> None:
        if total <= 0:  # 防除零
            self.label.setText("汇总: 共 0 首")
            return
        pct = fake_count / total * 100
        self.label.setText(
            f"汇总: 共 {total} 首 | 假无损 {fake_count} 首 ({pct:.1f}%)"
            f" | 均分 {avg_score:.1f}")

    def reset(self) -> None:
        self.label.setText("汇总: 尚未开始扫描")
