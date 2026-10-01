#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V13-1 频谱渲染冒烟（离屏）

端到端验证：ffmpeg 合成音频 → analyze_file 全链路 → spectrum 概要含
断崖 → SpectrumView 渲染（单曲线+标注、双曲线叠加、空数据占位）→
savefig 截图入 dev/。运行：python dev/render_spectrum.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from core.analyzer import SPECTRUM_POINTS, analyze_file, decode_spectrum_summary, spectrum_grid
from core.ffmpeg_locator import find_ffmpeg  # noqa: E402


def synth(path: Path, cutoff_hz: float, seconds: int = 8) -> None:
    """合成带低通特征的音频：多正弦叠加，cutoff 以上靠重采样截断"""
    # 生成 96k 高采样信号（含高频），再用 ffmpeg 重采样到 48k 触发低通
    subprocess.run(
        [find_ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error',
         '-f', 'lavfi', '-i',
         f'sine=frequency={int(cutoff_hz * 0.4)}:duration={seconds}',
         '-f', 'lavfi', '-i',
         f'sine=frequency={int(cutoff_hz * 0.7)}:duration={seconds}',
         '-filter_complex',
         f'[0:a][1:a]amix=inputs=2,aresample=48000,'
         f'lowpass=f={cutoff_hz},volume=0.5',
         str(path)], check=True)


def check(name, cond, extra=""):
    print(f"  {'✓' if cond else '✗'} {name} {extra}")
    assert cond, name


def main() -> None:
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix='spectrum_smoke_'))
    print("1. 端到端 analyze_file → spectrum 字段")
    good = tmp / 'good.flac'
    synth(good, 19000)          # 宽带：接近全带宽
    r = analyze_file(str(good))
    spec = r.get('spectrum')
    check('spectrum 字段存在', isinstance(spec, (bytes, bytearray)))
    check('256 点 float32', len(spec) == SPECTRUM_POINTS * 4,
          f'{len(spec)}B')
    arr = decode_spectrum_summary(spec)
    check('数值合理（平台>-90dB）', float(arr.max()) > -60)

    print("2. SpectrumView 三态渲染")
    from PyQt6.QtGui import QPixmap
    from widgets.spectrum_view import SpectrumView

    view = SpectrumView()
    view.resize(760, 300)
    # 单曲线 + 断崖/截止标注
    cliff = r['cliff_freq'] or 18000.0
    view.set_data(spec, cliff_freq=cliff, cutoff_60db=r['cutoffs']['-60dB'])
    pm = QPixmap(view.size())
    view.render(pm)
    pm.save(str(tmp / 'spec_single.png'))
    check('单曲线渲染', Path(tmp / 'spec_single.png').stat().st_size > 1000)

    # 双曲线叠加
    fake = tmp / 'fake.flac'
    synth(fake, 15000)          # 窄带：模拟有损源
    r2 = analyze_file(str(fake))
    view.set_data(r2['spectrum'], cliff_freq=r2['cliff_freq'],
                  cutoff_60db=r2['cutoffs']['-60dB'])
    view.set_overlay(spec)
    pm2 = QPixmap(view.size())
    view.render(pm2)
    pm2.save(str(tmp / 'spec_overlay.png'))
    check('双曲线渲染', Path(tmp / 'spec_overlay.png').stat().st_size > 1000)

    # 空数据占位
    view.set_data(b'')
    pm3 = QPixmap(view.size())
    view.render(pm3)
    pm3.save(str(tmp / 'spec_empty.png'))
    check('空数据占位渲染', Path(tmp / 'spec_empty.png').stat().st_size > 100)

    print("3. Detail/Compare 集成")
    from models.result_item import STATUS_DONE, ResultItem
    from widgets.detail_dialog import DetailDialog
    from widgets.compare_dialog import MODE_COMPARE, CompareDialog
    from core.deduper import DupGroup

    item = ResultItem(
        filename=good.name, stem=good.stem, filepath=str(good),
        score=r['score'], cutoff_60db=r['cutoffs']['-60dB'], dr=r['dr'],
        is_fake=r['fake_lossless'], fake_reasons=r['fake_reasons'],
        status=STATUS_DONE, detail=r)
    dlg = DetailDialog(item)
    check('详情对话框构造含频谱', dlg.spectrum._db1 is not None
          and len(dlg.spectrum._db1) == SPECTRUM_POINTS)

    item2 = ResultItem(
        filename=fake.name, stem=fake.stem, filepath=str(fake),
        score=r2['score'], cutoff_60db=r2['cutoffs']['-60dB'], dr=r2['dr'],
        is_fake=r2['fake_lossless'], status=STATUS_DONE, detail=r2)
    g = DupGroup(label='叠加冒烟', items=[item, item2])
    cd = CompareDialog([g], MODE_COMPARE)
    check('对比对话框叠加启用', cd.spectrum.isVisibleTo(cd)
          and cd.spectrum._db2 is not None)

    # 截图拷贝到 dev/ 留档
    import shutil
    for f in ('spec_single.png', 'spec_overlay.png'):
        shutil.copy(tmp / f, ROOT / 'dev' / f)
    print(f"\n冒烟通过；截图: dev/spec_single.png, dev/spec_overlay.png")


if __name__ == '__main__':
    main()
