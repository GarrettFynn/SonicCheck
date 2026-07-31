#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据模型：单首歌曲的分析结果"""

from dataclasses import dataclass, field

STATUS_PENDING = "pending"
STATUS_ANALYZING = "analyzing"
STATUS_DONE = "done"
STATUS_ERROR = "error"


@dataclass
class ResultItem:
    filename: str = ""          # 原文件名（含扩展名）
    stem: str = ""              # 无扩展名
    filepath: str = ""          # 完整路径（绝对路径）
    score: float = 0.0          # 综合评分 0-100
    cutoff_60db: float = 0.0    # -60dB 截止频率
    dr: float = 0.0             # 动态范围
    is_fake: bool = False       # 是否假无损
    fake_reasons: list = field(default_factory=list)  # 假无损原因列表
    status: str = STATUS_PENDING  # pending / analyzing / done / error
    error_message: str = ""     # status == error 时的失败原因
    detail: dict = field(default_factory=dict)  # 完整分析结果（M3 导出 CSV 用）
