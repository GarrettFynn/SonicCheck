#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量重命名：按扫描判定结果给音频文件加质量后缀

迁移自 rename_by_quality.py，按已确认的 4 处设计升级：
1. 不经过 CSV 中转：直接从内存 ResultItem 列表建计划
2. 按文件路径推导（递归扫描下不同子目录同名歌曲不撞车）
3. 后缀归一化：先剥离已有质量后缀 → 按本次判定重新加（支持改判，
   防止 xxx_假无损_真无损.flac 套娃）；新旧名相同则为无操作跳过
4. 同步范围收窄：只同步同 stem 的 .lrc，不同步 .cue 等其他文件

边界（③-3）：仅处理本次扫描到且分析成功的文件；目标已存在 → 跳过；
单文件失败（只读/被占用）不中断整批，由上层在日志标红。
"""

import os
from dataclasses import dataclass
from pathlib import Path

from core.fsutil import companion_lrc
from core.quality_marks import (apply_mark, mark_for, strip_quality_mark)
from models.result_item import STATUS_DONE, ResultItem

KIND_AUDIO = "audio"
KIND_LRC = "lrc"


@dataclass
class RenameOp:
    old_path: str
    new_path: str
    kind: str        # KIND_AUDIO / KIND_LRC
    reason: str      # "真无损" / "假无损"


@dataclass
class RenameSkip:
    path: str
    why: str         # 跳过原因（无变化 / 目标已存在）


def strip_quality_suffix(stem: str) -> str:
    """循环剥离已知质量标记（前后缀形式都剥，含用户自定义标记）"""
    return strip_quality_mark(stem)


def build_rename_plan(items: list) -> tuple:
    """从分析结果建重命名计划。

    返回 (plan, skipped)：plan 为可执行 RenameOp 列表，
    skipped 为 RenameSkip 列表（含无操作与目标冲突，供预览展示）。

    按路径排序后处理：撞名时谁先谁后是确定的（与扫描完成顺序无关）。
    """
    items = sorted(items, key=lambda i: i.filepath)
    plan: list = []
    skipped: list = []
    planned_targets = set()  # 防计划内部撞名

    for item in items:
        if item.status != STATUS_DONE:
            continue
        reason = "假无损" if item.is_fake else "真无损"

        old = Path(item.filepath)
        new_stem = apply_mark(strip_quality_suffix(old.stem), item.is_fake)
        candidates = [(old, KIND_AUDIO)]
        lrc_old = companion_lrc(old)
        if lrc_old is not None:
            candidates.append((lrc_old, KIND_LRC))

        for old_path, kind in candidates:
            new_path = old_path.parent / (new_stem + old_path.suffix)
            if old_path == new_path:
                skipped.append(RenameSkip(str(old_path), "已是目标名，无需变更"))
                continue
            new_key = str(new_path).lower()  # Windows 大小写不敏感
            if new_path.exists() or new_key in planned_targets:
                skipped.append(RenameSkip(str(old_path),
                                          f"目标已存在: {new_path.name}"))
                continue
            planned_targets.add(new_key)
            plan.append(RenameOp(str(old_path), str(new_path), kind, reason))

    return plan, skipped


def execute_ops(plan: list, action) -> list:
    """通用计划执行器：action(old: Path, new: Path) 完成单条文件操作。

    统一返回 [(op, ok, error)]，单条 OSError 失败不中断整批——
    重命名/安全复制/去重移动三个执行器曾为三份同构拷贝（C3 收敛）。
    """
    results = []
    for op in plan:
        try:
            action(Path(op.old_path), Path(op.new_path))
            results.append((op, True, ""))
        except OSError as exc:
            results.append((op, False, str(exc)))
    return results


def execute_plan(plan: list) -> list:
    """执行重命名，返回 [(op, ok, error)]；单条失败不中断整批"""
    return execute_ops(plan, lambda old, new: os.rename(old, new))


def execute_copy_plan(plan: list) -> list:
    """安全模式用：不改动原文件，复制到 op.new_path（自动建目录）。

    目标已存在则该条失败（不覆盖，防止安全输出目录里的旧件被误毁）。
    """
    import shutil

    def _copy(old: Path, new: Path) -> None:
        if new.exists():
            raise FileExistsError(f"目标已存在: {new.name}")
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, new)

    return execute_ops(plan, _copy)
