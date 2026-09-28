#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3 核心测试：renamer + csv_exporter（不依赖 Qt，托管 Python 可跑）

⚠ 全程在 dev/tmp_m3_sandbox 沙盒副本上操作，绝不改动 samples/ 原始文件。

覆盖：
1. 后缀剥离（含套娃）与改判（假→真）
2. .lrc 同步、子目录递归安全
3. 目标已存在 → 冲突跳过；error 行不进计划
4. 执行后再建计划 → 全部无操作（防重复）
5. CSV：旧脚本全列+完整路径、UTF-8-BOM、error 行不导出
"""

import csv
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.analyzer import analyze_file  # noqa: E402
from core.csv_exporter import CSV_COLUMNS, export_csv  # noqa: E402
from core.renamer import (KIND_AUDIO, KIND_LRC, build_rename_plan,  # noqa: E402
                          execute_plan, strip_quality_suffix)
from models.result_item import (STATUS_DONE, STATUS_ERROR,  # noqa: E402
                                ResultItem)

SAMPLES = ROOT / "samples"
SANDBOX = ROOT / "dev" / "tmp_m3_sandbox"

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def build_sandbox() -> None:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    (SANDBOX / "sub").mkdir(parents=True)

    # 1) 改判场景：真无损内容但带着 _假无损 后缀（子目录，验证递归安全）
    shutil.copy2(SAMPLES / "ALL RED (Explicit) - Playboi Carti_EM_真无损.flac",
                 SANDBOX / "sub" / "ALL RED_假无损.flac")
    # 2) 无后缀的假无损文件 + 同名歌词
    shutil.copy2(SAMPLES / "Because I Hear You - Toe_假无损.flac",
                 SANDBOX / "Because I Hear You - Toe.flac")
    (SANDBOX / "Because I Hear You - Toe.lrc").write_text(
        "[00:00.00]测试歌词", encoding="utf-8")
    # 3) 冲突场景：X.flac 判假，但 X_假无损.flac 已存在（占位 dummy）
    shutil.copy2(SAMPLES / "music-sample-96000hz-24bit.flac",
                 SANDBOX / "X.flac")
    (SANDBOX / "X_假无损.flac").write_text("占位文件", encoding="utf-8")


def analyze_sandbox() -> list:
    items = []
    for p in sorted(SANDBOX.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (
                '.flac', '.wav', '.mp3', '.m4a', '.ogg', '.ape', '.wma',
                '.aac', '.opus'):
            continue
        try:
            data = analyze_file(str(p))
            items.append(ResultItem(
                filename=p.name, stem=p.stem, filepath=str(p),
                score=round(float(data['score']), 1),
                cutoff_60db=float(data['cutoffs']['-60dB']),
                dr=round(float(data['dr']), 1),
                is_fake=bool(data['fake_lossless']),
                fake_reasons=list(data['fake_reasons']),
                status=STATUS_DONE, detail=data))
        except Exception as exc:
            items.append(ResultItem(
                filename=p.name, stem=p.stem, filepath=str(p),
                status=STATUS_ERROR, error_message=str(exc)))
    return items


def main() -> int:
    print("[0] 后缀剥离单元测试")
    check("普通名不动", strip_quality_suffix("歌名") == "歌名")
    check("剥单后缀", strip_quality_suffix("歌名_假无损") == "歌名")
    check("剥套娃", strip_quality_suffix("歌名_假无损_真无损") == "歌名")
    check("EM 等原名保留",
          strip_quality_suffix("ALL RED (Explicit)_EM") ==
          "ALL RED (Explicit)_EM")

    print("\n[1] 搭沙盒并分析")
    build_sandbox()
    items = analyze_sandbox()
    done = [i for i in items if i.status == STATUS_DONE]
    err = [i for i in items if i.status == STATUS_ERROR]
    check("分析成功 3 首", len(done) == 3, f"done={len(done)}")
    check("dummy 占位文件进 error", len(err) == 1,
          err[0].filename if err else "无 error 行")
    by_stem = {i.stem: i for i in done}
    check("改判样本判真", not by_stem["ALL RED_假无损"].is_fake)
    check("Toe 判假", by_stem["Because I Hear You - Toe"].is_fake)
    check("X 判假", by_stem["X"].is_fake)

    print("\n[2] 建重命名计划")
    plan, skipped = build_rename_plan(items)
    audio_ops = [op for op in plan if op.kind == KIND_AUDIO]
    lrc_ops = [op for op in plan if op.kind == KIND_LRC]
    check("音频 op 2 个", len(audio_ops) == 2, f"{len(audio_ops)}")
    check("歌词 op 1 个", len(lrc_ops) == 1)
    conflicts = [s for s in skipped if "目标已存在" in s.why]
    check("X.flac 冲突被跳过", len(conflicts) == 1
          and conflicts[0].path.endswith("X.flac"))
    check("error 行不进计划", not any("占位" in op.old_path or
                                      op.old_path.endswith("占位文件")
                                      for op in plan))

    print("\n[3] 执行重命名")
    results = execute_plan(plan)
    check("全部执行成功", all(ok for _, ok, _ in results))
    check("改判落盘: sub/ALL RED_真无损.flac",
          (SANDBOX / "sub" / "ALL RED_真无损.flac").is_file())
    check("音频落盘: Because I Hear You - Toe_假无损.flac",
          (SANDBOX / "Because I Hear You - Toe_假无损.flac").is_file())
    check("歌词同步: Because I Hear You - Toe_假无损.lrc",
          (SANDBOX / "Because I Hear You - Toe_假无损.lrc").is_file()
          and not (SANDBOX / "Because I Hear You - Toe.lrc").exists())
    check("冲突源文件未动: X.flac", (SANDBOX / "X.flac").is_file())
    check("冲突目标未动: X_假无损.flac（仍是 dummy）",
          (SANDBOX / "X_假无损.flac").read_text(encoding="utf-8") == "占位文件")

    print("\n[4] 二次建计划（模拟结果已同步路径后）——应全部无操作")
    for i in items:  # 模拟 _on_audio_renamed 的路径同步
        if i.status != STATUS_DONE:
            continue
        for op, ok, _ in results:
            if ok and op.kind == KIND_AUDIO and op.old_path == i.filepath:
                p = Path(op.new_path)
                i.filepath, i.filename, i.stem = str(p), p.name, p.stem
    plan2, skipped2 = build_rename_plan(items)
    check("二次计划为空", len(plan2) == 0, f"{len(plan2)}")
    noops = [s for s in skipped2 if "无需变更" in s.why]
    check("3 项无操作（2 音频 + 已同步歌词）", len(noops) == 3,
          f"{len(noops)}")

    print("\n[5] CSV 导出")
    csv_path = SANDBOX / "export_test.csv"
    rows = export_csv(items, str(csv_path))
    check("导出 3 行（error 不导出）", rows == 3, f"{rows}")
    raw = csv_path.read_bytes()
    check("UTF-8-BOM 头", raw.startswith(b'\xef\xbb\xbf'))
    with open(csv_path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        check("列 = 旧脚本全列+完整路径", reader.fieldnames == CSV_COLUMNS,
              str(reader.fieldnames))
        table = list(reader)
    # 注意：本测试在重命名后导出，文件名（stem）已带质量后缀
    toe = next(r for r in table
               if r['文件名'] == "Because I Hear You - Toe_假无损")
    check("假无损标记", toe['假无损'] == '是')
    check("评分 28.5", toe['综合评分'] == '28.5', toe['综合评分'])
    check("完整路径列", toe['完整路径'].endswith(
        "Because I Hear You - Toe_假无损.flac"), toe['完整路径'])
    check("假无损原因非空", "8400" in toe['假无损原因'], toe['假无损原因'])

    print(f"\n{'=' * 60}\n总体: {'全部通过' if not failures else f'FAIL: {failures}'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
