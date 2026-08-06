#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加密音频解锁：网易云 .ncm / QQ音乐 QMC 系（.mflac .mgg .qmc* 等）→ 标准音频

纯 Python 实现，零新增依赖。算法逐字节对照 unlock-music CLI v0.2.12 参考实现
（QMC 尾部 EKey → TEA-CBC 派生 → map/RC4/static 流密码；NCM AES-128-ECB
+ 自定义 KeyBox XOR），常量与流程与参考一致。

免责：本模块仅用于用户已合法获得（已购买/已下载）的文件的本地格式转换与
互操作，请勿用于侵犯版权或违反平台服务条款的用途。

能力边界：
- QQ音乐新版（客户端 ≥11.6 下载的 STag/musicex 尾部文件）密钥不在文件内，
  无法离线解密，会抛出带引导信息的 UnlockError
- 内层为有损格式（mp3/ogg/m4a）时，转 FLAC/WAV 只是换容器，不提升音质
"""

import base64
import io
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ══════════════════════════════════════════════════════════════════
# AES-128（ECB 单块加/解密，NCM 用；数据量极小，纯 Python 足够）
# ══════════════════════════════════════════════════════════════════

def _gf_mul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _build_sboxes():
    sbox = [0] * 256
    inv = [0] * 256
    for i in range(256):
        # 乘法逆元
        inv_mul = 0 if i == 0 else next(
            x for x in range(1, 256) if _gf_mul(i, x) == 1)
        s = inv_mul ^ ((inv_mul << 1) | (inv_mul >> 7)) & 0xFF \
            ^ ((inv_mul << 2) | (inv_mul >> 6)) & 0xFF \
            ^ ((inv_mul << 3) | (inv_mul >> 5)) & 0xFF \
            ^ ((inv_mul << 4) | (inv_mul >> 4)) & 0xFF ^ 0x63
        s &= 0xFF
        sbox[i] = s
        inv[s] = i
    return sbox, inv


_SBOX, _INV_SBOX = _build_sboxes()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _aes_key_expand(key: bytes) -> list:
    """AES-128 密钥扩展 → 11 个 16 字节轮密钥（按 4x4 状态矩阵列序）"""
    w = list(key)
    for i in range(16, 176, 4):
        t = w[i - 4:i]
        if i % 16 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // 16 - 1]
        w.extend(w[i - 16 + j] ^ t[j] for j in range(4))
    return [w[r * 16:(r + 1) * 16] for r in range(11)]


def _xor_state(s: list, rk: list) -> list:
    return [a ^ b for a, b in zip(s, rk)]


def _aes_encrypt_block(block: bytes, rks: list) -> bytes:
    s = _xor_state(list(block), rks[0])
    for rnd in range(1, 10):
        s = [_SBOX[b] for b in s]
        s = [s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3],
             s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]]
        out = [0] * 16
        for c in range(4):
            col = s[c * 4:c * 4 + 4]
            out[c * 4 + 0] = _gf_mul(col[0], 2) ^ _gf_mul(col[1], 3) \
                ^ col[2] ^ col[3]
            out[c * 4 + 1] = col[0] ^ _gf_mul(col[1], 2) \
                ^ _gf_mul(col[2], 3) ^ col[3]
            out[c * 4 + 2] = col[0] ^ col[1] ^ _gf_mul(col[2], 2) \
                ^ _gf_mul(col[3], 3)
            out[c * 4 + 3] = _gf_mul(col[0], 3) ^ col[1] ^ col[2] \
                ^ _gf_mul(col[3], 2)
        s = _xor_state(out, rks[rnd])
    s = [_SBOX[b] for b in s]
    s = [s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3],
         s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]]
    return bytes(_xor_state(s, rks[10]))


def _aes_decrypt_block(block: bytes, rks: list) -> bytes:
    s = _xor_state(list(block), rks[10])
    for rnd in range(9, 0, -1):
        s = [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11],
             s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]]
        s = [_INV_SBOX[b] for b in s]
        s = _xor_state(s, rks[rnd])
        out = [0] * 16
        for c in range(4):
            col = s[c * 4:c * 4 + 4]
            for r in range(4):
                out[c * 4 + r] = (_gf_mul(col[0], 14) ^ _gf_mul(col[1], 11)
                                  ^ _gf_mul(col[2], 13) ^ _gf_mul(col[3], 9)) \
                    if r == 0 else \
                    (_gf_mul(col[0], 9) ^ _gf_mul(col[1], 14)
                     ^ _gf_mul(col[2], 11) ^ _gf_mul(col[3], 13)) \
                    if r == 1 else \
                    (_gf_mul(col[0], 13) ^ _gf_mul(col[1], 9)
                     ^ _gf_mul(col[2], 14) ^ _gf_mul(col[3], 11)) \
                    if r == 2 else \
                    (_gf_mul(col[0], 11) ^ _gf_mul(col[1], 13)
                     ^ _gf_mul(col[2], 9) ^ _gf_mul(col[3], 14))
        s = out
    s = [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11],
         s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]]
    s = [_INV_SBOX[b] for b in s]
    return bytes(_xor_state(s, rks[0]))


def _aes_ecb(data: bytes, key: bytes, decrypt: bool) -> bytes:
    if len(data) % 16:
        raise ValueError("AES-ECB 数据长度非 16 字节倍数")
    rks = _aes_key_expand(key)
    fn = _aes_decrypt_block if decrypt else _aes_encrypt_block
    return b"".join(fn(data[i:i + 16], rks) for i in range(0, len(data), 16))


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data


# ══════════════════════════════════════════════════════════════════
# 通用
# ══════════════════════════════════════════════════════════════════

class UnlockError(Exception):
    """用户可读的解锁失败原因"""


class UnlockCancelled(Exception):
    """用户取消：流式解密/转码中断，调用方据此静默收尾（不算失败）"""


@dataclass
class UnlockResult:
    ok: bool
    src: str
    dst: str = ""
    enc_fmt: str = ""        # 'ncm' / 'qmc'
    inner_fmt: str = ""      # 解出的内层格式：flac/mp3/ogg/...
    title: str = ""
    artist: str = ""
    message: str = ""
    extra: dict = field(default_factory=dict)


def _le32(b, o) -> int:
    return struct.unpack_from('<I', b, o)[0]


def _be32(b, o) -> int:
    return struct.unpack_from('>I', b, o)[0]


_AUDIO_EXTS = {
    'mp3': '.mp3', 'flac': '.flac', 'ogg': '.ogg',
    'wav': '.wav', 'aac': '.m4a', 'ape': '.ape',
}


def detect_audio_format(data: bytes) -> str:
    """按 magic 识别解出的内层音频格式（与参考实现同口径）"""
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return 'mp3'
    if data[:4] == b'fLaC':
        return 'flac'
    if data[:4] == b'OggS':
        return 'ogg'
    if data[:4] == b'RIFF':
        return 'wav'
    if data[:4] == b'MAC ':
        return 'ape'
    if data[:3] == b'ID3':
        return 'mp3'
    if len(data) >= 8 and data[4:8] == b'ftyp':
        return 'aac'
    return ''


# ══════════════════════════════════════════════════════════════════
# NCM（网易云）
# ══════════════════════════════════════════════════════════════════

_NCM_MAGIC = b'CTENFDAM'
_NCM_SCORE_KEY = bytes.fromhex('687a4852416d736f356b496e62617857')
_NCM_META_KEY = bytes.fromhex('2331346c6a6b5f215c5d2630553c2728')


def _ncm_build_key_box(key: bytes) -> np.ndarray:
    """网易云自定义 key-box（注意：不是标准 RC4 KSA，含 last_byte 链）"""
    box = list(range(256))
    last = 0
    koff = 0
    for i in range(256):
        swap = box[i]
        c = (swap + last + key[koff]) & 0xFF
        koff += 1
        if koff >= len(key):
            koff = 0
        box[i] = box[c]
        box[c] = swap
        last = c
    return np.array(box, dtype=np.uint8)


def _ncm_xor_audio(audio: bytes, box: np.ndarray, offset: int = 0) -> bytes:
    """keyBox XOR 流（numpy 分块向量化；j=(i+1)&0xFF 按流内绝对偏移）。
    offset：本块在音频流中的起始绝对偏移（流式解密时传 >0）"""
    data = np.frombuffer(audio, dtype=np.uint8)
    out = np.empty_like(data)
    box32 = box.astype(np.int64)
    chunk = 1 << 20
    for start in range(0, len(data), chunk):
        end = min(start + chunk, len(data))
        i = np.arange(offset + start, offset + end, dtype=np.int64)
        j = (i + 1) & 0xFF
        ks = box32[(box32[j] + box32[(box32[j] + j) & 0xFF]) & 0xFF]
        out[start:end] = data[start:end] ^ ks.astype(np.uint8)
    return out.tobytes()


def _ncm_parse_meta(meta_raw: bytes) -> dict:
    """解 NCM 头部 AES 加密的 JSON 元数据（标题/歌手/专辑）；失败返回 {}"""
    try:
        meta = bytes(b ^ 0x63 for b in meta_raw)
        b64 = meta[22:]  # 去掉 "163 key(Don't modify):" 前缀
        cipher = base64.b64decode(b64)
        plain = _pkcs7_unpad(_aes_ecb(cipher, _NCM_META_KEY, decrypt=True))
        text = plain.decode('utf-8', errors='replace')
        if text.startswith('music:'):
            text = text[6:]
        j = json.loads(text)
        artists = j.get('artist') or []
        names = [a[0] for a in artists
                 if isinstance(a, (list, tuple)) and a and a[0]]
        return {
            'title': j.get('musicName') or '',
            'artist': ' / '.join(names),
            'album': j.get('album') or '',
        }
    except Exception:
        return {}


def _ncm_read_header(f) -> tuple:
    """从文件对象顺序读取 NCM 头部 → (key_box, info)。
    返回时 f 恰好停留在音频数据起点。内存/流式两条路径共用。"""
    magic = f.read(10)
    if len(magic) < 10 or magic[:8] != _NCM_MAGIC:
        raise UnlockError("不是有效的 NCM 文件（缺少 CTENFDAM 头）")

    def read_exact(n, msg):
        b = f.read(n)
        if len(b) < n:
            raise UnlockError(msg)
        return b

    key_len = _le32(read_exact(4, "NCM 头部不完整"), 0)
    if not (1 <= key_len <= (1 << 20)):
        raise UnlockError("NCM 密钥长度非法")
    raw_key = bytes(b ^ 0x64 for b in read_exact(key_len, "NCM 密钥数据不完整"))
    try:
        dec_key = _pkcs7_unpad(_aes_ecb(raw_key, _NCM_SCORE_KEY, decrypt=True))
    except Exception:
        raise UnlockError("NCM 密钥数据损坏")
    if len(dec_key) <= 17:
        raise UnlockError("NCM 密钥数据损坏")
    box = _ncm_build_key_box(dec_key[17:])

    meta_len = _le32(read_exact(4, "NCM 头部不完整"), 0)
    if meta_len > (4 << 20):
        raise UnlockError("NCM 元数据长度非法")
    info = _ncm_parse_meta(read_exact(meta_len, "NCM 元数据不完整")) \
        if meta_len else {}

    read_exact(5, "NCM 头部不完整")  # CRC32(4) + 版本(1)
    cover_frame_len = _le32(read_exact(4, "NCM 头部不完整"), 0)
    img_len = _le32(read_exact(4, "NCM 头部不完整"), 0)
    if cover_frame_len > (32 << 20) or img_len > (32 << 20):
        raise UnlockError("NCM 封面长度非法")
    if cover_frame_len and img_len > cover_frame_len:
        raise UnlockError("NCM 封面长度非法")
    cover_skip = cover_frame_len if cover_frame_len else img_len
    if img_len:
        info['cover'] = read_exact(img_len, "NCM 封面数据不完整")
    if cover_skip > img_len:
        read_exact(cover_skip - img_len, "NCM 封面数据不完整")
    return box, info


def _decrypt_ncm(data: bytes):
    """返回 (audio_bytes, info_dict)。info 含 title/artist/album/cover"""
    f = io.BytesIO(data)
    box, info = _ncm_read_header(f)
    audio = _ncm_xor_audio(data[f.tell():], box)
    return audio, info


# ══════════════════════════════════════════════════════════════════
# QMC（QQ音乐 / Moo：qmc* mflac* mgg* bkc* tkm 等）
# ══════════════════════════════════════════════════════════════════

QMC_EXTS = {
    'qmc', 'qmc0', 'qmc2', 'qmc3', 'qmc4', 'qmc6', 'qmc8',
    'qmcflac', 'qmcogg', 'tkm',
    'bkcmp3', 'bkcm4a', 'bkcflac', 'bkcwav', 'bkcape', 'bkcogg', 'bkcwma',
    '666c6163', '6d7033', '6f6767', '6d3461', '776176', 'mmp4',
    'mflac', 'mflac0', 'mflac1', 'mflaca', 'mflach', 'mflacl', 'mflacm',
    'mgg', 'mgg0', 'mgg1', 'mgga', 'mggh', 'mggl', 'mggm',
}

_MSG_NO_KEY = ("此文件的密钥未嵌入文件（QQ音乐新版 STag 格式，"
               "约客户端 11.6+ 下载），无法离线解密。"
               "解决办法：用旧版客户端（≤19.43）重新下载。")

_MSG_MUSICEX_NEED_KEY = (
    "此文件为 QQ音乐新版 musicex 加密，密钥不在文件内。"
    "请点击本窗口的「导入 QQ 音乐密钥」按钮（需 QQ 音乐客户端已打开，"
    "并登录下载过这些歌的账号；不熟流程可点旁边的「教程」），"
    "导入后重新解锁即可。")

_QMC_KEY_THRESHOLD = 300
_QMC_SIMPLE_KEY = bytes([0x69, 0x56, 0x46, 0x38, 0x2B, 0x20, 0x15, 0x0B])
_QMC_DERIVE_V2_KEY1 = bytes([
    0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59, 0x21, 0x40,
    0x23, 0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28])
_QMC_DERIVE_V2_KEY2 = bytes([
    0x2A, 0x2A, 0x23, 0x21, 0x28, 0x23, 0x24, 0x25,
    0x26, 0x5E, 0x61, 0x31, 0x63, 0x5A, 0x2C, 0x54])
_QMC_V2_PREFIX = b'QQMusic EncV2,Key:'

# QMCv1 静态密码盒（unlock-music cipher_static.go，256 字节）
_QMC_STATIC_BOX = bytes([
    0x77, 0x48, 0x32, 0x73, 0xDE, 0xF2, 0xC0, 0xC8,
    0x95, 0xEC, 0x30, 0xB2, 0x51, 0xC3, 0xE1, 0xA0,
    0x9E, 0xE6, 0x9D, 0xCF, 0xFA, 0x7F, 0x14, 0xD1,
    0xCE, 0xB8, 0xDC, 0xC3, 0x4A, 0x67, 0x93, 0xD6,
    0x28, 0xC2, 0x91, 0x70, 0xCA, 0x8D, 0xA2, 0xA4,
    0xF0, 0x08, 0x61, 0x90, 0x7E, 0x6F, 0xA2, 0xE0,
    0xEB, 0xAE, 0x3E, 0xB6, 0x67, 0xC7, 0x92, 0xF4,
    0x91, 0xB5, 0xF6, 0x6C, 0x5E, 0x84, 0x40, 0xF7,
    0xF3, 0x1B, 0x02, 0x7F, 0xD5, 0xAB, 0x41, 0x89,
    0x28, 0xF4, 0x25, 0xCC, 0x52, 0x11, 0xAD, 0x43,
    0x68, 0xA6, 0x41, 0x8B, 0x84, 0xB5, 0xFF, 0x2C,
    0x92, 0x4A, 0x26, 0xD8, 0x47, 0x6A, 0x7C, 0x95,
    0x61, 0xCC, 0xE6, 0xCB, 0xBB, 0x3F, 0x47, 0x58,
    0x89, 0x75, 0xC3, 0x75, 0xA1, 0xD9, 0xAF, 0xCC,
    0x08, 0x73, 0x17, 0xDC, 0xAA, 0x9A, 0xA2, 0x16,
    0x41, 0xD8, 0xA2, 0x06, 0xC6, 0x8B, 0xFC, 0x66,
    0x34, 0x9F, 0xCF, 0x18, 0x23, 0xA0, 0x0A, 0x74,
    0xE7, 0x2B, 0x27, 0x70, 0x92, 0xE9, 0xAF, 0x37,
    0xE6, 0x8C, 0xA7, 0xBC, 0x62, 0x65, 0x9C, 0xC2,
    0x08, 0xC9, 0x88, 0xB3, 0xF3, 0x43, 0xAC, 0x74,
    0x2C, 0x0F, 0xD4, 0xAF, 0xA1, 0xC3, 0x01, 0x64,
    0x95, 0x4E, 0x48, 0x9F, 0xF4, 0x35, 0x78, 0x95,
    0x7A, 0x39, 0xD6, 0x6A, 0xA0, 0x6D, 0x40, 0xE8,
    0x4F, 0xA8, 0xEF, 0x11, 0x1D, 0xF3, 0x1B, 0x3F,
    0x3F, 0x07, 0xDD, 0x6F, 0x5B, 0x19, 0x30, 0x19,
    0xFB, 0xEF, 0x0E, 0x37, 0xF0, 0x0E, 0xCD, 0x16,
    0x49, 0xFE, 0x53, 0x47, 0x13, 0x1A, 0xBD, 0xA4,
    0xF1, 0x40, 0x19, 0x60, 0x0E, 0xED, 0x68, 0x09,
    0x06, 0x5F, 0x4D, 0xCF, 0x3D, 0x1A, 0xFE, 0x20,
    0x77, 0xE4, 0xD9, 0xDA, 0xF9, 0xA4, 0x2B, 0x76,
    0x1C, 0x71, 0xDB, 0x00, 0xBC, 0xFD, 0x0C, 0x6C,
    0xA5, 0x47, 0xF7, 0xF6, 0x00, 0x79, 0x4A, 0x11,
])


# ── TEA（腾讯 TEA-CBC，oi_symmetry_decrypt2 框架）──
_M32 = 0xFFFFFFFF


def _tea_decrypt_block(block: bytearray, k) -> None:
    """TEA 单块解密：大端字，DELTA=0x9E3779B9，16 轮（与参考一致）"""
    v0, v1 = struct.unpack_from('>II', block, 0)
    delta = 0x9E3779B9
    s = 0xE3779B90  # = delta*16 mod 2^32
    for _ in range(16):
        v1 = (v1 - (((((v0 << 4) & _M32) + k[2]) & _M32)
                    ^ ((v0 + s) & _M32)
                    ^ (((v0 >> 5) + k[3]) & _M32))) & _M32
        v0 = (v0 - (((((v1 << 4) & _M32) + k[0]) & _M32)
                    ^ ((v1 + s) & _M32)
                    ^ (((v1 >> 5) + k[1]) & _M32))) & _M32
        s = (s - delta) & _M32
    struct.pack_into('>II', block, 0, v0, v1)


def _tencent_tea_decrypt(in_buf: bytes, key: bytes) -> bytes:
    """腾讯 TEA-CBC：PadLen|Pad|Salt(2)|Body|Zero(7) 框架"""
    salt_len, zero_len = 2, 7
    if len(in_buf) % 8:
        raise UnlockError("TEA 输入长度非法")
    if len(in_buf) < 16:
        raise UnlockError("TEA 输入过短")
    k = struct.unpack('>IIII', key[:16])

    dest = bytearray(in_buf[:8])
    _tea_decrypt_block(dest, k)
    pad_len = dest[0] & 0x07
    out_len = len(in_buf) - 1 - pad_len - salt_len - zero_len
    if out_len < 0:
        raise UnlockError("TEA 解密失败")
    out = bytearray(out_len)

    iv_prev = bytes(8)
    iv_cur = bytes(in_buf[:8])
    in_pos = 8
    dest_idx = 1 + pad_len

    def crypt_block():
        nonlocal in_pos, dest_idx, iv_prev, iv_cur, dest
        iv_prev = iv_cur
        iv_cur = bytes(in_buf[in_pos:in_pos + 8])
        dest = bytearray(a ^ b for a, b in zip(dest, iv_cur))
        _tea_decrypt_block(dest, k)
        in_pos += 8
        dest_idx = 0

    i = 1
    while i <= salt_len:
        if dest_idx < 8:
            dest_idx += 1
            i += 1
        else:
            crypt_block()
    out_pos = 0
    while out_pos < out_len:
        if dest_idx < 8:
            out[out_pos] = dest[dest_idx] ^ iv_prev[dest_idx]
            dest_idx += 1
            out_pos += 1
        else:
            crypt_block()
    for _ in range(zero_len):
        if dest_idx < 8 and dest[dest_idx] != iv_prev[dest_idx]:
            raise UnlockError("QQ 音乐密钥校验失败")
    return bytes(out)


# ── EKey 派生 ──
def _qmc_b64(b: bytes) -> bytes:
    try:
        return base64.b64decode(b)
    except Exception:
        raise UnlockError("QQ 音乐密钥格式无效")


def _qmc_derive_key_v1(dec: bytes) -> bytes:
    if len(dec) < 16:
        raise UnlockError("无效的 QQ 音乐密钥")
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i * 2] = _QMC_SIMPLE_KEY[i]
        tea_key[i * 2 + 1] = dec[i]
    rs = _tencent_tea_decrypt(dec[8:], bytes(tea_key))
    return dec[:8] + rs


def _qmc_derive_key_v2(raw: bytes) -> bytes:
    buf = _tencent_tea_decrypt(raw, _QMC_DERIVE_V2_KEY1)
    buf = _tencent_tea_decrypt(buf, _QMC_DERIVE_V2_KEY2)
    return _qmc_b64(buf)


def _qmc_derive_key(raw_key: bytes) -> bytes:
    dec = _qmc_b64(raw_key)
    if dec[:len(_QMC_V2_PREFIX)] == _QMC_V2_PREFIX:
        dec = _qmc_derive_key_v2(dec[len(_QMC_V2_PREFIX):])
    return _qmc_derive_key_v1(dec)


# ── 流密码（map / RC4 / static；keystream 只依赖绝对偏移，可随机访问）──
class _QmcMapCipher:
    """密钥 1..300 字节：idx=(o²+71214)%n，值循环左移 r=((idx&7)+4)%8 位"""

    def __init__(self, key: bytes):
        self.key = np.frombuffer(key, dtype=np.uint8).astype(np.int64)
        self.n = len(key)

    def decrypt_into(self, buf: np.ndarray, file_offset: int) -> None:
        n = len(buf)
        off = np.arange(file_offset, file_offset + n, dtype=np.int64)
        off = np.where(off > 0x7FFF, off % 0x7FFF, off)
        idx = (off * off + 71214) % self.n
        v = self.key[idx]
        r = ((idx & 0x07) + 4) % 8
        mask = ((v << r) & 0xFF) | (v >> r)
        buf ^= mask.astype(np.uint8)


class _QmcStaticCipher:
    """旧版 QMCv1：固定 256 字节盒，idx=(o²+27)&0xFF"""

    box = np.frombuffer(_QMC_STATIC_BOX, dtype=np.uint8)

    def decrypt_into(self, buf: np.ndarray, file_offset: int) -> None:
        n = len(buf)
        off = np.arange(file_offset, file_offset + n, dtype=np.int64)
        off = np.where(off > 0x7FFF, off % 0x7FFF, off)
        idx = (off * off + 27) & 0xFF
        buf ^= self.box[idx]


class _QmcRc4Cipher:
    """密钥 >300 字节：分段 RC4（首 128 字节特殊，之后 5120 字节一段）"""

    SEG = 5120
    FIRST = 128

    def __init__(self, key: bytes):
        self.key = key
        self.n = len(key)
        # 参考实现 box 为字节数组：初始化值按字节截断（i & 0xFF）
        box = [i & 0xFF for i in range(self.n)]
        j = 0
        for i in range(self.n):
            j = (j + box[i] + key[i % self.n]) % self.n
            box[i], box[j] = box[j], box[i]
        self.box = box
        h = 1
        for v in key:
            if v == 0:
                continue
            nxt = (h * v) & _M32
            if nxt == 0 or nxt <= h:
                break
            h = nxt
        self.hash = h

    def _segment_skip(self, seg_id: int) -> int:
        seed = self.key[seg_id % self.n]
        if seed == 0:
            return 0
        return int(self.hash / ((seg_id + 1) * seed) * 100.0) % self.n

    def _dec_a_segment(self, buf: bytearray, off: int, length: int,
                       offset: int) -> None:
        b = list(self.box)
        n = self.n
        j = k = 0
        skip_len = (offset % self.SEG) + self._segment_skip(offset // self.SEG)
        i = -skip_len
        while i < length:
            j = (j + 1) % n
            k = (b[j] + k) % n
            b[j], b[k] = b[k], b[j]
            if i >= 0:
                buf[off + i] ^= b[(b[j] + b[k]) % n]
            i += 1

    def decrypt_into(self, buf: np.ndarray, file_offset: int) -> None:
        raw = bytearray(buf.tobytes())
        offset = file_offset
        to_process = len(raw)
        processed = 0
        if offset < self.FIRST:
            bs = min(to_process, self.FIRST - offset)
            for i in range(bs):
                raw[processed + i] ^= self.key[self._segment_skip(offset + i)]
            offset += bs
            to_process -= bs
            processed += bs
        if to_process and offset % self.SEG:
            bs = min(to_process, self.SEG - offset % self.SEG)
            self._dec_a_segment(raw, processed, bs, offset)
            offset += bs
            to_process -= bs
            processed += bs
        while to_process > self.SEG:
            self._dec_a_segment(raw, processed, self.SEG, offset)
            offset += self.SEG
            to_process -= self.SEG
            processed += self.SEG
        if to_process > 0:
            self._dec_a_segment(raw, processed, to_process, offset)
        buf[:] = np.frombuffer(bytes(raw), dtype=np.uint8)


def _qmc_cipher_for(real_key: bytes):
    if len(real_key) > _QMC_KEY_THRESHOLD:
        return _QmcRc4Cipher(real_key)
    if real_key:
        return _QmcMapCipher(real_key)
    return _QmcStaticCipher()


def _default_ekey_lookup(meta: dict) -> str:
    """从本地密钥库查 musicex ekey（按 song_mid / 客户端文件名）"""
    try:
        from core.qqmusic_key import EkeyStore
        return EkeyStore().get(meta.get('song_mid', ''),
                               meta.get('filename', ''))
    except Exception:
        return ''


# 测试可替换的查找钩子：musicex meta → ekey 字符串（'' 表示未找到）
_ekey_lookup = _default_ekey_lookup


def _default_tag_fetcher(song_mid: str) -> dict:
    """按 song_mid 联网补齐 QQ 音乐标签（title/artist/album/cover）"""
    try:
        from core.qqmusic_key import fetch_track_info
        return fetch_track_info(song_mid)
    except Exception:
        return {}


# 测试可替换的标签补全钩子：song_mid → 标签 dict（{} 表示失败/降级）
_tag_fetcher = _default_tag_fetcher


def _qmc_parse_footer(tail: bytes, size: int):
    """解析 QMC 尾部。tail=文件末尾若干字节（至少 16+184；QTag 大元数据
    需完整包含），size=文件真实总大小。
    返回 (audio_len, ekey_bytes_or_None, musicex_meta_or_None)。
    STag 无内嵌密钥直接抛 UnlockError；musicex 返回 meta 供密钥库查询。"""
    n = len(tail)
    if size < 4 or n < 4:
        return size, None, None
    t4 = tail[-4:]
    if t4 == b'QTag':
        if n < 8:
            raise UnlockError(_MSG_NO_KEY)
        meta_len = _be32(tail, n - 8)
        audio_len = size - 8 - meta_len
        if not (0 <= meta_len <= (1 << 20)) or audio_len < 0:
            raise UnlockError("QMC 尾部元数据长度非法")
        if n < 8 + meta_len:
            raise UnlockError("QMC 尾部数据不完整")
        items = tail[n - 8 - meta_len:n - 8].split(b',')
        if not items or not items[0]:
            raise UnlockError(_MSG_NO_KEY)
        # QTag 内容为 "songmid,ekey"（qmdec 实测格式）；单段时按纯 ekey 兼容
        ekey = items[1] if len(items) >= 2 and items[1] else items[0]
        return audio_len, ekey, None
    if t4 == b'STag':
        raise UnlockError(_MSG_NO_KEY)
    if t4 == b'cex\x00':  # musicex：密钥在客户端，需查本地密钥库
        from core.qqmusic_key import parse_musicex_info_from_bytes
        meta = parse_musicex_info_from_bytes(tail, size)
        if meta:
            return meta['audio_size'], None, meta
        raise UnlockError(_MSG_MUSICEX_NEED_KEY)
    key_len = _le32(tail, n - 4)
    if 1 <= key_len <= 0xFFFF and key_len < size - 4 and n >= 4 + key_len:
        audio_len = size - 4 - key_len
        ekey = tail[n - 4 - key_len:n - 4].rstrip(b'\x00')
        return audio_len, ekey, None
    return size, None, None  # 无尾部：旧版静态密码


def _qmc_resolve_ekey(ekey, meta, ekey_text: str):
    """musicex 密钥裁决：显式传入 > 本地密钥库；都无果抛引导错误"""
    if ekey is None and meta is not None:
        if not ekey_text:
            ekey_text = _ekey_lookup(meta)
        if ekey_text:
            ekey = ekey_text.encode('utf-8')
        else:
            raise UnlockError(_MSG_MUSICEX_NEED_KEY)
    return ekey


def _decrypt_qmc(data: bytes, ekey_text: str = ''):
    """返回 (audio_bytes, info_dict)。
    musicex 文件优先用传入的 ekey_text，其次查本地密钥库；
    解出后在 info['musicex_mid'] 携带 song_mid 供联网补标签。"""
    audio_len, ekey, meta = _qmc_parse_footer(data, len(data))
    ekey = _qmc_resolve_ekey(ekey, meta, ekey_text)
    real_key = _qmc_derive_key(ekey) if ekey else b''
    cipher = _qmc_cipher_for(real_key)
    buf = np.frombuffer(data[:audio_len], dtype=np.uint8).copy()  # 可写副本
    cipher.decrypt_into(buf, 0)
    info = {}
    if meta and meta.get('song_mid'):
        info['musicex_mid'] = meta['song_mid']
    return buf.tobytes(), info


# ══════════════════════════════════════════════════════════════════
# 对外接口
# ══════════════════════════════════════════════════════════════════

ENCRYPTED_EXTS = {'.ncm'} | {'.' + e for e in QMC_EXTS}


def detect_encrypted_type(path) -> str:
    """识别加密格式：'ncm' / 'qmc' / ''（不读全文，只看头+扩展名）"""
    p = Path(path)
    try:
        with open(p, 'rb') as f:
            head = f.read(8)
    except OSError:
        return ''
    if head == _NCM_MAGIC:
        return 'ncm'
    segments = p.name.lower().split('.')[1:]
    if any(seg in QMC_EXTS for seg in segments):
        return 'qmc'
    return ''


def find_encrypted_files(folder: str) -> list:
    """递归查找文件夹内可识别的加密音频（ncm/qmc 系），返回绝对路径列表"""
    root = Path(folder)
    if not root.is_dir():
        return []
    out = []
    for p in root.rglob('*'):
        if p.is_file() and detect_encrypted_type(p):
            out.append(str(p.resolve()))
    return sorted(out, key=str.lower)


def decrypt_bytes(data: bytes, enc_type: str, ekey_text: str = ''):
    """解密内存数据 → (audio_bytes, info)；格式不明/失败抛 UnlockError。
    ekey_text：musicex 文件的显式密钥（一般不传，走本地密钥库查找）"""
    if enc_type == 'ncm':
        audio, info = _decrypt_ncm(data)
    elif enc_type == 'qmc':
        audio, info = _decrypt_qmc(data, ekey_text)
    else:
        raise UnlockError("无法识别的加密格式")
    inner = detect_audio_format(audio)
    if not inner:
        raise UnlockError("解密后不是可识别的音频（密钥可能已失效或文件损坏）")
    info['inner_fmt'] = inner
    return audio, info


# ══════════════════════════════════════════════════════════════════
# 流式解密：分块读写，大文件（200MB+）内存占用恒定（约一个分块）
# ══════════════════════════════════════════════════════════════════

_STREAM_CHUNK = 8 << 20   # 8 MB；测试可改小以覆盖跨块边界
_QMC_TAIL_READ = (1 << 20) + 4096 + 32  # 覆盖 QTag 大元数据 + musicex 尾块


def _detect_inner_or_raise(head: bytes) -> str:
    inner = detect_audio_format(head)
    if not inner:
        raise UnlockError("解密后不是可识别的音频（密钥可能已失效或文件损坏）")
    return inner


def _decrypt_ncm_stream(f, out, progress_cb=None, cancel_check=None) -> dict:
    """NCM 流式解密：头部一次读入（含封面），音频分块 XOR 写出。
    返回 info（含 inner_fmt/title/artist/album/cover）"""
    box, info = _ncm_read_header(f)
    hdr_end = f.tell()
    f.seek(0, 2)
    total = max(0, f.tell() - hdr_end)
    f.seek(hdr_end)
    done = 0
    first = True
    while True:
        if cancel_check is not None and cancel_check():
            raise UnlockCancelled()
        chunk = f.read(_STREAM_CHUNK)
        if not chunk:
            break
        dec = _ncm_xor_audio(chunk, box, offset=done)
        if first:
            info['inner_fmt'] = _detect_inner_or_raise(dec[:4100])
            first = False
        out.write(dec)
        done += len(chunk)
        if progress_cb:
            progress_cb(min(done, total), total)
    if first:
        raise UnlockError("NCM 音频数据为空")
    return info


def _decrypt_qmc_stream(f, out, progress_cb=None, ekey_text='',
                        cancel_check=None) -> dict:
    """QMC 流式解密：只读尾部定位密钥/边界，音频分块解密写出。
    三种流密码的 keystream 只依赖绝对偏移，天然支持分块。"""
    f.seek(0, 2)
    size = f.tell()
    tail_len = min(size, _QMC_TAIL_READ)
    f.seek(size - tail_len)
    tail = f.read(tail_len)
    audio_len, ekey, meta = _qmc_parse_footer(tail, size)
    ekey = _qmc_resolve_ekey(ekey, meta, ekey_text)
    real_key = _qmc_derive_key(ekey) if ekey else b''
    cipher = _qmc_cipher_for(real_key)

    info = {}
    if meta and meta.get('song_mid'):
        info['musicex_mid'] = meta['song_mid']
    f.seek(0)
    done = 0
    first = True
    while done < audio_len:
        if cancel_check is not None and cancel_check():
            raise UnlockCancelled()
        chunk = f.read(min(_STREAM_CHUNK, audio_len - done))
        if not chunk:
            raise UnlockError("文件读取不完整")
        buf = np.frombuffer(chunk, dtype=np.uint8).copy()  # 可写副本
        cipher.decrypt_into(buf, done)
        dec = buf.tobytes()
        if first:
            info['inner_fmt'] = _detect_inner_or_raise(dec[:4100])
            first = False
        out.write(dec)
        done += len(chunk)
        if progress_cb:
            progress_cb(min(done, audio_len), audio_len)
    if first:
        raise UnlockError("音频数据为空")
    return info


# ══════════════════════════════════════════════════════════════════
# 输出管线：解密 → 命名 → （可选）ffmpeg 打标签 / 转码
# ══════════════════════════════════════════════════════════════════

import re
import subprocess
import tempfile
import time

from core.ffmpeg_locator import find_ffmpeg, hidden_subprocess_kwargs
from core.tag_renamer import sanitize_filename

_MUX_TIMEOUT = 120  # 秒（基础值，实际按输入大小缩放，见 _mux_timeout_for）

LOSSY_FMTS = {'mp3', 'ogg', 'aac'}


def _base_name(src: str, info: dict) -> str:
    title, artist = info.get('title', ''), info.get('artist', '')
    if title and artist:
        name = f"{title} - {artist}"
    elif title:
        name = title
    else:
        # 剥加密/音频扩展段（mflac0、flac、ncm 等）
        parts = Path(src).name.split('.')
        drop = {e for e in QMC_EXTS} | {'ncm'} | \
            {v.lstrip('.') for v in _AUDIO_EXTS.values()}
        while len(parts) > 1 and parts[-1].lower() in drop:
            parts.pop()
        name = '.'.join(parts)
        # 剥 QQ 音乐下载名的音质标记尾缀（_EM/_EG/_EA 等）
        name = re.sub(r'_E[A-Z]$', '', name)
    return sanitize_filename(name) or Path(src).stem or "未命名"


def _unique_path(directory: Path, name: str, ext: str) -> Path:
    dst = directory / f"{name}{ext}"
    n = 1
    while dst.exists():
        dst = directory / f"{name} ({n}){ext}"
        n += 1
    return dst


def _mux_timeout_for(path: str) -> int:
    """按输入文件大小缩放转码超时：基础 120s，超出部分按 2MB/s 保守速率追加
    （300MB hi-res → ~150s；1GB → ~512s），慢盘/大文件不再误杀"""
    try:
        size = Path(path).stat().st_size
    except OSError:
        size = 0
    return max(_MUX_TIMEOUT, int(size / (2 << 20)))


def _run_ffmpeg(args: list, timeout: int = _MUX_TIMEOUT,
                cancel_check=None) -> str:
    """运行 ffmpeg，返回 'ok' / 'error' / 'timeout'。
    Popen 轮询：cancel_check 触发时立即 kill 子进程并抛 UnlockCancelled。"""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise UnlockError("未找到 ffmpeg（内置目录与系统 PATH 均不可用）")
    proc = subprocess.Popen(
        [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error'] + args,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **hidden_subprocess_kwargs())
    deadline = time.monotonic() + timeout
    while True:
        if cancel_check is not None and cancel_check():
            proc.kill()
            proc.wait()
            raise UnlockCancelled()
        ret = proc.poll()
        if ret is not None:
            return 'ok' if ret == 0 else 'error'
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait()
            return 'timeout'
        time.sleep(0.05)


def _write_meta_file(info: dict, work_dir: Path) -> str:
    """把标签写成 ffmetadata1 文件（UTF-8 落盘，绕开 Windows 命令行
    ANSI 编码导致的中文标签乱码）。无标签返回 ''。"""
    lines = [';FFMETADATA1']
    for key in ('title', 'artist', 'album'):
        v = (info.get(key) or '').strip()
        if not v:
            continue
        # ffmetadata1 转义规则：\ = ; # 与换行需反斜杠转义
        v = (v.replace('\\', '\\\\').replace('=', '\\=')
             .replace(';', '\\;').replace('#', '\\#')
             .replace('\n', '\\\n').replace('\r', ''))
        lines.append(f'{key}={v}')
    if len(lines) == 1:
        return ''
    fd, path = tempfile.mkstemp(suffix='.txt', prefix='unlock_meta_',
                                dir=str(work_dir))
    with open(fd, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return path


def _write_output(raw_path: str, info: dict, dst: Path, work_dir: Path,
                  need_transcode: str, cancel_check=None) -> None:
    """raw_path：解密裸流临时文件（本函数负责清理/接管）。
    need_transcode: ''=保持内层格式（仅打标签）/ 'flac' / 'wav'
    任何异常（含取消）都会清掉可能残留的半截 dst——dst 由 _unique_path
    保证调用前不存在，删除安全。"""
    try:
        _write_output_inner(raw_path, info, dst, work_dir, need_transcode,
                            cancel_check)
    except BaseException:
        try:
            Path(dst).unlink()
        except OSError:
            pass
        raise


def _write_output_inner(raw_path: str, info: dict, dst: Path,
                        work_dir: Path, need_transcode: str,
                        cancel_check=None) -> None:
    inner = info['inner_fmt']
    inner_ext = _AUDIO_EXTS.get(inner, '.bin')
    cover = info.get('cover') or b''
    can_cover = (not need_transcode) and bool(cover) and \
        inner in ('flac', 'mp3')

    # 给裸流正确的扩展名，帮助 ffmpeg 探测格式
    proper = Path(raw_path).with_suffix(inner_ext)
    try:
        Path(raw_path).replace(proper)
        raw_path = str(proper)
    except OSError:
        pass

    meta_path = _write_meta_file(info, work_dir)
    if not need_transcode and not meta_path and not can_cover:
        Path(raw_path).replace(dst)  # 无需任何处理：直接落盘
        return

    # ffmpeg 标签统一走 ffmetadata 文件输入（-map_metadata），中文安全
    meta_in = ['-i', meta_path] if meta_path else []
    meta_map = ['-map_metadata', '1' if meta_path else '0'] if meta_path else []

    cover_path = ''
    try:
        if can_cover:
            fd2, cover_path = tempfile.mkstemp(suffix='.jpg',
                                               prefix='unlock_cover_',
                                               dir=str(work_dir))
            with open(fd2, 'wb') as f:
                f.write(cover)

        if need_transcode:
            codec = ['-c:a', 'flac'] if need_transcode == 'flac' \
                else ['-c:a', 'pcm_s24le']
            meta_idx = str(len(meta_in) // 2)  # 元数据输入的序号
            status = _run_ffmpeg(['-i', raw_path] + meta_in + codec
                                 + (['-map_metadata', meta_idx]
                                    if meta_in else [])
                                 + [str(dst)],
                                 timeout=_mux_timeout_for(raw_path),
                                 cancel_check=cancel_check)
            if status != 'ok':
                raise UnlockError(
                    "ffmpeg 转码超时（文件过大或磁盘过慢），请重试"
                    if status == 'timeout' else "ffmpeg 转码失败")
            return

        # 保持格式：-c copy 打标签（flac/mp3 顺带内嵌封面）
        if can_cover:
            inputs = ['-i', raw_path, '-i', cover_path] + meta_in
            status = _run_ffmpeg(inputs + ['-map', '0:a', '-map', '1:v',
                                           '-c', 'copy',
                                           '-disposition:v:0', 'attached_pic']
                                 + (['-map_metadata', '2'] if meta_in else [])
                                 + [str(dst)],
                                 timeout=_mux_timeout_for(raw_path),
                                 cancel_check=cancel_check)
            if status == 'ok':
                return
            try:  # 失败（非取消）时清掉半截 dst，走保底 rename
                Path(dst).unlink()
            except OSError:
                pass
        if meta_in:
            status = _run_ffmpeg(['-i', raw_path] + meta_in + ['-c', 'copy']
                                 + meta_map + [str(dst)],
                                 timeout=_mux_timeout_for(raw_path),
                                 cancel_check=cancel_check)
            if status == 'ok':
                return
            try:
                Path(dst).unlink()
            except OSError:
                pass
        Path(raw_path).replace(dst)  # 打标签失败：保底输出无标签文件
        raw_path = ''
    finally:
        for p in (raw_path, cover_path, meta_path):
            if p:
                try:
                    Path(p).unlink()
                except OSError:
                    pass


def unlock_file(src: str, out_dir: str, target: str = 'auto',
                fetch_tags: bool = False, progress_cb=None,
                cancel_check=None) -> UnlockResult:
    """解锁单个加密音频（流式：大文件内存占用恒定，约一个 8MB 分块）。

    target: 'auto'（保留内层格式）/ 'flac' / 'wav'。
    fetch_tags: QQ musicex 文件解锁后联网补齐歌名/歌手/专辑/封面
                （需要已导入过密钥缓存登录态；失败自动降级为无标签）。
    progress_cb(done_bytes, total_bytes)：音频解密进度回调（可选）。
    cancel_check() 返回 True 时中断解密/转码并抛 UnlockCancelled
                （不算失败，调用方静默收尾；临时文件与半截输出已清理）。
    源文件只读不动；输出到 out_dir（自动建目录、撞名加序号）。
    """
    src_path = Path(src)
    result = UnlockResult(ok=False, src=str(src_path))
    raw_path = ''
    try:
        enc_type = detect_encrypted_type(src_path)
        if not enc_type:
            raise UnlockError("无法识别的加密格式（仅支持 .ncm 与 QQ音乐 QMC 系）")
        result.enc_fmt = enc_type

        out_root = Path(out_dir)
        out_root.mkdir(parents=True, exist_ok=True)

        # 流式解密到裸流临时文件（位于输出目录，保证同盘 rename）
        fd, raw_path = tempfile.mkstemp(suffix='.bin', prefix='unlock_',
                                        dir=str(out_root))
        try:
            with open(fd, 'wb') as out:
                with open(src_path, 'rb') as fin:
                    if enc_type == 'ncm':
                        info = _decrypt_ncm_stream(fin, out, progress_cb,
                                                   cancel_check)
                    else:
                        info = _decrypt_qmc_stream(fin, out, progress_cb,
                                                   cancel_check=cancel_check)
        except BaseException:
            try:
                Path(raw_path).unlink()
            except OSError:
                pass
            raw_path = ''
            raise

        # QQ musicex：联网补标签（失败静默降级，不影响解锁）
        if cancel_check is not None and cancel_check():
            raise UnlockCancelled()
        if fetch_tags and info.get('musicex_mid') and not info.get('title'):
            extra = _tag_fetcher(info['musicex_mid'])
            for k in ('title', 'artist', 'album', 'cover'):
                if extra.get(k):
                    info[k] = extra[k]
        inner = info['inner_fmt']
        result.inner_fmt = inner
        result.title = info.get('title', '')
        result.artist = info.get('artist', '')

        if target not in ('auto', 'flac', 'wav'):
            target = 'auto'
        need_transcode = '' if target == 'auto' or target == inner else target
        if need_transcode and inner in LOSSY_FMTS:
            result.message = (f"内层为 {inner}（有损），转 {target.upper()} "
                              f"只是换容器，音质不变")

        ext = _AUDIO_EXTS.get(inner, '.bin') if not need_transcode \
            else f'.{target}'
        dst = _unique_path(out_root, _base_name(str(src_path), info), ext)
        _write_output(raw_path, info, dst, out_root, need_transcode,
                      cancel_check)
        raw_path = ''  # _write_output 已接管清理

        result.ok = True
        result.dst = str(dst)
        return result
    except UnlockCancelled:
        raise  # 取消不是失败：清理完临时文件后继续上抛
    except UnlockError as exc:
        result.message = str(exc)
        return result
    except OSError as exc:
        result.message = f"文件读写失败: {exc}"
        return result
    finally:
        if raw_path:  # 异常路径兜底，杜绝临时文件泄漏
            try:
                Path(raw_path).unlink()
            except OSError:
                pass
