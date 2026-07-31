#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描调度器：QThreadPool + QRunnable，协作式取消（③-2，M2 内建）

取消语义：置标志位；队列中的 worker 启动后立即退出，运行中的 worker
在阶段边界退出；转码中的 ffmpeg 子进程会被立即 kill。
最慢情况下一个阶段边界（<1s）内停止。
"""

import threading

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal

from threads.scan_worker import ScanWorker


class ScanManager(QObject):
    file_started = pyqtSignal(str)      # filepath
    file_done = pyqtSignal(object)      # ResultItem（done / error）
    progress = pyqtSignal(int, int)     # 已处理, 总数（含取消/失败）
    log = pyqtSignal(str)
    scan_finished = pyqtSignal(bool)    # True = 被停止，False = 自然完成

    def __init__(self, parent=None):
        super().__init__(parent)
        # 独立线程池：不影响全局池，clear()/线程数控制互不干擾
        self._pool = QThreadPool(self)
        self._cancel = threading.Event()
        self._total = 0
        self._settled = 0      # 已定论（完成/失败/被取消）的任务数
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, files: list, threads: int, analyze_seconds: int) -> None:
        self._cancel.clear()
        self._total = len(files)
        self._settled = 0
        self._running = True
        self._pool.setMaxThreadCount(max(1, int(threads)))
        self.log.emit(
            f"开始扫描 {self._total} 首 | 线程数 {threads} | "
            f"每首分析前 {analyze_seconds} 秒")
        for fp in files:
            worker = ScanWorker(fp, analyze_seconds, self._cancel.is_set)
            worker.signals.started.connect(self._on_started)
            worker.signals.result.connect(self._on_result)
            worker.signals.cancelled.connect(self._on_cancelled)
            self._pool.start(worker)

    def stop(self) -> None:
        """协作式取消（③-2）：仅置标志位。

        不调用 QThreadPool.clear()：被清掉的队列任务不会执行 run()，
        也就不会发任何回调，结算计数会永久缺失导致 scan_finished 不发。
        本设计保证每个 worker 恰好发一次终结回调（result 或 cancelled），
        未开始的 worker 启动后在入口检查标志位立即退出，开销可忽略。
        """
        if not self._running:
            return
        self._cancel.set()
        self.log.emit("正在停止：等待运行中的任务退出…")

    def wait_done(self, msecs: int = -1) -> bool:
        return self._pool.waitForDone(msecs)

    # ---------------- worker 回调（主线程槽） ----------------
    def _on_started(self, filepath: str) -> None:
        if not self._cancel.is_set():
            self.file_started.emit(filepath)

    def _on_result(self, item) -> None:
        self._settled += 1
        self.file_done.emit(item)
        self.progress.emit(self._settled, self._total)
        self._maybe_finish()

    def _on_cancelled(self, filepath: str) -> None:
        self._settled += 1
        self.progress.emit(self._settled, self._total)
        self._maybe_finish()

    def _maybe_finish(self) -> None:
        if self._running and self._settled >= self._total:
            self._running = False
            stopped = self._cancel.is_set()
            self.log.emit("扫描已停止" if stopped else "扫描完成")
            self.scan_finished.emit(stopped)
