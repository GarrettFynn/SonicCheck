#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结果表格数据层（V20-1）：QAbstractTableModel + QSortFilterProxyModel

ResultTableModel：rows = list[ResultItem]，角色化暴露五列数据；
状态用枚举存 UserRole（顺手替换旧"按显示文本匹配"的脆弱实现）。
ResultProxyModel：真/假筛选 + 文本搜索合一（下沉 v1.3.0 的手工
setRowHidden 遍历），支持动态 filter 参数。

v2.0 起 ResultTable（widgets/result_table.py）内部改走本模块；
对外 API 不变，main_window 零改动。
"""

from enum import Enum

from PyQt6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
                          Qt)
from PyQt6.QtGui import QColor

from models.result_item import (STATUS_ANALYZING, STATUS_CANCELLED, STATUS_DONE,
                                STATUS_ERROR, STATUS_PENDING, ResultItem)

COLUMNS = ("文件名", "评分", "截止(-60dB)", "DR(dB)", "状态")

COLOR_TRUE = QColor("#4EC9B0")
COLOR_FAKE = QColor("#F44747")
COLOR_GRAY = QColor("#808080")
COLOR_ERROR = QColor("#F48771")

PATH_ROLE = Qt.ItemDataRole.UserRole + 1      # 完整路径（对外契约保留）
KIND_ROLE = Qt.ItemDataRole.UserRole + 2      # 状态种类：StatusKind
RESULT_ITEM_ROLE = Qt.ItemDataRole.UserRole + 3   # data() 直接吐 ResultItem


class StatusKind(Enum):
    TRUE = "true"
    FAKE = "fake"
    ERROR = "error"
    ANALYZING = "analyzing"
    PENDING = "pending"
    CANCELLED = "cancelled"


_STATUS_TEXT = {
    StatusKind.TRUE: "● 真无损",
    StatusKind.FAKE: "● 假无损",
    StatusKind.ERROR: "! 失败",
    StatusKind.ANALYZING: "分析中…",
    StatusKind.PENDING: "○ 等待",
    StatusKind.CANCELLED: "已取消",
}
_STATUS_COLOR = {
    StatusKind.TRUE: COLOR_TRUE,
    StatusKind.FAKE: COLOR_FAKE,
    StatusKind.ERROR: COLOR_ERROR,
    StatusKind.ANALYZING: COLOR_GRAY,
    StatusKind.PENDING: COLOR_GRAY,
    StatusKind.CANCELLED: COLOR_GRAY,
}


def kind_of(item: ResultItem) -> StatusKind:
    if item.status == STATUS_DONE:
        return StatusKind.FAKE if item.is_fake else StatusKind.TRUE
    if item.status == STATUS_ERROR:
        return StatusKind.ERROR
    if item.status == STATUS_ANALYZING:
        return StatusKind.ANALYZING
    if item.status == STATUS_CANCELLED:
        return StatusKind.CANCELLED
    return StatusKind.PENDING


class ResultTableModel(QAbstractTableModel):
    """rows: list[ResultItem]（model 持有顺序；排序交给 proxy）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[ResultItem] = []
        self._row_by_path: dict[str, int] = {}

    # ---------------- Qt 基础 ----------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and \
                role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._rows[index.row()]
        col = index.column()
        if role == PATH_ROLE and col == 0:
            return item.filepath
        if role == KIND_ROLE and col == 4:
            return kind_of(item)
        if role == RESULT_ITEM_ROLE:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return item.filename
            if col == 1:
                return round(item.score, 1)
            if col == 2:
                return int(round(item.cutoff_60db))
            if col == 3:
                return round(item.dr, 1)
            if col == 4:
                return _STATUS_TEXT[kind_of(item)]
        if role == Qt.ItemDataRole.ForegroundRole and col == 4:
            return _STATUS_COLOR[kind_of(item)]
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 0:
                return item.filepath
            if col == 4:
                tips = list(item.fake_reasons)
                hint = item.detail.get('upscale_hint', '') \
                    if item.status == STATUS_DONE else ''
                if hint:
                    tips.append('⚠ ' + hint)
                if tips:
                    return "\n".join(tips)
                if item.status == STATUS_ERROR and item.error_message:
                    return item.error_message
        return None

    # ---------------- 数据操作（ResultTable 外壳调用） ----------------
    def reset_rows(self, items: list) -> None:
        self.beginResetModel()
        self._rows = list(items)
        self._reindex()
        self.endResetModel()

    def append_row(self, item: ResultItem) -> None:
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(item)
        self._row_by_path[item.filepath] = row
        self.endInsertRows()

    def update_by_path(self, filepath: str, item: ResultItem) -> bool:
        idx = self._row_by_path.get(filepath, -1)
        if idx < 0 or self._rows[idx].filepath != filepath:
            # 映射失效（防御）：线性回退
            for i, r in enumerate(self._rows):
                if r.filepath == filepath:
                    idx = i
                    break
            else:
                return False
        self._rows[idx] = item
        self._row_by_path[filepath] = idx
        self.dataChanged.emit(self.index(idx, 0), self.index(idx, 4))
        return True

    def remove_by_paths(self, paths) -> int:
        targets = set(paths)
        idxs = [i for i, r in enumerate(self._rows) if r.filepath in targets]
        for offset, i in enumerate(sorted(idxs, reverse=True)):
            self.beginRemoveRows(QModelIndex(), i, i)
            del self._rows[i]
            self.endRemoveRows()
        self._reindex()
        return len(idxs)

    def mark_cancelled(self) -> int:
        """把等待/分析中行置为已取消（数据层状态，替代按文本匹配）"""
        count = 0
        for i, r in enumerate(self._rows):
            k = kind_of(r)
            if k in (StatusKind.PENDING, StatusKind.ANALYZING):
                r.status = STATUS_CANCELLED
                count += 1
                self.dataChanged.emit(self.index(i, 0), self.index(i, 4))
        return count

    def _reindex(self):
        self._row_by_path = {r.filepath: i for i, r in enumerate(self._rows)}

    # ---------------- 外部只读访问 ----------------
    def item_at(self, proxy_index) -> ResultItem | None:
        """由 proxy 索引取 ResultItem（视图层选中行用）"""
        return proxy_index.data(RESULT_ITEM_ROLE)

    def row_of_path(self, filepath: str) -> int:
        return self._row_by_path.get(filepath, -1)

    @property
    def rows(self) -> list:
        return self._rows

class ResultProxyModel(QSortFilterProxyModel):
    """真/假筛选 + 文本搜索合一（v1.3.0 手工 setRowHidden 遍历下沉至此）。

    kind_filter: None=全部；StatusKind.TRUE/FAKE=只看对应种类。
    search_text: 文件名/完整路径/标签歌名/歌手子串（大小写不敏感）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kind = None
        self._needle = ''

    def set_kind_filter(self, kind) -> None:
        self._kind = kind
        self.invalidateFilter()

    def set_search_text(self, text: str) -> None:
        self._needle = (text or '').strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent) -> bool:
        model = self.sourceModel()
        idx4 = model.index(source_row, 4, source_parent)
        kind = idx4.data(KIND_ROLE)
        if self._kind is not None and kind is not self._kind:
            return False
        if not self._needle:
            return True
        idx0 = model.index(source_row, 0, source_parent)
        item = idx0.data(RESULT_ITEM_ROLE)
        if item is None:
            return False
        if self._needle in item.filename.lower()                 or self._needle in item.filepath.lower():
            return True
        meta = item.detail.get('meta', {})
        hay = f"{meta.get('title', '')} {meta.get('artist', '')}".lower()
        return self._needle in hay
