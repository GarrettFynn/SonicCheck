#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重复音乐检测与清除计划（功能 C）

分组口径（三路并查，覆盖"相同音乐不同音质不同名字"）：
1. 标签分组：title+artist 都有标签的，按规范化标签合并；
   缺标签的退回"剥质量后缀的规范化文件名"——纯名字撞车必须内容签名
   复核通过才合并，且轨号/超短名（01、a）不参与名字合并
   （v1.1.1 误报加固：不同专辑的 01.flac 曾被误并成重复组；去重是
   移走文件的破坏性操作，宁漏勿错）
2. 内容签名合并：时长 ±0.3s 且 DR ±0.2 且立体声相关 ±0.01 且
   -60dB 截止 ±50Hz → 同一编码内容（转码/重采样指标几乎不变，
   能抓住"不同名字"的同内容文件）；时长 ≤0 或三指标全为兜底 0 值
   的退化结果不参与签名合并（否则一批短文件/坏文件会因"全 0 相等"
   并成假重复组）
3. 声纹合并（v1.4.0，可选）：调用方传 fingerprints（chromaprint
   raw 指纹，见 core/fingerprint.py）时启用第三路——跨改名/裁剪/
   live 变体识别；命中组在 DupGroup.fp_note 标注来源与相似度。
   不传 fingerprints 时行为与 v1.3.0 完全一致。

清除方式（已拍板 C1）：非最优文件移动到目标文件夹 `_待清除/` 子目录，
可恢复；同 stem 的 .lrc 一并移动；目标撞名自动加 (n) 序号。
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from core.fsutil import companion_lrc
from core.renamer import execute_ops, strip_quality_suffix
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
    fp_note: str = ""           # 声纹来源标注（如"声纹相似 93%"），空=无声纹路

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


def _is_generic_stem(norm_stem: str) -> bool:
    """轨号/超短文件名（01、track 里剥出的数字、单双字符）：
    同名撞车极常见且不代表同一首歌，禁止参与纯名字合并"""
    return len(norm_stem) <= 2 or norm_stem.isdigit()


def _signature_valid(item) -> bool:
    """指标退化防护：无时长或三个内容指标全为兜底 0 值（分析失败/
    超短文件的常见形态）的结果不可信，不参与签名合并"""
    d = item.detail
    if d['meta'].get('duration', 0) <= 0:
        return False
    return not (d['dr'] == 0 and d['correlation'] == 0
                and d['cutoffs']['-60dB'] == 0)


def _same_signature(a, b) -> bool:
    if not _signature_valid(a) or not _signature_valid(b):
        return False
    da, db = a.detail, b.detail
    if abs(da['meta'].get('duration', 0) - db['meta'].get('duration', 0)) \
            > DURATION_TOL:
        return False
    if abs(da['dr'] - db['dr']) > DR_TOL:
        return False
    if abs(da['correlation'] - db['correlation']) > CORR_TOL:
        return False
    return abs(da['cutoffs']['-60dB'] - db['cutoffs']['-60dB']) <= CUTOFF_TOL


def find_duplicate_groups(items: list, fingerprints: dict = None) -> list:
    """返回 DupGroup 列表（每组 ≥2 个，按评分降序，items[0] 为建议保留）。

    fingerprints: {filepath: chromaprint raw 字节串}（可选，V14-2 声纹
    第三路）；None 时行为与 v1.3.0 完全一致。声纹命中的组在 fp_note
    标注"声纹相似 N%"（组内声纹对的最低相似度，保守展示）。
    """
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
        key = _tag_key(i)
        if key.startswith('name:') and _is_generic_stem(key[5:]):
            continue  # 轨号/超短名不参与名字合并，只走路 2 签名
        by_key.setdefault(key, []).append(i)
    for key, group in by_key.items():
        for j in range(1, len(group)):
            if key.startswith('name:') \
                    and not _same_signature(group[0], group[j]):
                # 纯文件名撞车不足以判定重复（不同专辑的 01.flac、
                # 下载站通用命名），必须签名复核；tag: 组保持原口径
                continue
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

    # 路 3：声纹合并（可选，宁漏勿错——阈值口径在 fingerprint 模块）
    fp_pairs = {}   # (id_a, id_b) -> 相似度，供组标注
    if fingerprints:
        from core import fingerprint as _fp
        durations = {i.filepath: i.detail['meta'].get('duration', 0)
                     for i in done}
        by_path = {i.filepath: i for i in done}
        matches = _fp.fingerprint_matches(fingerprints, durations)
        for pa, others in matches.items():
            ia = by_path.get(pa)
            if ia is None:
                continue
            for pb, sim in others.items():
                ib = by_path.get(pb)
                if ib is None:
                    continue
                union(id(ia), id(ib))
                fp_pairs[(id(ia), id(ib))] = sim

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
        # 组内声纹标注：找组内成员间直接命中的声纹对（间接并入无直接
        # 对的不标，避免夸大）
        ids = {id(i) for i in members}
        sims = [s for (x, y), s in fp_pairs.items()
                if x in ids and y in ids]
        fp_note = ''
        if sims:
            fp_note = f"声纹相似 {min(sims) * 100:.0f}%"
        groups.append(DupGroup(label=label, items=members, fp_note=fp_note))
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
            lrc_old = companion_lrc(old)
            if lrc_old is not None:
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

    def _move(old: Path, new: Path) -> None:
        d = str(new.parent)
        if d not in moved_dirs:  # 目标目录只建一次
            os.makedirs(d, exist_ok=True)
            moved_dirs.add(d)
        os.rename(old, new)

    return execute_ops(plan, _move)


# ---------------- 去重还原（_待清除 → 移回原位） ----------------
def record_restore_map(clear_dir: str, moved: list) -> None:
    """把本次成功移动的 new→old 映射合并写入 manifest（供还原用）"""
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
