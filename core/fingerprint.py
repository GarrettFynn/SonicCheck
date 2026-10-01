#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""声纹指纹采集与比对（V14-1/V14-2）：chromaprint raw 指纹

采集：内置 ffmpeg 的 chromaprint muxer（V14-0 实测结论）——
  ffmpeg -i <file> -f chromaprint -fp_format raw -
输出写到 stdout：uint32 小端字流，类真实音频密度约 0.125 秒/字
（240s → 1917 字）。全长采集（不截断），滑移比对才能覆盖 live 前奏差异。

比对：候选生成用"全字倒排 + 共享字数门槛"（numpy 排序式实现，禁用
固定位置锚点——裁剪/前奏差异会让任何固定锚点错位）；候选对再按时长差
动态滑移 + 归一化汉明距离精算。静音段会产生跨歌曲的相同高频字，倒排
阶段按"字出现文件数"封顶剔除（非信息字）。

阈值口径（宁漏勿错）：共享字数 ≥ SHARED_WORD_MIN 才成候选；
归一化汉明距离 ≤ MATCH_DISTANCE 判"同一录音"。
"""

import os as _os
import subprocess
import time

import numpy as np

from core.ffmpeg_locator import find_ffmpeg, hidden_subprocess_kwargs

# ── 可调口径（回归用例锁定）──
SHARED_WORD_MIN = 8      # 候选门槛：两文件共享的不同字数下限
MATCH_DISTANCE = 0.30    # 归一化汉明距离 ≤ 此值判同一录音
WORD_COMMON_CAP = 50     # 单字出现在超过此数量的文件中 → 非信息字，剔除
SECONDS_PER_WORD = 0.125 # raw 指纹密度（V14-0 实测），滑移范围据此换算
SLIDE_MARGIN_WORDS = 64  # 滑移余量（约 8s，覆盖 live 前奏/结尾差异）


class FingerprintError(Exception):
    """用户可读的指纹采集失败"""


def fingerprint_file(path: str, cancel_check=None) -> bytes:
    """全长采集 chromaprint raw 指纹 → uint32 小端字节串；失败返回 b''。

    cancel_check() 为 True 时中断（kill 子进程）并抛 FingerprintError
    之外不区分——统一返回 b''，调用方按"无指纹"降级。
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise FingerprintError("未找到 ffmpeg（内置目录与系统 PATH 均不可用）")
    cmd = [ffmpeg, '-i', str(path), '-f', 'chromaprint', '-fp_format', 'raw', '-']
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,
                                **hidden_subprocess_kwargs())
    except OSError:
        return b''

    chunks = []
    deadline = time.monotonic() + _timeout_for(path)
    fd = proc.stdout.fileno()
    try:
        while True:
            if cancel_check is not None and cancel_check():
                proc.kill()
                proc.wait()
                return b''
            # os.read 有数据即返回（BufferedReader.read 会阻塞到凑满，
            # 取消与超时将永远轮不到执行）
            chunk = _os.read(fd, 1 << 16)
            if chunk:
                chunks.append(chunk)
            ret = proc.poll()
            if ret is not None:
                if ret != 0:
                    return b''   # 解码失败/损坏文件：按无指纹降级
                break
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                return b''
            if not chunk:
                time.sleep(0.02)   # 暂无数据：避免 busy loop
    except OSError:
        try:
            proc.kill()
            proc.wait()
        except OSError:
            pass
        return b''
    data = b''.join(chunks)
    if len(data) % 4:
        data = data[:len(data) // 4 * 4]
    return data


def _timeout_for(path: str) -> int:
    """全长采集的超时按文件大小缩放（约 30 倍速采集，下限 120s）"""
    try:
        import os
        size = os.path.getsize(path)
    except OSError:
        return 600
    return max(120, int(size / (4 << 20)) + 60)


def words_of(fp: bytes) -> np.ndarray:
    """指纹字节串 → uint32 字数组（小端）"""
    return np.frombuffer(fp, dtype='<u4')


# ══════════════════════════════════════════════════════════════════
# 比对（V14-2）：候选生成 + 滑移精算
# ══════════════════════════════════════════════════════════════════

_POPCOUNT_LUT = np.array([bin(i).count('1') for i in range(256)],
                         dtype=np.uint8)


def normalized_distance(a: np.ndarray, b: np.ndarray, shift: int) -> float:
    """滑移 shift 后的归一化汉明距离（0-1）。shift>0 表示 a 右移。

    只在重叠区计算；重叠 < 64 字视为不可信（返回 1.0）。
    """
    if shift >= 0:
        aw, bw = a[shift:], b[:len(b) - shift] if shift else b
    else:
        aw, bw = a[:len(a) + shift], b[-shift:]
    n = min(len(aw), len(bw))
    if n < 64:
        return 1.0
    x = np.bitwise_xor(aw[:n], bw[:n])
    bytes_view = x.view(np.uint8)
    bits = _POPCOUNT_LUT[bytes_view].sum()
    return float(bits) / (32.0 * n)


def pair_distance(fp_a: bytes, fp_b: bytes, dur_a: float,
                  dur_b: float) -> float:
    """候选对精算：按需滑移取最小归一化距离。

    滑移范围 = ceil(|时长差| / SECONDS_PER_WORD) + SLIDE_MARGIN_WORDS。
    """
    a, b = words_of(fp_a), words_of(fp_b)
    if len(a) < 64 or len(b) < 64:
        return 1.0
    slide = int(abs(dur_a - dur_b) / SECONDS_PER_WORD) + SLIDE_MARGIN_WORDS
    best = 1.0
    for shift in range(-slide, slide + 1):
        d = normalized_distance(a, b, shift)
        if d < best:
            best = d
            if best < 0.05:
                break   # 实际命中，无需继续扫
    return best


def candidate_pairs(fingerprints: dict) -> list:
    """全字倒排（排序式）生成候选对。

    fingerprints: {path: raw_bytes}
    返回 [(path_a, path_b)]（a<b，去重）。静音等非信息字按
    WORD_COMMON_CAP 剔除；共享字数 < SHARED_WORD_MIN 的对不产生。
    """
    paths = [p for p, fp in fingerprints.items()
             if fp and len(fp) >= 256]   # <64 字的短指纹不参与
    if len(paths) < 2:
        return []

    # 拼接大数组 + 文件索引，整体排序找同字组（比 dict 倒排省内存）
    all_words = np.concatenate([words_of(fingerprints[p]) for p in paths])
    owner = np.concatenate([
        np.full(len(words_of(fingerprints[p])), i, dtype=np.int32)
        for i, p in enumerate(paths)])
    order = np.argsort(all_words, kind='stable')
    all_words = all_words[order]
    owner = owner[order]

    # 同字组的文件去重集合
    boundaries = np.flatnonzero(np.diff(all_words)) + 1
    starts = np.concatenate([[0], boundaries])
    ends = np.concatenate([boundaries, [len(all_words)]])

    pair_count: dict = {}
    for s, e in zip(starts, ends):
        if e - s < 2:
            continue
        files = np.unique(owner[s:e])
        if len(files) < 2 or len(files) > WORD_COMMON_CAP:
            continue   # 唯一字无对；超高频字（静音类）剔除
        for i in range(len(files)):
            for j in range(i + 1, len(files)):
                key = (int(files[i]), int(files[j]))
                pair_count[key] = pair_count.get(key, 0) + 1

    out = []
    for (i, j), cnt in pair_count.items():
        if cnt >= SHARED_WORD_MIN:
            pa, pb = paths[i], paths[j]
            out.append((pa, pb) if pa < pb else (pb, pa))
    return out


def fingerprint_matches(fingerprints: dict, durations: dict,
                        progress_cb=None, cancel_check=None) -> dict:
    """完整比对：候选 → 精算 → {path: {other_path: 相似度}}。

    相似度 = 1 - 归一化汉明距离（百分比展示用）。
    """
    pairs = candidate_pairs(fingerprints)
    result: dict = {}
    total = len(pairs)
    for k, (pa, pb) in enumerate(pairs):
        if cancel_check is not None and cancel_check():
            break
        if progress_cb is not None and k % 20 == 0:
            progress_cb(k, total)
        d = pair_distance(fingerprints[pa], fingerprints[pb],
                          durations.get(pa, 0), durations.get(pb, 0))
        if d <= MATCH_DISTANCE:
            result.setdefault(pa, {})[pb] = 1.0 - d
            result.setdefault(pb, {})[pa] = 1.0 - d
    return result
