#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 验收：新引擎 vs 旧脚本 逐字段比对（决策 3 的强制验证）

规则：
- FLAC 等非 WAV 文件：新引擎所有数值字段必须与旧脚本一致（容差 1e-4），
  判定/评分/原因必须完全相同 —— 这是 numpy 向量化不改变口径的证明。
- WAV 文件：旧脚本存在频率轴 bug（直读 44.1k 但频率轴按 48k 算），
  新引擎统一转码后结果为修复值，差异属预期，单独标注不判 FAIL。
"""

import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 旧脚本以命令名调用 ffmpeg/ffprobe，需要 PATH 可见
os.environ['PATH'] = r"C:\ProgramData\chocolatey\bin" + os.pathsep \
    + os.environ.get('PATH', '')

LEGACY_PATH = ROOT / "dev" / "legacy" / "audio_compare_多进程并行版.py"
SAMPLES = [
    ROOT / "samples" / "ALL RED (Explicit) - Playboi Carti_EM_真无损.flac",
    ROOT / "samples" / "Because I Hear You - Toe_假无损.flac",
    ROOT / "samples" / "music-sample-44100hz-16bit.wav",
    ROOT / "samples" / "music-sample-96000hz-24bit.flac",
]

spec = importlib.util.spec_from_file_location("legacy_audio_compare",
                                              LEGACY_PATH)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)

from core.analyzer import analyze_file  # noqa: E402

NUM_TOL = 1e-4


def run_legacy(filepath: str) -> dict:
    a = legacy.AudioAnalyzer(filepath)
    try:
        r = a.analyze()
        r['score'] = legacy.score_quality(r)
        return r
    finally:
        a.cleanup()


def cmp_field(name, old, new, tol=NUM_TOL):
    if isinstance(old, float) or isinstance(new, float):
        diff = abs(float(old) - float(new))
        ok = diff <= tol
        return ok, f"{old!r} vs {new!r} (diff={diff:.2e})"
    ok = old == new
    return ok, f"{old!r} vs {new!r}"


def compare_one(path: Path) -> bool:
    is_wav = path.suffix.lower() == '.wav'
    print(f"\n{'=' * 74}\n样本: {path.name}"
          f"{'  [WAV：旧脚本有频率轴 bug，差异为预期修复]' if is_wav else ''}")

    t0 = time.perf_counter()
    old = run_legacy(str(path))
    t1 = time.perf_counter()
    new = analyze_file(str(path), analyze_seconds=30)
    t2 = time.perf_counter()
    print(f"耗时: 旧脚本 {t1 - t0:.2f}s | 新引擎 {t2 - t1:.2f}s")

    checks = []
    mo, mn = old['meta'], new['meta']
    for k in ('sample_rate', 'bit_depth', 'channels', 'codec', 'bitrate'):
        checks.append((f"meta.{k}", *cmp_field(k, mo.get(k), mn.get(k))))
    for k in ('-40dB', '-60dB', '-80dB'):
        checks.append((f"cutoffs[{k}]",
                       *cmp_field(k, old['cutoffs'][k], new['cutoffs'][k])))
    checks.append(("cliff", *cmp_field("cliff", old['cliff'], new['cliff'])))
    checks.append(("cliff_freq",
                   *cmp_field("cliff_freq", old['cliff_freq'],
                              new['cliff_freq'])))
    checks.append(("dr", *cmp_field("dr", old['dr'], new['dr'])))
    checks.append(("correlation",
                   *cmp_field("correlation", old['correlation'],
                              new['correlation'])))
    checks.append(("peak", *cmp_field("peak", old['peak'], new['peak'])))
    checks.append(("fake_lossless",
                   *cmp_field("fake", old['fake_lossless'],
                              new['fake_lossless'])))
    checks.append(("fake_reasons",
                   *cmp_field("reasons", old['fake_reasons'],
                              new['fake_reasons'])))
    checks.append(("score",
                   *cmp_field("score", old['score'], new['score'], tol=0)))

    all_ok = True
    for name, ok, detail in checks:
        if is_wav and not ok:
            tag = "DIFF(预期)"
        elif not ok:
            tag = "FAIL"
            all_ok = False
        else:
            tag = "OK"
        print(f"  [{tag:9s}] {name:16s} {detail}")

    verdict = "PASS" if all_ok else ("PASS(WAV修复差异)" if is_wav else "FAIL")
    print(f"  → {verdict}")
    return all_ok or is_wav


def main() -> int:
    print("新引擎 vs 旧脚本 逐字段比对（4 样本）")
    ok = True
    for p in SAMPLES:
        if not p.is_file():
            print(f"缺少样本: {p}")
            ok = False
            continue
        try:
            ok = compare_one(p) and ok
        except Exception as exc:
            print(f"样本 {p.name} 比对异常: {exc}")
            ok = False
    print(f"\n{'=' * 74}\n总体: {'全部通过' if ok else '存在 FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
