#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键运行全部测试套件：python tests/run_all.py

自动发现本目录下所有 test_*.py，逐个用当前解释器独立进程运行；
任何套件失败则整体退出码非零，供发版前自检/CI 使用。
"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parent
    suites = sorted(here.glob('test_*.py'))
    if not suites:
        print("未发现任何 test_*.py 套件")
        return 1

    failed = []
    for suite in suites:
        print(f"\n{'=' * 64}\n▶ {suite.name}\n{'=' * 64}", flush=True)
        r = subprocess.run([sys.executable, str(suite)], cwd=str(here))
        if r.returncode != 0:
            failed.append(suite.name)

    print(f"\n{'=' * 64}")
    if failed:
        print(f"✗ 失败套件（{len(failed)}/{len(suites)}）: {', '.join(failed)}")
        return 1
    print(f"✓ 全部 {len(suites)} 个测试套件通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
