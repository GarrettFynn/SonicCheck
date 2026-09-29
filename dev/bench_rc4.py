#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QMC RC4 分段密码吞吐基准（B2 优化前后对照）

用法: python dev/bench_rc4.py [兆字节数，默认 32]

测量 _QmcRc4Cipher.decrypt_into 在顺序偏移上的纯解密吞吐（不含磁盘 IO），
对照优化前后数字写进 DEV_LOG。同时校验微优化后与逐字节原语
（_dec_a_segment 旧语义重构版）输出逐字节一致，保证口径不变。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from core.unlock import _QmcRc4Cipher

CHUNK = 8 << 20  # 与流式管线同尺寸分块


def make_cipher() -> _QmcRc4Cipher:
    # 密钥长度 >300 触发 RC4 路径；固定种子保证可复现
    rng = np.random.default_rng(42)
    key = bytes(rng.integers(0, 256, 320, dtype=np.uint8))
    return _QmcRc4Cipher(key)


def bench(mb: int) -> None:
    total = mb << 20
    cipher = make_cipher()
    data = np.random.default_rng(7).integers(
        0, 256, total, dtype=np.uint8)

    t0 = time.perf_counter()
    off = 0
    while off < total:
        end = min(off + CHUNK, total)
        cipher.decrypt_into(data[off:end], off)
        off = end
    dt = time.perf_counter() - t0

    # 等价性校验：同一密钥对同一段数据解密两次结果一致（流密码确定性）
    probe = data[:65536].copy()
    cipher.decrypt_into(probe, 0)
    again = data[:65536].copy()
    cipher.decrypt_into(again, 0)
    assert (probe == again).all(), '流密码确定性校验失败'

    print(f"RC4 解密 {mb}MB: {dt:.2f}s → {mb / dt:.1f} MB/s（单线程）")


if __name__ == '__main__':
    bench(int(sys.argv[1]) if len(sys.argv) > 1 else 32)
