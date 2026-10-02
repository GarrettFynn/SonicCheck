#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTML 扫描报告生成（V13-5）：单文件自包含，无模板库、无外链

结构：报告头 → 汇总卡片 → 明细表 → 假无损"证据卡"（判定原因 +
内联 SVG 频谱）。频谱数据取 detail['spectrum']（256 点 float32，
分档网格与 core.analyzer.spectrum_grid 一致），渲染为 <polyline>。
Qt-free，CLI（V14-4）与 GUI 共用。
"""

import html
import math
import struct
from datetime import datetime
from pathlib import Path

from core.analyzer import SPECTRUM_POINTS, _SPECTRUM_F_MAX, _SPECTRUM_F_MIN
from models.result_item import STATUS_DONE

# 与 SpectrumView 同色板
_SVG_W, _SVG_H = 640, 160
_CLIFF_COLOR = "#F44747"
_CUTOFF_COLOR = "#D7BA7D"
_CURVE_COLOR = "#4EC9B0"
_GRID_COLOR = "#3C3C3C"
_TEXT_COLOR = "#808080"


def _decode_spectrum(data: bytes):
    if not data or len(data) != SPECTRUM_POINTS * 4:
        return None
    return struct.unpack(f'<{SPECTRUM_POINTS}f', data)


def _svg_spectrum(spectrum: bytes, cliff_freq: float,
                  cutoff_freq: float) -> str:
    """256 点概要 → 内联 SVG（阶梯上缘折线 + 标注竖线）"""
    db = _decode_spectrum(spectrum)
    if db is None:
        return ''
    lo, hi = math.log10(_SPECTRUM_F_MIN), math.log10(_SPECTRUM_F_MAX)

    def fx(freq):
        t = (math.log10(max(freq, _SPECTRUM_F_MIN)) - lo) / (hi - lo)
        return 34 + t * (_SVG_W - 42)

    def fy(v):
        t = (min(max(v, -100.0), 0.0) + 100.0) / 100.0
        return 8 + (1.0 - t) * (_SVG_H - 28)

    pts = []
    grid_end = _SPECTRUM_F_MAX
    for i, v in enumerate(db):
        if v < -119.0:
            continue
        # 审查修复（低）：网格指数分母是 SPECTRUM_POINTS（256 档
        # 共 257 条边），与 analyzer.spectrum_grid 一致；原 /255 使
        # 报告 SVG 高端相对 GUI 偏差约 2.8%
        f1 = _SPECTRUM_F_MIN * (grid_end / _SPECTRUM_F_MIN) ** (i / SPECTRUM_POINTS)
        f2 = _SPECTRUM_F_MIN * (grid_end / _SPECTRUM_F_MIN) ** ((i + 1) / SPECTRUM_POINTS)
        y = fy(v)
        if pts:
            pts.append(f"{fx(f1):.1f},{y:.1f}")
        else:
            pts.append(f"{fx(f1):.1f},{y:.1f}")
        pts.append(f"{fx(f2):.1f},{y:.1f}")
    if not pts:
        return ''
    parts = [
        f'<svg viewBox="0 0 {_SVG_W} {_SVG_H}" width="100%" height="{_SVG_H}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img">',
        f'<rect x="34" y="8" width="{_SVG_W - 42}" height="{_SVG_H - 28}" '
        f'fill="#252526"/>',
    ]
    for f in (1000, 10000):
        x = fx(f)
        parts.append(f'<line x1="{x:.1f}" y1="8" x2="{x:.1f}" '
                     f'y2="{_SVG_H - 20}" stroke="{_GRID_COLOR}"/>')
        parts.append(f'<text x="{x:.1f}" y="{_SVG_H - 6}" fill="{_TEXT_COLOR}" '
                     f'font-size="10" text-anchor="middle">{f // 1000}k</text>')
    for dbv in (-20, -40, -60, -80):
        y = fy(dbv)
        parts.append(f'<line x1="34" y1="{y:.1f}" x2="{_SVG_W - 8}" y2="{y:.1f}" '
                     f'stroke="{_GRID_COLOR}"/>')
        parts.append(f'<text x="30" y="{y + 3:.1f}" fill="{_TEXT_COLOR}" '
                     f'font-size="10" text-anchor="end">{dbv}</text>')
    for freq, color, dash in ((cliff_freq, _CLIFF_COLOR, '6 3'),
                              (cutoff_freq, _CUTOFF_COLOR, '')):
        if freq > 0:
            x = fx(freq)
            dash_attr = f' stroke-dasharray="{dash}"' if dash else ''
            parts.append(f'<line x1="{x:.1f}" y1="8" x2="{x:.1f}" '
                         f'y2="{_SVG_H - 20}" stroke="{color}"{dash_attr}/>')
    parts.append(
        f'<polyline points="{" ".join(pts)}" fill="none" '
        f'stroke="{_CURVE_COLOR}" stroke-width="1.2"/>')
    parts.append('</svg>')
    return '\n'.join(parts)


_CSS = """
body{background:#1E1E1E;color:#D4D4D4;font-family:'Segoe UI',
'microsoft yahei',sans-serif;margin:24px;max-width:1100px}
h1{font-size:20px}h2{font-size:16px;margin-top:28px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.card{background:#252526;border-radius:8px;padding:12px 20px;
min-width:120px}.card .num{font-size:26px;font-weight:600}
.true .num{color:#4EC9B0}.fake .num{color:#F44747}
.err .num{color:#F48771}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #3C3C3C;padding:5px 9px;text-align:left}
th{background:#252526;position:sticky;top:0}
tr.fake-row td{background:rgba(244,71,71,.08)}
.ecard{background:#252526;border:1px solid #3C3C3C;border-radius:8px;
padding:12px 16px;margin:12px 0}
.ecard h3{margin:0 0 4px;font-size:14px;color:#F44747}
.ecard .why{color:#F48771;font-size:13px;margin:4px 0 8px}
.muted{color:#808080;font-size:12px}
a{color:#4EC9B0}
"""


def _esc(s) -> str:
    return html.escape(str(s))


def _row_class(item) -> str:
    if item.status != STATUS_DONE:
        return 'err-row'
    return 'fake-row' if item.is_fake else ''


def export_html_report(items: list, folder: str, path: str,
                       include_true_cards: bool = False) -> int:
    """生成报告，返回写入的明细行数。

    items: ResultItem 列表（含未完成的行，报告如实分类）；
    include_true_cards: False（默认）= 仅假无损出证据卡（决策点②）。
    """
    done = [i for i in items if i.status == STATUS_DONE]
    fake = [i for i in done if i.is_fake]
    err = len(items) - len(done)
    avg = (sum(i.score for i in done) / len(done)) if done else 0.0
    gen_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    out = ['<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">',
           '<title>SonicCheck 扫描报告</title>',
           f'<style>{_CSS}</style></head><body>',
           f'<h1>声鉴·曲库管家 SonicCheck — 扫描报告</h1>',
           f'<p class="muted">生成时间 {gen_at} · 扫描目录 '
           f'{_esc(folder) or "（未记录）"}</p>',

           '<div class="cards">',
           f'<div class="card"><div class="num">{len(done)}</div>'
           f'扫描成功</div>',
           f'<div class="card true"><div class="num">'
           f'{len(done) - len(fake)}</div>真无损</div>',
           f'<div class="card fake"><div class="num">{len(fake)}</div>'
           f'假无损</div>',
           f'<div class="card err"><div class="num">{err}</div>'
           f'失败/其他</div>',
           f'<div class="card"><div class="num">{avg:.1f}</div>'
           f'平均评分</div>',
           '</div>',

           '<h2>明细</h2><table><thead><tr><th>文件</th><th>判定</th>'
           '<th>评分</th><th>-60dB截止</th><th>DR(dB)</th><th>格式</th>'
           '<th>采样率/位深</th><th>歌手(标签)</th></tr></thead><tbody>']
    rows = 0
    for i in sorted(items, key=lambda x: (x.status != STATUS_DONE,
                                          not (x.status == STATUS_DONE
                                              and x.is_fake))):
        cls = _row_class(i)
        if i.status == STATUS_DONE:
            m = i.detail.get('meta', {})
            verdict = ('<span style="color:#F44747">假无损</span>'
                       if i.is_fake
                       else '<span style="color:#4EC9B0">真无损</span>')
            cells = (
                _esc(i.filename),
                verdict,
                f'{i.score:g}',
                f"{i.detail['cutoffs']['-60dB']:.0f} Hz",
                f"{i.detail['dr']:.1f}",
                _esc(m.get('format', '?')),
                f"{m.get('sample_rate', 0)} Hz / {m.get('bit_depth', 0)}bit",
                _esc(m.get('artist', '') or '—'),
            )
        else:
            verdict = ('<span style="color:#F48771">失败</span>'
                       if i.status != 'pending' and i.status != 'analyzing'
                       else '—')
            cells = (_esc(i.filename), verdict, '—', '—', '—', '—', '—', '—')
        out.append(f'<tr class="{cls}"><td>' + '</td><td>'.join(cells)
                   + '</td></tr>')
        rows += 1
    out.append('</tbody></table>')

    cards = fake if not include_true_cards else done
    if cards:
        out.append('<h2>证据卡（判定依据）</h2>')
        for i in cards:
            d = i.detail
            if i.is_fake:
                why = '<br>'.join(_esc(x) for x in (i.fake_reasons or ['—']))
            else:
                why = '未检出假无损特征'
            hint = d.get('upscale_hint', '')
            if hint:
                why += f'<br>⚠ {_esc(hint)}'
            svg = _svg_spectrum(d.get('spectrum', b''),
                                d.get('cliff_freq', 0),
                                d.get('cutoffs', {}).get('-60dB', 0))
            out.append('<div class="ecard">'
                       f'<h3>{_esc(i.filename)}　'
                       f'{"假无损" if i.is_fake else "真无损"}'
                       f'（评分 {i.score:g}）</h3>'
                       f'<div class="why">{why}</div>'
                       + (svg or '<p class="muted">频谱数据不可用'
                                 '（请重新扫描）</p>')
                       + '</div>')
    out.append('<p class="muted">由 SonicCheck 生成 · 判定基于频谱特征与'
               '统计指标，仅供参考</p></body></html>')
    Path(path).write_text('\n'.join(out), encoding='utf-8')
    return rows
