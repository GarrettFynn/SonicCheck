#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结果表格：工具栏（筛选/导出CSV/一键重命名）+ 五列结果表"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from models.result_item import (STATUS_ANALYZING, STATUS_DONE, STATUS_ERROR,
                                ResultItem)

COLUMNS = ("文件名", "评分", "截止(-60dB)", "DR(dB)", "状态")

COLOR_TRUE = QColor("#4EC9B0")   # 真无损：青色
COLOR_FAKE = QColor("#F44747")   # 假无损：红色
COLOR_GRAY = QColor("#808080")   # 等待 / 分析中
COLOR_ERROR = QColor("#F48771")  # 失败：橙色

PATH_ROLE = Qt.ItemDataRole.UserRole + 1  # 第 0 列存完整路径，供按路径定位行

FILTER_ALL = "全部"
FILTER_TRUE = "只看真无损"
FILTER_FAKE = "只看假无损"


class ResultTable(QWidget):
    export_clicked = pyqtSignal()
    rename_clicked = pyqtSignal()
    compare_clicked = pyqtSignal()      # B：同名对比
    dedupe_clicked = pyqtSignal()       # C：去重清除
    restore_clicked = pyqtSignal()      # C+：去重还原（_待清除 → 移回原位）
    tag_rename_clicked = pyqtSignal()   # D：标签改名
    playlist_clicked = pyqtSignal()     # E：歌单导入
    detail_requested = pyqtSignal(str)  # A：双击行查看详情（filepath）

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # ---- 工具栏（②-1：导出 CSV 只保留此处） ----
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(QLabel("筛选:"))
        self.combo_filter = QComboBox(self)
        self.combo_filter.addItems([FILTER_ALL, FILTER_TRUE, FILTER_FAKE])
        bar.addWidget(self.combo_filter)
        bar.addStretch(1)
        self.btn_export = QPushButton("导出CSV", self)
        self.btn_export.setEnabled(False)   # M3 启用
        self.btn_rename = QPushButton("一键重命名", self)
        self.btn_rename.setEnabled(False)   # M3 启用
        bar.addWidget(self.btn_export)
        bar.addWidget(self.btn_rename)
        lay.addLayout(bar)

        # 按钮 → 对外信号（M1 遗留修复：占位期未接，M3 启用后必须接）
        self.btn_export.clicked.connect(self.export_clicked)
        self.btn_rename.clicked.connect(self.rename_clicked)

        # ---- 工具栏第二排（M4.5 新功能入口） ----
        bar2 = QHBoxLayout()
        bar2.setContentsMargins(0, 0, 0, 0)
        self.btn_compare = QPushButton("同名对比", self)
        self.btn_dedupe = QPushButton("去重清除", self)
        self.btn_restore = QPushButton("还原清除", self)
        self.btn_restore.setToolTip("把 _待清除/ 里的文件移回原来的位置")
        self.btn_tag_rename = QPushButton("标签改名", self)
        self.btn_playlist = QPushButton("歌单导入", self)
        for b in (self.btn_compare, self.btn_dedupe, self.btn_restore,
                  self.btn_tag_rename, self.btn_playlist):
            b.setEnabled(False)  # 有扫描结果后启用
            bar2.addWidget(b)
        bar2.addStretch(1)
        bar2.addWidget(QLabel("双击行查看详情", self))
        lay.addLayout(bar2)

        self.btn_compare.clicked.connect(self.compare_clicked)
        self.btn_dedupe.clicked.connect(self.dedupe_clicked)
        self.btn_restore.clicked.connect(self.restore_clicked)
        self.btn_tag_rename.clicked.connect(self.tag_rename_clicked)
        self.btn_playlist.clicked.connect(self.playlist_clicked)

        # ---- 表格（MVP 用 QTableWidget，风险表已备案：卡顿再换 Model/View） ----
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection)  # 多选
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)  # P1：表头排序
        # 右键菜单：打开所在文件夹 / 复制完整路径
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        lay.addWidget(self.table, 1)

        self.combo_filter.currentTextChanged.connect(self.apply_filter)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)

    def _on_cell_double_clicked(self, row: int, _col: int) -> None:
        cell = self.table.item(row, 0)
        if cell:
            path = cell.data(PATH_ROLE)
            if path:
                self.detail_requested.emit(path)

    # ---------------- 数据 ----------------
    def add_row(self, item: ResultItem) -> None:
        """新增一行（M2 起由扫描结果驱动）

        注意：插入期间必须临时关闭排序，否则 QTableWidget 会在 setItem
        过程中实时重排，导致同一行的各列数据错位（QTableWidget 经典坑）。
        """
        self.table.setSortingEnabled(False)
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(item.filename)
        name_item.setToolTip(item.filepath)
        name_item.setData(PATH_ROLE, item.filepath)
        self.table.setItem(row, 0, name_item)

        self._set_num(row, 1, round(item.score, 1))
        self._set_num(row, 2, int(round(item.cutoff_60db)))
        self._set_num(row, 3, round(item.dr, 1))

        status_item, kind = self._make_status_item(item)
        status_item.setData(Qt.ItemDataRole.UserRole, kind)
        self.table.setItem(row, 4, status_item)

        # 恢复排序（会按当前排序列立即重排，此时行数据已完整）
        self.table.setSortingEnabled(True)
        # 新增行需立即服从当前筛选条件
        self.apply_filter(self.combo_filter.currentText())

    def _set_num(self, row: int, col: int, value) -> None:
        """数值列：DisplayRole 存数字，保证排序按数值而非字符串"""
        cell = QTableWidgetItem()
        cell.setData(Qt.ItemDataRole.DisplayRole, value)
        cell.setTextAlignment(Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, col, cell)

    @staticmethod
    def _make_status_item(item: ResultItem):
        """状态列：色点 + 文字（②-3：不用 emoji，保证跨平台渲染一致）"""
        kind = None
        if item.status == STATUS_DONE:
            if item.is_fake:
                text, color, kind = "● 假无损", COLOR_FAKE, "fake"
            else:
                text, color, kind = "● 真无损", COLOR_TRUE, "true"
        elif item.status == STATUS_ERROR:
            text, color = "! 失败", COLOR_ERROR
        elif item.status == STATUS_ANALYZING:
            text, color = "分析中…", COLOR_GRAY
        else:  # pending
            text, color = "○ 等待", COLOR_GRAY

        cell = QTableWidgetItem(text)
        cell.setForeground(color)
        if item.fake_reasons:
            cell.setToolTip("\n".join(item.fake_reasons))  # ⑦-4：原因放 tooltip
        elif item.status == STATUS_ERROR and item.error_message:
            cell.setToolTip(item.error_message)
        return cell, kind

    def update_row_by_path(self, filepath: str, item: ResultItem) -> bool:
        """按完整路径定位并整行更新（M2 扫描回填用）。

        与 add_row 同理：更新期间必须临时关闭排序，否则 setItem 触发
        的实时重排会让同一行后续列写到错误位置。
        """
        target = -1
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell and cell.data(PATH_ROLE) == filepath:
                target = row
                break
        if target < 0:
            return False

        self.table.setSortingEnabled(False)
        name_item = QTableWidgetItem(item.filename)
        name_item.setToolTip(item.filepath)
        name_item.setData(PATH_ROLE, item.filepath)
        self.table.setItem(target, 0, name_item)
        self._set_num(target, 1, round(item.score, 1))
        self._set_num(target, 2, int(round(item.cutoff_60db)))
        self._set_num(target, 3, round(item.dr, 1))
        status_item, kind = self._make_status_item(item)
        status_item.setData(Qt.ItemDataRole.UserRole, kind)
        self.table.setItem(target, 4, status_item)
        self.table.setSortingEnabled(True)
        self.apply_filter(self.combo_filter.currentText())
        return True

    def clear_rows(self) -> None:
        self.table.setRowCount(0)

    # ---------------- 选中行 / 右键菜单（M4.6） ----------------
    def selected_paths(self) -> list:
        """当前选中行的完整路径（去重，保序）"""
        paths: list = []
        for idx in self.table.selectionModel().selectedRows():
            cell = self.table.item(idx.row(), 0)
            if cell:
                p = cell.data(PATH_ROLE)
                if p and p not in paths:
                    paths.append(p)
        return paths

    def remove_rows_by_paths(self, paths: list) -> None:
        """按路径删除行（安全模式去重=索引清除时用）"""
        targets = set(paths)
        for row in range(self.table.rowCount() - 1, -1, -1):
            cell = self.table.item(row, 0)
            if cell and cell.data(PATH_ROLE) in targets:
                self.table.removeRow(row)

    def _on_context_menu(self, pos) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act_open = menu.addAction("打开所在文件夹")
        act_copy = menu.addAction(
            f"复制完整路径（{len(paths)} 条）" if len(paths) > 1
            else "复制完整路径")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_open:
            import os
            from pathlib import Path as _P
            os.startfile(str(_P(paths[0]).parent))  # noqa: 仅 Windows 产品形态
        elif chosen is act_copy:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText("\n".join(paths))

    # ---------------- 筛选（P1） ----------------
    def apply_filter(self, text: str) -> None:
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 4)
            kind = cell.data(Qt.ItemDataRole.UserRole) if cell else None
            hide = ((text == FILTER_TRUE and kind != "true")
                    or (text == FILTER_FAKE and kind != "fake"))
            self.table.setRowHidden(row, hide)

    def set_actions_enabled(self, enabled: bool) -> None:
        self.btn_export.setEnabled(enabled)
        self.btn_rename.setEnabled(enabled)
        self.btn_compare.setEnabled(enabled)
        self.btn_dedupe.setEnabled(enabled)
        self.btn_tag_rename.setEnabled(enabled)
        self.btn_playlist.setEnabled(enabled)

    def set_restore_enabled(self, enabled: bool) -> None:
        """还原按钮单独控制：当前文件夹存在可还原的清除记录时才可用"""
        self.btn_restore.setEnabled(enabled)
