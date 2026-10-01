#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""声纹指纹回归测试（V14-1/V14-2）

需要 ffmpeg（chromaprint muxer，full 版内置）。运行: python tests/test_fingerprint.py

覆盖:
1. 采集：合理性 / 确定性 / 取消 / 坏文件降级
2. 距离口径：同曲≈0 / 裁剪变体 ≤0.30 / 异曲 >0.30（旋律级合成样本）
3. 候选生成：共享字门槛 / 静音类高频字剔除 / 短指纹排除（纯 numpy 构造）
4. 完整比对：命中给相似度、异曲零混入
"""

import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from core import fingerprint as F
from core.ffmpeg_locator import find_ffmpeg

PASS = []
SR = 44100


def check(name, cond, extra=""):
    cond = bool(cond)
    PASS.append((name, cond))
    print(f"  {'✓' if cond else '✗'} {name} {extra}")
    assert cond, f"{name} {extra}"


def melody(path, scale, noise_seed, dur=20):
    """合成旋律：不同音阶=不同歌（单频正弦对色度特征区分度不足）"""
    rng = np.random.default_rng(noise_seed)
    total = int(SR * dur)
    audio = np.zeros(total)
    pos = i = 0
    while pos < total:
        note = int(SR * 0.5)
        f = scale[i % len(scale)] * (2 ** (i % 3))
        tt = np.arange(min(note, total - pos)) / SR
        env = np.minimum(1, np.minimum(tt * 20, (note / SR - tt) * 20))
        audio[pos:pos + len(tt)] += 0.4 * np.sin(2 * np.pi * f * tt) * env
        pos += note
        i += 1
    audio += 0.02 * rng.standard_normal(total)
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes())
    return path


def main():
    tmp = Path(tempfile.mkdtemp(prefix='fp_test_'))
    FF = find_ffmpeg()
    if not FF:
        print("未找到 ffmpeg，跳过")
        sys.exit(1)

    print("1. 距离口径（旋律级合成样本）")
    base = melody(str(tmp / 'base.wav'), [440, 494, 523, 587, 659], 1)
    mp3 = str(tmp / 'same.mp3')
    clip = str(tmp / 'clip.mp3')
    other = melody(str(tmp / 'other.wav'), [262, 330, 392, 440, 523], 99)
    subprocess.run([FF, '-y', '-hide_banner', '-loglevel', 'error',
                    '-i', base, '-b:a', '320k', mp3], check=True)
    subprocess.run([FF, '-y', '-hide_banner', '-loglevel', 'error',
                    '-i', base, '-ss', '3', '-b:a', '320k', clip], check=True)

    fps = {p: F.fingerprint_file(p) for p in (base, mp3, clip, other)}
    durs = {base: 20.0, mp3: 20.0, clip: 17.0, other: 20.0}
    check('采集非空', all(len(f) > 300 for f in fps.values()),
          str({Path(k).name: len(v) for k, v in fps.items()}))
    check('确定性', F.fingerprint_file(base) == fps[base])

    d_same = F.pair_distance(fps[base], fps[mp3], 20, 20)
    d_clip = F.pair_distance(fps[base], fps[clip], 20, 17)
    d_other = F.pair_distance(fps[base], fps[other], 20, 20)
    check('同曲 <0.05', d_same < 0.05, f'{d_same:.4f}')
    check('裁剪变体 ≤0.30', d_clip <= F.MATCH_DISTANCE, f'{d_clip:.4f}')
    check('异曲 >0.30', d_other > F.MATCH_DISTANCE, f'{d_other:.4f}')

    print("2. 降级路径")
    check('取消返回空',
          F.fingerprint_file(base, cancel_check=lambda: True) == b'')
    bad = tmp / 'bad.mp3'
    bad.write_bytes(b'not audio' * 200)
    check('坏文件返回空', F.fingerprint_file(str(bad)) == b'')

    print("3. 候选生成（纯 numpy 构造，不依赖 ffmpeg）")
    w = np.random.default_rng(7)
    fpa = w.integers(0, 2**31, 500, dtype=np.uint32).astype('<u4').tobytes()
    fpb = fpa                      # 同曲同指纹
    fpc = (np.concatenate([w.integers(0, 2**31, 450, dtype=np.uint32),
                           np.frombuffer(fpa[:200], dtype='<u4')])
           .astype('<u4').tobytes())   # 变体：尾部 50 字与 a 相同
    silence_word = np.uint32(0xDEADBEEF)
    common = np.full(20, silence_word, dtype='<u4').tobytes()
    fpd = (np.concatenate([w.integers(0, 2**31, 480, dtype=np.uint32),
                           np.frombuffer(common, dtype='<u4')])
           .astype('<u4').tobytes())   # d 与 e 只共享"静音类"高频字
    fpe = (np.concatenate([w.integers(0, 2**31, 480, dtype=np.uint32),
                           np.frombuffer(common, dtype='<u4')])
           .astype('<u4').tobytes())
    fpf = np.zeros(64, dtype='<u4').tobytes()   # 过短指纹

    fg = {'a': fpa, 'b': fpb, 'c': fpc, 'd': fpd, 'e': fpe, 'f': fpf}
    pairs = {frozenset(p) for p in F.candidate_pairs(fg)}
    check('完全相同成候选', frozenset(('a', 'b')) in pairs)
    check('部分重叠(50字≥门槛)成候选', frozenset(('a', 'c')) in pairs)
    check('仅高频字共享不成候选（静音剔除）',
          frozenset(('d', 'e')) not in pairs)
    check('短指纹不参与', not any('f' in p for p in pairs))
    # 退化用例：全相同字（静音型文件）——不同字数 1 < 门槛 → 正确拒绝
    fpg = b'\x11\x00\x00\x00' * 500
    fph = b'\x11\x00\x00\x00' * 500
    pairs2 = {frozenset(p) for p in F.candidate_pairs({'g': fpg, 'h': fph})}
    check('全相同字退化指纹不成候选（宁漏勿错）',
          frozenset(('g', 'h')) not in pairs2)

    print("4. 完整比对")
    matches = F.fingerprint_matches(
        {'a': fpa, 'b': fpb, 'c': fpc, 'd': fpd, 'e': fpe, 'f': fpf},
        {k: 60.0 for k in 'abcdef'})
    check('相同指纹命中', matches.get('a', {}).get('b', 0) > 0.7)
    check('异曲未混入', 'd' not in matches.get('e', {})
          and 'e' not in matches.get('d', {}))

    print("5. deduper 第三路接入")
    from core.deduper import find_duplicate_groups
    d5 = tmp / 'd5'
    d5.mkdir()
    from models.result_item import STATUS_DONE, ResultItem

    def item5(name, title, artist, dur=60.0, dr=9.0, corr=0.5, cutoff=20000.0):
        fp = str(d5 / name)
        Path(fp).write_bytes(b'x' * 64)
        return ResultItem(
            filename=name, stem=Path(name).stem, filepath=fp, score=80.0,
            cutoff_60db=cutoff, dr=dr, status=STATUS_DONE,
            detail={'meta': {'title': title, 'artist': artist,
                             'duration': dur, 'format': 'FLAC'},
                    'cutoffs': {'-60dB': cutoff}, 'dr': dr,
                    'correlation': corr})

    # 改名变体：无标签、不同名字、签名可辨（时长错开防签名路合并）→
    # 仅声纹路应合并并标注 fp_note
    i1 = item5('演唱会现场版.flac', '', '', dur=65.0, dr=11.0, corr=0.9)
    i2 = item5('MP3下载版.flac', '', '', dur=63.0, dr=8.0, corr=0.2)
    i3 = item5('完全不同的歌.flac', '', '', dur=64.0, dr=6.0, corr=0.7)
    w5 = np.random.default_rng(3)
    fp_same = w5.integers(0, 2**31, 500, dtype=np.uint32).astype('<u4').tobytes()
    fp_diff = w5.integers(0, 2**31, 500, dtype=np.uint32).astype('<u4').tobytes()
    fps5 = {i1.filepath: fp_same, i2.filepath: fp_same,
            i3.filepath: fp_diff}

    groups = find_duplicate_groups([i1, i2, i3], fingerprints=fps5)
    check('声纹路合并改名变体',
          len(groups) == 1 and len(groups[0].items) == 2,
          f'{[(len(g.items), g.label) for g in groups]}')
    check('fp_note 标注相似度',
          bool(groups) and groups[0].fp_note.startswith('声纹相似'),
          groups[0].fp_note if groups else '')

    # 不传 fingerprints：行为与 v1.3.0 一致（这两首签名可辨不合并）
    groups0 = find_duplicate_groups([i1, i2, i3])
    check('无指纹时向后兼容', not groups0)

    failed = [n for n, ok in PASS if not ok]
    print(f"\n结果: {len(PASS) - len(failed)}/{len(PASS)} 通过")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


if __name__ == '__main__':
    main()
