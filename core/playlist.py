#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""歌单导入：解析歌单 → 匹配曲库 → 复制到歌单命名的新文件夹（功能 E）

- 输入：md / csv / txt 歌单文本（每行一首，支持 `歌名 - 歌手`、
  Markdown 列表、编号列表、带表头 CSV），或网易云歌单链接（功能 F 本期范围）
- 匹配：标签 title 精确 > 文件名包含歌名；规范化=去空白+小写
- 执行：**复制**（不移动）音频与同 stem .lrc 到目标文件夹下以歌单名
  命名的新目录；未匹配条目生成报告
"""

import csv
import io
import json
import re
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from core.renamer import strip_quality_suffix
from core.tag_renamer import sanitize_filename
from models.result_item import STATUS_DONE

NETEASE_API_V6 = "https://music.163.com/api/v6/playlist/detail?id={}&n=100000"
SONG_DETAIL_API = "https://music.163.com/api/song/detail?ids={}"
FETCH_TIMEOUT = 15
SONG_BATCH = 200  # /api/song/detail 单次上限约 200 个 id（实测 500 只返 201）


def sanitize_music_u(raw: str) -> str:
    """从用户输入中提取 MUSIC_U 值（接受纯 token 或 'MUSIC_U=xxx; …'）"""
    raw = (raw or '').strip().strip('"\'')
    m = re.search(r'MUSIC_U=([0-9A-Fa-f]+)', raw)
    if m:
        return m.group(1)
    return raw if re.fullmatch(r'[0-9A-Fa-f]+', raw) else ""


@dataclass
class PlaylistEntry:
    title: str
    artist: str = ""


@dataclass
class MatchResult:
    matched: list = field(default_factory=list)    # [(entry, item)]
    unmatched: list = field(default_factory=list)  # [entry]


# ---------------- 解析 ----------------
_LINE_PREFIX = re.compile(
    r'^\s*(?:[-*+>]\s+|#{1,6}\s*|\d+[.、)]\s*|`+)')
_SPLIT = re.compile(r'\s+[-–—]\s+')


def _norm(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def _parse_line(line: str) -> PlaylistEntry:
    line = _LINE_PREFIX.sub('', line).strip().strip('`').strip()
    if not line:
        return PlaylistEntry("")
    if '\t' in line:  # 网页表格复制：歌名⇥歌手⇥…（取前两列）
        cols = [c.strip() for c in line.split('\t') if c.strip()]
        if cols:
            return PlaylistEntry(cols[0], cols[1] if len(cols) > 1 else "")
    parts = _SPLIT.split(line, maxsplit=1)
    if len(parts) == 2:
        return PlaylistEntry(parts[0].strip(), parts[1].strip())
    return PlaylistEntry(line)


def parse_playlist_text(text: str) -> list:
    """从文本解析歌单条目（自动识别 CSV 表头）"""
    lines = [ln for ln in text.replace('\r\n', '\n').split('\n')]
    entries: list = []

    # CSV 探测：首行含逗号且含表头关键词
    header_keys = {'歌名', '歌曲', '曲名', 'title', 'name', 'song'}
    first = next((ln for ln in lines if ln.strip()), '')
    if ',' in first and any(k in first.lower() for k in header_keys):
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if rows:
            header = [h.strip().lower() for h in rows[0]]
            try:
                t_idx = next(i for i, h in enumerate(header)
                             if h in header_keys)
            except StopIteration:
                t_idx = 0
            a_idx = next((i for i, h in enumerate(header)
                          if h in ('歌手', 'artist', 'artists')), None)
            for row in rows[1:]:
                if len(row) > t_idx and row[t_idx].strip():
                    entries.append(PlaylistEntry(
                        row[t_idx].strip(),
                        row[a_idx].strip() if a_idx is not None
                        and len(row) > a_idx else ""))
            return entries

    for ln in lines:
        if ln.strip().startswith('#'):  # Markdown 标题行不是歌曲条目
            continue
        e = _parse_line(ln)
        if e.title:
            entries.append(e)
    return entries


def parse_playlist_file(path: str) -> tuple:
    """读取 md/csv/txt 歌单文件，返回 (entries, 建议歌单名=文件名 stem)"""
    p = Path(path)
    text = p.read_text(encoding='utf-8-sig', errors='replace')
    return parse_playlist_text(text), p.stem


# ---------------- 网易云链接（功能 F 本期范围） ----------------
def extract_netease_id(text: str) -> str:
    """从链接或纯数字中提取歌单 ID"""
    m = re.search(r'playlist\?.*?id=(\d+)', text) or \
        re.search(r'playlist/(\d+)', text) or \
        re.search(r'\b(\d{5,})\b', text.strip())
    return m.group(1) if m else ""


def _netease_get(url: str, cookie: str = "") -> bytes:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
               'Referer': 'https://music.163.com/'}
    if cookie:
        headers['Cookie'] = f'MUSIC_U={cookie}'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read()


def _track_entry(t: dict) -> PlaylistEntry:
    artists = t.get('artists') or t.get('ar') or []
    return PlaylistEntry(
        t.get('name', ''),
        '/'.join(a.get('name', '') for a in artists))


def _fetch_song_details(ids: list, cookie: str = "") -> dict:
    """按 id 分批取歌曲详情，返回 {id: track}（SONG_BATCH 个/次）"""
    out: dict = {}
    for i in range(0, len(ids), SONG_BATCH):
        batch = ids[i:i + SONG_BATCH]
        qs = urllib.parse.quote(json.dumps(batch))
        raw = _netease_get(SONG_DETAIL_API.format(qs), cookie=cookie)
        data = json.loads(raw.decode('utf-8', errors='replace'))
        for s in data.get('songs') or []:
            if s.get('id') is not None:
                out[s['id']] = s
    return out


def fetch_netease_playlist(url_or_id: str, music_u: str = "") -> tuple:
    """拉取网易云公开歌单，返回 (entries, 歌单名, 歌单真实总数或 None)。

    全量策略（零登录）：v6 详情接口匿名即返回**完整 trackIds**，tracks 虽
    被限流截断（约前 10 首），但可用 trackIds 分批调歌曲详情接口补全，
    并保持歌单原顺序。仅当 trackIds 也缺失时才退化为截断警告/手动粘贴。
    MUSIC_U 仍可选（私密歌单等场景）。失败抛 RuntimeError。
    """
    pid = extract_netease_id(url_or_id)
    if not pid:
        raise RuntimeError("无法从输入中识别网易云歌单 ID")
    cookie = sanitize_music_u(music_u)
    try:
        raw = _netease_get(NETEASE_API_V6.format(pid), cookie=cookie)
        data = json.loads(raw.decode('utf-8', errors='replace'))
    except Exception as exc:
        raise RuntimeError(f"网易云歌单拉取失败: {exc}") from exc
    result = data.get('playlist') or data.get('result') or {}
    tracks = result.get('tracks') or []
    track_ids = [t.get('id') for t in (result.get('trackIds') or [])
                 if t.get('id') is not None]
    name = result.get('name', f'网易云歌单_{pid}')
    if not tracks and not track_ids:
        raise RuntimeError("歌单为空或需要登录，请改用歌单文本手动粘贴")

    if track_ids and len(track_ids) > len(tracks):
        # 截断 → 用全量 trackIds 分批补全
        try:
            by_id = _fetch_song_details(track_ids, cookie)
        except Exception as exc:
            raise RuntimeError(f"歌单补全失败（已获取前 {len(tracks)} 首）: "
                               f"{exc}，可重试或改用文本粘贴") from exc
        for t in tracks:  # 截断 tracks 兜底（补全接口偶发漏歌时）
            tid = t.get('id')
            if tid is not None and tid not in by_id:
                by_id[tid] = t
        entries = [_track_entry(by_id[tid]) for tid in track_ids
                   if tid in by_id and by_id[tid].get('name')]
        return entries, name, len(track_ids)

    entries = [_track_entry(t) for t in tracks if t.get('name')]
    total = result.get('trackCount') or len(entries)
    return entries, name, total


# ---------------- 匹配 ----------------
def match_entries(items: list, entries: list) -> MatchResult:
    """歌单条目匹配扫描结果（done 行）"""
    done = [i for i in items if i.status == STATUS_DONE]
    # 预建索引：规范化标签歌名 / 规范化文件名（剥后缀）
    index = []
    for i in done:
        meta = i.detail.get('meta', {})
        index.append({
            'item': i,
            'tag_title': _norm(meta.get('title', '')),
            'tag_artist': _norm(meta.get('artist', '')),
            'stem': _norm(strip_quality_suffix(i.stem)),
        })

    result = MatchResult()
    used = set()
    for e in entries:
        nt, na = _norm(e.title), _norm(e.artist)
        if not nt:
            result.unmatched.append(e)
            continue
        best = None
        # 优先级 1：标签歌名精确（有歌手时要求歌手也匹配）
        for cand in index:
            if not cand['tag_title'] or id(cand['item']) in used:
                continue
            if cand['tag_title'] == nt and (not na or not cand['tag_artist']
                                            or na in cand['tag_artist']
                                            or cand['tag_artist'] in na):
                best = cand['item']
                break
        # 优先级 2：文件名包含歌名
        if best is None:
            for cand in index:
                if id(cand['item']) in used:
                    continue
                if nt in cand['stem'] or cand['stem'] in nt:
                    best = cand['item']
                    break
        if best is not None:
            used.add(id(best))
            result.matched.append((e, best))
        else:
            result.unmatched.append(e)
    return result


# ---------------- 复制 ----------------
def copy_matched(matched: list, target_root: str, playlist_name: str) -> tuple:
    """复制匹配文件到 <target_root>/<歌单名>/，返回 (copied, failed, dest_dir)

    copied/failed 为 [(src, dst)] / [(src, error)]；同 stem .lrc 一并复制；
    目标已存在则跳过并记为 copied（幂等，重跑不产生副本）。
    """
    folder = sanitize_filename(playlist_name) or "新歌单"
    dest_dir = Path(target_root) / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied, failed = [], []
    for _entry, item in matched:
        src = Path(item.filepath)
        candidates = [src]
        lrc = src.parent / (src.stem + ".lrc")
        if lrc.is_file():
            candidates.append(lrc)
        for f in candidates:
            dst = dest_dir / f.name
            try:
                if not dst.exists():
                    shutil.copy2(f, dst)
                copied.append((str(f), str(dst)))
            except OSError as exc:
                failed.append((str(f), str(exc)))
    return copied, failed, str(dest_dir)
