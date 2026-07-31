#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重命名预览确认对话框（两段式：先预览，确认后执行）"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                             QHeaderView, QLabel, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from core.renamer import KIND_AUDIO, KIND_LRC

COLOR_FAKE = QColor("#F44747")
COLOR_TRUE = QColor("#4EC9B0")
COLOR_SKIP = QColor("#808080")


class RenameDialog(QDialog):
    """展示重命名计划（含跳过项），用户确认后才由调用方执行"""

    def __init__(self, plan: list, skipped: list, parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle("重命名预览")
        self.resize(760, 480)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        true_cnt = sum(1 for op in plan if op.reason == "真无损"
                       and op.kind == KIND_AUDIO)
        fake_cnt = sum(1 for op in plan if op.reason == "假无损"
                       and op.kind == KIND_AUDIO)
        lrc_cnt = sum(1 for op in plan if op.kind == KIND_LRC)
        summary = QLabel(
            f"待重命名 {len(plan)} 个文件：真无损 {true_cnt} 首、"
            f"假无损 {fake_cnt} 首（含同步歌词 {lrc_cnt} 个）；"
            f"跳过 {len(skipped)} 项", self)
        summary.setObjectName("RenameSummary")
        lay.addWidget(summary)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["类型", "原文件名", "新文件名"])
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)

        for op in plan:
            self._add_row("音频" if op.kind == KIND_AUDIO else "歌词",
                          Path(op.old_path).name,
                          Path(op.new_path).name,
                          COLOR_FAKE if op.reason == "假无损" else COLOR_TRUE,
                          tooltip=f"{op.old_path}\n→ {op.new_path}")
        for sk in skipped:
            self._add_row("跳过", Path(sk.path).name, sk.why, COLOR_SKIP,
                          tooltip=sk.path)

        warn = QLabel("⚠ 确认后将实际修改文件名，失败项会在日志标红且不中断整批",
                      self)
        warn.setObjectName("RenameWarn")
        lay.addWidget(warn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            f"确认执行（{len(plan)} 项）")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _add_row(self, kind: str, old_name: str, new_name: str,
                 color: QColor, tooltip: str = "") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, text in ((0, kind), (1, old_name), (2, new_name)):
            cell = QTableWidgetItem(text)
            cell.setToolTip(tooltip)
            if col != 1:
                cell.setForeground(color)
            self.table.setItem(row, col, cell)
