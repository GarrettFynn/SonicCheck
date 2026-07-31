#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重复组对比对话框（功能 B 同名对比 / 功能 C 去重清除预览）

布局：左侧组列表，右侧"指标 × 文件"对比表（每列一个文件）。
mode='compare'：纯对比，关闭按钮。
mode='dedupe'：表尾加"处置"行（首列 保留=绿，其余 待清除=红），
确认按钮文案为"移动到 _待清除（N 项）"。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                             QHeaderView, QLabel, QListWidget,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

COLOR_KEEP = QColor("#4EC9B0")
COLOR_CLEAR = QColor("#F44747")

METRIC_ROWS = [
    ("评分", lambda i: f"{i.score}"),
    ("判定", lambda i: "假无损" if i.is_fake else "真无损"),
    ("格式", lambda i: i.detail['meta'].get('format', '?')),
    ("采样率", lambda i: f"{i.detail['meta'].get('sample_rate', 0)} Hz"),
    ("位深度", lambda i: f"{i.detail['meta'].get('bit_depth', 0)} bit"),
    ("时长", lambda i: f"{i.detail['meta'].get('duration', 0):.1f} s"),
    ("-60dB 截止", lambda i: f"{i.detail['cutoffs']['-60dB']:.0f} Hz"),
    ("DR", lambda i: f"{i.detail['dr']:.1f} dB"),
    ("立体声相关", lambda i: f"{i.detail['correlation']:.3f}"),
    ("文件路径", lambda i: i.filepath),
]

MODE_COMPARE = "compare"
MODE_DEDUPE = "dedupe"


class CompareDialog(QDialog):
    def __init__(self, groups: list, mode: str = MODE_COMPARE,
                 parent: QWidget = None):
        super().__init__(parent)
        self._groups = groups
        self._mode = mode
        self.setWindowTitle("同名对比" if mode == MODE_COMPARE
                            else "去重清除预览")
        self.resize(860, 520)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        self.group_list = QListWidget(self)
        self.group_list.setFixedWidth(220)
        for g in groups:
            self.group_list.addItem(f"{g.label}（{len(g.items)} 个）")
        lay.addWidget(self.group_list)

        right = QVBoxLayout()
        self.hint = QLabel(self)
        right.addWidget(self.hint)
        self.table = QTableWidget(self)
        right.addWidget(self.table, 1)

        buttons = QDialogButtonBox(self)
        clear_cnt = sum(len(g.clear) for g in groups)
        if mode == MODE_DEDUPE:
            ok = buttons.addButton(f"移动到 _待清除（{clear_cnt} 项）",
                                   QDialogButtonBox.ButtonRole.AcceptRole)
            ok.clicked.connect(self.accept)
            no = buttons.addButton("取消",
                                   QDialogButtonBox.ButtonRole.RejectRole)
            no.clicked.connect(self.reject)
        else:
            no = buttons.addButton("关闭",
                                   QDialogButtonBox.ButtonRole.RejectRole)
            no.clicked.connect(self.reject)
        right.addWidget(buttons)
        lay.addLayout(right, 1)

        self.group_list.currentRowChanged.connect(self._show_group)
        if mode == MODE_DEDUPE:
            # 点击"处置"行可手动改保留项（默认最高分）
            self.table.cellClicked.connect(self._on_cell_clicked)
        if groups:
            self.group_list.setCurrentRow(0)

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if col < 1 or row != self.table.rowCount() - 1:
            return  # 只有处置行的文件列可点
        idx = self.group_list.currentRow()
        if idx < 0:
            return
        g = self._groups[idx]
        chosen = g.items[col - 1]
        if g.keep is chosen:
            return
        g.keep_override = chosen
        self._show_group(idx)  # 重绘处置行

    def _show_group(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._groups):
            return
        g = self._groups[idx]
        files = g.items
        rows = METRIC_ROWS + ([("处置", None)] if self._mode == MODE_DEDUPE
                              else [])
        keep = g.keep
        self.hint.setText(
            f"组「{g.label}」共 {len(files)} 个文件，按评分降序"
            + ("；绿色为保留项，点击「处置」行可改" if self._mode == MODE_DEDUPE
               else ""))

        self.table.setRowCount(len(rows))
        self.table.setColumnCount(len(files) + 1)
        self.table.setHorizontalHeaderLabels(["指标"] +
                                             [f.filename for f in files])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for r, (name, getter) in enumerate(rows):
            head = QTableWidgetItem(name)
            self.table.setItem(r, 0, head)
            for c, item in enumerate(files, start=1):
                if getter is not None:
                    cell = QTableWidgetItem(getter(item))
                    cell.setToolTip(item.filepath)
                else:  # 处置行：绿色=保留（点击可改），红色=待清除
                    if item is keep:
                        cell = QTableWidgetItem("● 保留（点击可改）")
                        cell.setForeground(COLOR_KEEP)
                    else:
                        cell = QTableWidgetItem("○ 待清除（点此改为保留）")
                        cell.setForeground(COLOR_CLEAR)
                self.table.setItem(r, c, cell)

        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(1, len(files) + 1):
            self.table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.Stretch)
