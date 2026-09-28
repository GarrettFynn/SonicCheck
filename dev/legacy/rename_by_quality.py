#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据 audio_compare 扫描结果批量重命名音频文件

功能:
  - 读取 CSV 结果文件
  - 真无损文件加后缀 "_真无损"
  - 假无损文件加后缀 "_假无损"
  - 同步重命名对应的 .lrc 歌词文件
  - 防重复叠加
  - 先预览，确认后执行

用法:
  python rename_by_quality.py --csv ".\report.csv" --folder "."
"""

import csv
import os
import sys
from pathlib import Path


def load_csv(csv_path):
    """读取 CSV，返回 {stem: 是否假无损} 字典"""
    results = {}
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            stem = row['文件名'].strip()
            is_fake = row['假无损'].strip() == '是'
            results[stem] = is_fake
    return results


def find_files_by_stem(folder, stem):
    """在文件夹中查找所有 stem 匹配的文件（含不同扩展名）"""
    matched = []
    for f in os.listdir(folder):
        if Path(f).stem == stem:
            matched.append(os.path.join(folder, f))
    return matched


def build_rename_plan(folder, csv_data):
    """
    生成重命名计划
    返回: [(旧路径, 新路径, 原因), ...]
    """
    plan = []
    suffix_map = {True: '_假无损', False: '_真无损'}

    for stem, is_fake in csv_data.items():
        suffix = suffix_map[is_fake]
        files = find_files_by_stem(folder, stem)

        if not files:
            print(f"⚠️  跳过: CSV 中有记录但文件夹中找不到文件 → {stem}")
            continue

        for old_path in files:
            old_name = Path(old_path).name
            ext = Path(old_path).suffix
            old_stem = Path(old_path).stem

            # 防重复：检查是否已包含任一后缀
            if old_stem.endswith('_真无损') or old_stem.endswith('_假无损'):
                print(f"⏭️  跳过（已标记）: {old_name}")
                continue

            new_stem = old_stem + suffix
            new_name = new_stem + ext
            new_path = os.path.join(folder, new_name)

            # 检查目标是否已存在
            if os.path.exists(new_path):
                print(f"⚠️  跳过（目标已存在）: {old_name} → {new_name}")
                continue

            reason = '假无损' if is_fake else '真无损'
            plan.append((old_path, new_path, reason))

    return plan


def preview_plan(plan):
    """打印预览"""
    if not plan:
        print("\n✅ 没有需要重命名的文件。")
        return False

    true_list = [p for p in plan if p[2] == '真无损']
    fake_list = [p for p in plan if p[2] == '假无损']

    print(f"\n{'='*65}")
    print(f"📋 重命名预览")
    print(f"{'='*65}")

    if true_list:
        print(f"\n【真无损】待标记 {len(true_list)} 个文件:")
        for old, new, _ in true_list:
            print(f"  {Path(old).name}")
            print(f"    → {Path(new).name}")

    if fake_list:
        print(f"\n【假无损】待标记 {len(fake_list)} 个文件:")
        for old, new, _ in fake_list:
            print(f"  {Path(old).name}")
            print(f"    → {Path(new).name}")

    print(f"\n{'='*65}")
    print(f"总计: {len(plan)} 个文件待重命名")
    print(f"  真无损: {len(true_list)} 个")
    print(f"  假无损: {len(fake_list)} 个")
    print(f"{'='*65}")
    return True


def execute_plan(plan):
    """执行重命名"""
    print("\n🚀 开始执行重命名...")
    success = 0
    failed = 0

    for old_path, new_path, reason in plan:
        try:
            os.rename(old_path, new_path)
            tag = "🚨假" if reason == '假无损' else "✅真"
            print(f"{tag} {Path(old_path).name} → {Path(new_path).name}")
            success += 1
        except Exception as e:
            print(f"❌ 失败: {Path(old_path).name} → {e}")
            failed += 1

    print(f"\n{'='*65}")
    print(f"✅ 完成: 成功 {success} 个, 失败 {failed} 个")
    print(f"{'='*65}")


def main():
    args = sys.argv[1:]

    csv_path = None
    folder = "."

    # 解析参数
    if '--csv' in args:
        idx = args.index('--csv')
        if idx + 1 < len(args):
            csv_path = args[idx + 1]
    if '--folder' in args:
        idx = args.index('--folder')
        if idx + 1 < len(args):
            folder = args[idx + 1]

    if not csv_path:
        print("""用法:
  python rename_by_quality.py --csv ".\report.csv" --folder "."

参数:
  --csv     CSV 结果文件路径（audio_compare 生成）
  --folder  目标文件夹路径（默认当前目录）
""")
        sys.exit(1)

    if not os.path.exists(csv_path):
        print(f"❌ CSV 文件不存在: {csv_path}")
        sys.exit(1)

    if not os.path.isdir(folder):
        print(f"❌ 文件夹不存在: {folder}")
        sys.exit(1)

    print(f"📂 读取 CSV: {csv_path}")
    print(f"📂 目标文件夹: {folder}")

    csv_data = load_csv(csv_path)
    print(f"📊 CSV 中共有 {len(csv_data)} 条记录")

    plan = build_rename_plan(folder, csv_data)

    if not preview_plan(plan):
        sys.exit(0)

    # 等待用户确认
    print("\n⚠️  以上操作将实际修改文件名，不可撤销！")
    confirm = input("确认执行? 输入 y 继续，其他键取消: ").strip().lower()

    if confirm == 'y':
        execute_plan(plan)
    else:
        print("\n❎ 已取消，未执行任何操作。")


if __name__ == "__main__":
    main()
