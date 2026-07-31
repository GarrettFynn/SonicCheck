#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""歌单导入对话框（功能 E + F 本期范围）

三种输入方式：
1. 直接粘贴歌单文本（手动兜底）
2. 从 md/csv/txt 文件导入
3. 粘贴网易云歌单链接解析（失败时提示改用方式 1）

流程：输入 → "解析并匹配"预览（已匹配/未匹配数）→ 确认复制。
"""

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog,
                             QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                             QPushButton, QVBoxLayout, QWidget)

from core.playlist import (fetch_netease_playlist, match_entries,
                           parse_playlist_file, parse_playlist_text)


class PlaylistDialog(QDialog):
    """构造时传入扫描结果 items 与目标根目录；exec 接受后可读
    self.entries / self.playlist_name / self.match_result"""

    def __init__(self, items: list, parent: QWidget = None):
        super().__init__(parent)
        self._items = items
        self.entries: list = []
        self.match_result = None
        self.setWindowTitle("歌单导入")
        self.resize(680, 560)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        # 歌单名 + 文件导入 + 网易云链接
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("歌单名:", self))
        self.edit_name = QLineEdit("新歌单", self)
        row1.addWidget(self.edit_name, 1)
        self.btn_file = QPushButton("从文件导入…", self)
        row1.addWidget(self.btn_file)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("网易云链接:", self))
        self.edit_url = QLineEdit(self)
        self.edit_url.setPlaceholderText(
            "粘贴 https://music.163.com/playlist?id=… 后点右侧解析")
        row2.addWidget(self.edit_url, 1)
        self.btn_fetch = QPushButton("解析链接", self)
        row2.addWidget(self.btn_fetch)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("高级:", self))
        self.edit_cookie = QLineEdit(self)
        self.edit_cookie.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_cookie.setPlaceholderText(
            "可选：MUSIC_U（你的登录 Cookie）可拉全量；仅本机使用，不上传不保存")
        row3.addWidget(self.edit_cookie, 1)
        lay.addLayout(row3)

        lay.addWidget(QLabel("歌单内容（每行一首：歌名 - 歌手）:", self))
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlaceholderText(
            "支持：\n"
            "  歌名 - 歌手\n"
            "  1. 歌名 - 歌手\n"
            "  - 歌名\n"
            "  或带表头的 CSV（歌名,歌手）")
        lay.addWidget(self.text_edit, 1)

        self.btn_match = QPushButton("解析并匹配", self)
        lay.addWidget(self.btn_match)
        self.preview_label = QLabel("尚未解析", self)
        self.preview_label.setWordWrap(True)
        lay.addWidget(self.preview_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        self.btn_ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.btn_ok.setText("复制到歌单文件夹")
        self.btn_ok.setEnabled(False)  # 匹配出结果才可用
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.btn_file.clicked.connect(self._on_import_file)
        self.btn_fetch.clicked.connect(self._on_fetch_netease)
        self.btn_match.clicked.connect(self._on_match)

    # ---------------- 输入 ----------------
    def _on_import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择歌单文件", "",
            "歌单文件 (*.md *.csv *.txt);;所有文件 (*)")
        if not path:
            return
        try:
            entries, name = parse_playlist_file(path)
        except OSError as exc:
            self.preview_label.setText(f"读取失败: {exc}")
            return
        self.edit_name.setText(name)
        self._fill_text(entries)

    def _on_fetch_netease(self) -> None:
        url = self.edit_url.text().strip()
        if not url:
            self.preview_label.setText("请先粘贴网易云歌单链接")
            return
        self.btn_fetch.setEnabled(False)
        self.preview_label.setText("正在拉取网易云歌单…")
        try:
            entries, name, total = fetch_netease_playlist(
                url, self.edit_cookie.text())
            self.edit_name.setText(name)
            self._fill_text(entries)
            if total is not None and len(entries) < total:
                self.preview_label.setText(
                    f"⚠ 网易云未登录仅返回前 {len(entries)} 首"
                    f"（歌单共 {total} 首），已导入这部分。\n"
                    f"要拉全量：在「高级」栏填入 MUSIC_U 后重新解析；"
                    f"或改用 App/网页复制全量歌单文本粘贴到下方文本框")
            else:
                self.preview_label.setText(
                    f"已解析 {len(entries)} 首，点击「解析并匹配」继续")
        except RuntimeError as exc:
            self.preview_label.setText(
                f"{exc}\n可改为在 App 内复制歌单文本后粘贴到上方文本框")
        finally:
            self.btn_fetch.setEnabled(True)

    def _fill_text(self, entries: list) -> None:
        self.text_edit.setPlainText("\n".join(
            f"{e.title} - {e.artist}" if e.artist else e.title
            for e in entries))

    # ---------------- 匹配 ----------------
    def _on_match(self) -> None:
        self.entries = parse_playlist_text(self.text_edit.toPlainText())
        if not self.entries:
            self.preview_label.setText("未解析到任何歌单条目")
            self.btn_ok.setEnabled(False)
            return
        self.match_result = match_entries(self._items, self.entries)
        m, u = len(self.match_result.matched), len(self.match_result.unmatched)
        preview = f"共 {len(self.entries)} 首：已匹配 {m} 首，未匹配 {u} 首"
        if u:
            misses = "、".join(e.title for e in
                               self.match_result.unmatched[:5])
            preview += f"\n未匹配示例: {misses}{' …' if u > 5 else ''}"
        self.preview_label.setText(preview)
        self.btn_ok.setEnabled(m > 0)
        self.btn_ok.setText(f"复制 {m} 首到歌单文件夹")

    @property
    def playlist_name(self) -> str:
        return self.edit_name.text().strip() or "新歌单"
