#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""左侧面板：来源（选择/拖拽文件夹）、控制（线程数/分析时长）、开始/停止"""

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                             QPushButton, QSpinBox, QVBoxLayout, QWidget)

PANEL_WIDTH = 220
DEFAULT_THREADS = 8   # ⑦-5：默认保守 8，上限 16
MAX_THREADS = 16
DEFAULT_SECONDS = 30  # ⑦-3：与现有脚本一致，从头取 30 秒
MIN_SECONDS, MAX_SECONDS = 5, 120


class DropArea(QLabel):
    """拖拽区：只接受文件夹，拒绝文件与其他对象"""

    folder_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("拖拽文件夹到此处", parent)
        self.setObjectName("DropArea")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(72)
        self.setWordWrap(True)
        self.setAcceptDrops(True)

    @staticmethod
    def _dir_from(mime) -> str:
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if Path(path).is_dir():
                    return path
        return ""

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and self._dir_from(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        folder = self._dir_from(event.mimeData())
        if folder:
            self.folder_dropped.emit(folder)
            event.acceptProposedAction()


class LeftPanel(QFrame):
    select_clicked = pyqtSignal()
    start_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    folder_dropped = pyqtSignal(str)
    marks_clicked = pyqtSignal()      # 质量标记设置
    guide_clicked = pyqtSignal()      # 使用指南

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LeftPanel")
        self.setFixedWidth(PANEL_WIDTH)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        lay.addWidget(self._section("来源"))
        self.btn_select = QPushButton("选择文件夹", self)
        self.drop_area = DropArea(self)
        self.folder_label = QLabel("未选择", self)
        self.folder_label.setObjectName("FolderLabel")
        lay.addWidget(self.btn_select)
        lay.addWidget(self.drop_area)
        lay.addWidget(self.folder_label)

        lay.addWidget(self._separator())
        lay.addWidget(self._section("控制"))
        self.spin_threads = QSpinBox(self)
        self.spin_threads.setRange(1, MAX_THREADS)
        self.spin_threads.setValue(DEFAULT_THREADS)
        self.spin_seconds = QSpinBox(self)
        self.spin_seconds.setRange(MIN_SECONDS, MAX_SECONDS)
        self.spin_seconds.setValue(DEFAULT_SECONDS)
        self.spin_seconds.setSuffix(" 秒")
        lay.addLayout(self._row("线程数", self.spin_threads))
        lay.addLayout(self._row("分析时长", self.spin_seconds))
        self.btn_marks = QPushButton("质量标记…", self)
        self.btn_marks.setToolTip("自定义真/假无损文件名标记的文本与位置")
        lay.addWidget(self.btn_marks)

        # 安全模式：所有产出只写安全输出目录，原文件零改动
        self.chk_safe = QCheckBox("安全模式", self)
        self.chk_safe.setToolTip(
            "开启后：扫描照旧只读原文件；重命名/去重/歌单等所有产出\n"
            "一律复制到「安全输出」目录操作，原文件零改动")
        self.safe_label = QLabel("", self)
        self.safe_label.setObjectName("FolderLabel")
        self.safe_label.setWordWrap(True)
        lay.addWidget(self.chk_safe)
        lay.addWidget(self.safe_label)

        self.btn_start = QPushButton("开始扫描", self)
        self.btn_start.setObjectName("StartButton")
        self.btn_stop = QPushButton("停止", self)
        self.btn_stop.setEnabled(False)  # 仅扫描中可用
        lay.addSpacing(4)
        lay.addWidget(self.btn_start)
        lay.addWidget(self.btn_stop)
        self.btn_guide = QPushButton("使用指南", self)
        lay.addSpacing(4)
        lay.addWidget(self.btn_guide)
        lay.addStretch(1)

        self.btn_select.clicked.connect(self.select_clicked)
        self.btn_start.clicked.connect(self.start_clicked)
        self.btn_stop.clicked.connect(self.stop_clicked)
        self.btn_marks.clicked.connect(self.marks_clicked)
        self.btn_guide.clicked.connect(self.guide_clicked)
        self.drop_area.folder_dropped.connect(self.folder_dropped)

    @staticmethod
    def _section(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setProperty("class", "SectionTitle")
        return lab

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setObjectName("Separator")
        line.setFrameShape(QFrame.Shape.HLine)
        return line

    @staticmethod
    def _row(label: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addStretch(1)
        row.addWidget(widget)
        return row

    @property
    def thread_count(self) -> int:
        return self.spin_threads.value()

    @property
    def analyze_seconds(self) -> int:
        return self.spin_seconds.value()

    def set_folder_display(self, path: str) -> None:
        width = self.folder_label.width() or (PANEL_WIDTH - 40)
        fm = self.folder_label.fontMetrics()
        self.folder_label.setText(
            fm.elidedText(path, Qt.TextElideMode.ElideMiddle, width))
        self.folder_label.setToolTip(path)

    def set_scanning(self, scanning: bool) -> None:
        """扫描中锁定来源操作与参数控件（②补充交互 + M4 打磨）"""
        self.btn_select.setEnabled(not scanning)
        self.drop_area.setEnabled(not scanning)
        self.spin_threads.setEnabled(not scanning)
        self.spin_seconds.setEnabled(not scanning)
        self.btn_start.setEnabled(not scanning)
        self.btn_stop.setEnabled(scanning)
