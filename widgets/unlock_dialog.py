#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""格式解锁对话框：网易云 .ncm / QQ音乐 QMC 系（.mflac .mgg .qmc* 等）

流程：添加文件/文件夹 → 选输出目录与目标格式 → 后台线程逐个解密。
源文件全程只读；输出格式「自动」保留内层原始格式（推荐），
强制 FLAC/WAV 时对无损内层是无损重封装，对有损内层只是换容器。
"""

import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

from core.qqmusic_key import EkeyStore, ensure_ekeys
from core.unlock import (ENCRYPTED_EXTS, UnlockCancelled,
                         detect_encrypted_type, find_encrypted_files,
                         unlock_file)
from widgets.qqmusic_guide_dialog import QQMusicGuideDialog

_FMT_TAG = {'ncm': 'NCM', 'qmc': 'QMC'}


class _WorkerSignals(QObject):
    progress = pyqtSignal(int, int, str)   # 已处理, 总数, 当前文件名
    file_progress = pyqtSignal(int, int, int)  # 行号, 已解字节, 总字节
    file_done = pyqtSignal(int, object)    # 列表行号, UnlockResult
    finished = pyqtSignal(bool)            # True = 被取消
    key_import_done = pyqtSignal(object)   # ensure_ekeys 统计 dict


class _UnlockWorker(QRunnable):
    def __init__(self, files: list, out_dir: str, target: str,
                 cancel: threading.Event, fetch_tags: bool = True):
        super().__init__()
        self.files = files
        self.out_dir = out_dir
        self.target = target
        self.cancel = cancel
        self.fetch_tags = fetch_tags
        self.signals = _WorkerSignals()

    def run(self) -> None:
        total = len(self.files)
        for idx, fp in enumerate(self.files):
            if self.cancel.is_set():
                self.signals.finished.emit(True)
                return
            self.signals.progress.emit(idx, total, Path(fp).name)
            try:
                result = unlock_file(
                    fp, self.out_dir, self.target,
                    fetch_tags=self.fetch_tags,
                    progress_cb=lambda d, t, i=idx:
                        self.signals.file_progress.emit(i, d, t),
                    cancel_check=self.cancel.is_set)
            except UnlockCancelled:
                # 用户取消：当前文件半截产物已由 unlock_file 清理，整批收尾
                self.signals.finished.emit(True)
                return
            except Exception as exc:  # 单文件异常不中断整批
                from core.unlock import UnlockResult
                result = UnlockResult(ok=False, src=fp, message=str(exc))
            self.signals.file_done.emit(idx, result)
        self.signals.finished.emit(False)


class _KeyImportWorker(QRunnable):
    """后台为列表中的 musicex 文件补齐 QQ 音乐密钥"""

    def __init__(self, files: list, cancel: threading.Event):
        super().__init__()
        self.files = files
        self.cancel = cancel
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            result = ensure_ekeys(
                self.files,
                progress=lambda i, t, n: self.signals.progress.emit(i, t, n),
                cancel_check=self.cancel.is_set)
        except Exception as exc:  # 保底：任何异常都转成用户可读结果
            result = {'total': 0, 'cached': 0, 'fetched': 0,
                      'failed': [(f, str(exc)) for f in self.files],
                      'auth_ok': False, 'cancelled': False}
        self.signals.key_import_done.emit(result)


class UnlockDialog(QDialog):
    """构造后可读 self.results（UnlockResult 列表）"""

    def __init__(self, default_out: str = "", parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle("格式解锁（ncm / mflac / qmc… → flac / wav）")
        self.resize(640, 560)
        self.results: list = []
        self._files: list = []
        self._cancel = threading.Event()
        self._cancel_import = threading.Event()
        self._running = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._row_of: dict = {}
        self._fmt_tags: dict = {}  # path → 'NCM'/'QMC'（完成后行内保留前缀）

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        # ── 文件列表 ──
        bar = QHBoxLayout()
        self.btn_add_files = QPushButton("添加文件…", self)
        self.btn_add_folder = QPushButton("添加文件夹…", self)
        self.btn_clear = QPushButton("清空", self)
        bar.addWidget(self.btn_add_files)
        bar.addWidget(self.btn_add_folder)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.list = QListWidget(self)
        lay.addWidget(self.list, 1)

        # ── 输出设置 ──
        row_out = QHBoxLayout()
        row_out.addWidget(QLabel("输出目录:", self))
        self.edit_out = QLineEdit(default_out, self)
        self.edit_out.setPlaceholderText("默认：首个文件的同级目录\\已解锁")
        btn_browse = QPushButton("浏览…", self)
        btn_browse.clicked.connect(self._on_browse)
        row_out.addWidget(self.edit_out, 1)
        row_out.addWidget(btn_browse)
        lay.addLayout(row_out)

        row_fmt = QHBoxLayout()
        row_fmt.addWidget(QLabel("输出格式:", self))
        self.combo_fmt = QComboBox(self)
        self.combo_fmt.addItems(["自动（保留内层格式，推荐）",
                                 "强制 FLAC", "强制 WAV"])
        row_fmt.addWidget(self.combo_fmt)
        self.check_tags = QCheckBox("联网补齐 QQ 音乐标签（歌名/歌手/专辑/封面）",
                                    self)
        self.check_tags.setChecked(True)
        self.check_tags.setToolTip(
            "解锁 QQ 音乐新版加密文件后，自动联网取回歌曲标签与封面并写入；\n"
            "需要已导入过密钥（登录态缓存于本机）。取消勾选则完全离线解锁。")
        row_fmt.addWidget(self.check_tags)
        row_fmt.addStretch(1)
        lay.addLayout(row_fmt)

        hint = QLabel(
            "提示：内层为 mp3/ogg/m4a 等有损格式时，转 FLAC/WAV 只是换容器，"
            "不会提升音质。请仅转换你合法获得（已购买/已下载）的文件。",
            self)
        hint.setWordWrap(True)
        hint.setObjectName("FolderLabel")
        lay.addWidget(hint)

        # ── QQ 音乐密钥（musicex 新版加密专用）──
        row_key = QHBoxLayout()
        self.key_status = QLabel(self)
        self.key_status.setObjectName("FolderLabel")
        self.check_auto_unlock = QCheckBox("导入后自动开始解锁", self)
        self.check_auto_unlock.setChecked(True)
        self.btn_import_keys = QPushButton("导入 QQ 音乐密钥", self)
        self.btn_import_keys.setToolTip(
            "为列表中的新版 .mflac/.mgg（musicex）文件获取解密密钥。\n"
            "需要 QQ 音乐客户端已打开并登录下载过这些歌的账号。")
        self.btn_qq_guide = QPushButton("教程", self)
        self.btn_qq_guide.setToolTip("QQ 音乐新版加密文件的解锁图文教程")
        row_key.addWidget(self.key_status, 1)
        row_key.addWidget(self.check_auto_unlock)
        row_key.addWidget(self.btn_import_keys)
        row_key.addWidget(self.btn_qq_guide)
        lay.addLayout(row_key)
        self._importing = False
        self._refresh_key_status()

        # ── 进度 ──
        self.progress = QProgressBar(self)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.status_label = QLabel("", self)
        lay.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close,
                                   self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        self.btn_start = QPushButton("开始解锁", self)
        self.btn_start.setEnabled(False)
        buttons.addButton(self.btn_start,
                          QDialogButtonBox.ButtonRole.AcceptRole)
        lay.addWidget(buttons)

        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_import_keys.clicked.connect(self._on_import_keys)
        self.btn_qq_guide.clicked.connect(self._on_qq_guide)

    # ---------------- QQ 音乐密钥导入 ----------------
    def _refresh_key_status(self) -> None:
        try:
            n = EkeyStore().count()
        except Exception:
            n = 0
        self.key_status.setText(
            f"QQ 音乐密钥库：已缓存 {n} 首"
            + ("（musicex 文件需先导入密钥）" if n == 0 else ""))

    def _on_qq_guide(self) -> None:
        QQMusicGuideDialog(self).exec()

    def _on_import_keys(self) -> None:
        if self._running or self._importing:
            return
        if not self._files:
            self.status_label.setText(
                "请先把新版 .mflac / .mgg 文件加入列表，再导入密钥")
            return
        self._importing = True
        self._cancel_import.clear()
        self.btn_import_keys.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(self._files))
        self.progress.setValue(0)
        self.status_label.setText("正在读取 QQ 音乐客户端登录态…")

        worker = _KeyImportWorker(list(self._files), self._cancel_import)
        worker.signals.progress.connect(self._on_import_progress)
        worker.signals.key_import_done.connect(self._on_import_done)
        self._pool.start(worker)

    def _on_import_progress(self, idx: int, total: int, name: str) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(idx)
        self.status_label.setText(f"正在换取密钥 ({idx + 1}/{total}): {name}")

    def _on_import_done(self, result: dict) -> None:
        self._importing = False
        self.btn_import_keys.setEnabled(True)
        self.btn_start.setEnabled(bool(self._files) and not self._running)
        self.progress.setValue(self.progress.maximum())
        self._refresh_key_status()

        cached, fetched = result.get('cached', 0), result.get('fetched', 0)
        failed = result.get('failed', [])
        if result.get('cancelled'):
            self.status_label.setText(
                f"密钥导入已取消：已获取 {fetched} 首（已落盘），"
                f"已有缓存 {cached} 首")
            return  # 取消后不自动续跑
        if not failed:
            self.status_label.setText(
                f"密钥已就绪：新获取 {fetched} 首，已有缓存 {cached} 首，"
                f"可以开始解锁")
        else:
            self.status_label.setText(
                f"密钥导入完成：成功 {fetched + cached} 首，失败 {len(failed)} 首")
            detail = '\n'.join(f"· {Path(p).name}：{why}"
                               for p, why in failed[:8])
            if len(failed) > 8:
                detail += f"\n… 等共 {len(failed)} 首"
            QMessageBox.warning(
                self, "部分密钥获取失败",
                "以下文件未能获取密钥（可点「教程」查看排查方法）：\n\n"
                + detail)
        # 一键续跑：拿到至少一个密钥后自动开始解锁
        if (self.check_auto_unlock.isChecked()
                and fetched + cached > 0 and self._files
                and not self._running):
            self._on_start()

    # ---------------- 文件收集 ----------------
    def _add(self, paths: list) -> None:
        added = 0
        for p in paths:
            if p in self._files:
                continue
            tag = detect_encrypted_type(p)
            if not tag:
                continue
            self._files.append(p)
            self._row_of[p] = self.list.count()
            self._fmt_tags[p] = tag
            item = QListWidgetItem(f"[{_FMT_TAG[tag]}] {Path(p).name}")
            item.setToolTip(p)
            self.list.addItem(item)
            added += 1
        if added:
            self.status_label.setText(f"共 {len(self._files)} 个待解锁文件")
        self.btn_start.setEnabled(bool(self._files) and not self._running)

    def _on_add_files(self) -> None:
        exts = ' '.join(f'*{e}' for e in sorted(ENCRYPTED_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择加密音频文件", "", f"加密音频 ({exts});;所有文件 (*)")
        if paths:
            self._add(paths)

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            found = find_encrypted_files(folder)
            if not found:
                self.status_label.setText("该文件夹内没有可识别的加密音频")
            self._add(found)

    def _on_clear(self) -> None:
        if self._running or self._importing:
            return
        self._files.clear()
        self._row_of.clear()
        self._fmt_tags.clear()
        self.list.clear()
        self.status_label.setText("")
        self.btn_start.setEnabled(False)

    def _on_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.edit_out.setText(folder)

    def _out_dir(self) -> str:
        out = self.edit_out.text().strip()
        if out:
            return out
        if self._files:
            return str(Path(self._files[0]).parent / "已解锁")
        return ""

    # ---------------- 执行 ----------------
    def _on_start(self) -> None:
        if self._running or not self._files:
            return
        out_dir = self._out_dir()
        if not out_dir:
            return
        target = {0: 'auto', 1: 'flac', 2: 'wav'}[
            self.combo_fmt.currentIndex()]
        self._running = True
        self._cancel.clear()
        self.results = []
        self.progress.setVisible(True)
        self.progress.setRange(0, len(self._files))
        self.progress.setValue(0)
        self.btn_start.setText("取消")
        self.btn_add_files.setEnabled(False)
        self.btn_add_folder.setEnabled(False)
        self.btn_clear.setEnabled(False)

        worker = _UnlockWorker(list(self._files), out_dir, target,
                               self._cancel,
                               fetch_tags=self.check_tags.isChecked())
        worker.signals.progress.connect(self._on_progress)
        worker.signals.file_progress.connect(self._on_file_progress)
        worker.signals.file_done.connect(self._on_file_done)
        worker.signals.finished.connect(self._on_finished)
        self._pool.start(worker)

    def _on_progress(self, idx: int, total: int, name: str) -> None:
        self.progress.setValue(idx)
        self.status_label.setText(f"正在解锁 ({idx + 1}/{total}): {name}")

    def _on_file_progress(self, idx: int, done: int, total: int) -> None:
        if total <= 0 or idx >= len(self._files):
            return
        pct = done * 100 // total
        name = Path(self._files[idx]).name
        self.status_label.setText(
            f"正在解锁 ({idx + 1}/{len(self._files)}): {name} — {pct}%")

    def _on_file_done(self, idx: int, result) -> None:
        self.results.append(result)
        item = self.list.item(idx)
        tag = _FMT_TAG.get(self._fmt_tags.get(result.src, ''), '')
        prefix = f"[{tag}] " if tag else ''
        name = Path(result.src).name
        if result.ok:
            suffix = f"✓ → {Path(result.dst).name}"
            if result.message:  # 有损转无损容器提示
                suffix += f"（{result.message}）"
            item.setText(f"{prefix}{name}  {suffix}")
        else:
            item.setText(f"{prefix}{name}  ✗ {result.message}")

    def _on_finished(self, cancelled: bool) -> None:
        self._running = False
        self.progress.setValue(self.progress.maximum())
        ok_cnt = sum(1 for r in self.results if r.ok)
        fail_cnt = len(self.results) - ok_cnt
        state = "已取消，" if cancelled else ""
        self.status_label.setText(
            f"{state}完成：成功 {ok_cnt} 个，失败 {fail_cnt} 个")
        self.btn_start.setText("开始解锁")
        self.btn_start.setEnabled(bool(self._files))
        self.btn_add_files.setEnabled(True)
        self.btn_add_folder.setEnabled(True)
        self.btn_clear.setEnabled(True)

    def reject(self) -> None:
        if self._running:
            self._cancel.set()
            self.status_label.setText("正在取消…")
        if self._importing:
            self._cancel_import.set()
            self.status_label.setText("正在取消密钥导入…")
        if self._running or self._importing:
            # 必须等线程池排空：池销毁时仍有运行中的 QRunnable 会崩溃
            self._pool.waitForDone(-1)
        super().reject()
