#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SonicCheck 入口：GUI 主流程 + CLI 无头扫描（V14-4）

CLI（不创建 QApplication，core 层本就 Qt-free）：
  python main.py --scan <目录> [--threads N] [--seconds N] [--out 路径]

--out 支持 .csv（默认）与 .html（v1.3.0 报告）。默认参数与 GUI 严格
一致（分析 30 秒 / 8 线程），保证 CLI 与 GUI 对同一目录产出相同判定。
打包版是 --windowed（无控制台），用 AttachConsole 把输出接回调用方
终端；双击启动（无父控制台）时输出落 soniccheck_cli.log。
"""

import sys
from pathlib import Path


def _attach_console() -> bool:
    """--windowed exe 下把 stdout/stderr 接回父控制台（win32 惯用法）"""
    if sys.platform != 'win32' or sys.stdout is not None:
        return sys.stdout is not None
    import ctypes
    ATTACH_PARENT_PROCESS = 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32
    if not kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
        return False
    for handle, name in ((1, 'CONOUT$'), (2, 'CONOUT$')):
        try:
            fd = kernel32.CreateFileW(handle, 0xC0000000, 3, None, 3, 0, None)
            if fd != -1:
                import os
                os.close(handle)
                os.dup2(fd, handle)
        except OSError:
            pass
    sys.stdout = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
    sys.stderr = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
    return True


def run_cli(args) -> int:
    """无头扫描：分析 → 导出。返回进程退出码。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from core.analyzer import ScanCancelled, analyze_file
    from core.scanner import find_audio_files

    files, skipped_dirs = find_audio_files(args.scan)
    if skipped_dirs:
        print(f"⚠ {skipped_dirs} 个子目录无法访问，已跳过")
    if not files:
        print(f"该文件夹（含子文件夹）中没有音频文件: {args.scan}")
        return 2
    print(f"共 {len(files)} 个音频文件，线程 {args.threads}，"
          f"每首分析前 {args.seconds} 秒")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futures = {pool.submit(analyze_file, fp, args.seconds): fp
                   for fp in files}
        for fut in as_completed(futures):
            fp = futures[fut]
            try:
                results.append(fut.result())
            except ScanCancelled:
                pass
            except Exception as exc:  # 单文件失败不中断整批
                print(f"  ✗ {Path(fp).name}: {exc}")
            done += 1
            print(f"[{done}/{len(files)}] 已处理", end='\r' if done < len(files)
                  else '\n', flush=True)

    out = Path(args.out)
    results.sort(key=lambda r: r['filepath'].lower())

    class _Shim:   # 导出层吃 ResultItem 属性，CLI 用最小适配对象
        __slots__ = ('filename', 'stem', 'filepath', 'score', 'cutoff_60db',
                     'dr', 'is_fake', 'fake_reasons', 'status',
                     'error_message', 'detail')

    def _to_shim(r: dict) -> '_Shim':
        s = _Shim()
        s.filename = Path(r['filepath']).name
        s.stem = Path(r['filepath']).stem
        s.filepath = r['filepath']
        s.score = r['score']
        s.cutoff_60db = r['cutoffs']['-60dB']
        s.dr = r['dr']
        s.is_fake = r['fake_lossless']
        s.fake_reasons = r['fake_reasons']
        s.status = 'done'
        s.error_message = ''
        s.detail = r
        return s

    items = [_to_shim(r) for r in results]
    if out.suffix.lower() == '.html':
        from core.html_report import export_html_report
        rows = export_html_report(items, args.scan, str(out))
    else:
        from core.csv_exporter import export_csv
        rows = export_csv(items, str(out))
    fake_cnt = sum(1 for r in results if r['fake_lossless'])
    print(f"完成：{len(results)} 首（假无损 {fake_cnt}），"
          f"已导出 {rows} 行 → {out}")
    return 0


def main() -> int:
    # CLI 分支：--scan 出现在参数里即走无头模式（不 import PyQt6）
    if '--scan' in sys.argv or '--help' in sys.argv or '-h' in sys.argv:
        import argparse
        parser = argparse.ArgumentParser(
            prog='SonicCheck',
            description='声鉴·曲库管家——无损真伪批量鉴定（无头模式）')
        parser.add_argument('--scan', required=True,
                            help='要扫描的音乐文件夹')
        parser.add_argument('--threads', type=int, default=8,
                            help='并行线程数（默认 8，与 GUI 默认一致）')
        parser.add_argument('--seconds', type=int, default=30,
                            help='每首分析的秒数（默认 30，与 GUI 一致）')
        parser.add_argument('--out', default='',
                            help='输出文件（.csv 或 .html；默认 ./SonicCheck报告.csv）')
        args = parser.parse_args()
        attached = _attach_console()
        if not attached:
            # 双击启动（无父控制台）：输出落日志文件
            import io
            log = open(Path.cwd() / 'soniccheck_cli.log', 'w',
                       encoding='utf-8', buffering=1)
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer or log.buffer,
                                          encoding='utf-8', buffering=1) \
                if sys.stdout else log
            sys.stderr = sys.stdout
        if not args.out:
            args.out = str(Path.cwd()
                           / f"SonicCheck报告_{__import__('datetime').datetime.now():%Y%m%d_%H%M%S}.csv")
        try:
            return run_cli(args)
        except KeyboardInterrupt:
            print("\n已中断")
            return 130

    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from main_window import (APP_NAME, APP_VERSION, ORG_NAME, MainWindow,
                             resource_path)

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    QApplication.setApplicationVersion(APP_VERSION)

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("resources/icon.png")))
    qss = Path(resource_path("resources/style.qss"))
    if qss.exists():
        app.setStyleSheet(qss.read_text(encoding="utf-8"))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
