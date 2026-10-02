#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判定可信度与 CLI 健壮性回归（v2.0.2 审查修复②）

A. analyzer：静音/极短文件不再误判假无损（insufficient 标记），正常
   宽带文件判定不变。需要 ffmpeg。
B. CLI：参数范围校验退出码 2；空目录退出码 3；--seconds 0 被拒。
运行: python tests/test_verdict_edges.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = []


def check(name, cond, extra=""):
    cond = bool(cond)
    PASS.append((name, cond))
    print(f"  {'✓' if cond else '✗'} {name} {extra}")
    assert cond, f"{name} {extra}"


def main():
    from core.analyzer import analyze_file
    from core.ffmpeg_locator import find_ffmpeg

    ff = find_ffmpeg()
    if not ff:
        print("未找到 ffmpeg，跳过")
        sys.exit(1)
    tmp = Path(tempfile.mkdtemp(prefix='verdict_'))

    print("1. 静音文件（35s 数字静音）")
    sil = tmp / 'silence.wav'
    subprocess.run([ff, '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                    '-t', '35', str(sil)], check=True)
    r = analyze_file(str(sil))
    check('不再误判假无损', not r['fake_lossless'],
          f"原因={r['fake_reasons']}")
    check('insufficient 标记置位', r.get('insufficient') is True)

    print("2. 宽带正常文件（粉噪，判定不受 0 值豁免影响）")
    noise = tmp / 'noise.wav'
    subprocess.run([ff, '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'anoisesrc=d=8:c=pink:a=0.5',
                    str(noise)], check=True)
    r2 = analyze_file(str(noise))
    check('判为真无损', not r2['fake_lossless'], str(r2['fake_reasons']))
    check('insufficient 为 False', r2.get('insufficient') is False)
    check('截止 >16k（真实带宽）', r2['cutoffs']['-60dB'] > 16000,
          f"{r2['cutoffs']['-60dB']:.0f}Hz")

    print("3. 真假阴性对照（窄带正弦：真实带宽不足仍应判假）")
    # 宽带源+一阶低通的滚降不足以把 -60dB 截止压到 16k 以下（粉噪
    # 天然衰减+滤波滚降叠加仍 >-60dB）；正弦音带宽天然≈单频，是更
    # 稳定的"真实窄带"对照
    tone = tmp / 'tone.wav'
    subprocess.run([ff, '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'sine=frequency=1000:duration=8',
                    str(tone)], check=True)
    r3 = analyze_file(str(tone))
    check('窄带源仍判假无损', r3['fake_lossless'],
          str(r3['fake_reasons'])[:60])
    check('窄带源不是 insufficient（有真实能量）',
          r3.get('insufficient') is False)

    print("4. CLI 参数校验与退出码")
    env = dict(os.environ, PYTHONIOENCODING='utf-8')

    def cli(*extra):
        return subprocess.run(
            [sys.executable, str(ROOT / 'main.py'), *extra],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', cwd=str(tmp), timeout=120, env=env)

    empty = tempfile.mkdtemp(prefix='empty_')
    r_cli = cli('--scan', empty)
    check('空目录退出码 3', r_cli.returncode == 3,
          f"rc={r_cli.returncode}")
    r_t0 = cli('--scan', empty, '--threads', '0')
    check('--threads 0 退出码 2（argparse 拒绝）', r_t0.returncode == 2,
          f"rc={r_t0.returncode}")
    r_s0 = cli('--scan', empty, '--seconds', '0')
    check('--seconds 0 退出码 2（防全库误判）', r_s0.returncode == 2,
          f"rc={r_s0.returncode}")
    check('错误信息含范围提示', '5~120' in (r_s0.stderr + r_s0.stdout))

    failed = [n for n, ok in PASS if not ok]
    print(f"\n结果: {len(PASS) - len(failed)}/{len(PASS)} 通过")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
