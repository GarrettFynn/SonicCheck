#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗口业务控制器（V20-2，从 main_window 纯搬移，逻辑零改动）

ScanController：扫描启停、worker 回调、进度与汇总（原 MainWindow 的
on_start_scan…_refresh_summary 一段）。
FileOpsController：导出/重命名/去重/还原/歌单/设置/解锁等文件操作
（原 on_export_csv…on_playlist 一段）。

控制器持窗口引用（mw），经 mw 访问 UI 控件与 QSettings——搬移是字面
级的，方法体除 self→mw 外不改动。MainWindow 中的同名方法变成一行
转发（保持对外形状与旧测试兼容）。
"""

import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QInputDialog,
                             QMessageBox, QProgressDialog)

from core.analyzer import AudioAnalyzer
from core.csv_exporter import default_csv_name, export_csv
from core.deduper import (CLEAR_DIR_NAME, build_clear_plan,
                          build_restore_plan, execute_move_plan,
                          find_duplicate_groups, load_restore_map,
                          prune_restore_map, record_restore_map)
from core.ffmpeg_locator import find_ffmpeg, find_ffprobe
from core.html_report import export_html_report
from core.fingerprint import fingerprint_file
from core.playlist import copy_matched
from core.quality_marks import POS_SUFFIX
from core.renamer import (KIND_AUDIO, build_rename_plan, execute_copy_plan,
                          execute_plan)
from core.tag_renamer import (FMT_ARTIST_TITLE, FMT_TITLE_ARTIST,
                              build_tag_rename_plan)
from models.result_item import STATUS_ANALYZING, STATUS_DONE, ResultItem
from threads.scan_manager import ScanManager
from widgets.compare_dialog import MODE_COMPARE, MODE_DEDUPE, CompareDialog
from widgets.detail_dialog import DetailDialog
from widgets.rename_dialog import RenameDialog
from widgets.settings_dialog import SettingsDialog
from widgets.unlock_dialog import UnlockDialog


class _FpSignals(QObject):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(dict)


class _FingerprintWorker(QRunnable):
    """后台批量计算 chromaprint 指纹（V14-2b）"""

    def __init__(self, paths: list, cancel: threading.Event):
        super().__init__()
        self.paths = paths
        self.cancel = cancel
        self.signals = _FpSignals()

    def run(self) -> None:
        fps = {}
        total = len(self.paths)
        for i, p in enumerate(self.paths):
            if self.cancel.is_set():
                break
            fps[p] = fingerprint_file(p, cancel_check=self.cancel.is_set)
            self.signals.progress.emit(i + 1, total)
        self.signals.done.emit(fps)


class ScanController(QObject):
    """扫描启停与回调（原 MainWindow.on_start_scan 一段，字面搬移）"""

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw

    # ---- 属性转发（窗口代码习惯 mw.scan.xxx） ----
    @property
    def manager(self) -> ScanManager:
        return self.mw._scan_manager

    def on_select_folder(self) -> None:
        mw = self.mw
        folder = QFileDialog.getExistingDirectory(
            mw, "选择音乐文件夹", mw._current_folder or "")
        if folder:
            mw.set_folder(folder)

    def on_start_scan(self) -> None:
        mw = self.mw
        if not mw._current_folder:
            mw.log_panel.log("尚未选择文件夹，无法开始扫描")
            return
        if mw._scan_manager.is_running:
            return  # 扫描中重复点击（左栏已锁定，双保险）
        if not find_ffmpeg() or not find_ffprobe():
            # M4 前置防护：宁可拒绝扫描，也不让整批文件全进 error 行
            mw.log_panel.log_error(
                "未找到 ffmpeg/ffprobe，无法扫描：请确认 resources/ffmpeg/ "
                "内置或系统 PATH 可用后重启程序")
            return

        files, skipped_dirs = find_audio_files_local(mw._current_folder)
        if skipped_dirs > 0:
            mw.log_panel.log_error(
                f"有 {skipped_dirs} 个子文件夹无法访问（权限不足或已损坏），已跳过")
        if not files:
            mw.log_panel.log("该文件夹（含子文件夹）中没有找到音频文件")
            return

        # ⑦-8：重复开始 = 清空重扫
        mw.result_table.clear_rows()
        mw._results.clear()
        mw.progress.reset()
        mw.summary_bar.reset()
        mw.result_table.set_actions_enabled(False)

        for fp in files:
            mw.result_table.add_row(ResultItem(
                filename=Path(fp).name, stem=Path(fp).stem, filepath=fp))

        mw.left_panel.set_scanning(True)
        mw.result_table.set_restore_enabled(False)
        mw.progress.set_progress(0, len(files))
        mw._scan_manager.start(files,
                               mw.left_panel.thread_count,
                               mw.left_panel.analyze_seconds)

    def on_stop_scan(self) -> None:
        self.mw._scan_manager.stop()

    def on_file_started(self, filepath: str) -> None:
        mw = self.mw
        mw.progress.set_current_file(Path(filepath).name)
        mw.result_table.update_row_by_path(filepath, ResultItem(
            filename=Path(filepath).name, stem=Path(filepath).stem,
            filepath=filepath, status=STATUS_ANALYZING))

    def on_file_done(self, item: ResultItem) -> None:
        mw = self.mw
        mw._results[item.filepath] = item
        mw.result_table.update_row_by_path(item.filepath, item)
        if item.status == STATUS_DONE:
            kind = "假无损" if item.is_fake else "真无损"
            mw.log_panel.log(f"{item.filename} → {kind}，评分 {item.score}")
        else:
            mw.log_panel.log(f"❌ {item.filename} 分析失败: {item.error_message}")
        mw._refresh_summary()

    def on_progress(self, done: int, total: int) -> None:
        self.mw.progress.set_progress(done, total)

    def on_scan_finished(self, stopped: bool) -> None:
        mw = self.mw
        mw.left_panel.set_scanning(False)
        mw.progress.set_current_file("—")
        # B1：退出批量模式——恢复排序/过滤（含停止路径，必须最先执行）
        mw.result_table.end_update()
        # 停止时把「等待/分析中…」的行统一标记为已取消，不再悬挂
        cancelled_rows = mw.result_table.mark_unfinished_cancelled() \
            if stopped else 0
        mw._refresh_summary()
        mw._refresh_restore_enabled()
        done_items = [i for i in mw._results.values()
                      if i.status == STATUS_DONE]
        # M3：有分析成功的结果才可用导出/重命名
        mw.result_table.set_actions_enabled(len(done_items) > 0)
        # M4：结束汇总日志（自然完成与停止都给出明细）
        true_cnt = sum(1 for i in done_items if not i.is_fake)
        fake_cnt = len(done_items) - true_cnt
        err_cnt = sum(1 for i in mw._results.values()
                      if i.status != STATUS_DONE)
        detail = (f"真无损 {true_cnt} 首，假无损 {fake_cnt} 首，"
                  f"失败 {err_cnt} 首")
        if stopped:
            suffix = f"，{cancelled_rows} 首已取消" if cancelled_rows else ""
            mw.log_panel.log(f"扫描已停止，已出结果 {len(done_items)} 首"
                             f"（{detail}{suffix}），结果保留在表格中")
        else:
            mw.log_panel.log(f"扫描完成：共 {len(done_items)} 首（{detail}）")


def find_audio_files_local(root: str):
    """转发 core.scanner（模块尾部 import 避免循环；字面搬移期间保持
    调用点形状不变）"""
    from core.scanner import find_audio_files
    return find_audio_files(root)


class FileOpsController(QObject):
    """文件操作（原 MainWindow.on_export_csv 一段，字面搬移）"""

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self._fp_cancel = threading.Event()
        self._fp_dlg = None

    # ---------------- 操作范围选择（M4.6） ----------------
    def _pick_scope(self, title: str, allow_fake_filter: bool = False,
                    allow_true_filter: bool = False):
        mw = self.mw
        sel_paths = mw.result_table.selected_paths()
        sel_items = [mw._results[p] for p in sel_paths
                     if p in mw._results]
        if not sel_items and not allow_fake_filter and not allow_true_filter:
            return list(mw._results.values())

        box = QMessageBox(mw)
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
            return list(mw._results.values())
        if btn_sel is not None and clicked is btn_sel:
            return sel_items
        if btn_fake is not None and clicked is btn_fake:
            return [i for i in mw._results.values() if i.is_fake]
        if btn_true is not None and clicked is btn_true:
            return [i for i in mw._results.values() if not i.is_fake]
        return None

    # ---------------- 导出 ----------------
    def on_export_csv(self) -> None:
        mw = self.mw
        items = self._pick_scope("导出 CSV", allow_fake_filter=True,
                                 allow_true_filter=True)
        if not items:
            if items is not None:
                mw.log_panel.log("所选范围没有文件")
            return
        if not any(i.status == STATUS_DONE for i in items):
            mw.log_panel.log("所选范围没有可导出的分析结果")
            return
        default = str(Path(mw._current_folder or str(Path.home()))
                      / default_csv_name())
        path, _ = QFileDialog.getSaveFileName(
            mw, "导出 CSV", default, "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            rows = export_csv(items, path)
        except OSError as exc:
            mw.log_panel.log_error(f"CSV 导出失败: {exc}")
            return
        mw.log_panel.log(f"CSV 已导出（{rows} 行）: {path}")

    def on_report(self) -> None:
        """V13-5：导出 HTML 报告（决策点②：默认仅假无损出证据卡）"""
        mw = self.mw
        items = self._pick_scope("导出报告", allow_fake_filter=True,
                                 allow_true_filter=True)
        if not items:
            if items is not None:
                mw.log_panel.log("所选范围没有文件")
            return
        if not any(i.status == STATUS_DONE for i in items):
            mw.log_panel.log("所选范围没有可导出的分析结果")
            return
        default = str(Path(mw._current_folder or str(Path.home()))
                      / f"SonicCheck报告_{datetime.now():%Y%m%d_%H%M}.html")
        path, _ = QFileDialog.getSaveFileName(
            mw, "导出 HTML 报告", default, "HTML 文件 (*.html)")
        if not path:
            return
        try:
            rows = export_html_report(items, mw._current_folder, path)
        except OSError as exc:
            mw.log_panel.log_error(f"HTML 报告导出失败: {exc}")
            return
        mw.log_panel.log(f"HTML 报告已导出（{rows} 行）: {path}")

    # ---------------- 设置 / 指南 / 解锁 ----------------
    def on_settings(self) -> None:
        mw = self.mw
        current = {
            "threads": mw.left_panel.spin_threads.value(),
            "seconds": mw.left_panel.spin_seconds.value(),
            "safe": mw.left_panel.chk_safe.isChecked(),
            "mark_true": mw._settings.value(
                "settings/marks/true", "_真无损"),
            "mark_fake": mw._settings.value(
                "settings/marks/fake", "_假无损"),
            "position": mw._settings.value(
                "settings/marks/position", POS_SUFFIX),
            "check_updates": mw._settings.value(
                "settings/check_updates", True, type=bool),
        }
        dialog = SettingsDialog(current, mw)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        v = dialog.values()
        mw.left_panel.spin_threads.setValue(v["threads"])
        mw.left_panel.spin_seconds.setValue(v["seconds"])
        mw.left_panel.chk_safe.setChecked(v["safe"])
        from core.quality_marks import set_config as set_mark_config
        set_mark_config(v["mark_true"], v["mark_fake"], v["position"])
        mw._settings.setValue("settings/marks/true", v["mark_true"])
        mw._settings.setValue("settings/marks/fake", v["mark_fake"])
        mw._settings.setValue("settings/marks/position", v["position"])
        mw._settings.setValue("settings/check_updates",
                              v["check_updates"])
        pos_text = "后缀" if v["position"] == "suffix" else "前缀"
        mw.log_panel.log(
            f"设置已更新: 线程 {v['threads']}，分析 {v['seconds']} 秒，"
            f"标记 真={v['mark_true']} 假={v['mark_fake']}（{pos_text}）")

    def on_guide(self) -> None:
        from widgets.guide_dialog import GuideDialog
        GuideDialog(self.mw).exec()

    def on_unlock(self) -> None:
        """格式解锁：ncm / mflac / qmc 等 → 标准音频（源文件只读）"""
        mw = self.mw
        default_out = (str(Path(mw._current_folder) / "已解锁")
                       if mw._current_folder else "")
        dialog = UnlockDialog(default_out, mw)
        dialog.exec()
        if dialog.results:
            ok_cnt = sum(1 for r in dialog.results if r.ok)
            fail = [r for r in dialog.results if not r.ok]
            mw.log_panel.log(
                f"格式解锁完成：成功 {ok_cnt} 个，失败 {len(fail)} 个")
            for r in fail:
                mw.log_panel.log_error(
                    f"解锁失败: {Path(r.src).name} ← {r.message}")
            if ok_cnt:
                out_dirs = {str(Path(r.dst).parent)
                            for r in dialog.results if r.ok}
                mw.log_panel.log(
                    f"解锁输出目录: {'; '.join(sorted(out_dirs))}")

    # ---------------- 重命名 ----------------
    def on_rename(self) -> None:
        mw = self.mw
        items = self._pick_scope("一键重命名")
        if items is None:
            return
        plan, skipped = build_rename_plan(items)
        if not plan:
            mw.log_panel.log("没有需要重命名的文件（均已是目标名或被跳过）")
            for sk in skipped:
                if "目标已存在" in sk.why:
                    mw.log_panel.log_error(f"跳过: {sk.path} ← {sk.why}")
            return

        dialog = RenameDialog(plan, skipped, mw)
        if dialog.exec() != RenameDialog.DialogCode.Accepted:
            mw.log_panel.log("重命名已取消，未执行任何操作")
            return
        self._run_rename_plan(plan, "重命名")

    def on_tag_rename(self) -> None:
        """功能 D：文件名规范化为 歌名-歌手 / 歌手-歌名"""
        mw = self.mw
        items = self._pick_scope("标签改名")
        if items is None:
            return
        choice, ok = QInputDialog.getItem(
            mw, "标签改名", "命名格式:",
            ["歌名-歌手", "歌手-歌名"], 0, False)
        if not ok:
            return
        fmt = FMT_TITLE_ARTIST if choice == "歌名-歌手" else FMT_ARTIST_TITLE

        plan, skipped = build_tag_rename_plan(items, fmt)
        if not plan:
            mw.log_panel.log("没有可按标签改名的文件（缺标签或已是目标名）")
            for sk in skipped:
                if "目标已存在" in sk.why:
                    mw.log_panel.log_error(f"跳过: {sk.path} ← {sk.why}")
            return

        dialog = RenameDialog(plan, skipped, mw)
        if dialog.exec() != RenameDialog.DialogCode.Accepted:
            mw.log_panel.log("标签改名已取消，未执行任何操作")
            return
        self._run_rename_plan(plan, "标签改名")

    def _run_rename_plan(self, plan: list, action_name: str) -> None:
        """执行改名计划：安全模式重定向到安全输出目录复制，逐条日志
        并同步表格（一键重命名与标签改名两条流程共用）。

        v2.0.1 修复：V20-2 搬移控制器时此方法漏搬（窗口删除、控制器
        未定义），确认改名对话框后 AttributeError 闪退。逻辑自
        v1.4.0 main_window 逐字迁回，self→mw。"""
        mw = self.mw
        if mw.safe_mode_on:
            for op in plan:  # 产出重定向：复制到安全输出目录，原文件不动
                op.new_path = mw._safe_map(op.new_path)
            results = execute_copy_plan(plan)
            mw.log_panel.log(
                f"安全模式：原件未动，改名件复制到 {mw._safe_root()}")
        else:
            results = execute_plan(plan)
        ok_cnt = 0
        for op, ok, err in results:
            old_name = Path(op.old_path).name
            if ok:
                ok_cnt += 1
                mw.log_panel.log(
                    f"{old_name} → {Path(op.new_path).name}")
                if op.kind == KIND_AUDIO:
                    self._on_audio_renamed(op.old_path, op.new_path)
            else:
                mw.log_panel.log_error(
                    f"{action_name}失败: {old_name} ← {err}")
        mw.log_panel.log(
            f"{action_name}完成: 成功 {ok_cnt} 个，"
            f"失败 {len(results) - ok_cnt} 个")

    def _on_audio_renamed(self, old_path: str, new_path: str) -> None:
        """音频改名后同步内存结果与表格行（歌词改名无需同步）"""
        mw = self.mw
        item = mw._results.pop(old_path, None)
        if item is None:
            return
        p = Path(new_path)
        item.filepath = str(p)
        item.filename = p.name
        item.stem = p.stem
        item.detail['filepath'] = str(p)
        item.detail['meta']['filename'] = p.name
        mw._results[item.filepath] = item
        mw.result_table.update_row_by_path(old_path, item)

    # ---------------- 对比 / 去重 / 还原 ----------------
    def on_show_detail(self, filepath: str) -> None:
        """功能 A：双击行查看详情"""
        mw = self.mw
        item = mw._results.get(filepath)
        if item and item.status == STATUS_DONE:
            DetailDialog(item, mw).exec()

    def on_compare(self) -> None:
        """功能 B：同名/同内容文件对比"""
        mw = self.mw
        groups = find_duplicate_groups(list(mw._results.values()))
        if not groups:
            mw.log_panel.log("未发现同名或同内容的重复文件")
            return
        CompareDialog(groups, MODE_COMPARE, mw).exec()

    def on_dedupe(self) -> None:
        """功能 C：去重清除（V14-2b 可选声纹比对）"""
        mw = self.mw
        done_items = [i for i in mw._results.values()
                      if i.status == STATUS_DONE]
        if len(done_items) < 2:
            mw.log_panel.log("可分析的完整结果不足 2 个，无重复可查")
            return

        box = QMessageBox(mw)
        box.setWindowTitle("去重清除")
        box.setText("去重前可选：启用声纹指纹比对")
        box.setInformativeText(
            "对已扫描的文件计算声纹指纹后再分组，可发现改名、不同音质、\n"
            "live/录音室变体等「签名不同但同一演奏」的重复。计算较耗时"
            "（每首约 0.5~2 秒），可随时取消。")
        chk_fp = QCheckBox("启用声纹比对", box)
        box.setCheckBox(chk_fp)
        btn_dedupe = box.addButton("开始去重",
                                   QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not btn_dedupe:
            return
        if not chk_fp.isChecked():
            self._run_dedupe(None)
            return

        paths = [i.filepath for i in done_items]
        self._fp_cancel = threading.Event()
        self._fp_dlg = QProgressDialog(
            f"正在计算声纹指纹（0/{len(paths)}）…", "取消", 0, len(paths),
            mw)
        self._fp_dlg.setWindowModality(
            __import__('PyQt6.QtCore', fromlist=['Qt']).Qt
            .WindowModality.WindowModal)
        self._fp_dlg.setMinimumDuration(0)
        # 关掉 auto 行为：value 到 max 时 autoClose/autoReset 也会发
        # canceled，会把取消标志误置位（正常完成被当作用户取消）；
        # 完成收尾在 _on_fp_done 手动关窗
        self._fp_dlg.setAutoReset(False)
        self._fp_dlg.setAutoClose(False)
        self._fp_dlg.canceled.connect(self._fp_cancel.set)
        worker = _FingerprintWorker(paths, self._fp_cancel)
        worker.signals.progress.connect(self._on_fp_progress)
        # 必须绑 method：lambda 无接收者对象，auto connection 退化为
        # Direct（槽在 worker 线程执行），与主线程 progress 槽竞态
        worker.signals.done.connect(self._on_fp_done)
        QThreadPool.globalInstance().start(worker)

    def _on_fp_progress(self, done: int, total: int) -> None:
        # QProgressDialog.setValue() 内部会 processEvents（Qt 文档行为），
        # value 到 max 时 autoClose 并可能重入处理 done 事件（把 _fp_dlg
        # 置 None）——全程用局部引用，setValue 后必须复查
        dlg = self._fp_dlg
        if dlg is None:
            return
        dlg.setLabelText(f"正在计算声纹指纹（{done}/{total}）…")
        dlg.setValue(done)
        if self._fp_dlg is not dlg:
            return   # setValue 重入期间批次已收尾

    def _on_fp_done(self, fps: dict) -> None:
        dlg, self._fp_dlg = self._fp_dlg, None
        if dlg is not None:
            # close() 会触发 canceled 信号（QDialog closeEvent→cancel
            # 链路），必须先断开，否则正常完成被误判为用户取消
            try:
                dlg.canceled.disconnect(self._fp_cancel.set)
            except TypeError:
                pass
            dlg.close()
        if self._fp_cancel.is_set():
            self.mw.log_panel.log("声纹比对已取消，未执行去重")
            return
        got = sum(1 for v in fps.values() if v)
        self.mw.log_panel.log(
            f"指纹计算完成：{got}/{len(fps)} 首，开始声纹去重")
        self._run_dedupe(fps)

    def _run_dedupe(self, fingerprints: dict) -> None:
        """去重主流程（fingerprints 可为 None）"""
        mw = self.mw
        groups = find_duplicate_groups(list(mw._results.values()),
                                       fingerprints=fingerprints)
        if not groups:
            mw.log_panel.log(
                "未发现同名、同内容或声纹相似的重复文件")
            return
        dialog = CompareDialog(groups, MODE_DEDUPE, mw)
        if dialog.exec() != CompareDialog.DialogCode.Accepted:
            mw.log_panel.log("去重清除已取消，未移动任何文件")
            return

        if mw.safe_mode_on:
            # 安全模式：不碰任何文件，只把被清除项从结果列表移除
            cleared = [i for g in groups for i in g.clear]
            for i in cleared:
                mw._results.pop(i.filepath, None)
            mw.result_table.remove_rows_by_paths(
                [i.filepath for i in cleared])
            mw._refresh_summary()
            mw.log_panel.log(
                f"安全模式：{len(cleared)} 个被清除项已从结果列表移除"
                "（原文件未动）；保留项将在产出操作时复制到安全输出目录")
            return

        plan, _ = build_clear_plan(groups, mw._current_folder)
        results = execute_move_plan(plan)
        ok_cnt = 0
        moved = []  # (new_path, old_path) 供还原清单
        cleared_paths = []  # 已移走的音频路径：从结果表移除（与安全模式一致）
        for op, ok, err in results:
            name = Path(op.old_path).name
            if ok:
                ok_cnt += 1
                moved.append((op.new_path, op.old_path))
                mw.log_panel.log(
                    f"已移入 {CLEAR_DIR_NAME}/: {name}（{op.group_label}）")
                if op.kind == KIND_AUDIO:
                    cleared_paths.append(op.old_path)
            else:
                mw.log_panel.log(f"移动失败: {name} ← {err}")
        if cleared_paths:
            # 被清除项移出结果列表：避免后续重命名/改名误操作 _待清除 里的文件
            for p in cleared_paths:
                mw._results.pop(p, None)
            mw.result_table.remove_rows_by_paths(cleared_paths)
            mw._refresh_summary()
        if moved:
            record_restore_map(
                str(Path(mw._current_folder) / CLEAR_DIR_NAME), moved)
        mw._refresh_restore_enabled()
        mw.log_panel.log(
            f"去重完成: 移入 {CLEAR_DIR_NAME}/ 共 {ok_cnt} 个，"
            f"失败 {len(results) - ok_cnt} 个；可点「还原清除」移回，"
            f"确认无误后手动删除该目录")

    def on_restore(self) -> None:
        """功能 C+：把 _待清除/ 里的文件按记录移回原位"""
        mw = self.mw
        clear_dir = str(Path(mw._current_folder) / CLEAR_DIR_NAME)
        plan, skipped = build_restore_plan(clear_dir)
        for path, why in skipped:
            mw.log_panel.log(f"还原跳过: {Path(path).name} ← {why}")
        if not plan:
            mw.log_panel.log("没有可还原的文件（_待清除 为空或已全部还原）")
            mw._refresh_restore_enabled()
            return

        box = QMessageBox(mw)
        box.setWindowTitle("还原清除")
        box.setText(f"将把 {len(plan)} 个文件从 {CLEAR_DIR_NAME}/ 移回原来的位置，继续？")
        box.addButton("还原", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton().text() != "还原":
            mw.log_panel.log("还原已取消")
            return

        results = execute_move_plan(plan)
        ok_cnt = 0
        restored = []
        for op, ok, err in results:
            name = Path(op.new_path).name
            if ok:
                ok_cnt += 1
                restored.append(op.old_path)
                mw.log_panel.log(f"已还原: {name}")
                if op.kind == 'audio':
                    self._on_audio_renamed(op.old_path, op.new_path)
            else:
                mw.log_panel.log_error(f"还原失败: {name} ← {err}")
        if restored:
            prune_restore_map(clear_dir, restored)
        mw._refresh_restore_enabled()
        mw.log_panel.log(
            f"还原完成: 成功 {ok_cnt} 个，失败 {len(results) - ok_cnt} 个")

    # ---------------- 歌单 ----------------
    def on_playlist(self) -> None:
        """功能 E：歌单匹配复制到新文件夹"""
        from PyQt6.QtWidgets import QApplication
        from widgets.playlist_dialog import PlaylistDialog
        mw = self.mw
        dialog = PlaylistDialog(list(mw._results.values()), mw)
        if dialog.exec() != PlaylistDialog.DialogCode.Accepted:
            return
        mr = dialog.match_result
        if mr is None or not mr.matched:
            mw.log_panel.log("歌单没有匹配到任何文件，未执行复制")
            return

        try:
            copied, failed, dest_dir = copy_matched(
                mr.matched,
                str(mw._safe_root()) if mw.safe_mode_on
                else mw._current_folder,
                dialog.playlist_name)
        except OSError as exc:
            # 歌单名净化后仍可能撞 Windows 保留名/目标被占用等
            mw.log_panel.log_error(f"歌单文件夹创建失败: {exc}")
            return
        audio_copied = len({c[1] for c in copied})
        if mw.safe_mode_on:
            mw.log_panel.log(
                f"安全模式：歌单文件夹建在安全输出目录 {dest_dir}")
        mw.log_panel.log(
            f"歌单「{dialog.playlist_name}」: 复制 {audio_copied} 个文件到 "
            f"{dest_dir}（未匹配 {len(mr.unmatched)} 首）")
        for src, err in failed:
            mw.log_panel.log_error(
                f"复制失败: {Path(src).name} ← {err}")
        if mr.unmatched:
            miss_text = "\n".join(
                f"{e.title} - {e.artist}" if e.artist else e.title
                for e in mr.unmatched)
            try:
                miss_path = Path(dest_dir) / "未匹配清单.txt"
                miss_path.write_text(miss_text, encoding="utf-8")
                mw.log_panel.log(f"未匹配清单已写入: {miss_path}")
            except OSError as exc:
                mw.log_panel.log_error(f"未匹配清单写入失败: {exc}")
            # 未匹配清单同步到剪贴板，方便去别处置歌
            QApplication.clipboard().setText(miss_text)
            mw.log_panel.log("未匹配清单已复制到剪贴板")
