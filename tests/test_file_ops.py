#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件操作类 core 模块回归测试：renamer / tag_renamer / deduper / playlist

全部在临时目录构造真实文件做计划→执行→验证往返，不依赖音频内容。

运行: python tests/test_file_ops.py
覆盖:
1. renamer：质量标记加/剥/套娃防护、撞名跳过、lrc 同步、执行与安全复制
2. tag_renamer：标签改名计划、非法字符净化、音频跳过时 lrc 跟随跳过
3. deduper：标签/内容签名分组、清除→manifest→还原往返
4. playlist：匹配优先级与短歌名误配防护
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import deduper, playlist, renamer, tag_renamer
from models.result_item import STATUS_DONE, ResultItem

PASS = []


def check(name, cond, extra=""):
    PASS.append((name, bool(cond)))
    print(f"  {'✓' if cond else '✗'} {name} {extra}")


def make_item(folder: Path, name: str, score=80.0, is_fake=False,
              title="", artist="", duration=200.0, dr=9.0, corr=0.5,
              cutoff=21000.0) -> ResultItem:
    """在 folder 里建真实音频占位文件，并返回对应 ResultItem"""
    fp = folder / name
    fp.write_bytes(b'audio')
    return ResultItem(
        filename=name, stem=fp.stem, filepath=str(fp),
        score=score, is_fake=is_fake, status=STATUS_DONE,
        detail={
            'filepath': str(fp),
            'meta': {'filename': name, 'title': title, 'artist': artist,
                     'duration': duration, 'format': 'FLAC',
                     'codec': 'flac', 'sample_rate': 44100,
                     'bit_depth': 16, 'bitrate': 900000},
            'cutoffs': {'-40dB': cutoff, '-60dB': cutoff,
                        '-80dB': cutoff},
            'dr': dr, 'correlation': corr,
            'fake_lossless': is_fake, 'fake_reasons': [],
            'score': score,
        })


def main():
    tmp = Path(tempfile.mkdtemp(prefix='fileops_test_'))

    # ══ 1. renamer ══
    print("1. renamer：质量标记计划与执行")
    d1 = tmp / 'r1'
    d1.mkdir()
    a = make_item(d1, '晴天 - 周杰伦.flac', is_fake=False)
    b = make_item(d1, '搁浅 - 周杰伦.flac', is_fake=True)
    (d1 / '晴天 - 周杰伦.lrc').write_text('lyric', encoding='utf-8')

    plan, skipped = renamer.build_rename_plan([a, b])
    tgts = {Path(op.new_path).name for op in plan}
    check('真无损加 _真无损', '晴天 - 周杰伦_真无损.flac' in tgts)
    check('假无损加 _假无损', '搁浅 - 周杰伦_假无损.flac' in tgts)
    check('lrc 同步进计划', '晴天 - 周杰伦_真无损.lrc' in tgts)

    # 套娃防护：已带标记的 stem 先剥再加，不叠加
    c = make_item(d1, '安静_假无损_真无损.flac', is_fake=True)
    plan2, _ = renamer.build_rename_plan([c])
    names = [Path(op.new_path).name for op in plan2]
    check('套娃剥净后重加', names == ['安静_假无损.flac'], str(names))

    # 无变化 → 无操作跳过
    plan3, skipped3 = renamer.build_rename_plan([c, c])
    check('重复条目只出一个计划', len(plan3) == 1)

    results = renamer.execute_plan(plan)
    check('执行全部成功', all(ok for _, ok, _ in results))
    check('文件已改名', (d1 / '晴天 - 周杰伦_真无损.flac').is_file())
    check('lrc 已跟随', (d1 / '晴天 - 周杰伦_真无损.lrc').is_file())

    # 安全复制：不覆盖已存在目标（用新文件，前面的执行已把旧文件改名）
    b2 = make_item(d1, '园游会 - 周杰伦.flac', is_fake=False)
    cp = renamer.RenameOp(b2.filepath,
                          str(d1 / '园游会 - 周杰伦_真无损.flac'), 'audio', 'x')
    r_cp = renamer.execute_copy_plan([cp])
    check('安全复制成功', r_cp[0][1] and
          (d1 / '园游会 - 周杰伦_真无损.flac').is_file())
    r_cp2 = renamer.execute_copy_plan([cp])
    check('安全复制不覆盖', not r_cp2[0][1])
    check('原件仍在', (d1 / '园游会 - 周杰伦.flac').is_file())

    # ══ 2. tag_renamer ══
    print("2. tag_renamer：标签改名")
    d2 = tmp / 'r2'
    d2.mkdir()
    t1 = make_item(d2, 'track01.flac', title='晴天', artist='周杰伦')
    t2 = make_item(d2, 'track02.flac', title='', artist='周杰伦')  # 缺标签
    t3 = make_item(d2, 'track03.flac', title='A/B:C', artist='D"E')
    (d2 / 'track01.lrc').write_text('x', encoding='utf-8')
    (d2 / 'track02.lrc').write_text('x', encoding='utf-8')

    plan_t, skipped_t = tag_renamer.build_tag_rename_plan([t1, t2, t3])
    names_t = {Path(op.new_path).name for op in plan_t}
    check('默认 歌名-歌手', '晴天-周杰伦.flac' in names_t)
    check('缺标签跳过', any('缺少歌名/歌手标签' in sk.why
                            for sk in skipped_t))
    check('非法字符净化', 'A B C-D E.flac' in names_t, str(names_t))
    check('lrc 跟随音频', '晴天-周杰伦.lrc' in names_t)
    check('缺标签的 lrc 不动', 'track02.lrc' not in names_t)

    plan_t2, _ = tag_renamer.build_tag_rename_plan(
        [t1], tag_renamer.FMT_ARTIST_TITLE)
    check('歌手-歌名格式',
          Path(plan_t2[0].new_path).name == '周杰伦-晴天.flac')

    check('sanitize 折叠空白', tag_renamer.sanitize_filename('a  /\\ b.')
          == 'a b')

    # ══ 3. deduper ══
    print("3. deduper：分组 → 清除 → 还原")
    d3 = tmp / 'r3'
    d3.mkdir()
    keep = make_item(d3, ' song.flac ', score=95.0, title='Song',
                     artist='Singer')
    dup1 = make_item(d3, 'song_假无损.flac', score=60.0, title='Song',
                     artist='Singer')
    dup2 = make_item(d3, '完全不同的名字.flac', score=50.0, title='',
                     artist='')  # 无标签，靠内容签名并入
    other = make_item(d3, '另一首.flac', score=70.0, title='Other',
                      artist='', duration=100.0, dr=5.0)
    (d3 / 'song_假无损.lrc').write_text('x', encoding='utf-8')

    groups = deduper.find_duplicate_groups([keep, dup1, dup2, other])
    check('三首同内容并为一组',
          len(groups) == 1 and len(groups[0].items) == 3,
          f'{len(groups)} 组')
    check('最高分是建议保留', groups and groups[0].keep is keep)
    check('不同内容不并组', other not in (groups[0].items if groups else []))

    plan_d, _ = deduper.build_clear_plan(groups, str(d3))
    clear_names = {Path(op.new_path).name for op in plan_d}
    check('清除计划含两首+lrc',
          {'song_假无损.flac', '完全不同的名字.flac',
           'song_假无损.lrc'} <= clear_names, str(clear_names))

    res_d = deduper.execute_move_plan(plan_d)
    check('移动全部成功', all(ok for _, ok, _ in res_d))
    moved = [(op.new_path, op.old_path) for op, ok, _ in res_d if ok]
    deduper.record_restore_map(str(d3 / deduper.CLEAR_DIR_NAME), moved)
    check('清除目录有文件',
          (d3 / deduper.CLEAR_DIR_NAME / 'song_假无损.flac').is_file())
    check('manifest 已写入',
          bool(deduper.load_restore_map(str(d3 / deduper.CLEAR_DIR_NAME))))

    plan_r, skipped_r = deduper.build_restore_plan(
        str(d3 / deduper.CLEAR_DIR_NAME))
    res_r = deduper.execute_move_plan(plan_r)
    check('还原全部成功', all(ok for _, ok, _ in res_r),
          str([e for _, ok, e in res_r if not ok]))
    deduper.prune_restore_map(
        str(d3 / deduper.CLEAR_DIR_NAME),
        [op.old_path for op, ok, _ in res_r if ok])
    check('文件已回原地', (d3 / 'song_假无损.flac').is_file())
    check('还原后 manifest 清空',
          not deduper.load_restore_map(str(d3 / deduper.CLEAR_DIR_NAME)))

    # ══ 4. playlist 匹配 ══
    print("4. playlist：匹配优先级与误配防护")
    d4 = tmp / 'r4'
    d4.mkdir()
    m1 = make_item(d4, '随便存的文件名.flac', title='晴天', artist='周杰伦')
    m2 = make_item(d4, '海阔天空-Beyond_真无损.flac')
    m3 = make_item(d4, '笑忘书-王菲.flac')

    entries = [playlist.PlaylistEntry('晴天', '周杰伦'),
               playlist.PlaylistEntry('海阔天空', 'Beyond'),
               playlist.PlaylistEntry('笑', ''),          # 单字：禁用模糊匹配
               playlist.PlaylistEntry('不存在的歌', '无名')]
    mr = playlist.match_entries([m1, m2, m3], entries)
    got = {e.title: i for e, i in mr.matched}
    check('标签精确匹配', got.get('晴天') is m1)
    check('文件名模糊匹配', got.get('海阔天空') is m2)
    check('单字歌名不误配', '笑' not in got)
    check('未匹配进清单', {e.title for e in mr.unmatched}
          == {'笑', '不存在的歌'})

    failed = [n for n, ok in PASS if not ok]
    print(f"\n结果: {len(PASS) - len(failed)}/{len(PASS)} 通过")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


def test_dedup_false_positive_guards():
    """v1.1.1 去重误报加固回归：纯名字撞车与退化指标不得产生重复组

    场景来源：不同专辑的 01.flac（轨号命名、无标签）曾被纯名字分组合并，
    用户批量确认后会移走不同歌曲——去重是破坏性操作，必须宁漏勿错。
    """
    print("5. deduper：纯文件名/退化指标误报防护")
    d5a, d5b = Path(tempfile.mkdtemp(prefix='dedup_a_')), \
        Path(tempfile.mkdtemp(prefix='dedup_b_'))

    # 轨号同名、无标签、内容签名不同（不同专辑的 01.flac）→ 不得合并
    t1 = make_item(d5a, '01.flac', title='', artist='',
                   duration=180.0, dr=12.0, corr=0.9, cutoff=22000.0)
    t2 = make_item(d5b, '01.flac', title='', artist='',
                   duration=241.0, dr=6.0, corr=0.3, cutoff=16000.0)
    check('轨号同名不同内容不误报',
          not deduper.find_duplicate_groups([t1, t2]))

    # 正向对照：轨号同名且签名一致 → 仍合并（真重复不受加固影响）
    t3 = make_item(d5b, '01 (1).flac', title='', artist='',
                   duration=180.0, dr=12.0, corr=0.9, cutoff=22000.0)
    check('轨号同名同内容仍合并',
          len(deduper.find_duplicate_groups([t1, t3])) == 1)

    # 非通用名撞名但签名不同 → 不合并（路 1 签名复核）
    n1 = make_item(d5a, '晴天.flac', title='', artist='',
                   duration=200.0, dr=9.0, corr=0.5, cutoff=21000.0)
    n2 = make_item(d5b, '晴天.flac', title='', artist='',
                   duration=260.0, dr=7.0, corr=0.4, cutoff=15000.0)
    check('同名不同内容不误报',
          not deduper.find_duplicate_groups([n1, n2]))

    # 退化指标（时长 0、三指标全 0）同名 → 不合并（兜底值不"相等"）
    z1 = make_item(d5a, '坏文件.flac', title='', artist='',
                   duration=0.0, dr=0.0, corr=0.0, cutoff=0.0)
    z2 = make_item(d5b, '坏文件.flac', title='', artist='',
                   duration=0.0, dr=0.0, corr=0.0, cutoff=0.0)
    check('退化指标不互相合并',
          not deduper.find_duplicate_groups([z1, z2]))

    failed = [n for n, ok in PASS if not ok]
    print(f"  结果: {len(PASS)} 项中失败 {len(failed)} 项")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


def test_playlist_parsing_guards():
    """v1.2.0 歌单解析三修回归：编号剥离 / GBK 回退 / 复制同名消歧"""
    print("6. playlist：解析编号剥离 / GBK 回退 / 复制同名消歧")
    pl = playlist

    # 「01 - 晴天 - 周杰伦」——歌单最常见格式之一，旧版解析成 title=01
    e = pl._parse_line('01 - 晴天 - 周杰伦')
    check('编号+短横剥离', (e.title, e.artist) == ('晴天', '周杰伦'),
          f'{e.title}|{e.artist}')
    e2 = pl._parse_line('12 - 海阔天空')
    check('编号+短横（无歌手）', e2.title == '海阔天空', e2.title)
    e3 = pl._parse_line('01 晴天 - 周杰伦')   # 编号后无短横
    check('裸编号兜底剥离', (e3.title, e3.artist) == ('晴天', '周杰伦'),
          f'{e3.title}|{e3.artist}')
    e4 = pl._parse_line('1998 - 陈奕迅')        # 4 位数字=歌名，不剥
    check('四位数字不误剥', e4.title == '1998', e4.title)
    e5 = pl._parse_line('晴天 - 周杰伦')        # 无编号行为不变
    check('无编号不受影响', (e5.title, e5.artist) == ('晴天', '周杰伦'))

    # GBK 歌单回退：旧版 utf-8+replace 整单乱码
    gbk_file = Path(tempfile.mkdtemp(prefix='gbk_')) / '歌单.txt'
    gbk_file.write_bytes('晴天 - 周杰伦'.encode('gb18030'))
    entries, _name = pl.parse_playlist_file(str(gbk_file))
    check('GBK 歌单可读', bool(entries) and entries[0].title == '晴天',
          str(entries[:1]))

    # 复制消歧：跨子目录同名不同大小 → (n) 序号而非静默丢歌
    d6 = Path(tempfile.mkdtemp(prefix='copy_'))
    (d6 / 'a').mkdir()
    (d6 / 'b').mkdir()
    items = [make_item(d6 / 'a', '晴天.flac'),
             make_item(d6 / 'b', '晴天.flac')]
    # make_item 会写同内容占位，重新写入不同大小以区分两首同名歌
    (d6 / 'a' / '晴天.flac').write_bytes(b'x' * 100)
    (d6 / 'b' / '晴天.flac').write_bytes(b'y' * 200)
    mr = pl.MatchResult()
    mr.matched = [(pl.PlaylistEntry('晴天', ''), items[0]),
                  (pl.PlaylistEntry('晴天', ''), items[1])]
    copied, failed, dest = pl.copy_matched(mr.matched, str(d6), '测试歌单')
    out_names = sorted(Path(c[1]).name for c in copied)
    check('同名两首都复制', out_names == ['晴天 (1).flac', '晴天.flac'],
          str(out_names))
    check('复制无失败', not failed, str(failed))
    # 幂等：重跑不产生副本
    copied2, _, _ = pl.copy_matched(mr.matched, str(d6), '测试歌单')
    check('重跑幂等', len(copied2) == 2
          and sorted(Path(c[1]).name for c in copied2)
          == ['晴天 (1).flac', '晴天.flac'])

    failed = [n for n, ok in PASS if not ok]
    print(f"  结果: {len(PASS)} 项中失败 {len(failed)} 项")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


def test_filename_safety():
    """v1.2.0 文件名安全回归：Windows 保留名 / 超长截断"""
    print("7. tag_renamer：保留名与超长截断")
    sf = tag_renamer.sanitize_filename
    check('保留名 CON', sf('CON') == 'CON_', sf('CON'))
    check('保留名小写 com1', sf('com1') == 'com1_', sf('com1'))
    check('保留名带点 NUL.txt', sf('NUL.txt') == 'NUL.txt_',
          sf('NUL.txt'))
    check('普通名不受影响', sf('晴天 - 周杰伦') == '晴天 - 周杰伦')
    long_name = '超长歌名' * 60   # 240 字符
    got = sf(long_name)
    check('超长截断到 120', 0 < len(got) <= 120, str(len(got)))
    check('截断后仍可用', bool(got.strip()))

    failed = [n for n, ok in PASS if not ok]
    print(f"  结果: {len(PASS)} 项中失败 {len(failed)} 项")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


if __name__ == '__main__':
    main()
    test_dedup_false_positive_guards()
    test_playlist_parsing_guards()
    test_filename_safety()
