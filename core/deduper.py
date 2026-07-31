#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重复音乐检测与清除计划（功能 C）

分组口径（两路并查，覆盖"相同音乐不同音质不同名字"）：
1. 标签分组：title+artist 都有标签的，按规范化标签合并；
   缺标签的退回"剥质量后缀的规范化文件名"
2. 内容签名合并：时长 ±0.3s 且 DR ±0.2 且立体声相关 ±0.01 且
   -60dB 截止 ±50Hz → 同一编码内容（转码/重采样指标几乎不变，
   能抓住"不同名字"的同内容文件）

已知局限：不同音质（如有损 vs 无损两个版本）且无标签且文件名也不同
的，无法自动识别——需要声纹指纹（chromaprint），二期再议。

清除方式（已拍板 C1）：非最优文件移动到目标文件夹 `_待清除/` 子目录，
可恢复；同 stem 的 .lrc 一并移动；目标撞名自动加 (n) 序号。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from core.renamer import strip_quality_suffix
from models.result_item import STATUS_DONE

CLEAR_DIR_NAME = "_待清除"
RESTORE_MANIFEST = ".restore_map.json"  # _待清除 内的还原映射（new→old）

# 内容签名容差（实测：44.1k WAV 与 96k FLAC 同一内容，DR 差 6e-5、
# 相关差 2e-6、截止完全一致；留足余量但不至于误合不同歌曲）
DURATION_TOL = 0.3
DR_TOL = 0.2
CORR_TOL = 0.01
CUTOFF_TOL = 50.0


@dataclass
class MoveOp:
    old_path: str
    new_path: str
    kind: str          # 'audio' / 'lrc'
    group_label: str   # 所属重复组说明（日志用）


@dataclass
class DupGroup:
    label: str                  # 组名（标签或文件名）
    items: list = field(default_factory=list)  # 按评分降序
    keep_override: object = None  # 用户在对比表手动指定的保留项（item）

    @property
    def keep(self):
        if self.keep_override is not None:
            return self.keep_override
        return self.items[0] if self.items else None

    @property
    def clear(self):
        k = self.keep
        return [i for i in self.items if i is not k]


def _norm(text: str) -> str:
    """规范化：去全部空白 + 小写（CJK 不受影响）"""
    return "".join(str(text or "").split()).lower()


def _tag_key(item) -> str:
    meta = item.detail.get('meta', {})
    title = meta.get('title', '')
    if title:
        return f"tag:{_norm(title)}|{_norm(meta.get('artist', ''))}"
    return f"name:{_norm(strip_quality_suffix(item.stem))}"


def _same_signature(a, b) -> bool:
    da, db = a.detail, b.detail
    if abs(da['meta'].get('duration', 0) - db['meta'].get('duration', 0)) \
            > DURATION_TOL:
        return False
    if abs(da['dr'] - db['dr']) > DR_TOL:
        return False
    if abs(da['correlation'] - db['correlation']) > CORR_TOL:
        return False
    return abs(da['cutoffs']['-60dB'] - db['cutoffs']['-60dB']) <= CUTOFF_TOL


def find_duplicate_groups(items: list) -> list:
    """返回 DupGroup 列表（每组 ≥2 个，按评分降序，items[0] 为建议保留）"""
    done = [i for i in items if i.status == STATUS_DONE]
    # 并查集
    parent = {id(i): id(i) for i in done}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    # 路 1：标签/文件名分组
    by_key = {}
    for i in done:
        by_key.setdefault(_tag_key(i), []).append(i)
    for group in by_key.values():
        for j in range(1, len(group)):
            union(id(group[0]), id(group[j]))

    # 路 2：内容签名两两合并（O(n²)，曲库万级内可接受；
    # 先按时长分桶降复杂度）
    done_sorted = sorted(done, key=lambda i: i.detail['meta'].get('duration', 0))
    for idx, a in enumerate(done_sorted):
        da = a.detail['meta'].get('duration', 0)
        for b in done_sorted[idx + 1:]:
            if b.detail['meta'].get('duration', 0) - da > DURATION_TOL:
                break
            if _same_signature(a, b):
                union(id(a), id(b))

    clusters = {}
    for i in done:
        clusters.setdefault(find(id(i)), []).append(i)

    groups = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda i: i.score, reverse=True)
        meta = members[0].detail.get('meta', {})
        label = (meta.get('title') or
                 strip_quality_suffix(members[0].stem)) or members[0].filename
        groups.append(DupGroup(label=label, items=members))
    groups.sort(key=lambda g: g.label.lower())
    return groups


def build_clear_plan(groups: list, target_root: str) -> tuple:
    """非最优文件移到 <target_root>/_待清除/。

    返回 (plan, skipped)：plan 为 MoveOp 列表；
    skipped 为 (path, 原因)——如同名 lrc 不存在等无需处理项（预留给日志）。
    """
    plan: list = []
    skipped: list = []
    clear_dir = Path(target_root) / CLEAR_DIR_NAME
    taken = set()  # 目标名占用（小写，Windows 不敏感）

    for g in groups:
        keep_name = g.keep.filename if g.keep else "?"
        for item in g.clear:
            old = Path(item.filepath)
            candidates = [(old, 'audio')]
            lrc_old = old.parent / (old.stem + ".lrc")
            if lrc_old.is_file():
                candidates.append((lrc_old, 'lrc'))
            for src, kind in candidates:
                dst = clear_dir / src.name
                n = 1
                while (str(dst).lower() in taken) or dst.exists():
                    dst = clear_dir / f"{src.stem} ({n}){src.suffix}"
                    n += 1
                taken.add(str(dst).lower())
                plan.append(MoveOp(str(src), str(dst), kind,
                                   f"{g.label}（保留: {keep_name}）"))
    return plan, skipped


def execute_move_plan(plan: list) -> list:
    """执行移动，返回 [(op, ok, error)]；单条失败不中断整批"""
    moved_dirs = set()
    results = []
    for op in plan:
        try:
            dst_dir = str(Path(op.new_path).parent)
            if dst_dir not in moved_dirs:
                os.makedirs(dst_dir, exist_ok=True)
                moved_dirs.add(dst_dir)
            os.rename(op.old_path, op.new_path)
            results.append((op, True, ""))
        except OSError as exc:
            results.append((op, False, str(exc)))
    return results


# ---------------- 去重还原（_待清除 → 移回原位） ----------------
def record_restore_map(clear_dir: str, moved: list) -> None:
    """把本次成功移动的 new→old 映射合并写入 manifest（供还原用）"""
    import json
    manifest = Path(clear_dir) / RESTORE_MANIFEST
    data = {}
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
    for new_path, old_path in moved:
        data[new_path] = old_path
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                        encoding='utf-8')


def load_restore_map(clear_dir: str) -> dict:
    """读取 manifest（不存在/损坏返回空 dict）"""
    import json
    manifest = Path(clear_dir) / RESTORE_MANIFEST
    if not manifest.is_file():
        return {}
    try:
        return json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def build_restore_plan(clear_dir: str) -> tuple:
    """从 manifest 建还原计划：_待清除 里的文件移回原始位置。

    返回 (plan, skipped)：skipped 为 (path, 原因)——源已不在（已还原过）
    或原位置已被占用的项；同时清理 manifest 中源已不在的死条目。
    """
    import json
    mapping = load_restore_map(clear_dir)
    plan: list = []
    skipped: list = []
    alive: dict = {}
    for new_path, old_path in mapping.items():
        src = Path(new_path)
        dst = Path(old_path)
        if not src.exists():
            skipped.append((new_path, "源文件已不在 _待清除（可能已还原或手动删除）"))
            continue
        if dst.exists():
            skipped.append((new_path, f"原位置已被占用: {dst.name}"))
            alive[new_path] = old_path  # 保留条目，以后可再试
            continue
        plan.append(MoveOp(str(src), str(dst),
                           'audio' if src.suffix.lower() != '.lrc' else 'lrc',
                           "还原"))
        alive[new_path] = old_path
    # 写回活条目（死条目清除；全空则删 manifest）
    manifest = Path(clear_dir) / RESTORE_MANIFEST
    try:
        if alive:
            manifest.write_text(json.dumps(alive, ensure_ascii=False,
                                           indent=1), encoding='utf-8')
        elif manifest.is_file():
            manifest.unlink()
    except OSError:
        pass
    return plan, skipped


def prune_restore_map(clear_dir: str, restored_new_paths: list) -> None:
    """还原成功后从 manifest 移除对应条目；全空则删 manifest"""
    import json
    mapping = load_restore_map(clear_dir)
    for p in restored_new_paths:
        mapping.pop(p, None)
    manifest = Path(clear_dir) / RESTORE_MANIFEST
    try:
        if mapping:
            manifest.write_text(json.dumps(mapping, ensure_ascii=False,
                                           indent=1), encoding='utf-8')
        elif manifest.is_file():
            manifest.unlink()
    except OSError:
        pass
