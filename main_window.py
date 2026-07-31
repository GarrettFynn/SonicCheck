#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗口：三栏布局（左栏 + 右侧主面板 + 底部日志）、整窗拖拽、状态记忆"""

import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QIcon
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                             QLabel, QMainWindow, QMessageBox, QPushButton,
                             QVBoxLayout, QWidget)

from core.analyzer import AudioAnalyzer
from core.csv_exporter import default_csv_name, export_csv
from core.deduper import (CLEAR_DIR_NAME, build_clear_plan,
                          build_restore_plan, execute_move_plan,
                          find_duplicate_groups, prune_restore_map,
                          record_restore_map, load_restore_map)
from core.ffmpeg_locator import find_ffmpeg, find_ffprobe
from core.playlist import copy_matched
from core.quality_marks import set_config as set_mark_config
from core.renamer import (KIND_AUDIO, build_rename_plan, execute_copy_plan,
                          execute_plan)
from core.scanner import find_audio_files
from core.tag_renamer import (FMT_ARTIST_TITLE, FMT_TITLE_ARTIST,
                              build_tag_rename_plan)
from models.result_item import (STATUS_ANALYZING, STATUS_DONE, ResultItem)
from threads.scan_manager import ScanManager
from widgets.compare_dialog import MODE_COMPARE, MODE_DEDUPE, CompareDialog
from widgets.detail_dialog import DetailDialog
from widgets.guide_dialog import GuideDialog
from widgets.left_panel import LeftPanel
from widgets.log_panel import LogPanel
from widgets.playlist_dialog import PlaylistDialog
from widgets.progress_bar import ProgressWidget
from widgets.quality_mark_dialog import QualityMarkDialog
from widgets.rename_dialog import RenameDialog
from widgets.result_table import ResultTable
from widgets.summary_bar import SummaryBar

APP_NAME = "SonicCheck"
DISPLAY_NAME = "声鉴·曲库管家"
APP_VERSION = "0.2.0"
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{DISPLAY_NAME} {APP_NAME} v{APP_VERSION}")
        self.resize(DEFAULT_W, DEFAULT_H)
        self.setMinimumSize(MIN_W, MIN_H)
        self.setAcceptDrops(True)  # P0：整窗接受文件夹拖拽

        self._settings = QSettings()
        self._current_folder = ""
        self._results = {}  # filepath -> ResultItem（M3 导出/重命名的数据源）

        self._load_marks_config()
        self._build_ui()
        self._connect_signals()
        self._restore_state()
        self._init_engine()
        self.log_panel.log("程序启动，请选择或拖拽一个音乐文件夹")
        self._refresh_restore_enabled()
        self._update_safe_label()
        # 首次启动自动打开使用指南（非模态，可边读边用）
        if not self._settings.value("guide_seen", False, type=bool):
            self._settings.setValue("guide_seen", True)
            self._first_guide = GuideDialog(self)
            self._first_guide.show()

    def _load_marks_config(self) -> None:
        """从 QSettings 恢复质量标记配置并注入 core 层"""
        from core.quality_marks import POS_SUFFIX
        set_mark_config(
            mark_true=self._settings.value("marks/true", "_真无损"),
            mark_fake=self._settings.value("marks/fake", "_假无损"),
            position=self._settings.value("marks/position", POS_SUFFIX))

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
        self.btn_settings.setEnabled(False)  # P2 二期开放
        self.btn_settings.setToolTip("设置面板将在二期开放，当前请使用左栏的线程数/分析时长")

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
        lp.marks_clicked.connect(self.on_marks)
        lp.guide_clicked.connect(self.on_guide)
        lp.chk_safe.toggled.connect(self._on_safe_toggled)
        self.result_table.export_clicked.connect(self.on_export_csv)
        self.result_table.rename_clicked.connect(self.on_rename)
        self.result_table.compare_clicked.connect(self.on_compare)
        self.result_table.dedupe_clicked.connect(self.on_dedupe)
        self.result_table.restore_clicked.connect(self.on_restore)
        self.result_table.tag_rename_clicked.connect(self.on_tag_rename)
        self.result_table.playlist_clicked.connect(self.on_playlist)
        self.result_table.detail_requested.connect(self.on_show_detail)

    # ---------------- 状态记忆（⑦-10） ----------------
    def _restore_state(self) -> None:
        geo = self._settings.value("window_geometry")
        if geo:
            self.restoreGeometry(geo)
        self.left_panel.chk_safe.setChecked(
            self._settings.value("safe/enabled", False, type=bool))
        last = self._settings.value("last_folder", "")
        if last and Path(last).is_dir():
            self.set_folder(last)
            self.log_panel.log("已恢复上次使用的文件夹")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("window_geometry", self.saveGeometry())
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
        self._current_folder = folder
        self.left_panel.set_folder_display(folder)
        self._settings.setValue("last_folder", folder)
        self.log_panel.log(f"当前文件夹: {folder}")
        self._update_safe_label()
        self._refresh_restore_enabled()

    # ---------------- 质量标记 / 使用指南 / 安全模式 ----------------
    def on_marks(self) -> None:
        dialog = QualityMarkDialog(self)
        if dialog.exec() != QualityMarkDialog.DialogCode.Accepted:
            return
        v = dialog.values()
        set_mark_config(v["mark_true"], v["mark_fake"], v["position"])
        self._settings.setValue("marks/true", v["mark_true"])
        self._settings.setValue("marks/fake", v["mark_fake"])
        self._settings.setValue("marks/position", v["position"])
        pos_text = "后缀" if v["position"] == "suffix" else "前缀"
        self.log_panel.log(
            f"质量标记已更新: 真={v['mark_true']} 假={v['mark_fake']} "
            f"（{pos_text}），对之后的重命名/标签改名生效")

    def on_guide(self) -> None:
        GuideDialog(self).exec()

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
        self._settings.setValue("safe/enabled", checked)
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
        ok = (not self.safe_mode_on
              and not self._scan_manager.is_running
              and bool(self._current_folder)
              and bool(load_restore_map(
                  str(Path(self._current_folder) / CLEAR_DIR_NAME))))
        self.result_table.set_restore_enabled(ok)

    def on_select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择音乐文件夹", self._current_folder or "")
        if folder:
            self.set_folder(folder)

    def on_start_scan(self) -> None:
        if not self._current_folder:
            self.log_panel.log("尚未选择文件夹，无法开始扫描")
            return
        if self._scan_manager.is_running:
            return  # 扫描中重复点击（左栏已锁定，双保险）
        if not find_ffmpeg() or not find_ffprobe():
            # M4 前置防护：宁可拒绝扫描，也不让整批文件全进 error 行
            self.log_panel.log_error(
                "未找到 ffmpeg/ffprobe，无法扫描：请确认 resources/ffmpeg/ "
                "内置或系统 PATH 可用后重启程序")
            return

        files, skipped_dirs = find_audio_files(self._current_folder)
        if skipped_dirs > 0:
            self.log_panel.log_error(
                f"有 {skipped_dirs} 个子文件夹无法访问（权限不足或已损坏），已跳过")
        if not files:
            self.log_panel.log("该文件夹（含子文件夹）中没有找到音频文件")
            return

        # ⑦-8：重复开始 = 清空重扫
        self.result_table.clear_rows()
        self._results.clear()
        self.progress.reset()
        self.summary_bar.reset()
        self.result_table.set_actions_enabled(False)

        for fp in files:
            self.result_table.add_row(ResultItem(
                filename=Path(fp).name, stem=Path(fp).stem, filepath=fp))

        self.left_panel.set_scanning(True)
        self.result_table.set_restore_enabled(False)
        self.progress.set_progress(0, len(files))
        self._scan_manager.start(files,
                                 self.left_panel.thread_count,
                                 self.left_panel.analyze_seconds)

    def on_stop_scan(self) -> None:
        self._scan_manager.stop()

    # ---------------- 扫描回调 ----------------
    def on_file_started(self, filepath: str) -> None:
        self.progress.set_current_file(Path(filepath).name)
        self.result_table.update_row_by_path(filepath, ResultItem(
            filename=Path(filepath).name, stem=Path(filepath).stem,
            filepath=filepath, status=STATUS_ANALYZING))

    def on_file_done(self, item: ResultItem) -> None:
        self._results[item.filepath] = item
        self.result_table.update_row_by_path(item.filepath, item)
        if item.status == STATUS_DONE:
            kind = "假无损" if item.is_fake else "真无损"
            self.log_panel.log(f"{item.filename} → {kind}，评分 {item.score}")
        else:
            self.log_panel.log(f"❌ {item.filename} 分析失败: {item.error_message}")
        self._refresh_summary()

    def on_progress(self, done: int, total: int) -> None:
        self.progress.set_progress(done, total)

    def on_scan_finished(self, stopped: bool) -> None:
        self.left_panel.set_scanning(False)
        self.progress.set_current_file("—")
        self._refresh_summary()
        self._refresh_restore_enabled()
        done_items = [i for i in self._results.values()
                      if i.status == STATUS_DONE]
        # M3：有分析成功的结果才可用导出/重命名
        self.result_table.set_actions_enabled(len(done_items) > 0)
        # M4：结束汇总日志（自然完成与停止都给出明细）
        true_cnt = sum(1 for i in done_items if not i.is_fake)
        fake_cnt = len(done_items) - true_cnt
        err_cnt = sum(1 for i in self._results.values()
                      if i.status != STATUS_DONE)
        detail = (f"真无损 {true_cnt} 首，假无损 {fake_cnt} 首，"
                  f"失败 {err_cnt} 首")
        if stopped:
            self.log_panel.log(f"扫描已停止，已出结果 {len(done_items)} 首"
                               f"（{detail}），结果保留在表格中")
        else:
            self.log_panel.log(f"扫描完成：共 {len(done_items)} 首（{detail}）")

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
        """返回作用域内的 ResultItem 列表；用户取消返回 None。

        有选中行或允许真假无损筛选时弹窗询问，否则直接全部。
        """
        sel_paths = self.result_table.selected_paths()
        sel_items = [self._results[p] for p in sel_paths
                     if p in self._results]
        if not sel_items and not allow_fake_filter and not allow_true_filter:
            return list(self._results.values())

        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText("本次操作作用于哪些文件？")
        btn_all = box.addButton("全部文件",
                                QMessageBox.ButtonRole.AcceptRole)
        btn_sel = box.addButton(f"仅选中行（{len(sel_items)}）",
                                QMessageBox.ButtonRole.AcceptRole) \
            if sel_items else None
        btn_fake = box.addButton("仅假无损",
                                 QMessageBox.ButtonRole.AcceptRole) \
            if allow_fake_filter else None
        btn_true = box.addButton("仅真无损",
                                 QMessageBox.ButtonRole.AcceptRole) \
            if allow_true_filter else None
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_all:
            return list(self._results.values())
        if btn_sel is not None and clicked is btn_sel:
            return sel_items
        if btn_fake is not None and clicked is btn_fake:
            return [i for i in self._results.values() if i.is_fake]
        if btn_true is not None and clicked is btn_true:
            return [i for i in self._results.values() if not i.is_fake]
        return None

    def on_export_csv(self) -> None:
        items = self._pick_scope("导出 CSV", allow_fake_filter=True,
                                 allow_true_filter=True)
        if not items:
            if items is not None:
                self.log_panel.log("所选范围没有文件")
            return
        if not any(i.status == STATUS_DONE for i in items):
            self.log_panel.log("所选范围没有可导出的分析结果")
            return
        default = str(Path(self._current_folder or str(Path.home()))
                        / default_csv_name())
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", default, "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            rows = export_csv(items, path)
        except OSError as exc:
            self.log_panel.log_error(f"CSV 导出失败: {exc}")
            return
        self.log_panel.log(f"CSV 已导出（{rows} 行）: {path}")

    def on_rename(self) -> None:
        items = self._pick_scope("一键重命名")
        if items is None:
            return
        plan, skipped = build_rename_plan(items)
        if not plan:
            self.log_panel.log("没有需要重命名的文件（均已是目标名或被跳过）")
            for sk in skipped:
                if "目标已存在" in sk.why:
                    self.log_panel.log_error(f"跳过: {sk.path} ← {sk.why}")
            return

        dialog = RenameDialog(plan, skipped, self)
        if dialog.exec() != RenameDialog.DialogCode.Accepted:
            self.log_panel.log("重命名已取消，未执行任何操作")
            return

        if self.safe_mode_on:
            for op in plan:  # 产出重定向：复制到安全输出目录，原文件不动
                op.new_path = self._safe_map(op.new_path)
            results = execute_copy_plan(plan)
            self.log_panel.log(
                f"安全模式：原件未动，改名件复制到 {self._safe_root()}")
        else:
            results = execute_plan(plan)
        ok_cnt = 0
        for op, ok, err in results:
            old_name = Path(op.old_path).name
            if ok:
                ok_cnt += 1
                self.log_panel.log(
                    f"{old_name} → {Path(op.new_path).name}")
                if op.kind == KIND_AUDIO:
                    self._on_audio_renamed(op.old_path, op.new_path)
            else:
                self.log_panel.log_error(f"重命名失败: {old_name} ← {err}")
        self.log_panel.log(
            f"重命名完成: 成功 {ok_cnt} 个，失败 {len(results) - ok_cnt} 个")

    def _on_audio_renamed(self, old_path: str, new_path: str) -> None:
        """音频改名后同步内存结果与表格行（歌词改名无需同步）"""
        item = self._results.pop(old_path, None)
        if item is None:
            return
        p = Path(new_path)
        item.filepath = str(p)
        item.filename = p.name
        item.stem = p.stem
        item.detail['filepath'] = str(p)
        item.detail['meta']['filename'] = p.name
        self._results[item.filepath] = item
        self.result_table.update_row_by_path(old_path, item)

    # ---------------- M4.5 新功能槽 ----------------
    def on_show_detail(self, filepath: str) -> None:
        """功能 A：双击行查看详情"""
        item = self._results.get(filepath)
        if item and item.status == STATUS_DONE:
            DetailDialog(item, self).exec()

    def on_compare(self) -> None:
        """功能 B：同名/同内容文件对比"""
        groups = find_duplicate_groups(list(self._results.values()))
        if not groups:
            self.log_panel.log("未发现同名或同内容的重复文件")
            return
        CompareDialog(groups, MODE_COMPARE, self).exec()

    def on_dedupe(self) -> None:
        """功能 C：去重清除（C1 移到 _待清除/，可恢复；安全模式=仅索引清除）"""
        groups = find_duplicate_groups(list(self._results.values()))
        if not groups:
            self.log_panel.log("未发现同名或同内容的重复文件")
            return
        dialog = CompareDialog(groups, MODE_DEDUPE, self)
        if dialog.exec() != CompareDialog.DialogCode.Accepted:
            self.log_panel.log("去重清除已取消，未移动任何文件")
            return

        if self.safe_mode_on:
            # 安全模式：不碰任何文件，只把被清除项从结果列表移除
            cleared = [i for g in groups for i in g.clear]
            for i in cleared:
                self._results.pop(i.filepath, None)
            self.result_table.remove_rows_by_paths(
                [i.filepath for i in cleared])
            self._refresh_summary()
            self.log_panel.log(
                f"安全模式：{len(cleared)} 个被清除项已从结果列表移除"
                "（原文件未动）；保留项将在产出操作时复制到安全输出目录")
            return

        plan, _ = build_clear_plan(groups, self._current_folder)
        results = execute_move_plan(plan)
        ok_cnt = 0
        moved = []  # (new_path, old_path) 供还原清单
        for op, ok, err in results:
            name = Path(op.old_path).name
            if ok:
                ok_cnt += 1
                moved.append((op.new_path, op.old_path))
                self.log_panel.log(
                    f"已移入 {CLEAR_DIR_NAME}/: {name}（{op.group_label}）")
                if op.kind == KIND_AUDIO:
                    self._on_audio_renamed(op.old_path, op.new_path)
            else:
                self.log_panel.log(f"移动失败: {name} ← {err}")
        if moved:
            record_restore_map(
                str(Path(self._current_folder) / CLEAR_DIR_NAME), moved)
        self._refresh_restore_enabled()
        self.log_panel.log(
            f"去重完成: 移入 {CLEAR_DIR_NAME}/ 共 {ok_cnt} 个，"
            f"失败 {len(results) - ok_cnt} 个；可点「还原清除」移回，"
            f"确认无误后手动删除该目录")

    def on_restore(self) -> None:
        """功能 C+：把 _待清除/ 里的文件按记录移回原位"""
        clear_dir = str(Path(self._current_folder) / CLEAR_DIR_NAME)
        plan, skipped = build_restore_plan(clear_dir)
        for path, why in skipped:
            self.log_panel.log(f"还原跳过: {Path(path).name} ← {why}")
        if not plan:
            self.log_panel.log("没有可还原的文件（_待清除 为空或已全部还原）")
            self._refresh_restore_enabled()
            return

        box = QMessageBox(self)
        box.setWindowTitle("还原清除")
        box.setText(f"将把 {len(plan)} 个文件从 {CLEAR_DIR_NAME}/ 移回原来的位置，继续？")
        box.addButton("还原", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton().text() != "还原":
            self.log_panel.log("还原已取消")
            return

        results = execute_move_plan(plan)
        ok_cnt = 0
        restored = []
        for op, ok, err in results:
            name = Path(op.new_path).name
            if ok:
                ok_cnt += 1
                restored.append(op.old_path)
                self.log_panel.log(f"已还原: {name}")
                if op.kind == 'audio':
                    self._on_audio_renamed(op.old_path, op.new_path)
            else:
                self.log_panel.log_error(f"还原失败: {name} ← {err}")
        if restored:
            prune_restore_map(clear_dir, restored)
        self._refresh_restore_enabled()
        self.log_panel.log(
            f"还原完成: 成功 {ok_cnt} 个，失败 {len(results) - ok_cnt} 个")

    def on_tag_rename(self) -> None:
        """功能 D：文件名规范化为 歌名-歌手 / 歌手-歌名"""
        items = self._pick_scope("标签改名")
        if items is None:
            return
        choice, ok = QInputDialog.getItem(
            self, "标签改名", "命名格式:",
            ["歌名-歌手", "歌手-歌名"], 0, False)
        if not ok:
            return
        fmt = FMT_TITLE_ARTIST if choice == "歌名-歌手" else FMT_ARTIST_TITLE

        plan, skipped = build_tag_rename_plan(items, fmt)
        if not plan:
            self.log_panel.log("没有可按标签改名的文件（缺标签或已是目标名）")
            for sk in skipped:
                if "目标已存在" in sk.why:
                    self.log_panel.log_error(f"跳过: {sk.path} ← {sk.why}")
            return

        dialog = RenameDialog(plan, skipped, self)
        if dialog.exec() != RenameDialog.DialogCode.Accepted:
            self.log_panel.log("标签改名已取消，未执行任何操作")
            return

        if self.safe_mode_on:
            for op in plan:  # 产出重定向：复制到安全输出目录，原文件不动
                op.new_path = self._safe_map(op.new_path)
            results = execute_copy_plan(plan)
            self.log_panel.log(
                f"安全模式：原件未动，改名件复制到 {self._safe_root()}")
        else:
            results = execute_plan(plan)
        ok_cnt = 0
        for op, ok2, err in results:
            old_name = Path(op.old_path).name
            if ok2:
                ok_cnt += 1
                self.log_panel.log(
                    f"{old_name} → {Path(op.new_path).name}")
                if op.kind == KIND_AUDIO:
                    self._on_audio_renamed(op.old_path, op.new_path)
            else:
                self.log_panel.log_error(f"改名失败: {old_name} ← {err}")
        self.log_panel.log(
            f"标签改名完成: 成功 {ok_cnt} 个，失败 {len(results) - ok_cnt} 个")

    def on_playlist(self) -> None:
        """功能 E：歌单匹配复制到新文件夹"""
        dialog = PlaylistDialog(list(self._results.values()), self)
        if dialog.exec() != PlaylistDialog.DialogCode.Accepted:
            return
        mr = dialog.match_result
        if mr is None or not mr.matched:
            self.log_panel.log("歌单没有匹配到任何文件，未执行复制")
            return

        copied, failed, dest_dir = copy_matched(
            mr.matched,
            str(self._safe_root()) if self.safe_mode_on
            else self._current_folder,
            dialog.playlist_name)
        audio_copied = len({c[1] for c in copied})
        if self.safe_mode_on:
            self.log_panel.log(f"安全模式：歌单文件夹建在安全输出目录 {dest_dir}")
        self.log_panel.log(
            f"歌单「{dialog.playlist_name}」: 复制 {audio_copied} 个文件到 "
            f"{dest_dir}（未匹配 {len(mr.unmatched)} 首）")
        for src, err in failed:
            self.log_panel.log_error(
                f"复制失败: {Path(src).name} ← {err}")
        if mr.unmatched:
            miss_text = "\n".join(
                f"{e.title} - {e.artist}" if e.artist else e.title
                for e in mr.unmatched)
            try:
                miss_path = Path(dest_dir) / "未匹配清单.txt"
                miss_path.write_text(miss_text, encoding="utf-8")
                self.log_panel.log(f"未匹配清单已写入: {miss_path}")
            except OSError as exc:
                self.log_panel.log_error(f"未匹配清单写入失败: {exc}")
            # 未匹配清单同步到剪贴板，方便去别处置歌
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(miss_text)
            self.log_panel.log("未匹配清单已复制到剪贴板")
