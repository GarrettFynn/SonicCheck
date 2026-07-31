#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质量标记配置：真/假无损文件名标记的文本与位置（用户可自定义）

core 模块不依赖 Qt：main_window 启动时从 QSettings 读取并 set_config() 注入；
测试可直接 set_config()。

口径（2026-07-30 与用户拍板）：
- 标记文本任意（含分隔符），位置可选 后缀（默认）/ 前缀
- 判定列与 CSV 的"假无损"列文字不变（那是判定口径，不是文件名标记）
- 剥标记时同时识别「默认标记 + 当前自定义标记」两种位置，
  避免换过标记后旧文件剥不掉
"""

DEFAULT_MARK_TRUE = "_真无损"
DEFAULT_MARK_FAKE = "_假无损"
POS_SUFFIX = "suffix"
POS_PREFIX = "prefix"

_config = {
    "mark_true": DEFAULT_MARK_TRUE,
    "mark_fake": DEFAULT_MARK_FAKE,
    "position": POS_SUFFIX,
}


def set_config(mark_true: str = None, mark_fake: str = None,
               position: str = None) -> None:
    if mark_true is not None:
        _config["mark_true"] = mark_true
    if mark_fake is not None:
        _config["mark_fake"] = mark_fake
    if position in (POS_SUFFIX, POS_PREFIX):
        _config["position"] = position


def get_config() -> dict:
    return dict(_config)


def mark_for(is_fake: bool) -> str:
    return _config["mark_fake"] if is_fake else _config["mark_true"]


def apply_mark(stem: str, is_fake: bool) -> str:
    """按配置位置把标记拼到 stem 上"""
    mark = mark_for(is_fake)
    if _config["position"] == POS_PREFIX:
        return mark + stem
    return stem + mark


def known_marks() -> tuple:
    """所有应识别的标记文本：默认 + 当前自定义（去重，保序）"""
    seen: list = []
    for m in (DEFAULT_MARK_TRUE, DEFAULT_MARK_FAKE,
              _config["mark_true"], _config["mark_fake"]):
        if m and m not in seen:
            seen.append(m)
    return tuple(seen)


def strip_quality_mark(stem: str) -> str:
    """循环剥离已知质量标记（前缀与后缀形式都剥，防多层套娃）"""
    changed = True
    while changed and stem:
        changed = False
        for mark in known_marks():
            if mark and stem.endswith(mark):
                stem = stem[:-len(mark)]
                changed = True
            if mark and stem.startswith(mark):
                stem = stem[len(mark):]
                changed = True
    return stem
