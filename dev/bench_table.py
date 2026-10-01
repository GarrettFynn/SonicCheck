#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结果表格性能基准（V20-1d，v2.0 Model/View 重构的持续基准）

运行: QT_QPA_PLATFORM=offscreen python dev/bench_table.py [行数，默认 20000]

基准项（ROADMAP V20-1 验收线）：
1. 插入 N 行（add_row 循环，无批量模式包裹——v2.0 起不需要）
2. 回填 N 行（update_row_by_path×N，模拟扫描回填）
3. 排序点击（评分列 sort 切换）
4. 文本搜索过滤（invalidateFilter 全量重算）
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_tmp = Path(os.environ.get("BENCH_SETTINGS_DIR", tempfile_dir := __import__(
    "tempfile").mkdtemp(prefix="bench_tbl_")))
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                  str(_tmp))
QSettings.setDefaultFormat(QSettings.Format.IniFormat)

from widgets.result_table import ResultTable  # noqa: E402
from models.result_item import STATUS_DONE, ResultItem  # noqa: E402


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    app = QApplication([])
    rt = ResultTable()
    rt.resize(1000, 600)
    rt.show()

    def mk(i: int) -> ResultItem:
        return ResultItem(
            filename=f'song_{i:06d}.flac', stem=f'song_{i:06d}',
            filepath=f'C:/bench/song_{i:06d}.flac', score=100.0 - i % 100,
            cutoff_60db=18000 + i % 4000, dr=6.0 + i % 12,
            is_fake=(i % 3 == 0), status=STATUS_DONE,
            detail={'meta': {'title': f'歌曲{i}', 'artist': f'歌手{i % 50}'},
                    'cutoffs': {'-60dB': 18000 + i % 4000},
                    'dr': 6.0 + i % 12})

    t0 = time.perf_counter()
    for i in range(n):
        rt.add_row(mk(i))
    t_insert = time.perf_counter() - t0

    t0 = time.perf_counter()
    for i in range(n):
        rt.update_row_by_path(f'C:/bench/song_{i:06d}.flac', mk(i))
    t_update = time.perf_counter() - t0

    t0 = time.perf_counter()
    rt.proxy.sort(1, __import__('PyQt6.QtCore', fromlist=['Qt'])
                  .Qt.SortOrder.DescendingOrder)
    t_sort = time.perf_counter() - t0

    t0 = time.perf_counter()
    rt.proxy.set_search_text('歌手7')
    t_search = time.perf_counter() - t0
    vis = rt.proxy.rowCount()
    rt.proxy.set_search_text('')

    print(f"行数 {n}:")
    print(f"  插入       {t_insert:6.2f}s（验收 <2s）")
    print(f"  回填       {t_update:6.2f}s")
    print(f"  评分排序   {t_sort:6.3f}s（验收 <0.3s）")
    print(f"  搜索过滤   {t_search:6.3f}s（命中 {vis} 行）")
    assert t_insert < 5.0, f"插入过慢: {t_insert:.2f}s"
    assert t_sort < 1.0, f"排序过慢: {t_sort:.3f}s"
    rt.hide()
    print("基准通过")


if __name__ == '__main__':
    main()
