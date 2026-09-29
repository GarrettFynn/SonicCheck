#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件系统小工具：renamer / tag_renamer / deduper / playlist 共用"""

from pathlib import Path


def companion_lrc(audio_path) -> Path:
    """同 stem 的 .lrc 歌词伴随文件；不存在返回 None。

    改名/清除/歌单复制四条链路都要「音频动则歌词跟着动」，此前是
    四份相同的三行拷贝，口径收敛到一处。
    """
    p = Path(audio_path)
    lrc = p.parent / (p.stem + ".lrc")
    return lrc if lrc.is_file() else None
