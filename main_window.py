#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗口：三栏布局（左栏 + 右侧主面板 + 底部日志）、整窗拖拽、状态记忆"""

import sys
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QIcon
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame,
                             QHBoxLayout, QInputDialog,
                             QLabel, QMainWindow, QMessageBox, QProgressDialog,
                             QPushButton, QVBoxLayout, QWidget)

from core.analyzer import AudioAnalyzer
from core.csv_exporter import default_csv_name, export_csv
from core.deduper import (CLEAR_DIR_NAME, build_clear_plan,
                          build_restore_plan, execute_move_plan,
                          find_duplicate_groups, prune_restore_map,
                          record_restore_map, load_restore_map)
from core.ffmpeg_locator import find_ffmpeg, find_ffprobe
from core.fingerprint import fingerprint_file
from core.html_report import export_html_report
from core.playlist import copy_matched
from core.quality_marks import POS_SUFFIX, set_config as set_mark_config
from core.renamer import (KIND_AUDIO, build_rename_plan, execute_copy_plan,
                          execute_plan)
from core.scanner import find_audio_files
from core.tag_renamer import (FMT_ARTIST_TITLE, FMT_TITLE_ARTIST,
                              build_tag_rename_plan)
from models.result_item import (STATUS_ANALYZING, STATUS_DONE, ResultItem)
from app_controllers import FileOpsController, ScanController
from threads.scan_manager import ScanManager
from widgets.compare_dialog import MODE_COMPARE, MODE_DEDUPE, CompareDialog
from widgets.detail_dialog import DetailDialog
from widgets.guide_dialog import GuideDialog
from widgets.left_panel import LeftPanel
from widgets.log_panel import LogPanel
from widgets.playlist_dialog import PlaylistDialog
from widgets.progress_bar import ProgressWidget
from widgets.rename_dialog import RenameDialog
from widgets.result_table import ResultTable
from widgets.settings_dialog import SettingsDialog
from widgets.summary_bar import SummaryBar
from widgets.unlock_dialog import UnlockDialog

APP_NAME = "SonicCheck"
DISPLAY_NAME = "声鉴·曲库管家"
APP_VERSION = "2.0.1"
ORG_NAME = "SonicCheck"

DEFAULT_W, DEFAULT_H = 1200, 800
MIN_W, MIN_H = 900, 600


def resource_path(rel: str) -> str:
    """资源路径：兼容源码运行与 PyInstaller 打包（--add-data）两种环境"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return str(Path(base) / rel)
    return str(Path(__file__).resolve().parent / rel)


class MainWindow(QMainWindow):
    # V20-1.1（审查修复①）：更新检查结果由后台线程 emit 此信号回主线程
    # ——QTimer 不能在无事件循环的 Python 线程里用（原实现提示永不出现）
    update_hint_ready = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{DISPLAY_NAME} {APP_NAME} v{APP_VERSION}")
        self.resize(DEFAULT_W, DEFAULT_H)
        self.setMinimumSize(MIN_W, MIN_H)
        self.setAcceptDrops(True)  # P0：整窗接受文件夹拖拽

        self._settings = QSettings()
        self._current_folder = ""
        self._results = {}  # filepath -> ResultItem（M3 导出/重命名的数据源）

        self._migrate_settings()  # V13-2：旧键一次性迁入 settings/
        self._load_marks_config()
        self._build_ui()
        self._connect_signals()
        # 引擎初始化必须先于状态恢复：_restore_state 恢复上次文件夹会走到
        # _refresh_restore_enabled → 读 _scan_manager，晚于此处初始化会
        # AttributeError（v1.1.0 起的存量启动崩溃，扫描后重启即触发）
        self._init_engine()
        self._restore_state()
        self.log_panel.log("程序启动，请选择或拖拽一个音乐文件夹")
        self._refresh_restore_enabled()
        self._update_safe_label()
        # V20-4：启动 3 秒后后台检查新版本（可关，静默失败）
        self.update_hint_ready.connect(self._show_update_hint)
        if self._settings.value("settings/check_updates", True, type=bool):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(3000, self._check_updates_bg)
        # 首次启动自动打开使用指南（非模态，可边读边用）
        if not self._settings.value("guide_seen", False, type=bool):
            self._settings.setValue("guide_seen", True)
            self._first_guide = GuideDialog(self)
            self._first_guide.show()

    def _load_marks_config(self) -> None:
        """从 QSettings 恢复质量标记配置并注入 core 层"""
        from core.quality_marks import POS_SUFFIX
        set_mark_config(
            mark_true=self._settings.value("settings/marks/true", "_真无损"),
            mark_fake=self._settings.value("settings/marks/fake", "_假无损"),
            position=self._settings.value("settings/marks/position", POS_SUFFIX))

    def _init_engine(self) -> None:
        """M2：检查 ffmpeg/ffprobe 可用性并清理历史临时文件（⑦-7）"""
        ffmpeg, ffprobe = find_ffmpeg(), find_ffprobe()
        if not ffmpeg or not ffprobe:
            self.log_panel.log(
                "⚠ 未找到 ffmpeg/ffprobe：请确认 resources/ffmpeg/ 内置"
                "或系统 PATH 可用，否则扫描会全部失败")
        else:
            self.log_panel.log("分析引擎就绪（ffmpeg/ffprobe 已定位）")
        removed = AudioAnalyzer.sweep_temp_dir()
        if removed > 0:
            self.log_panel.log(f"已清理 {removed} 个历史临时文件")

        self._scan_manager = ScanManager(self)
        mgr = self._scan_manager
        # V20-2：业务控制器（窗口只留布局/状态/转发）
        self.scan = ScanController(self)
        self.ops = FileOpsController(self)
        mgr.file_started.connect(self.on_file_started)
        mgr.file_done.connect(self.on_file_done)
        mgr.progress.connect(self.on_progress)
        mgr.log.connect(self.log_panel.log)
        mgr.scan_finished.connect(self.on_scan_finished)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.left_panel = LeftPanel(self)
        body.addWidget(self.left_panel)

        right = QWidget(self)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(12, 12, 12, 8)
        right_lay.setSpacing(8)
        self.result_table = ResultTable(self)
        self.progress = ProgressWidget(self)
        self.summary_bar = SummaryBar(self)
        right_lay.addWidget(self.result_table, 1)
        right_lay.addWidget(self.progress)
        right_lay.addWidget(self.summary_bar)
        body.addWidget(right, 1)

        body_wrap = QWidget(self)
        body_wrap.setLayout(body)
        root.addWidget(body_wrap, 1)

        self.log_panel = LogPanel(self)
        root.addWidget(self.log_panel)

        self.setCentralWidget(central)

    def _build_header(self) -> QFrame:
        header = QFrame(self)
        header.setObjectName("HeaderBar")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)

        icon_label = QLabel(header)
        icon_label.setPixmap(QIcon(resource_path("resources/icon.png")).pixmap(20, 20))
        title = QLabel(f"{DISPLAY_NAME} {APP_NAME}", header)
        title.setObjectName("AppTitle")
        version = QLabel(f"v{APP_VERSION}", header)
        version.setObjectName("VersionLabel")

        self.btn_settings = QPushButton("设置", header)
        self.btn_settings.setToolTip("扫描参数、质量标记等统一设置")

        lay.addWidget(icon_label)
        lay.addWidget(title)
        lay.addWidget(version)
        lay.addStretch(1)
        lay.addWidget(self.btn_settings)
        return header

    def _connect_signals(self) -> None:
        lp = self.left_panel
        lp.select_clicked.connect(self.on_select_folder)
        lp.folder_dropped.connect(self.set_folder)
        lp.start_clicked.connect(self.on_start_scan)
        lp.stop_clicked.connect(self.on_stop_scan)
        lp.guide_clicked.connect(self.on_guide)
        lp.unlock_clicked.connect(self.on_unlock)
        lp.chk_safe.toggled.connect(self._on_safe_toggled)
        self.result_table.export_clicked.connect(self.on_export_csv)
        self.result_table.report_clicked.connect(self.on_report)
        self.result_table.rename_clicked.connect(self.on_rename)
        self.result_table.compare_clicked.connect(self.on_compare)
        self.result_table.dedupe_clicked.connect(self.on_dedupe)
        self.result_table.restore_clicked.connect(self.on_restore)
        self.result_table.tag_rename_clicked.connect(self.on_tag_rename)
        self.result_table.playlist_clicked.connect(self.on_playlist)
        self.result_table.detail_requested.connect(self.on_show_detail)
        self.btn_settings.clicked.connect(self.on_settings)
        # V13-2：左栏扫描参数改动即时持久化（设置面板与左栏双向同步）
        self.left_panel.spin_threads.valueChanged.connect(
            lambda v: self._settings.setValue("settings/threads", v))
        self.left_panel.spin_seconds.valueChanged.connect(
            lambda v: self._settings.setValue("settings/seconds", v))

    # ---------------- 状态记忆（⑦-10） ----------------
    RECENT_MAX = 5

    def _check_updates_bg(self) -> None:
        """V20-4：后台线程查 Latest，结果回主线程提示（失败静默）"""
        import threading
        def work():
            # 审查修复①：线程里只 emit 信号（queued 回主线程槽）。
            # QTimer.singleShot 不能在无事件循环的 Python 线程使用——
            # 回调永不派发，更新提示曾是静默死功能
            from core.update_check import check
            self.update_hint_ready.emit(check(APP_VERSION))

        threading.Thread(target=work, daemon=True).start()

    def _show_update_hint(self, new_tag: str) -> None:
        if not new_tag:
            return
        self.log_panel.log(
            f"发现新版本 {new_tag}，当前 v{APP_VERSION}——"
            f"下载见 GitHub Releases 页")
        self.statusBar().showMessage(
            f"新版本 {new_tag} 可用（GitHub Releases 可下载）", 15000)

    # V13-2：v1.2.x 及以前的 QSettings 旧键 → settings/ 命名空间
    _SETTINGS_MIGRATIONS = {
        "safe/enabled": "settings/safe",
        "marks/true": "settings/marks/true",
        "marks/fake": "settings/marks/fake",
        "marks/position": "settings/marks/position",
        "last_folder": "settings/last_folder",
        "recent_folders": "settings/recent_folders",
        "table/header_state": "settings/table/header_state",
    }

    def _migrate_settings(self) -> None:
        """一次性迁移：读旧键 → 写新键 → 删旧键。幂等，可反复执行。"""
        for old, new in self._SETTINGS_MIGRATIONS.items():
            if self._settings.contains(old):
                v = self._settings.value(old)
                if v is not None:
                    self._settings.setValue(new, v)
                self._settings.remove(old)

    def _load_recent_folders(self) -> list:
        raw = self._settings.value("settings/recent_folders", []) or []
        if isinstance(raw, str):
            raw = [raw]
        return [f for f in raw if Path(f).is_dir()]

    def _remember_folder(self, folder: str) -> None:
        """V13-4：加入最近列表（去重置顶，5 个滚动淘汰）并刷新菜单"""
        folders = self._load_recent_folders()
        if folder in folders:
            folders.remove(folder)
        folders.insert(0, folder)
        folders = folders[:self.RECENT_MAX]
        self._settings.setValue("settings/recent_folders", folders)
        self.left_panel.set_recent_folders(folders)

    def _restore_state(self) -> None:
        geo = self._settings.value("window_geometry")
        if geo:
            self.restoreGeometry(geo)
        self.left_panel.chk_safe.setChecked(
            self._settings.value("settings/safe", False, type=bool))
        # V13-2：左栏扫描参数从设置持久化恢复（与设置面板同源）
        self.left_panel.spin_threads.setValue(
            self._settings.value("settings/threads", 8, type=int))
        self.left_panel.spin_seconds.setValue(
            self._settings.value("settings/seconds", 30, type=int))
        # V13-3：恢复列宽/排序状态（QByteArray）
        header_state = self._settings.value("settings/table/header_state")
        if header_state:
            self.result_table.restore_header_state(header_state)
        last = self._settings.value("settings/last_folder", "")
        # V13-4：恢复最近文件夹菜单（无论 last 是否有效都要建）
        self.left_panel.set_recent_folders(self._load_recent_folders())
        if last and Path(last).is_dir():
            self.set_folder(last)
            self.log_panel.log("已恢复上次使用的文件夹")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("window_geometry", self.saveGeometry())
        # V13-3：保存列宽/排序状态
        self._settings.setValue("settings/table/header_state",
                                self.result_table.save_header_state())
        # M2：退出前先停止线程池（协作式取消），再关闭窗口
        if self._scan_manager.is_running:
            self._scan_manager.stop()
            self._scan_manager.wait_done(3000)
        super().closeEvent(event)

    # ---------------- 整窗拖拽 ----------------
    @staticmethod
    def _first_local_dir(mime) -> str:
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if Path(path).is_dir():
                    return path
        return ""

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        # M4：扫描中锁定整窗拖拽（②补充交互，与左栏拖拽区一致）
        if self._scan_manager.is_running:
            event.ignore()
            return
        if event.mimeData().hasUrls() and self._first_local_dir(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if self._scan_manager.is_running:
            event.ignore()
            return
        folder = self._first_local_dir(event.mimeData())
        if folder:
            self.set_folder(folder)
            event.acceptProposedAction()

    # ---------------- 业务槽（M1 为占位实现） ----------------
    def set_folder(self, folder: str) -> None:
        # 审查修复⑥：切换到不同文件夹时清空上次结果——否则旧文件夹的
        # 结果残留，去重/重命名/歌单会把 A 的文件写进 B 的目录树
        changed = folder != self._current_folder
        if changed and self._results:
            self._results.clear()
            self.result_table.clear_rows()
            self.progress.reset()
            self.summary_bar.reset()
            self.result_table.set_actions_enabled(False)
            self.log_panel.log("已切换文件夹，清空上次扫描结果")
        self._current_folder = folder
        self.left_panel.set_folder_display(folder)
        self._settings.setValue("settings/last_folder", folder)
        self._remember_folder(folder)
        self.log_panel.log(f"当前文件夹: {folder}")
        self._update_safe_label()
        self._refresh_restore_enabled()

    # ---------------- 质量标记 / 使用指南 / 安全模式 ----------------
    def on_settings(self) -> None:
        return self.ops.on_settings()

    def on_guide(self) -> None:
        return self.ops.on_guide()

    def on_unlock(self) -> None:
        return self.ops.on_unlock()

    @property
    def safe_mode_on(self) -> bool:
        return self.left_panel.chk_safe.isChecked()

    def _safe_root(self) -> Path:
        """安全输出目录：目标文件夹同级的 <文件夹名>_安全输出/"""
        target = Path(self._current_folder)
        return target.parent / f"{target.name}_安全输出"

    def _safe_map(self, path: str) -> str:
        """把目标文件夹内的路径映射到安全输出目录（保留相对子目录）"""
        p = Path(path)
        try:
            rel = p.relative_to(self._current_folder)
        except ValueError:
            rel = Path(p.name)
        return str(self._safe_root() / rel)

    def _on_safe_toggled(self, checked: bool) -> None:
        self._settings.setValue("settings/safe", checked)
        self._update_safe_label()
        self._refresh_restore_enabled()
        if checked:
            self.log_panel.log(
                "安全模式已开启：所有产出将写入安全输出目录，原文件零改动")
        else:
            self.log_panel.log("安全模式已关闭：重命名/去重将直接作用于原文件")

    def _update_safe_label(self) -> None:
        if self.safe_mode_on and self._current_folder:
            self.left_panel.safe_label.setText(
                f"安全输出: {self._safe_root()}")
        elif self.safe_mode_on:
            self.left_panel.safe_label.setText(
                "安全模式已开启（选择文件夹后显示输出目录）")
        else:
            self.left_panel.safe_label.setText("")

    def _refresh_restore_enabled(self) -> None:
        """还原按钮：非安全模式、非扫描中、当前文件夹有可还原记录时可用"""
        # 兜底：初始化早期（_scan_manager 未建）也允许调用，视为非扫描中
        mgr = getattr(self, '_scan_manager', None)
        ok = (not self.safe_mode_on
              and (mgr is None or not mgr.is_running)
              and bool(self._current_folder)
              and bool(load_restore_map(
                  str(Path(self._current_folder) / CLEAR_DIR_NAME))))
        self.result_table.set_restore_enabled(ok)

    def on_select_folder(self) -> None:
        return self.scan.on_select_folder()

    def on_start_scan(self) -> None:
        return self.scan.on_start_scan()

    def on_stop_scan(self) -> None:
        return self.scan.on_stop_scan()

    # ---------------- 扫描回调 ----------------
    def on_file_started(self, filepath: str) -> None:
        return self.scan.on_file_started(filepath)

    def on_file_done(self, item: ResultItem) -> None:
        return self.scan.on_file_done(item)

    def on_progress(self, done: int, total: int) -> None:
        return self.scan.on_progress(done, total)

    def on_scan_finished(self, stopped: bool) -> None:
        return self.scan.on_scan_finished(stopped)

    def _refresh_summary(self) -> None:
        done_items = [i for i in self._results.values()
                      if i.status == STATUS_DONE]
        total = len(done_items)
        fake = sum(1 for i in done_items if i.is_fake)
        avg = (sum(i.score for i in done_items) / total) if total else 0.0
        self.summary_bar.update_stats(total, fake, avg)

    # ---------------- 操作范围选择（M4.6） ----------------
    def _pick_scope(self, title: str, allow_fake_filter: bool = False,
                    allow_true_filter: bool = False):
        return self.ops._pick_scope(title, allow_fake_filter,
                                    allow_true_filter)

    def on_export_csv(self) -> None:
        return self.ops.on_export_csv()

    def on_report(self) -> None:
        return self.ops.on_report()

    def on_rename(self) -> None:
        return self.ops.on_rename()



    # ---------------- M4.5 新功能槽 ----------------
    def on_show_detail(self, filepath: str) -> None:
        return self.ops.on_show_detail(filepath)

    def on_compare(self) -> None:
        return self.ops.on_compare()

    def on_dedupe(self) -> None:
        return self.ops.on_dedupe()




    def on_restore(self) -> None:
        return self.ops.on_restore()

    def on_tag_rename(self) -> None:
        return self.ops.on_tag_rename()

    def on_playlist(self) -> None:
        return self.ops.on_playlist()
