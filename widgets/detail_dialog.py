#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件详情对话框（功能 A）：展示单首完整元数据与分析指标"""

from pathlib import Path

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                             QVBoxLayout, QWidget)


def _fmt_size(path: str) -> str:
    try:
        size = Path(path).stat().st_size
    except OSError:
        return "未知"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024:.1f} KB"


class DetailDialog(QDialog):
    def __init__(self, item, parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle(f"文件详情 - {item.filename}")
        self.setMinimumWidth(520)

        d = item.detail
        meta = d.get('meta', {})
        cutoffs = d.get('cutoffs', {})

        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        def row(label, value):
            lab = QLabel(str(value), self)
            lab.setTextInteractionFlags(
                lab.textInteractionFlags()
                | lab.textInteractionFlags().TextSelectableByMouse)
            form.addRow(label, lab)

        row("文件名", item.filename)
        row("完整路径", item.filepath)
        row("文件大小", _fmt_size(item.filepath))
        row("歌名(标签)", meta.get('title') or "—")
        row("歌手(标签)", meta.get('artist') or "—")
        row("格式 / 编码", f"{meta.get('format', '?')} / {meta.get('codec', '?')}")
        row("采样率", f"{meta.get('sample_rate', 0)} Hz")
        row("位深度", f"{meta.get('bit_depth', 0)} bit")
        br = meta.get('bitrate', 0)
        row("比特率", f"{br // 1000} kbps" if br else "—")
        dur = meta.get('duration', 0)
        row("时长", f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else "—")
        row("声道数", meta.get('channels', 0))
        row("-40dB 截止", f"{cutoffs.get('-40dB', 0):.0f} Hz")
        row("-60dB 截止", f"{cutoffs.get('-60dB', 0):.0f} Hz")
        row("-80dB 截止", f"{cutoffs.get('-80dB', 0):.0f} Hz")
        cliff_text = (f"{d.get('cliff_freq', 0):.0f} Hz 处断崖"
                      if d.get('cliff') else "无")
        row("频谱断崖", cliff_text)
        row("动态范围 DR", f"{d.get('dr', 0):.1f} dB")
        row("立体声相关", f"{d.get('correlation', 0):.3f}")
        row("峰值电平", f"{d.get('peak', 0):.2f} dB")
        row("综合评分", f"{item.score}")
        row("判定", "假无损" if item.is_fake else "真无损")
        if item.fake_reasons:
            row("判定原因", "\n".join(item.fake_reasons))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        btn_open = buttons.addButton("打开所在文件夹",
                                     QDialogButtonBox.ButtonRole.ActionRole)
        btn_open.clicked.connect(lambda: self._open_folder(item.filepath))
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @staticmethod
    def _open_folder(filepath: str) -> None:
        import os
        os.startfile(str(Path(filepath).parent))  # noqa: 仅 Windows 产品形态
