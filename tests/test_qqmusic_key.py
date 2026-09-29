#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/qqmusic_key.py 与 musicex 解锁链路测试

运行: python tests/test_qqmusic_key.py
覆盖:
1. musicex 尾部解析（文件版 + 内存版）
2. EkeyStore 双键索引 / 持久化 / 去重计数
3. fetch_ekey 请求构造与响应解析（mock urlopen，不联网）
4. ensure_ekeys 全流程（mock 内存提取与接口）
5. musicex 端到端解锁：密钥库命中 → unlock_file 成功；未命中 → 引导错误
6. extract_cookie_from_process 冒烟（无客户端时优雅失败，不抛异常）
"""

import json
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import unlock as U
from core import qqmusic_key as Q
from test_unlock import make_ekey, make_flac

import numpy as np

PASS = []


def check(name, cond, extra=""):
    cond = bool(cond)
    PASS.append((name, cond))
    print(f"  {'✓' if cond else '✗'} {name} {extra}")
    # assert 化（C4）：失败立即带栈中断而非跑完打印汇总，CI 可见出错点
    assert cond, f"{name} {extra}"


SONG_MID = 'TESTMID0001234'
CLI_FILENAME = 'F0M0TESTMID0001234.mflac'


def build_musicex_tail(song_mid: str, filename: str) -> bytes:
    """合成 musicex 尾块（≥184 字节）：[28:88] mid, [88:184] 文件名"""
    tail = bytearray(184)
    mid_raw = song_mid.encode('utf-16-le')
    tail[28:28 + len(mid_raw)] = mid_raw
    fn_raw = filename.encode('utf-16-le')
    tail[88:88 + len(fn_raw)] = fn_raw
    return bytes(tail)


def build_musicex_file(audio: bytes, tail: bytes) -> bytes:
    return audio + tail + struct.pack('<I', len(tail)) + b'\x00' * 4 \
        + Q.MUSICEX_MAGIC


def encrypt_with_map(audio: bytes, real_key: bytes) -> bytes:
    buf = np.frombuffer(audio, dtype=np.uint8).copy()
    U._QmcMapCipher(real_key).decrypt_into(buf, 0)
    return bytes(buf)


class _FakeResp:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main():
    tmp = Path(tempfile.mkdtemp(prefix='qqkey_test_'))
    os.chdir(tmp)
    store_file = tmp / 'ekeys.json'
    Q.STORE_PATH = store_file  # 全程隔离真实密钥库

    print("1. musicex 尾部解析")
    tail = build_musicex_tail(SONG_MID, CLI_FILENAME)
    audio = b'fLaC' + os.urandom(5000)
    data = build_musicex_file(audio, tail)
    Path('a.mflac').write_bytes(data)
    info = Q.parse_musicex_info('a.mflac')
    check('文件版 song_mid', info.get('song_mid') == SONG_MID)
    check('文件版 filename', info.get('filename') == CLI_FILENAME)
    check('文件版 audio_size', info.get('audio_size') == len(audio))
    info2 = Q.parse_musicex_info_from_bytes(data)
    check('内存版一致', info2 == info)
    check('非 musicex 返回空', Q.parse_musicex_info_from_bytes(b'x' * 600) == {})

    print("2. EkeyStore")
    store = Q.EkeyStore(store_file)
    store.put('EKEY_ABC', SONG_MID, CLI_FILENAME)
    store.save()
    store2 = Q.EkeyStore(store_file)  # 重新加载验证持久化
    check('按 mid 取', store2.get(SONG_MID) == 'EKEY_ABC')
    check('按文件名取', store2.get('', CLI_FILENAME) == 'EKEY_ABC')
    check('未命中返回空', store2.get('NOPE') == '')
    check('双键去重计数为 1', store2.count() == 1)
    store2.put('EKEY_X', 'OTHERMID', 'other.mgg')
    check('计数为 2', store2.count() == 2)
    store2.set_auth('cookie123', '456789')
    store2.save()
    check('登录态持久化', Q.EkeyStore(store_file).get_auth()
          == ('cookie123', '456789'))

    print("3. fetch_ekey（mock urlopen）")
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured['body'] = json.loads(req.data.decode())
        captured['cookie'] = req.headers.get('Cookie')
        captured['ua'] = req.headers.get('User-agent')
        return _FakeResp({'req_1': {'code': 0, 'data': {
            'midurlinfo': [{'ekey': 'EKEY_FROM_API'}]}}})

    orig_urlopen = Q.urllib.request.urlopen
    Q.urllib.request.urlopen = fake_urlopen
    try:
        ek = Q.fetch_ekey(SONG_MID, CLI_FILENAME, 'mycookie', '12345')
        check('返回 ekey', ek == 'EKEY_FROM_API')
        param = captured['body']['req_1']['param']
        check('songmid 正确', param['songmid'] == [SONG_MID])
        check('filename 正确',
              param['filename'] == ['F0M0TESTMID0001234.mflac'])
        check('cookie 头透传', captured['cookie'] == 'mycookie')

        def fake_urlopen_nokey(req, timeout=0):
            return _FakeResp({'req_1': {'code': -1, 'data': {}}})
        Q.urllib.request.urlopen = fake_urlopen_nokey
        try:
            Q.fetch_ekey(SONG_MID, CLI_FILENAME, 'c', '1')
            check('无密钥抛 KeyFetchError', False)
        except Q.KeyFetchError as e:
            check('无密钥抛 KeyFetchError', '未返回密钥' in str(e))
    finally:
        Q.urllib.request.urlopen = orig_urlopen

    print("4. ensure_ekeys 全流程（mock 提取与接口）")
    Path('b.mflac').write_bytes(data)  # 第二个 musicex 文件（同 mid）
    orig_extract = Q.extract_cookie_from_process
    orig_fetch = Q.fetch_ekey
    Q.extract_cookie_from_process = lambda: {
        'ok': True, 'cookie': 'fresh', 'uin': '999', 'error': ''}
    Q.fetch_ekey = lambda mid, fn, c, u: f'EKEY_FOR_{mid}'
    try:
        fresh_store = Q.EkeyStore(tmp / 'ekeys2.json')
        r = Q.ensure_ekeys(['a.mflac', 'b.mflac'], store=fresh_store)
        check('总数 2', r['total'] == 2, str(r))
        check('新取 2', r['fetched'] == 2)
        check('无失败', not r['failed'])
        check('落盘可查',
              fresh_store.get(SONG_MID, '') == f'EKEY_FOR_{SONG_MID}')
        check('登录态已缓存', fresh_store.get_auth() == ('fresh', '999'))
        r2 = Q.ensure_ekeys(['a.mflac'], store=fresh_store)
        check('二次运行全部命中缓存',
              r2['cached'] == 1 and r2['fetched'] == 0)

        # 客户端未运行 → 失败原因用户可读
        Q.extract_cookie_from_process = lambda: {
            'ok': False, 'error': '未检测到运行中的 QQ 音乐客户端'}
        r3 = Q.ensure_ekeys(['a.mflac'],
                            store=Q.EkeyStore(tmp / 'ekeys3.json'))
        check('无客户端时给出可读原因',
              r3['failed'] and '未检测到' in r3['failed'][0][1])
    finally:
        Q.extract_cookie_from_process = orig_extract
        Q.fetch_ekey = orig_fetch

    print("5. musicex 端到端解锁")
    flac = make_flac('src.flac', seconds=4)
    real_key = os.urandom(150)
    enc_audio = encrypt_with_map(flac, real_key)
    mflac = build_musicex_file(enc_audio, tail)
    Path('歌 - 手.mflac').write_bytes(mflac)

    # 5a. 密钥库无此歌 → 引导错误
    Q.STORE_PATH = tmp / 'empty_store.json'
    r = U.unlock_file('歌 - 手.mflac', str(tmp / 'out_none'))
    check('无密钥时报引导错误',
          not r.ok and '导入 QQ 音乐密钥' in r.message, r.message[:24] + '…')

    # 5b. 写入密钥库 → 解锁成功且逐字节一致
    store3 = Q.EkeyStore(tmp / 'full_store.json')
    store3.put(make_ekey(real_key).decode(), SONG_MID, CLI_FILENAME)
    store3.save()
    Q.STORE_PATH = tmp / 'full_store.json'
    audio_out, _ = U.decrypt_bytes(mflac, 'qmc')
    check('解密逐字节一致', audio_out == flac)
    r = U.unlock_file('歌 - 手.mflac', str(tmp / 'out_ok'))
    check('unlock_file 成功', r.ok, r.message)
    check('输出为 flac', r.ok and r.dst.endswith('.flac'), r.dst)

    # 5c. 显式传入 ekey（不依赖密钥库）
    Q.STORE_PATH = tmp / 'empty_store.json'
    audio_out2, _ = U.decrypt_bytes(mflac, 'qmc',
                                    ekey_text=make_ekey(real_key).decode())
    check('显式 ekey 直通', audio_out2 == flac)

    print("6. extract_cookie_from_process 冒烟")
    auth = Q.extract_cookie_from_process()
    check('返回结构完整且不抛异常',
          isinstance(auth, dict) and 'ok' in auth,
          'ok=' + str(auth.get('ok')))

    print("7. fetch_track_info（mock 详情接口 + 封面）")
    song_json = {'req_1': {'data': {'track_info': {
        'mid': SONG_MID, 'title': '测试歌',
        'singer': [{'name': '歌手甲'}],
        'album': {'name': '测试专辑', 'mid': 'ALBUMMID123'}}}}}
    cover_bytes = b'\xff\xd8\xff\xe0' + os.urandom(5000)

    def fake_urlopen2(req, timeout=0):
        url = req.full_url
        if 'musicu.fcg' in url:
            return _FakeResp(song_json)
        if 'y.gtimg.cn' in url:
            return _FakeResp_raw(cover_bytes)
        raise AssertionError('意外 URL: ' + url)

    class _FakeRespRaw:
        def __init__(self, raw): self._raw = raw
        def read(self): return self._raw
        def __enter__(self): return self
        def __exit__(self, *a): return False
    _FakeResp_raw = _FakeRespRaw

    Q.urllib.request.urlopen = fake_urlopen2
    try:
        ti = Q.fetch_track_info(SONG_MID, 'c', '123')
        check('标题', ti.get('title') == '测试歌')
        check('歌手', ti.get('artist') == '歌手甲')
        check('专辑', ti.get('album') == '测试专辑')
        check('封面字节', ti.get('cover') == cover_bytes)

        # mid 不匹配（接口串歌）→ 拒绝并降级
        def fake_urlopen_wrong_mid(req, timeout=0):
            if 'musicu.fcg' in req.full_url:
                return _FakeResp({'req_1': {'data': {'track_info': {
                    'mid': 'WRONG', 'title': '错的歌'}}}})
            return _FakeResp_raw(cover_bytes)
        Q.urllib.request.urlopen = fake_urlopen_wrong_mid
        check('mid 不匹配降级为空',
              Q.fetch_track_info(SONG_MID, 'c', '1') == {})
        Q.urllib.request.urlopen = fake_urlopen2

        def fake_urlopen_fail(req, timeout=0):
            raise Q.urllib.error.URLError('boom')
        Q.urllib.request.urlopen = fake_urlopen_fail
        check('网络失败降级为空', Q.fetch_track_info(SONG_MID, 'c', '1') == {})
    finally:
        Q.urllib.request.urlopen = orig_urlopen

    print("8. 解锁自动补标签 + 文件名清理")
    Q.STORE_PATH = tmp / 'full_store.json'  # 5c 切走过，切回带密钥的库
    # 真 JPEG 封面（ffmpeg 生成）
    import subprocess
    from core.ffmpeg_locator import find_ffmpeg, find_ffprobe
    subprocess.run([find_ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'color=red:size=64x64',
                    '-frames:v', '1', 'cover.jpg'], check=True)
    real_jpg = Path('cover.jpg').read_bytes()

    U._tag_fetcher = lambda mid: {
        'title': '欺骗游戏', 'artist': '马思唯', 'album': '测试专辑',
        'cover': real_jpg}
    try:
        Path('欺骗游戏 - 马思唯_EM.mflac').write_bytes(mflac)
        r = U.unlock_file('欺骗游戏 - 马思唯_EM.mflac',
                          str(tmp / 'out_tag'), fetch_tags=True)
        check('解锁成功', r.ok, r.message)
        check('输出按标签命名',
              r.ok and Path(r.dst).name == '欺骗游戏 - 马思唯.flac', r.dst)
        if r.ok:
            probe = subprocess.run(
                [find_ffprobe(), '-v', 'quiet', '-print_format', 'json',
                 '-show_format', '-show_streams', r.dst],
                capture_output=True, text=True, encoding='utf-8')
            d = json.loads(probe.stdout)
            tags = d.get('format', {}).get('tags', {})
            check('标题标签写入',
                  any(v == '欺骗游戏' for v in tags.values()), str(tags))
            has_pic = any(s.get('codec_type') == 'video'
                          for s in d.get('streams', []))
            check('封面已内嵌', has_pic)

        # 不补标签：纯离线 + 文件名剥 _EM 尾缀
        U._tag_fetcher = lambda mid: {}
        r2 = U.unlock_file('欺骗游戏 - 马思唯_EM.mflac',
                           str(tmp / 'out_notag'), fetch_tags=False)
        check('离线解锁成功', r2.ok, r2.message)
        check('文件名剥 _EM 尾缀',
              r2.ok and Path(r2.dst).name == '欺骗游戏 - 马思唯.flac', r2.dst)
    finally:
        U._tag_fetcher = U._default_tag_fetcher

    failed = [n for n, ok in PASS if not ok]
    print(f"\n结果: {len(PASS) - len(failed)}/{len(PASS)} 通过")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


if __name__ == '__main__':
    main()
