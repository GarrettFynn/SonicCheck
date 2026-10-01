#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结果表格：工具栏（筛选/搜索/导出CSV/一键重命名）+ 五列结果表"""

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

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
        # V13-3：文本搜索（文件名/歌名/歌手标签子串，与真假筛选叠加）
        self.edit_search = QLineEdit(self)
        self.edit_search.setPlaceholderText("搜索文件名/歌名/歌手…")
        self.edit_search.setClearButtonEnabled(True)
        self.edit_search.setMaximumWidth(240)
        bar.addWidget(self.edit_search, 1)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)  # 防抖：万行表全遍历不宜每击键
        self._search_timer.timeout.connect(
            lambda: self.apply_filter(self.combo_filter.currentText()))
        self.edit_search.textChanged.connect(
            lambda: self._search_timer.start())
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

        # 批量模式状态：扫描期间排序/过滤统一推迟到 end_update（B1）
        self._batch = False
        self._row_by_path: dict = {}   # 批量期间 filepath → 行号（行序稳定）
        # V13-3：filepath → meta（搜索用标签歌名/歌手），add_row 时维护
        self._meta_of_row: dict = {}

    def _on_cell_double_clicked(self, row: int, _col: int) -> None:
        cell = self.table.item(row, 0)
        if cell:
            path = cell.data(PATH_ROLE)
            if path:
                self.detail_requested.emit(path)

    # ---------------- 批量模式（B1：万级曲库插行/回填去 O(n²)） ----------------
    def begin_update(self) -> None:
        """进入批量模式：扫描开始前调用一次，end_update 收尾。

        旧行为：每插一行都「关排序→填行→开排序（全表重排）→全表过滤」，
        万级行是 O(n² log n)，UI 冻结分钟级。批量模式下排序与过滤推迟
        到 end_update 一次完成，插行/回填降为 O(n)；批量期间排序关闭、
        行序稳定，update_row_by_path 的路径→行号映射才可靠。
        重绘保持开启：扫描中结果行仍逐条渐进显示。
        """
        self._batch = True
        self._row_by_path.clear()
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionsClickable(False)

    def end_update(self) -> None:
        """退出批量模式：扫描结束（含停止）时调用，恢复排序/过滤。"""
        if not self._batch:
            return
        self._batch = False
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.setSortingEnabled(True)   # 按当前排序列重排一次
        self._row_by_path.clear()
        self.apply_filter(self.combo_filter.currentText())

    # ---------------- 数据 ----------------
    def _fill_row(self, row: int, item: ResultItem):
        """整行写入五列（add_row / update_row_by_path 共用）。

        调用方负责保证此刻排序处于关闭状态——否则 setItem 触发实时
        重排，同一行的后续列会写到错误位置（QTableWidget 经典坑）。
        """
        name_item = QTableWidgetItem(item.filename)
        name_item.setToolTip(item.filepath)
        name_item.setData(PATH_ROLE, item.filepath)
        self.table.setItem(row, 0, name_item)
        self._meta_of_row[item.filepath] = item.detail.get('meta', {})

        self._set_num(row, 1, round(item.score, 1))
        self._set_num(row, 2, int(round(item.cutoff_60db)))
        self._set_num(row, 3, round(item.dr, 1))

        status_item, kind = self._make_status_item(item)
        status_item.setData(Qt.ItemDataRole.UserRole, kind)
        self.table.setItem(row, 4, status_item)
        return kind

    def _row_hidden(self, kind) -> bool:
        """按当前筛选决定单行可见性（O(1)，批量模式下替代全表过滤）"""
        text = self.combo_filter.currentText()
        return ((text == FILTER_TRUE and kind != "true")
                or (text == FILTER_FAKE and kind != "fake"))

    def add_row(self, item: ResultItem) -> None:
        """新增一行（M2 起由扫描结果驱动）"""
        if self._batch:
            row = self.table.rowCount()
            self.table.insertRow(row)
            kind = self._fill_row(row, item)
            self._row_by_path[item.filepath] = row
            self.table.setRowHidden(row, self._row_hidden(kind))
            return
        self.table.setSortingEnabled(False)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._fill_row(row, item)
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

        批量模式下行序稳定，走 _row_by_path O(1) 查表（万级曲库下旧的
        全表线性查找每文件被 started/done 各调一次，约 2×n² 次取单元格）；
        非批量（重命名等单点更新）回退线性查找。
        """
        target = self._row_by_path.get(filepath, -1)
        if target >= 0:
            cell = self.table.item(target, 0)
            if not cell or cell.data(PATH_ROLE) != filepath:
                target = -1  # 映射失效（防御），回退线性查找
        if target < 0:
            for row in range(self.table.rowCount()):
                cell = self.table.item(row, 0)
                if cell and cell.data(PATH_ROLE) == filepath:
                    target = row
                    break
            if target < 0:
                return False

        if self._batch:
            kind = self._fill_row(target, item)
            self.table.setRowHidden(target, self._row_hidden(kind))
            return True
        self.table.setSortingEnabled(False)
        self._fill_row(target, item)
        self.table.setSortingEnabled(True)
        self.apply_filter(self.combo_filter.currentText())
        return True

    def clear_rows(self) -> None:
        self._row_by_path.clear()
        self._meta_of_row.clear()
        self.table.setRowCount(0)

    # ---------------- 列宽/排序状态记忆（V13-3） ----------------
    def save_header_state(self) -> bytes:
        return self.table.horizontalHeader().saveState()

    def restore_header_state(self, state: bytes) -> bool:
        if not state:
            return False
        return self.table.horizontalHeader().restoreState(state)

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

    def mark_unfinished_cancelled(self) -> int:
        """扫描停止后，把仍为「等待 / 分析中…」的行标记为「已取消」。
        返回标记数量（自然完成时没有任何未完结行，返回 0）。"""
        count = 0
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 4)
            if cell and cell.text() in ("○ 等待", "分析中…"):
                new_cell = QTableWidgetItem("已取消")
                new_cell.setForeground(COLOR_GRAY)
                new_cell.setData(Qt.ItemDataRole.UserRole, None)
                self.table.setItem(row, 4, new_cell)
                count += 1
        return count

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
        # V13-3：按右键首行的标签/扩展名快速筛选
        act_artist = act_fmt = None
        first_path = paths[0]
        meta = self._meta_of_row.get(first_path, {})
        artist = (meta.get('artist') or '').strip()
        if artist:
            act_artist = menu.addAction(f"只看此歌手: {artist[:16]}")
        ext = Path(first_path).suffix.lower().lstrip('.')
        if ext:
            act_fmt = menu.addAction(f"只看此格式: {ext.upper()}")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_open:
            import os
            os.startfile(str(Path(paths[0]).parent))  # noqa: 仅 Windows 产品形态
        elif chosen is act_copy:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText("\n".join(paths))
        elif chosen is act_artist and artist:
            self.edit_search.setText(artist)
        elif chosen is act_fmt and ext:
            self.edit_search.setText(f".{ext}")

    # ---------------- 筛选（P1；V13-3 扩展文本搜索） ----------------
    def apply_filter(self, text: str) -> None:
        needle = self.edit_search.text().strip().lower()
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 4)
            kind = cell.data(Qt.ItemDataRole.UserRole) if cell else None
            hide = ((text == FILTER_TRUE and kind != "true")
                    or (text == FILTER_FAKE and kind != "fake"))
            if not hide and needle:
                hide = not self._row_matches_search(row, needle)
            self.table.setRowHidden(row, hide)

    def _row_matches_search(self, row: int, needle: str) -> bool:
        """文件名（含完整路径）/标签歌名/歌手任一子串命中"""
        name_cell = self.table.item(row, 0)
        if name_cell:
            if needle in name_cell.text().lower():
                return True
            meta = self._meta_of_row.get(name_cell.data(PATH_ROLE) or '', {})
            hay = f"{meta.get('title', '')} {meta.get('artist', '')}".lower()
            if needle in hay:
                return True
        return False

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
