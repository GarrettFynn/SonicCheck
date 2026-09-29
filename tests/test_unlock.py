#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/unlock.py 回归测试：全部用合成文件做加密→解密往返验证

运行: python tests/test_unlock.py
覆盖:
1. AES-128 ECB 标准测试向量
2. 腾讯 TEA-CBC 加解密往返（测试侧实现 encrypt）
3. NCM 全结构合成 → 解密逐字节一致 + 元数据/封面提取
4. QMC map 密码（1..300 字节密钥）+ 数字尾部 EKey 全链路
5. QMC RC4 密码（>300 字节密钥）+ 数字尾部
6. QMC static 旧版（无尾部）
7. STag 无内嵌密钥 → 明确的用户可读错误
8. unlock_file 端到端：输出文件 + ffmpeg 标签
"""

import base64
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import unlock as U
from core.ffmpeg_locator import find_ffmpeg, find_ffprobe

_M = 0xFFFFFFFF
PASS = []


def check(name, cond, extra=""):
    cond = bool(cond)
    PASS.append((name, cond))
    print(f"  {'✓' if cond else '✗'} {name} {extra}")
    # assert 化（C4）：失败立即带栈中断而非跑完打印汇总，CI 可见出错点
    assert cond, f"{name} {extra}"


# ── 测试侧辅助：PKCS7 / TEA 加密 ──
def pkcs7_pad(b: bytes) -> bytes:
    pad = 16 - len(b) % 16
    return b + bytes([pad]) * pad


def tea_encrypt_block(block: bytes, k) -> bytes:
    v0, v1 = struct.unpack('>II', block)
    delta = 0x9E3779B9
    s = 0
    for _ in range(16):
        s = (s + delta) & _M
        v0 = (v0 + (((((v1 << 4) & _M) + k[0]) & _M)
                    ^ ((v1 + s) & _M)
                    ^ (((v1 >> 5) + k[1]) & _M))) & _M
        v1 = (v1 + (((((v0 << 4) & _M) + k[2]) & _M)
                    ^ ((v0 + s) & _M)
                    ^ (((v0 >> 5) + k[3]) & _M))) & _M
    return struct.pack('>II', v0, v1)


def tea_cbc_encrypt(body: bytes, key16: bytes) -> bytes:
    """oi_symmetry_encrypt2 框架（decrypt 的逆）：1+padLen|Salt2|Body|Zero7"""
    pad_len = (8 - (10 + len(body)) % 8) % 8
    plain = bytes([pad_len]) + b'P' * pad_len + b'\x00\x00' + body \
        + b'\x00' * 7
    assert len(plain) % 8 == 0
    k = struct.unpack('>IIII', key16)
    out = b''
    x_prev = c_prev = None
    for i in range(0, len(plain), 8):
        p = plain[i:i + 8]
        x = p if i == 0 else bytes(a ^ b for a, b in zip(p, c_prev))
        c = tea_encrypt_block(x, k)
        if i > 0:
            c = bytes(a ^ b for a, b in zip(c, x_prev))
        out += c
        x_prev, c_prev = x, c
    return out


def make_ekey(real_key: bytes) -> bytes:
    """由真实密钥合成内嵌 EKey（deriveKeyV1 的逆过程）"""
    hdr8, body = real_key[:8], real_key[8:]
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i * 2] = U._QMC_SIMPLE_KEY[i]
        tea_key[i * 2 + 1] = hdr8[i]
    dec = hdr8 + tea_cbc_encrypt(body, bytes(tea_key))
    return base64.b64encode(dec)


# ── 合成音频源 ──
def make_flac(path: str, seconds=6) -> bytes:
    subprocess.run([find_ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', f'sine=frequency=1000:duration={seconds}',
                    path], check=True)
    return Path(path).read_bytes()


# ── NCM 合成（解密流程的逆） ──
def ncm_encrypt(audio: bytes, key_material: bytes, meta: dict,
                cover: bytes = b'') -> bytes:
    plain_key = b'neteasecloudmusic' + key_material  # 17 字节前缀 + 密钥
    enc_key = U._aes_ecb(pkcs7_pad(plain_key), U._NCM_SCORE_KEY, decrypt=False)
    enc_key = bytes(b ^ 0x64 for b in enc_key)

    meta_plain = b'music:' + json.dumps(meta, ensure_ascii=False).encode()
    enc_meta = U._aes_ecb(pkcs7_pad(meta_plain), U._NCM_META_KEY,
                          decrypt=False)
    meta_block = b"163 key(Don't modify):" + base64.b64encode(enc_meta)
    meta_block = bytes(b ^ 0x63 for b in meta_block)

    box = U._ncm_build_key_box(key_material)
    enc_audio = U._ncm_xor_audio(audio, box)  # XOR 流，对称

    return (U._NCM_MAGIC + b'\x00\x00'
            + struct.pack('<I', len(enc_key)) + enc_key
            + struct.pack('<I', len(meta_block)) + meta_block
            + b'\x00' * 5
            + struct.pack('<I', 0) + struct.pack('<I', len(cover)) + cover
            + enc_audio)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='unlock_test_'))
    os.chdir(tmp)

    print("1. AES-128 ECB 标准向量")
    key = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
    pt = bytes.fromhex('00112233445566778899aabbccddeeff')
    ct_expect = '69c4e0d86a7b0430d8cdb78070b4c55a'
    ct = U._aes_ecb(pt, key, decrypt=False)
    check('加密', ct.hex() == ct_expect, ct.hex())
    check('解密', U._aes_ecb(ct, key, decrypt=True) == pt)

    print("2. TEA-CBC 往返")
    body = os.urandom(100)
    k16 = os.urandom(16)
    enc = tea_cbc_encrypt(body, k16)
    check('TEA 解密 == 原文', U._tencent_tea_decrypt(enc, k16) == body)

    flac = make_flac('src.flac')

    print("3. NCM 全链路")
    key_material = os.urandom(64)
    cover = b'\xff\xd8\xff\xe0' + os.urandom(500)  # 伪 JPEG
    meta = {'musicName': '测试歌曲', 'album': '测试专辑',
            'artist': [['张三', 123], ['李四', 456]]}
    ncm = ncm_encrypt(flac, key_material, meta, cover)
    Path('测试.ncm').write_bytes(ncm)
    r = U.unlock_file('测试.ncm', str(tmp / 'out'))
    check('解密成功', r.ok, r.message)
    check('内层为 flac', r.inner_fmt == 'flac')
    if r.ok:
        out = Path(r.dst).read_bytes()
        # 输出经过 ffmpeg 打标签（-c copy），音频流应与源一致——
        # 用 ffprobe 校验可读 + 标签正确，并直接对比解密中间结果
        audio_direct, info = U.decrypt_bytes(ncm, 'ncm')
        check('音频逐字节一致', audio_direct == flac)
        check('标题提取', info.get('title') == '测试歌曲')
        check('歌手提取', info.get('artist') == '张三 / 李四')
        probe = subprocess.run(
            [find_ffprobe(), '-v', 'quiet', '-print_format', 'json',
             '-show_format', r.dst], capture_output=True, text=True,
            encoding='utf-8')
        tags = json.loads(probe.stdout).get('format', {}).get('tags', {})
        check('输出文件可读且带标题标签',
              any(v == '测试歌曲' for v in tags.values()), str(tags))

    print("4. QMC map 密码 + 数字尾部 EKey")
    real_key = os.urandom(150)          # 1..300 → map
    cipher = U._QmcMapCipher(real_key)
    import numpy as np
    buf = np.frombuffer(flac, dtype=np.uint8).copy()
    cipher.decrypt_into(buf, 0)         # XOR 流：加密=解密
    ekey = make_ekey(real_key)
    qmc = bytes(buf) + ekey + struct.pack('<I', len(ekey))
    Path('测试.mflac').write_bytes(qmc)
    audio_out, _ = U.decrypt_bytes(qmc, 'qmc')
    check('音频逐字节一致', audio_out == flac)

    print("5. QMC RC4 密码（>300 字节密钥）")
    real_key = os.urandom(400)
    cipher = U._QmcRc4Cipher(real_key)
    buf = np.frombuffer(flac, dtype=np.uint8).copy()
    cipher.decrypt_into(buf, 0)
    ekey = make_ekey(real_key)
    qmc = bytes(buf) + ekey + struct.pack('<I', len(ekey))
    audio_out, _ = U.decrypt_bytes(qmc, 'qmc')
    check('音频逐字节一致', audio_out == flac)

    print("6. QMC static 旧版（无尾部）")
    cipher = U._QmcStaticCipher()
    buf = np.frombuffer(flac, dtype=np.uint8).copy()
    cipher.decrypt_into(buf, 0)
    qmc = bytes(buf) + b'\xff\xff\xff\xff'[:0]  # 保持原尾（flac 尾部不会构成合法 keyLen 时走 static）
    try:
        audio_out, _ = U.decrypt_bytes(qmc, 'qmc')
        check('音频逐字节一致', audio_out == flac)
    except U.UnlockError as e:
        check('音频逐字节一致', False, str(e))

    print("7. STag（无内嵌密钥）")
    stag = flac + b'\x00' * 16 + struct.pack('>I', 16) + b'STag'
    try:
        U.decrypt_bytes(stag, 'qmc')
        check('抛出引导错误', False, "未抛异常")
    except U.UnlockError as e:
        check('抛出引导错误', '密钥未嵌入' in str(e), str(e)[:30] + '…')

    print("7b. QTag（songmid,ekey 双段尾部）")
    real_key = os.urandom(150)
    buf = np.frombuffer(flac, dtype=np.uint8).copy()
    U._QmcMapCipher(real_key).decrypt_into(buf, 0)
    ek = make_ekey(real_key)
    qtag_meta = b'004JOZQa26j1g4,' + ek   # qmdec 实测格式：songmid,ekey
    qtag = bytes(buf) + qtag_meta + struct.pack('>I', len(qtag_meta)) + b'QTag'
    audio_out, _ = U.decrypt_bytes(qtag, 'qmc')
    check('双段 QTag 逐字节一致', audio_out == flac)
    # 兼容单段（纯 ekey 无 mid）
    qtag1 = bytes(buf) + ek + struct.pack('>I', len(ek)) + b'QTag'
    audio_out, _ = U.decrypt_bytes(qtag1, 'qmc')
    check('单段 QTag 兼容', audio_out == flac)

    print("8. unlock_file 端到端（QMC mflac → 输出 flac）")
    Path('歌名 - 歌手.mflac').write_bytes(
        (lambda b: bytes(b))(np.frombuffer(flac, dtype=np.uint8).copy()))
    buf = np.frombuffer(flac, dtype=np.uint8).copy()
    U._QmcMapCipher(real_key[:150]).decrypt_into(buf, 0)
    ek = make_ekey(real_key[:150])
    Path('歌名 - 歌手.mflac').write_bytes(bytes(buf) + ek
                                         + struct.pack('<I', len(ek)))
    r = U.unlock_file('歌名 - 歌手.mflac', str(tmp / 'out2'))
    check('解密成功', r.ok, r.message)
    check('输出为 flac', r.ok and r.dst.endswith('.flac'), r.dst)
    check('文件名保留原名', r.ok and '歌名 - 歌手' in r.dst)

    print("9. 流式解密：跨块边界 + 进度回调")
    U._STREAM_CHUNK = 64 * 1024  # 强制多块，覆盖边界
    try:
        # 9a. NCM 流式 == 内存版逐字节
        events = []
        with open('测试.ncm', 'rb') as fin, open('raw_n.bin', 'wb') as out:
            info = U._decrypt_ncm_stream(
                fin, out, progress_cb=lambda d, t: events.append((d, t)))
        raw = Path('raw_n.bin').read_bytes()
        check('NCM 流式逐字节一致', raw == flac)
        check('NCM 流式 inner_fmt', info.get('inner_fmt') == 'flac')
        check('NCM 流式标题', info.get('title') == '测试歌曲')
        check('NCM 流式封面', info.get('cover') == cover)
        check('进度单调且收尾 100%',
              len(events) > 1 and events[-1][0] == events[-1][1]
              and all(a[0] <= b[0] for a, b in zip(events, events[1:])))

        # 9b. QMC 流式 == 内存版逐字节（跨块 + 进度）
        events = []
        with open('测试.mflac', 'rb') as fin, open('raw_q.bin', 'wb') as out:
            info = U._decrypt_qmc_stream(
                fin, out, progress_cb=lambda d, t: events.append((d, t)))
        raw = Path('raw_q.bin').read_bytes()
        check('QMC 流式逐字节一致', raw == flac)
        check('QMC 流式 inner_fmt', info.get('inner_fmt') == 'flac')
        check('QMC 进度收尾 100%',
              len(events) > 1 and events[-1][0] == events[-1][1])

        # 9c. XOR offset 参数等价
        box = U._ncm_build_key_box(key_material)
        half = len(flac) // 2
        two = U._ncm_xor_audio(flac[:half], box, 0) \
            + U._ncm_xor_audio(flac[half:], box, half)
        check('XOR offset 分段等价', two == U._ncm_xor_audio(flac, box))

        # 9d. unlock_file 在小分块下端到端（跨块）
        r = U.unlock_file('歌名 - 歌手.mflac', str(tmp / 'out3'))
        check('小分块端到端成功', r.ok, r.message)
    finally:
        U._STREAM_CHUNK = 8 << 20

    failed = [n for n, ok in PASS if not ok]
    print(f"\n结果: {len(PASS) - len(failed)}/{len(PASS)} 通过")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


if __name__ == '__main__':
    main()
