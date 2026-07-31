#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSV 导出（③-4）：旧脚本全列 + 完整路径列，UTF-8-BOM

列与 audio_compare_多进程并行版.py 的 analyze_single_file 输出完全一致，
末尾追加"完整路径"列（递归扫描下同名文件消歧必需）。
只导出分析成功（done）的行，与旧脚本跳过失败的行为一致。
"""

import csv
from datetime import datetime

from models.result_item import STATUS_DONE

CSV_COLUMNS = [
    '文件名', '格式', '编码', '采样率', '位深度', '比特率_kb',
    '截止_40dB', '截止_60dB', '截止_80dB', '动态范围_dB', '立体声相关',
    '假无损', '假无损原因', '综合评分', '完整路径',
]


def default_csv_name() -> str:
    return f"AudioQuality_{datetime.now():%Y%m%d_%H%M%S}.csv"


def item_to_row(item) -> dict:
    d = item.detail
    meta = d['meta']
    cutoffs = d['cutoffs']
    return {
        '文件名': item.stem,
        '格式': meta['format'],
        '编码': meta['codec'],
        '采样率': meta['sample_rate'],
        '位深度': meta['bit_depth'],
        '比特率_kb': meta['bitrate'] // 1000 if meta['bitrate'] else '',
        '截止_40dB': round(cutoffs['-40dB']),
        '截止_60dB': round(cutoffs['-60dB']),
        '截止_80dB': round(cutoffs['-80dB']),
        '动态范围_dB': round(d['dr'], 1),
        '立体声相关': round(d['correlation'], 3),
        '假无损': '是' if d['fake_lossless'] else '否',
        '假无损原因': '; '.join(d['fake_reasons']) if d['fake_lossless'] else '',
        '综合评分': round(d['score'], 1),
        '完整路径': item.filepath,
    }


def export_csv(items: list, csv_path: str) -> int:
    """导出 done 结果到 CSV（UTF-8-BOM），返回写入行数"""
    rows = [item_to_row(i) for i in items if i.status == STATUS_DONE]
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
