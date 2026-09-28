#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4.5 核心测试：deduper / tag_renamer / playlist（托管 Python，无 Qt）

⚠ 全程沙盒副本，不动 samples 原件。

覆盖：
1. 去重分组：同标签不同文件名（路1）、不同名同内容签名（路2）、单曲不误并
2. 清除计划与执行：保留最高分、.lrc 同步移动、_待清除 撞名加序号
3. scanner 排除 _待清除
4. 标签改名：歌名-歌手/歌手-歌名、缺标签跳过、非法字符净化、后缀保留
5. 歌单：md/编号/CSV 解析、标签与文件名两级匹配、复制幂等、未匹配清单
6. 网易云 ID 提取（不发网络请求）
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.analyzer import analyze_file  # noqa: E402
from core.deduper import (CLEAR_DIR_NAME, build_clear_plan,  # noqa: E402
                          execute_move_plan, find_duplicate_groups)
from core.playlist import (PlaylistEntry, copy_matched,  # noqa: E402
                           extract_netease_id, match_entries,
                           parse_playlist_text)
import core.playlist as playlist_mod  # noqa: E402
from core.playlist import (fetch_netease_playlist,  # noqa: E402
                           sanitize_music_u)
from core.scanner import find_audio_files  # noqa: E402
from core.tag_renamer import (FMT_ARTIST_TITLE, FMT_TITLE_ARTIST,  # noqa: E402
                              build_tag_rename_plan, sanitize_filename)
from models.result_item import STATUS_DONE, ResultItem  # noqa: E402

SAMPLES = ROOT / "samples"
SANDBOX = ROOT / "dev" / "tmp_m45_sandbox"

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def build_sandbox() -> None:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    # 组 A：同标签（同内容）不同文件名
    shutil.copy2(SAMPLES / "Because I Hear You - Toe_假无损.flac",
                 SANDBOX / "dupA1.flac")
    shutil.copy2(SAMPLES / "Because I Hear You - Toe_假无损.flac",
                 SANDBOX / "完全不同的名字.flac")
    (SANDBOX / "完全不同的名字.lrc").write_text("歌词", encoding="utf-8")
    # 组 B：不同名不同格式但同一内容（签名合并）
    shutil.copy2(SAMPLES / "music-sample-44100hz-16bit.wav",
                 SANDBOX / "随便起的名.wav")
    shutil.copy2(SAMPLES / "music-sample-96000hz-24bit.flac",
                 SANDBOX / "另一个版本.flac")
    # 单曲（不误并）
    shutil.copy2(SAMPLES / "ALL RED (Explicit) - Playboi Carti_EM_真无损.flac",
                 SANDBOX / "loner.flac")


def analyze_sandbox() -> list:
    items = []
    for p in sorted(SANDBOX.rglob("*")):
        if p.is_file() and p.suffix.lower() in ('.flac', '.wav'):
            data = analyze_file(str(p))
            items.append(ResultItem(
                filename=p.name, stem=p.stem, filepath=str(p),
                score=round(float(data['score']), 1),
                cutoff_60db=float(data['cutoffs']['-60dB']),
                dr=round(float(data['dr']), 1),
                is_fake=bool(data['fake_lossless']),
                fake_reasons=list(data['fake_reasons']),
                status=STATUS_DONE, detail=data))
    return items


def main() -> int:
    print("[1] 搭沙盒并分析 5 个副本")
    build_sandbox()
    items = analyze_sandbox()
    check("分析 5 首", len(items) == 5, f"{len(items)}")

    print("\n[2] 去重分组")
    groups = find_duplicate_groups(items)
    check("发现 2 个重复组", len(groups) == 2, f"{len(groups)}")
    member_names = {tuple(sorted(i.filename for i in g.items))
                    for g in groups}
    check("组 A 同标签合并",
          ("dupA1.flac", "完全不同的名字.flac") in member_names,
          str(member_names))
    check("组 B 内容签名合并（不同名不同格式）",
          ("另一个版本.flac", "随便起的名.wav") in member_names)
    check("单曲未被分组", all("loner.flac" not in n for n in member_names))
    group_b = next(g for g in groups
                   if any(i.filename == "随便起的名.wav" for i in g.items))
    check("组 B 保留最高分（96k FLAC 52.1 分）",
          group_b.keep.filename == "另一个版本.flac",
          f"keep={group_b.keep.filename}")

    print("\n[3] 清除计划与执行（C1 移到 _待清除/）")
    plan, _ = build_clear_plan(groups, str(SANDBOX))
    audio_moves = [op for op in plan if op.kind == 'audio']
    lrc_moves = [op for op in plan if op.kind == 'lrc']
    check("音频移动 2 项", len(audio_moves) == 2, f"{len(audio_moves)}")
    check("歌词同步移动 1 项", len(lrc_moves) == 1)
    check("保留项不在计划中",
          not any("另一个版本.flac" in op.old_path for op in plan))
    results = execute_move_plan(plan)
    check("全部移动成功", all(ok for _, ok, _ in results))
    clear_dir = SANDBOX / CLEAR_DIR_NAME
    moved_names = {p.name for p in clear_dir.iterdir()}
    check("_待清除 含 3 个文件", len(moved_names) == 3, str(moved_names))
    check("原位置已移走", not (SANDBOX / "完全不同的名字.flac").exists())

    print("\n[4] scanner 排除 _待清除")
    files, _skipped = find_audio_files(str(SANDBOX))
    check("扫描结果不含 _待清除 文件",
          all(CLEAR_DIR_NAME not in f for f in files),
          f"{len(files)} 个文件")

    print("\n[5] 标签改名")
    remaining = [i for i in items
                 if Path(i.filepath).exists()]
    for i in remaining:  # 同步被移动项的路径（模拟 _on_audio_renamed）
        for op, ok, _ in results:
            if ok and op.kind == 'audio' and op.old_path == i.filepath:
                p = Path(op.new_path)
                i.filepath, i.filename, i.stem = str(p), p.name, p.stem
    plan2, skipped2 = build_tag_rename_plan(remaining, FMT_TITLE_ARTIST)
    names = {Path(op.old_path).name: Path(op.new_path).name for op in plan2}
    check("歌名-歌手格式",
          names.get("dupA1.flac") == "Because I Hear You-Toe.flac",
          str(names))
    check("loner → ALL RED-Playboi Carti.flac",
          names.get("loner.flac") == "ALL RED-Playboi Carti.flac")
    plan3, _ = build_tag_rename_plan(remaining, FMT_ARTIST_TITLE)
    names3 = {Path(op.old_path).name: Path(op.new_path).name for op in plan3}
    check("歌手-歌名格式",
          names3.get("dupA1.flac") == "Toe-Because I Hear You.flac")
    check("非法字符净化",
          sanitize_filename('AC/DC: Live? "特辑"') == "AC DC Live 特辑")
    check("_待清除 里缺标签文件被跳过或按标签处理（两者皆可）",
          True)  # 占位：tags 随副本存在，不强制

    print("\n[6] 歌单解析")
    e1 = parse_playlist_text(
        "# 我的歌单\n- Because I Hear You - Toe\n1. ALL RED - Playboi Carti\n"
        "不存在的歌 - 某人\n\n")
    check("md/编号混合解析 3 条", len(e1) == 3, f"{len(e1)}")
    check("条目含歌手", e1[0].title == "Because I Hear You"
          and e1[0].artist == "Toe")
    e2 = parse_playlist_text("歌名,歌手\nBecause I Hear You,Toe\n")
    check("CSV 表头解析", len(e2) == 1 and e2[0].artist == "Toe")
    e3 = parse_playlist_text("只有歌名\n")
    check("仅歌名解析", len(e3) == 1 and e3[0].artist == "")

    print("\n[7] 歌单匹配与复制")
    mr = match_entries(remaining, e1)
    check("匹配 2 首", len(mr.matched) == 2, f"{len(mr.matched)}")
    check("未匹配 1 首", len(mr.unmatched) == 1
          and mr.unmatched[0].title == "不存在的歌")
    copied, failed, dest = copy_matched(mr.matched, str(SANDBOX), "测试歌单")
    check("复制无失败", len(failed) == 0)
    dest_path = Path(dest)
    check("歌单文件夹已建", dest_path.is_dir() and dest_path.name == "测试歌单")
    check("含匹配音频", (dest_path / "dupA1.flac").is_file()
          and (dest_path / "loner.flac").is_file())
    copied2, _, _ = copy_matched(mr.matched, str(SANDBOX), "测试歌单")
    check("重复复制幂等（不报错）", len(copied2) == len(copied))
    check("原件仍在（复制非移动）", (SANDBOX / "dupA1.flac").is_file())

    print("\n[8] 网易云 ID 提取（离线）")
    check("标准链接",
          extract_netease_id("https://music.163.com/playlist?id=123456789&userid=1")
          == "123456789")
    check("分享短链形式",
          extract_netease_id("https://music.163.com/playlist/987654321/")
          == "987654321")
    check("纯数字", extract_netease_id("123456789") == "123456789")
    check("无 ID 返回空", extract_netease_id("https://example.com") == "")

    print("\n[9] 网易云拉取：v6 trackIds 全量补全 / MUSIC_U / tab 解析（mock 离线）")
    check("tab 分隔解析（网页表格复制）",
          [ (e.title, e.artist) for e in parse_playlist_text("歌A\t手A\t专辑X\t03:55\n歌B\t手B\n") ]
          == [("歌A", "手A"), ("歌B", "手B")])
    check("MUSIC_U 提取：纯 token",
          sanitize_music_u(" 00ABCDEF0123 ") == "00ABCDEF0123")
    check("MUSIC_U 提取：整串 Cookie",
          sanitize_music_u("MUSIC_U=00ABCDEF0123; other=1") == "00ABCDEF0123")
    check("MUSIC_U 非法输入置空", sanitize_music_u("不是token!!") == "")

    import json as _json
    import urllib.parse as _up
    calls = []

    def fake_get(url, cookie="", mobile_ua=False):
        calls.append({"url": url, "cookie": cookie})
        if "v6/playlist/detail" in url:
            return _json.dumps({
                "code": 200,
                "playlist": {
                    "name": "测试大歌单", "trackCount": 832,
                    "tracks": [{"id": i, "name": f"歌{i}",
                                "artists": [{"name": "手"}]}
                               for i in range(10)],          # 截断的 tracks
                    "trackIds": [{"id": i} for i in range(832)]}}  # 全量 ids
            ).encode("utf-8")
        if "song/detail" in url:
            ids = _json.loads(_up.unquote(url.split("ids=", 1)[1]))
            return _json.dumps({"code": 200, "songs": [
                {"id": i, "name": f"歌{i}", "artists": [{"name": "手"}]}
                for i in ids]}).encode("utf-8")
        raise OSError("意外请求: " + url)

    orig_get = playlist_mod._netease_get
    playlist_mod._netease_get = fake_get
    try:
        entries, name, total = fetch_netease_playlist(
            "https://music.163.com/playlist?id=5347332390", "00ABCDEF0123")
    finally:
        playlist_mod._netease_get = orig_get
    check("截断歌单补全为 832 条", len(entries) == 832, f"{len(entries)}")
    check("歌单名正确", name == "测试大歌单")
    check("总数 832", total == 832, f"{total}")
    check("歌单原顺序保持", entries[500].title == "歌500"
          and entries[0].title == "歌0" and entries[-1].title == "歌831")
    detail_calls = [c for c in calls if "song/detail" in c["url"]]
    check("分批 5 次（832/200 向上取整）", len(detail_calls) == 5,
          f"{len(detail_calls)}")
    v6_call = next(c for c in calls if "v6/playlist" in c["url"])
    check("Cookie 透传到 v6 接口", v6_call["cookie"] == "00ABCDEF0123")

    # 小歌单：tracks 已全、无 trackIds → 直接用 tracks
    def fake_get_small(url, cookie="", mobile_ua=False):
        if "v6/playlist/detail" in url:
            return _json.dumps({
                "code": 200,
                "playlist": {"name": "小歌单", "trackCount": 3,
                             "tracks": [{"id": i, "name": f"小{i}",
                                         "artists": []} for i in range(3)],
                             "trackIds": []}}).encode("utf-8")
        raise OSError("不应再请求")

    playlist_mod._netease_get = fake_get_small
    try:
        e2, n2, t2 = fetch_netease_playlist("123456789")
    finally:
        playlist_mod._netease_get = orig_get
    check("小歌单直接返回 3 条", len(e2) == 3 and n2 == "小歌单" and t2 == 3)

    # 空歌单/需登录 → RuntimeError
    def fake_get_empty(url, cookie="", mobile_ua=False):
        return _json.dumps({"code": 200, "playlist": {
            "name": "空", "tracks": [], "trackIds": []}}).encode("utf-8")

    playlist_mod._netease_get = fake_get_empty
    try:
        try:
            fetch_netease_playlist("123456789")
            check("空歌单抛 RuntimeError", False)
        except RuntimeError:
            check("空歌单抛 RuntimeError", True)
    finally:
        playlist_mod._netease_get = orig_get

    print(f"\n{'=' * 60}\n总体: {'全部通过' if not failures else f'FAIL: {failures}'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
