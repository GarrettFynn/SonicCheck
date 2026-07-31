#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 Worker：QRunnable 包装单文件分析（multiprocessing → 线程池，技术栈已锁定）

QRunnable 不是 QObject 不能带信号，采用标准 signals 对象模式。
协作式取消（③-2）：worker 在开始、转码轮询、阶段边界检查取消标志；
已取消的任务静默退出，不向表格发结果。
"""

from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from core.analyzer import ScanCancelled, analyze_file
from models.result_item import (STATUS_ANALYZING, STATUS_DONE, STATUS_ERROR,
                                ResultItem)


class WorkerSignals(QObject):
    started = pyqtSignal(str)        # filepath：开始分析
    result = pyqtSignal(object)      # ResultItem：完成（含失败态）
    cancelled = pyqtSignal(str)      # filepath：开始前即被取消（用于计数）


class ScanWorker(QRunnable):
    def __init__(self, filepath: str, analyze_seconds: int, cancel_check):
        super().__init__()
        self.filepath = filepath
        self.analyze_seconds = analyze_seconds
        self._cancel_check = cancel_check
        self.signals = WorkerSignals()

    def run(self) -> None:
        if self._cancel_check is not None and self._cancel_check():
            self.signals.cancelled.emit(self.filepath)
            return

        self.signals.started.emit(self.filepath)
        try:
            data = analyze_file(self.filepath,
                                analyze_seconds=self.analyze_seconds,
                                cancel_check=self._cancel_check)
        except ScanCancelled:
            self.signals.cancelled.emit(self.filepath)
            return
        except Exception as exc:  # 异常 → error 行进表格，不静默丢弃
            item = ResultItem(
                filename=Path(self.filepath).name,
                stem=Path(self.filepath).stem,
                filepath=self.filepath,
                status=STATUS_ERROR,
                error_message=str(exc),
            )
            self.signals.result.emit(item)
            return

        meta = data['meta']
        item = ResultItem(
            filename=meta['filename'],
            stem=Path(self.filepath).stem,
            filepath=self.filepath,
            score=round(float(data['score']), 1),
            cutoff_60db=float(data['cutoffs']['-60dB']),
            dr=round(float(data['dr']), 1),
            is_fake=bool(data['fake_lossless']),
            fake_reasons=list(data['fake_reasons']),
            status=STATUS_DONE,
            detail=data,
        )
        self.signals.result.emit(item)
