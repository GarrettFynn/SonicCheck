#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SonicCheck 入口：GUI 主流程 + CLI 无头扫描（V14-4）

CLI（不创建 QApplication，core 层本就 Qt-free）：
  python main.py --scan <目录> [--threads N] [--seconds N] [--out 路径]

--out 支持 .csv（默认）与 .html（v1.3.0 报告）。默认参数与 GUI 严格
一致（分析 30 秒 / 8 线程），保证 CLI 与 GUI 对同一目录产出相同判定。

退出码（v2.0.2 审查修复②）：0 成功 / 2 参数错误（argparse 标准） /
3 目录中没有音频文件 / 4 全部分析失败（0 行结果）。

打包版是 --windowed（无控制台）：先 AttachConsole 接回调用方终端再
解析参数（argparse 的报错/帮助也能被看到）；双击启动（无父控制台）
时输出落 ~/.soniccheck/soniccheck_cli.log（不依赖 cwd 可写）。
"""

import sys
from pathlib import Path


def _attach_console() -> bool:
    """--windowed exe 下把 stdout/stderr 接回父控制台（win32 惯用法）。

    v2.0.2 审查修复：删除旧版 CreateFileW(句柄数字,…) 段——该调用把
    fd 数字当文件名指针传参（参数错误，实为访问违例，被 CPython 转
    OSError 后由 except 吞掉），真实可用的是 AttachConsole 之后直接
    open('CONOUT$')。"""
    if sys.platform != 'win32' or sys.stdout is not None:
        return sys.stdout is not None
    import ctypes
    kernel32 = ctypes.windll.kernel32
    if not kernel32.AttachConsole(0xFFFFFFFF):   # ATTACH_PARENT_PROCESS
        return False
    sys.stdout = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
    sys.stderr = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
    return True


def _range_int(lo: int, hi: int, what: str):
    """argparse type：范围校验（v2.0.2 审查修复②——此前 --threads 0
    直接 ValueError、--seconds 0 把全库误判成假无损且退出码仍为 0）"""
    import argparse

    def parse(text: str) -> int:
        try:
            v = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f'{what}必须是整数: {text!r}')
        if not lo <= v <= hi:
            raise argparse.ArgumentTypeError(
                f'{what}须在 {lo}~{hi} 之间，当前 {v}')
        return v
    return parse


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
        return 3
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

    if not results:
        print("全部文件分析失败，无结果可导出（检查 ffmpeg 是否可用）")
        return 4

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
    try:
        if out.suffix.lower() == '.html':
            from core.html_report import export_html_report
            rows = export_html_report(items, args.scan, str(out))
        else:
            from core.csv_exporter import export_csv
            rows = export_csv(items, str(out))
    except OSError as exc:
        print(f"导出失败: {exc}")
        return 4
    fake_cnt = sum(1 for r in results if r['fake_lossless'])
    print(f"完成：{len(results)} 首（假无损 {fake_cnt}），"
          f"已导出 {rows} 行 → {out}")
    return 0


def _install_crash_log() -> None:
    """GUI 未捕获异常落盘 ~/.soniccheck/crash.log（v2.0.1）。

    --windowed 打包后 stderr 不可见，闪退（如 V20-2 漏搬方法的
    AttributeError）此前不留任何痕迹；钩子把 traceback 写文件并
    保留默认行为。CLI 分支不安装（异常直接打终端）。"""
    import traceback

    log_dir = Path.home() / '.soniccheck'

    def _hook(tp, val, tb) -> None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / 'crash.log', 'a', encoding='utf-8') as f:
                f.write(f"\n==== {__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} ====\n")
                traceback.print_exception(tp, val, tb, file=f)
        except OSError:
            pass
        sys.__excepthook__(tp, val, tb)   # 保留默认行为（控制台/错误框）

    sys.excepthook = _hook


def main() -> int:
    # CLI 分支：--scan 出现在参数里即走无头模式（不 import PyQt6）
    if '--scan' in sys.argv or '--help' in sys.argv or '-h' in sys.argv:
        # v2.0.2：先接管控制台再解析参数——argparse 的报错/帮助文本
        # 才能在 --windowed 版下被用户看到
        if not _attach_console():
            # 双击启动（无父控制台）：输出落用户目录（不依赖 cwd 可写）
            log_dir = Path.home() / '.soniccheck'
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                sys.stdout = open(log_dir / 'soniccheck_cli.log', 'w',
                                  encoding='utf-8', buffering=1)
                sys.stderr = sys.stdout
            except OSError:
                pass

        import argparse
        parser = argparse.ArgumentParser(
            prog='SonicCheck',
            description='声鉴·曲库管家——无损真伪批量鉴定（无头模式）',
            epilog='退出码：0 成功 / 2 参数错误 / 3 无音频文件 / 4 全部失败')
        parser.add_argument('--scan', required=True,
                            help='要扫描的音乐文件夹')
        # 范围与 GUI 左栏 spinbox 一致（widgets/left_panel.py 1..16 / 5..120）
        parser.add_argument('--threads', type=_range_int(1, 16, '线程数'),
                            default=8,
                            help='并行线程数 1~16（默认 8，与 GUI 一致）')
        parser.add_argument('--seconds', type=_range_int(5, 120, '分析秒数'),
                            default=30,
                            help='每首分析的秒数 5~120（默认 30，与 GUI 一致）')
        parser.add_argument('--out', default='',
                            help='输出文件（.csv 或 .html；默认 ./SonicCheck报告.csv）')
        args = parser.parse_args()
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

    _install_crash_log()

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
