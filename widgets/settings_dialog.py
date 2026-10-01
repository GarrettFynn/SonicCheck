#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置对话框（V13-2）：扫描 / 质量标记 / 高级 三组，收拢散落配置

左栏的线程数/分析时长与设置面板双向同步（决策点①：扫描是最高频操作，
入口保留左栏；质量标记从左栏移除归设置面板）。
"""

from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QPushButton, QSpinBox, QVBoxLayout, QWidget)

from core.quality_marks import POS_PREFIX, POS_SUFFIX


class SettingsDialog(QDialog):
    """exec 接受后用 values() 取全部设置（由主窗口负责落 QSettings）"""

    def __init__(self, values: dict, parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # ── 扫描 ──
        grp_scan = QGroupBox("扫描", self)
        form_scan = QFormLayout(grp_scan)
        self.spin_threads = QSpinBox(self)
        self.spin_threads.setRange(1, 16)
        self.spin_threads.setToolTip("并行分析的线程数；过大可能拖满 CPU")
        self.spin_seconds = QSpinBox(self)
        self.spin_seconds.setRange(5, 120)
        self.spin_seconds.setSuffix(" 秒")
        self.spin_seconds.setToolTip("每首歌从头分析的时长（越长越准越慢）")
        form_scan.addRow("线程数", self.spin_threads)
        form_scan.addRow("每首分析时长", self.spin_seconds)
        self.chk_safe = QCheckBox("默认开启安全模式（产出写入安全输出目录）",
                                  self)
        form_scan.addRow("", self.chk_safe)
        lay.addWidget(grp_scan)

        # ── 质量标记 ──
        grp_marks = QGroupBox("质量标记", self)
        form_marks = QFormLayout(grp_marks)
        self.edit_true = QPushButton(self)
        self.edit_fake = QPushButton(self)
        form_marks.addRow("真无损标记", self._mark_row(self.edit_true,
                                                       "mark_true"))
        form_marks.addRow("假无损标记", self._mark_row(self.edit_fake,
                                                       "mark_fake"))
        lay.addWidget(grp_marks)

        # ── 高级 ──
        grp_adv = QGroupBox("高级", self)
        form_adv = QFormLayout(grp_adv)
        tip = QLabel("密钥库与临时配置目录：~/.soniccheck/（仅本机）", self)
        tip.setWordWrap(True)
        form_adv.addRow(tip)
        btn_open = QPushButton("打开配置文件夹", self)
        btn_open.clicked.connect(self._open_config_dir)
        form_adv.addRow("", btn_open)
        self.chk_updates = QCheckBox("启动时检查新版本（仅提示，不自动下载）",
                                     self)
        form_adv.addRow("", self.chk_updates)
        lay.addWidget(grp_adv)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        # 载入初值
        v = values
        self.spin_threads.setValue(int(v.get("threads", 8)))
        self.spin_seconds.setValue(int(v.get("seconds", 30)))
        self.chk_safe.setChecked(bool(v.get("safe", False)))
        self.chk_updates.setChecked(bool(v.get("check_updates", True)))
        self._mark_values = {"mark_true": v.get("mark_true", "_真无损"),
                             "mark_fake": v.get("mark_fake", "_假无损"),
                             "position": v.get("position", POS_SUFFIX)}
        self._refresh_mark_buttons()

    # ---------------- 质量标记编辑 ----------------
    def _mark_row(self, button: QPushButton, key: str) -> QWidget:
        """标记文本按钮 + 前后缀切换（沿用原 QualityMarkDialog 交互）"""
        row = QHBoxLayout()
        button.clicked.connect(lambda: self._edit_mark(key))
        row.addWidget(button, 1)
        btn_pos = QPushButton("切换前/后缀", self)
        btn_pos.clicked.connect(self._toggle_position)
        row.addWidget(btn_pos)
        wrap = QWidget(self)
        wrap.setLayout(row)
        return wrap

    def _edit_mark(self, key: str) -> None:
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "编辑标记", "标记文本（留空恢复默认）:",
            text=self._mark_values[key])
        if not ok:
            return
        self._mark_values[key] = text.strip() or \
            ("_真无损" if key == "mark_true" else "_假无损")
        self._refresh_mark_buttons()

    def _toggle_position(self) -> None:
        self._mark_values["position"] = (POS_PREFIX
                                         if self._mark_values["position"]
                                         == POS_SUFFIX else POS_SUFFIX)
        self._refresh_mark_buttons()

    def _refresh_mark_buttons(self) -> None:
        pos = "前缀" if self._mark_values["position"] == POS_PREFIX \
            else "后缀"
        self.edit_true.setText(
            f"{self._mark_values['mark_true']}（{pos}）")
        self.edit_fake.setText(
            f"{self._mark_values['mark_fake']}（{pos}）")

    @staticmethod
    def _open_config_dir() -> None:
        import os
        from pathlib import Path
        d = Path.home() / ".soniccheck"
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # noqa: 仅 Windows 产品形态

    # ---------------- 取值 ----------------
    def values(self) -> dict:
        return {
            "threads": self.spin_threads.value(),
            "seconds": self.spin_seconds.value(),
            "safe": self.chk_safe.isChecked(),
            "check_updates": self.chk_updates.isChecked(),
            **self._mark_values,
        }
