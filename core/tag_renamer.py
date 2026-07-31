#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标签改名：文件名规范化为 歌名-歌手 / 歌手-歌名（功能 D）

口径（已拍板）：
- 默认 `歌名-歌手`，`歌手-歌名` 可选
- 缺 title 或 artist 标签的文件跳过并日志
- Windows 非法字符 \\ / : * ? " < > | 替换为空格并折叠
- 质量后缀保留在末尾（按本次判定重新加，与 M3 口径一致）
- 同 stem .lrc 同步；目标撞名跳过；单条失败不中断
"""

import re
from pathlib import Path

from core.quality_marks import apply_mark
from core.renamer import (KIND_AUDIO, KIND_LRC, RenameOp, RenameSkip,
                          execute_plan, strip_quality_suffix)
from models.result_item import STATUS_DONE

FMT_TITLE_ARTIST = "title-artist"   # 歌名-歌手（默认）
FMT_ARTIST_TITLE = "artist-title"   # 歌手-歌名

_ILLEGAL = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name: str) -> str:
    """Windows 文件名净化：非法字符→空格，折叠空白，去尾部点/空格"""
    name = _ILLEGAL.sub(' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name.rstrip('. ').strip()


def build_tag_rename_plan(items: list, name_format: str = FMT_TITLE_ARTIST) -> tuple:
    """返回 (plan, skipped)，结构同 renamer.build_rename_plan

    按路径排序后处理：撞名时谁先谁后是确定的（与扫描完成顺序无关），
    避免同名标签的重复文件改名结果随线程调度抖动。
    """
    items = sorted(items, key=lambda i: i.filepath)
    plan: list = []
    skipped: list = []
    planned_targets = set()

    for item in items:
        if item.status != STATUS_DONE:
            continue
        meta = item.detail.get('meta', {})
        title, artist = meta.get('title', ''), meta.get('artist', '')
        if not title or not artist:
            skipped.append(RenameSkip(item.filepath, "缺少歌名/歌手标签"))
            continue

        base = (f"{title}-{artist}" if name_format == FMT_TITLE_ARTIST
                else f"{artist}-{title}")
        base = sanitize_filename(base)
        if not base:
            skipped.append(RenameSkip(item.filepath, "标签净化后为空"))
            continue

        # 质量标记：原 stem 带过的，按本次判定与当前标记配置重新加
        old = Path(item.filepath)
        had_suffix = strip_quality_suffix(old.stem) != old.stem
        new_stem = apply_mark(base, item.is_fake) if had_suffix else base

        # 先判定音频本体：跳过（撞名/已是目标名）时 .lrc 必须一并跳过，
        # 否则 lrc 会被单独改成别人目标的同名文件（张冠李戴）
        new_audio = old.parent / (new_stem + old.suffix)
        if old == new_audio:
            skipped.append(RenameSkip(item.filepath, "已是目标名，无需变更"))
            continue
        new_key = str(new_audio).lower()
        if new_audio.exists() or new_key in planned_targets:
            skipped.append(RenameSkip(item.filepath,
                                      f"目标已存在: {new_audio.name}"))
            continue
        planned_targets.add(new_key)
        plan.append(RenameOp(item.filepath, str(new_audio), KIND_AUDIO,
                             "标签改名"))

        # .lrc 跟随音频一起改
        lrc_old = old.parent / (old.stem + ".lrc")
        if lrc_old.is_file():
            lrc_new = old.parent / (new_stem + ".lrc")
            lrc_key = str(lrc_new).lower()
            if lrc_new.exists() or lrc_key in planned_targets:
                skipped.append(RenameSkip(str(lrc_old),
                                          f"目标已存在: {lrc_new.name}"))
            else:
                planned_targets.add(lrc_key)
                plan.append(RenameOp(str(lrc_old), str(lrc_new), KIND_LRC,
                                     "标签改名"))
    return plan, skipped


__all__ = ["FMT_TITLE_ARTIST", "FMT_ARTIST_TITLE", "sanitize_filename",
           "build_tag_rename_plan", "execute_plan"]
