#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结果表格：工具栏（筛选/搜索/导出CSV/报告/一键重命名）+ 五列结果表

v2.0 起内部为 Model/View（widgets/result_model.py），对外 API 与
QTableWidget 时代完全一致——调用方（main_window）零改动：
add_row / update_row_by_path / clear_rows / selected_paths /
remove_rows_by_paths / mark_unfinished_cancelled / begin_update /
end_update / apply_filter / save-restore_header_state。
begin_update/end_update 保留为空操作（model 原生高效），v2.1 删除。
"""

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QPushButton, QTableView,
                             QVBoxLayout, QWidget)

from models.result_item import ResultItem
from widgets.result_model import (COLUMNS, KIND_ROLE, PATH_ROLE, RESULT_ITEM_ROLE,
                                  ResultProxyModel, ResultTableModel, StatusKind)

FILTER_ALL = "全部"
FILTER_TRUE = "只看真无损"
FILTER_FAKE = "只看假无损"

_KIND_BY_FILTER = {FILTER_TRUE: StatusKind.TRUE, FILTER_FAKE: StatusKind.FAKE}


class ResultTable(QWidget):
    export_clicked = pyqtSignal()
    report_clicked = pyqtSignal()       # V13-5：HTML 报告
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
        self._search_timer.setInterval(300)  # 防抖：连续击键不逐次 invalidate
        self._search_timer.timeout.connect(
            lambda: self.apply_filter(self.combo_filter.currentText()))
        self.edit_search.textChanged.connect(
            lambda: self._search_timer.start())
        bar.addStretch(1)
        self.btn_export = QPushButton("导出CSV", self)
        self.btn_export.setEnabled(False)   # M3 启用
        self.btn_report = QPushButton("导出报告(HTML)", self)
        self.btn_report.setEnabled(False)   # V13-5：有结果后启用
        self.btn_report.setToolTip(
            "生成单文件 HTML 报告（汇总+明细+假无损证据卡），可直接发送")
        self.btn_rename = QPushButton("一键重命名", self)
        self.btn_rename.setEnabled(False)   # M3 启用
        bar.addWidget(self.btn_export)
        bar.addWidget(self.btn_report)
        bar.addWidget(self.btn_rename)
        lay.addLayout(bar)

        # 按钮 → 对外信号（M1 遗留修复：占位期未接，M3 启用后必须接）
        self.btn_export.clicked.connect(self.export_clicked)
        self.btn_report.clicked.connect(self.report_clicked)
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

        # ---- 表格（v2.0：Model/View） ----
        self.model = ResultTableModel(self)
        self.proxy = ResultProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView(self)
        self.table.setModel(self.proxy)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)  # P1：表头排序（v2.0 起扫描中可用）
        # V20-1 性能：排序转 model 物理排序（proxy 逐对 lessThan 对 2 万行
        # 要 ~28 万次跨 Python data()，实测 0.65s；model list.sort ~0.05s）。
        # proxy.sortRole 置无效值，动态排序不再触发；顺序由指示器驱动。
        self.table.horizontalHeader().setSortIndicator(
            0, Qt.SortOrder.AscendingOrder)
        self.table.horizontalHeader().sortIndicatorChanged.connect(
            self._on_sort_indicator)
        self.proxy.setSortRole(-1)   # 不触发 proxy 动态排序（无效 role）
        # 右键菜单：打开所在文件夹 / 复制完整路径 / 快速筛选
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        lay.addWidget(self.table, 1)

        self.combo_filter.currentTextChanged.connect(self.apply_filter)
        self.table.doubleClicked.connect(self._on_double_clicked)

    def _on_double_clicked(self, proxy_index) -> None:
        path = proxy_index.siblingAtColumn(0).data(PATH_ROLE)
        if path:
            self.detail_requested.emit(path)

    def _on_sort_indicator(self, col: int, order) -> None:
        """表头点击 → model 物理排序（proxy 保持 source 顺序直通）"""
        self.model.sort_by_column(col, order)

    def apply_current_sort(self) -> None:
        """按当前表头指示器重排（审查修复⑤）：重扫灌入的行是路径序，
        不重排的话指示器箭头与实际顺序不一致（用户按箭头读表会读错）"""
        hdr = self.table.horizontalHeader()
        if hdr.sortIndicatorSection() >= 0:
            self.model.sort_by_column(hdr.sortIndicatorSection(),
                                      hdr.sortIndicatorOrder())

    # ---------------- 数据（对外 API，内部转 model 调用） ----------------
    def add_row(self, item: ResultItem) -> None:
        """新增一行（M2 起由扫描结果驱动）"""
        self.model.append_row(item)

    def update_row_by_path(self, filepath: str, item: ResultItem) -> bool:
        """按完整路径定位并整行更新（M2 扫描回填用）"""
        return self.model.update_by_path(filepath, item)

    def rename_row_path(self, old_path: str, new_path: str) -> bool:
        """改名行同步（审查修复④）：item 已被调用方原地改写后调用"""
        return self.model.rename_path(old_path, new_path)

    def clear_rows(self) -> None:
        self.model.reset_rows([])

    def mark_unfinished_cancelled(self) -> int:
        """扫描停止后，把「等待 / 分析中…」行标记为「已取消」（数据层状态）"""
        return self.model.mark_cancelled()

    def begin_update(self) -> None:
        """兼容空实现（v2.0 model 原生高效，B1 批量模式不再需要）"""
        # 保留签名：main_window 扫描开始仍会调用；v2.1 删除

    def end_update(self) -> None:
        """兼容空实现（同上）"""

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
            p = idx.siblingAtColumn(0).data(PATH_ROLE)
            if p and p not in paths:
                paths.append(p)
        return paths

    def remove_rows_by_paths(self, paths: list) -> None:
        """按路径删除行（安全模式去重=索引清除时用）"""
        self.model.remove_by_paths(paths)

    def _item_of(self, path: str) -> ResultItem | None:
        """按路径取 ResultItem（右键菜单读取标签用）"""
        row = self.model.row_of_path(path)
        if row >= 0:
            return self.model.rows[row]
        return None

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
        item = self._item_of(first_path)
        meta = item.detail.get('meta', {}) if item else {}
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

    # ---------------- 筛选（P1；V13-3 扩展；v2.0 下沉 proxy） ----------------
    def apply_filter(self, text: str) -> None:
        self.proxy.set_kind_filter(_KIND_BY_FILTER.get(text))
        self.proxy.set_search_text(self.edit_search.text())

    def set_actions_enabled(self, enabled: bool) -> None:
        self.btn_export.setEnabled(enabled)
        self.btn_report.setEnabled(enabled)
        self.btn_rename.setEnabled(enabled)
        self.btn_compare.setEnabled(enabled)
        self.btn_dedupe.setEnabled(enabled)
        self.btn_tag_rename.setEnabled(enabled)
        self.btn_playlist.setEnabled(enabled)

    def set_restore_enabled(self, enabled: bool) -> None:
        """还原按钮单独控制：当前文件夹存在可还原的清除记录时才可用"""
        self.btn_restore.setEnabled(enabled)
