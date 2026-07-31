#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质量标记设置对话框：自定义真/假无损文件名标记的文本与位置

只改文件名标记，不动判定口径（表格判定列/CSV 假无损列文字不变）。
配置由调用方读写 QSettings 并注入 core.quality_marks。
"""

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QRadioButton,
                             QVBoxLayout, QWidget)

from core.quality_marks import (POS_PREFIX, POS_SUFFIX, get_config)


class QualityMarkDialog(QDialog):
    """exec 接受后用 values() 取 {mark_true, mark_fake, position}"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle("质量标记设置")
        self.setMinimumWidth(420)

        cfg = get_config()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        lay.addWidget(QLabel(
            "扫描判定后给文件名加的质量标记（仅影响文件名，不影响判定结果）:",
            self))

        form = QFormLayout()
        self.edit_true = QLineEdit(cfg["mark_true"], self)
        self.edit_fake = QLineEdit(cfg["mark_fake"], self)
        form.addRow("真无损标记:", self.edit_true)
        form.addRow("假无损标记:", self.edit_fake)
        lay.addLayout(form)

        row = QHBoxLayout()
        row.addWidget(QLabel("标记位置:", self))
        self.radio_suffix = QRadioButton("后缀（歌名_假无损.flac）", self)
        self.radio_prefix = QRadioButton("前缀（_假无损歌名.flac）", self)
        (self.radio_suffix if cfg["position"] == POS_SUFFIX
         else self.radio_prefix).setChecked(True)
        row.addWidget(self.radio_suffix)
        row.addWidget(self.radio_prefix)
        row.addStretch(1)
        lay.addLayout(row)

        self.preview_label = QLabel(self)
        lay.addWidget(self.preview_label)
        self.edit_true.textChanged.connect(self._refresh_preview)
        self.edit_fake.textChanged.connect(self._refresh_preview)
        self.radio_suffix.toggled.connect(self._refresh_preview)
        self._refresh_preview()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(
            QDialogButtonBox.StandardButton.RestoreDefaults).setText("恢复默认")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(
            QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self._restore_defaults)
        lay.addWidget(buttons)

    def _restore_defaults(self) -> None:
        from core.quality_marks import (DEFAULT_MARK_FAKE,
                                        DEFAULT_MARK_TRUE)
        self.edit_true.setText(DEFAULT_MARK_TRUE)
        self.edit_fake.setText(DEFAULT_MARK_FAKE)
        self.radio_suffix.setChecked(True)

    def _refresh_preview(self) -> None:
        t, f = self.edit_true.text(), self.edit_fake.text()
        if self.radio_suffix.isChecked():
            self.preview_label.setText(
                f"预览: 歌名{t}.flac　/　歌名{f}.flac")
        else:
            self.preview_label.setText(
                f"预览: {t}歌名.flac　/　{f}歌名.flac")

    def accept(self) -> None:
        if not self.edit_true.text().strip() \
                or not self.edit_fake.text().strip():
            self.preview_label.setText("⚠ 标记文本不能为空")
            return
        super().accept()

    def values(self) -> dict:
        return {
            "mark_true": self.edit_true.text(),
            "mark_fake": self.edit_fake.text(),
            "position": POS_SUFFIX if self.radio_suffix.isChecked()
            else POS_PREFIX,
        }
