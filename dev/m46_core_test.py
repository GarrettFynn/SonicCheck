#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4.6 核心测试（托管 Python，无 Qt）：自定义标记 / 还原 / 手动保留 / 安全模式核心

⚠ 全程沙盒副本，不动 samples 原件。测完由调用方 rm -rf 沙盒。

覆盖：
1. quality_marks：默认/自定义文本、前后缀位置、剥标记（默认+自定义+套娃）
2. renamer/tag_renamer 接入自定义标记（前缀模式）
3. deduper：手动保留项生效、manifest 记录、还原计划（含占用冲突/死条目清理）
4. scanner 排除 _安全输出 目录
5. execute_copy_plan：复制不覆盖已有目标
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.deduper import (CLEAR_DIR_NAME, DupGroup, build_clear_plan,  # noqa: E402
                          build_restore_plan, execute_move_plan,
                          load_restore_map, prune_restore_map,
                          record_restore_map)
from core.quality_marks import (POS_PREFIX, POS_SUFFIX, apply_mark,  # noqa: E402
                                mark_for, set_config,
                                strip_quality_mark)
from core.renamer import (KIND_AUDIO, build_rename_plan,  # noqa: E402
                          execute_copy_plan, strip_quality_suffix)
from core.scanner import find_audio_files  # noqa: E402
from models.result_item import STATUS_DONE, ResultItem  # noqa: E402

SAMPLES = ROOT / "samples"
SANDBOX = ROOT / "dev" / "tmp_m46_sandbox"

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def make_item(path: Path, score: float = 50.0, is_fake: bool = True) -> ResultItem:
    item = ResultItem(filename=path.name, stem=path.stem,
                      filepath=str(path), status=STATUS_DONE)
    item.score = score
    item.is_fake = is_fake
    item.detail = {'meta': {'duration': 30.0, 'title': 'T', 'artist': 'A'},
                   'dr': 10.0, 'correlation': 0.9,
                   'cutoffs': {'-60dB': 15000.0}, 'filepath': str(path)}
    return item


def main() -> int:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)

    print("[1] quality_marks 基础")
    set_config("_真无损", "_假无损", POS_SUFFIX)  # 复位默认
    check("默认后缀标记", apply_mark("歌名", True) == "歌名_假无损"
          and apply_mark("歌名", False) == "歌名_真无损")
    set_config("[真]", "[假]", POS_PREFIX)  # 参数顺序: mark_true, mark_fake
    check("自定义前缀标记", apply_mark("歌名", True) == "[假]歌名"
          and mark_for(False) == "[真]")
    check("剥自定义前缀", strip_quality_mark("[假]歌名") == "歌名")
    check("剥自定义后缀套娃", strip_quality_mark("歌名[假][真]") == "歌名")
    check("剥默认+自定义混合", strip_quality_mark("[假]歌名_假无损") == "歌名")
    set_config("_真无损", "_假无损", POS_SUFFIX)  # 还原默认防污染后续
    check("还原后剥默认", strip_quality_suffix("歌名_假无损") == "歌名")

    print("\n[2] renamer 接入自定义标记（前缀模式）")
    src = SAMPLES / "Because I Hear You - Toe_假无损.flac"
    p1 = SANDBOX / "songA.flac"
    shutil.copy2(src, p1)
    set_config("【真】", "【假】", POS_PREFIX)  # 参数顺序: mark_true, mark_fake
    item1 = make_item(p1, is_fake=True)
    plan, _ = build_rename_plan([item1])
    check("前缀标记计划", len(plan) == 1
          and Path(plan[0].new_path).name == "【假】songA.flac",
          str([Path(o.new_path).name for o in plan]))
    set_config("_真无损", "_假无损", POS_SUFFIX)  # 还原

    print("\n[3] deduper 手动保留项")
    low = make_item(p1, score=30.0)
    high = make_item(SANDBOX / "x.flac", score=90.0)
    g = DupGroup(label="g", items=[high, low])  # 契约：items 按评分降序
    check("默认保留最高分", g.keep.score == 90.0)
    g.keep_override = low
    check("手动指定后保留改变", g.keep.score == 30.0
          and g.clear[0].score == 90.0)

    print("\n[4] manifest 记录与还原计划")
    clear_dir = SANDBOX / CLEAR_DIR_NAME
    moved_src = SANDBOX / "sub" / "old.flac"
    moved_src.parent.mkdir(parents=True)
    shutil.copy2(src, moved_src)
    (SANDBOX / "sub" / "old.lrc").write_text("x", encoding="utf-8")
    plan, _ = build_clear_plan(
        [DupGroup(label="g2", items=[make_item(p1, score=99.0),
                                     make_item(moved_src, score=10.0)])],
        str(SANDBOX))  # 契约：items 按评分降序，clear=moved_src（含 lrc）
    results = execute_move_plan(plan)
    ok_moved = [(op.new_path, op.old_path) for op, ok, _ in results if ok]
    record_restore_map(str(clear_dir), ok_moved)
    check("manifest 已写 2 条（音频+lrc）",
          len(load_restore_map(str(clear_dir))) == 2,
          str(load_restore_map(str(clear_dir))))

    rplan, rskip = build_restore_plan(str(clear_dir))
    check("还原计划 2 条", len(rplan) == 2, f"{len(rplan)}")
    check("还原目标回原位（含 sub 子目录）",
          any(op.new_path.endswith("sub\\old.flac")
              or op.new_path.endswith("sub/old.flac") for op in rplan))
    # 占用冲突：原位置放一个新文件 → 该条进 skipped
    dead = clear_dir / "dead.flac"
    shutil.copy2(src, dead)
    record_restore_map(str(clear_dir),
                       [(str(dead), str(SANDBOX / "occupied.flac"))])
    (SANDBOX / "occupied.flac").write_text("占位", encoding="utf-8")
    rplan2, rskip2 = build_restore_plan(str(clear_dir))
    check("占用冲突进 skipped", any("已被占用" in w for _, w in rskip2),
          str(rskip2))
    check("占用项保留在 manifest", len(load_restore_map(str(clear_dir))) == 3)
    # 死条目清理：源被手动删 → 从 manifest 清除
    dead.unlink()
    build_restore_plan(str(clear_dir))
    check("死条目已清理", len(load_restore_map(str(clear_dir))) == 2)

    # 执行还原
    rplan3, _ = build_restore_plan(str(clear_dir))
    rresults = execute_move_plan(rplan3)
    ok_restore = [op.old_path for op, ok, _ in rresults if ok]
    prune_restore_map(str(clear_dir), ok_restore)
    check("还原成功 2 个", len(ok_restore) == 2)
    check("音频回到原位", moved_src.is_file()
          and (SANDBOX / "sub" / "old.lrc").is_file())
    check("manifest 已清空删除",
          not (clear_dir / ".restore_map.json").exists())

    print("\n[5] scanner 排除 _安全输出 目录")
    safe_dir = SANDBOX / f"{SANDBOX.name}_安全输出"
    safe_dir.mkdir()
    shutil.copy2(src, safe_dir / "copy.flac")
    files, _ = find_audio_files(str(SANDBOX))
    check("安全输出目录被排除",
          not any("_安全输出" in f for f in files),
          f"扫到 {len(files)} 个")

    print("\n[6] execute_copy_plan 不覆盖已有目标")
    op_plan = []
    from core.renamer import RenameOp
    dst = SANDBOX / "exists.flac"
    dst.write_text("旧件", encoding="utf-8")
    op_plan.append(RenameOp(str(p1), str(dst), KIND_AUDIO, "测试"))
    cresults = execute_copy_plan(op_plan)
    check("已有目标 → 该条失败不覆盖",
          not cresults[0][1] and dst.read_text(encoding="utf-8") == "旧件")
    op_plan2 = [RenameOp(str(p1), str(SANDBOX / "new" / "ok.flac"),
                         KIND_AUDIO, "测试")]
    c2 = execute_copy_plan(op_plan2)
    check("正常复制成功且原件在",
          c2[0][1] and (SANDBOX / "new" / "ok.flac").is_file()
          and p1.is_file())

    print(f"\n{'=' * 60}\n总体: {'全部通过' if not failures else f'FAIL: {failures}'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
