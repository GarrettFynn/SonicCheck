#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ音乐 musicex 密钥获取：从运行中的 Windows 客户端提取登录态，
调官方 CgiGetVkey 接口换取每首歌的 ekey，并持久化到本地密钥库。

内存扫描与接口参数移植自 qmdec（MIT，github.com/Sophomoresty/qmdec）：
- cookie：QQMusic.exe 进程内存中查找 "qqmusic_key=" 标记（Win32 只读扫描）
- musicex 尾部：末 8 字节 magic "musicex\\0"；-16 处 LE u32 为尾块长度；
  尾块内 [28:88] UTF-16-LE 为 song_mid，[88:184] UTF-16-LE 为客户端文件名
- ekey 接口：POST https://u.y.qq.com/cgi-bin/musicu.fcg
  （module=vkey.GetVkeyServer, method=CgiGetVkey）

安全与免责：
- 全程只读扫描进程内存，不注入、不修改客户端；
- cookie 与 ekey 仅保存在本机 ~/.soniccheck/qqmusic_ekeys.json，不上传；
- 仅为用户自己账号已合法下载的文件取回解密密钥，请勿用于侵权行为。
"""

import json
import struct
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

MUSICEX_MAGIC = b'musicex\x00'
_COOKIE_MARKER = b'qqmusic_key='

STORE_PATH = Path.home() / '.soniccheck' / 'qqmusic_ekeys.json'

_API_URL = 'https://u.y.qq.com/cgi-bin/musicu.fcg'
_API_TIMEOUT = 15


# ══════════════════════════════════════════════════════════════════
# Windows DPAPI：登录 cookie 落盘加密（仅当前 Windows 用户可解）
# ══════════════════════════════════════════════════════════════════

_DPAPI_PREFIX = 'dpapi:'


def _dpapi_crypt(data: bytes, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData；非 Windows 或失败抛 OSError"""
    if sys.platform != 'win32':
        raise OSError('DPAPI 仅支持 Windows')
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', ctypes.wintypes.DWORD),
                    ('pbData', ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), buf)
    blob_out = DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0,
              ctypes.byref(blob_out)):
        raise OSError('DPAPI 调用失败')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _encrypt_cookie(cookie: str):
    """cookie → 落盘字符串。DPAPI 可用返回 'dpapi:...' 前缀密文。

    加密失败返回 None：cookie 等价于账号登录凭据，宁可不入库也绝不明文
    落盘（B3：旧实现此处静默返回原文，DPAPI 不可用时登录态会被明文
    写进 ~/.soniccheck/ 且用户毫不知情）。"""
    if not cookie:
        return ''
    try:
        import base64
        enc = _dpapi_crypt(cookie.encode('utf-8'), protect=True)
        return _DPAPI_PREFIX + base64.b64encode(enc).decode('ascii')
    except OSError:
        return None


def _decrypt_cookie(stored: str) -> str:
    """落盘字符串 → cookie；DPAPI 解密失败（换机器/换用户）按无登录态处理"""
    if not stored:
        return ''
    if not stored.startswith(_DPAPI_PREFIX):
        return stored
    try:
        import base64
        raw = base64.b64decode(stored[len(_DPAPI_PREFIX):])
        return _dpapi_crypt(raw, protect=False).decode('utf-8')
    except (OSError, ValueError):
        return ''


class KeyFetchError(Exception):
    """用户可读的密钥获取失败原因"""


# ══════════════════════════════════════════════════════════════════
# musicex 尾部解析
# ══════════════════════════════════════════════════════════════════

def parse_musicex_info(path) -> dict:
    """解析 musicex 文件尾部 → {'song_mid','filename','audio_size'}；
    不是 musicex 或结构非法时返回 {}。只读文件末尾几 KB。"""
    try:
        p = Path(path)
        fsize = p.stat().st_size
        if fsize < 200:
            return {}
        with open(p, 'rb') as f:
            f.seek(-8, 2)
            if f.read(8) != MUSICEX_MAGIC:
                return {}
            f.seek(-16, 2)
            tail_size = struct.unpack('<I', f.read(4))[0]
            if not (184 <= tail_size <= 4096) or 16 + tail_size > fsize:
                return {}
            f.seek(-(16 + tail_size), 2)
            tail = f.read(tail_size)
        if len(tail) < 184:
            return {}
        song_mid = tail[28:88].decode('utf-16-le', errors='ignore')
        filename = tail[88:184].decode('utf-16-le', errors='ignore')
        return {
            'song_mid': song_mid.rstrip('\x00'),
            'filename': filename.rstrip('\x00'),
            'audio_size': fsize - 16 - tail_size,
        }
    except OSError:
        return {}


def parse_musicex_info_from_bytes(data: bytes, total_size: int = 0) -> dict:
    """内存版 musicex 尾部解析（供 unlock.py 使用），结构同文件版。
    data 为文件末尾若干字节（至少 16+184）；total_size 为文件真实总大小
    （缺省取 len(data)，即传入全文时）。"""
    n = len(data)
    size = total_size or n
    if n < 200 or data[-8:] != MUSICEX_MAGIC:
        return {}
    tail_size = struct.unpack_from('<I', data, n - 16)[0]
    if not (184 <= tail_size <= 4096) or 16 + tail_size > size \
            or n < 16 + tail_size:
        return {}
    tail = data[n - 16 - tail_size:n - 16]
    song_mid = tail[28:88].decode('utf-16-le', errors='ignore')
    filename = tail[88:184].decode('utf-16-le', errors='ignore')
    return {
        'song_mid': song_mid.rstrip('\x00'),
        'filename': filename.rstrip('\x00'),
        'audio_size': size - 16 - tail_size,
    }


# ══════════════════════════════════════════════════════════════════
# 本地密钥库（JSON 持久化）
# ══════════════════════════════════════════════════════════════════

class EkeyStore:
    """ekey 与登录态的本地持久化。ekey 按 song_mid 与文件名双键索引。

    并发语义（B2）：并行解锁时各 worker 持有独立实例，save() 在模块锁
    内先重读磁盘再合并写入（read-modify-write 收敛），否则两个实例
    互相整体覆盖会静默丢掉对方刚存入的 ekey。"""

    _IO_LOCK = threading.Lock()   # 跨实例串行化合并落盘

    def __init__(self, path=None):
        self.path = Path(path) if path else STORE_PATH
        self._ekeys: dict = {}
        self._cookie = ''
        self._uin = ''
        self.cookie_dropped = False  # 最近一次 save 因无法加密而未入库 cookie
        self.save_failed = False     # 最近一次 save 写盘失败
        self._load()

    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding='utf-8'))
            self._ekeys = dict(d.get('ekeys') or {})
            self._cookie = _decrypt_cookie(d.get('cookie') or '')
            self._uin = d.get('uin') or ''
        except (OSError, ValueError):
            pass

    def save(self) -> bool:
        """合并落盘（并发安全），返回写盘是否成功。

        - 磁盘键先并入再写（read-modify-write 收敛），并行实例互不覆盖
        - cookie 加密失败（DPAPI 不可用）时整段不入库，置 cookie_dropped
          供调用方提示"登录态仅本次有效"；绝不明文落盘
        - 写盘失败置 save_failed 并返回 False，不再静默吞掉（旧实现
          except OSError: pass 会让"密钥已缓存"的假象在重启后落空）
        """
        with self._IO_LOCK:
            # 合并落盘：磁盘上的键（可能是其他实例刚写入的）先并入，
            # 本实例新增的键优先，避免 last-writer-wins 覆盖丢数据
            try:
                disk = json.loads(self.path.read_text(encoding='utf-8'))
                merged = dict(disk.get('ekeys') or {})
            except (OSError, ValueError):
                merged = {}
            merged.update(self._ekeys)
            self._ekeys = merged
            cookie_field = _encrypt_cookie(self._cookie)
            self.cookie_dropped = (cookie_field is None
                                   and bool(self._cookie))
            payload = {'ekeys': self._ekeys, 'uin': self._uin}
            if cookie_field is not None:
                payload['cookie'] = cookie_field
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix('.tmp')
                tmp.write_text(json.dumps(payload, ensure_ascii=False,
                                          indent=1), encoding='utf-8')
                tmp.replace(self.path)
                self.save_failed = False
                return True
            except OSError:
                self.save_failed = True
                return False

    # ── ekey ──
    @staticmethod
    def _keys_of(song_mid: str = '', filename: str = ''):
        if song_mid:
            yield 'mid:' + song_mid
        if filename:
            yield 'file:' + filename.lower()

    def get(self, song_mid: str = '', filename: str = '') -> str:
        for k in self._keys_of(song_mid, filename):
            v = self._ekeys.get(k)
            if v:
                return v
        return ''

    def put(self, ekey: str, song_mid: str = '', filename: str = '') -> None:
        for k in self._keys_of(song_mid, filename):
            self._ekeys[k] = ekey

    def count(self) -> int:
        # mid:/file: 双键指向同一 ekey，按值去重计数
        return len(set(self._ekeys.values()))

    # ── 登录态 ──
    def set_auth(self, cookie: str, uin: str) -> None:
        self._cookie, self._uin = cookie, uin

    def get_auth(self):
        return self._cookie, self._uin

    def clear_auth(self) -> None:
        self._cookie = self._uin = ''


# ══════════════════════════════════════════════════════════════════
# 从运行中的 QQMusic.exe 提取登录 cookie（Win32 只读内存扫描）
# ══════════════════════════════════════════════════════════════════

def _cookie_from_chunk(buf, length: int):
    """在已读缓冲（c_char 数组，有效长度 length）里找登录 cookie。

    返回 {'cookie','uin'} 或 None。用 buf.raw 零拷贝视图查找，替代旧的
    bytes(buf[:n]) 整块复制（50MB 窗口时省一次全量拷贝）。"""
    raw = buf.raw
    pos = raw.find(_COOKIE_MARKER, 0, length)
    if pos < 0:
        return None
    seg = raw[pos:min(pos + 512, length)]
    cookie = seg.split(b'\x00')[0].decode('utf-8', errors='ignore').strip()
    for sep in ('\n', '\r'):
        if sep in cookie:
            cookie = cookie.split(sep)[0]
    import re
    m = re.search(r'qqmusic_uin=(\d+)', cookie)
    if m:
        return {'cookie': cookie, 'uin': m.group(1)}
    return None


def extract_cookie_from_process() -> dict:
    """扫描 QQMusic.exe 进程内存提取 cookie。
    返回 {'ok': bool, 'cookie': str, 'uin': str, 'error': str}"""
    if sys.platform != 'win32':
        return {'ok': False, 'error': '仅支持 Windows 平台'}

    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi

    # B3：显式声明签名。不声明时 ctypes 默认 restype=c_int，64 位进程上
    # HANDLE 是 64 位值，高位被截断属于依赖未文档化行为的隐患
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    kernel32.VirtualQueryEx.argtypes = [wt.HANDLE, wt.LPCVOID,
                                        wt.LPCVOID, ctypes.c_size_t]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.ReadProcessMemory.argtypes = [wt.HANDLE, wt.LPCVOID, wt.LPVOID,
                                           ctypes.c_size_t,
                                           ctypes.POINTER(ctypes.c_size_t)]
    kernel32.ReadProcessMemory.restype = wt.BOOL
    psapi.EnumProcesses.argtypes = [ctypes.POINTER(wt.DWORD), wt.DWORD,
                                    ctypes.POINTER(wt.DWORD)]
    psapi.EnumProcesses.restype = wt.BOOL
    psapi.GetModuleBaseNameA.argtypes = [wt.HANDLE, wt.HMODULE,
                                         wt.LPSTR, wt.DWORD]
    psapi.GetModuleBaseNameA.restype = wt.DWORD

    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400
    MAX_PATH = 260

    enum_buf = (wt.DWORD * 4096)()
    cb_needed = wt.DWORD()
    # 数组实参直接传（argtype=POINTER(DWORD) 时 byref(数组) 反而不被接受）
    psapi.EnumProcesses(enum_buf, ctypes.sizeof(enum_buf),
                        ctypes.byref(cb_needed))
    num_procs = cb_needed.value // ctypes.sizeof(wt.DWORD)
    pid = None
    for i in range(num_procs):
        candidate = enum_buf[i]
        if not candidate:
            continue
        h = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, candidate)
        if not h:
            continue
        try:
            name_buf = (ctypes.c_char * MAX_PATH)()
            if psapi.GetModuleBaseNameA(h, None, name_buf, MAX_PATH):
                if name_buf.value == b'QQMusic.exe':
                    pid = candidate
                    break
        finally:
            kernel32.CloseHandle(h)

    if pid is None:
        return {'ok': False,
                'error': '未检测到运行中的 QQ 音乐客户端，请先打开并登录'}

    h_proc = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h_proc:
        return {'ok': False,
                'error': '无法读取 QQ 音乐进程，请尝试以管理员身份运行本软件'}

    try:
        class MBI(ctypes.Structure):
            _fields_ = [
                ('BaseAddress', ctypes.c_void_p),
                ('AllocationBase', ctypes.c_void_p),
                ('AllocationProtect', wt.DWORD),
                ('RegionSize', ctypes.c_size_t),
                ('State', wt.DWORD),
                ('Protect', wt.DWORD),
                ('Type', wt.DWORD),
            ]

        MEM_COMMIT = 0x1000
        READABLE = {0x02, 0x04, 0x06, 0x20, 0x40, 0x60, 0x80}
        CHUNK = 64 * 1024 * 1024   # 大区域分窗读取粒度
        OVERLAP = 1024             # 窗口重叠：标记/凭据跨窗边界时不漏检
        mbi = MBI()
        addr = 0
        bytes_read = ctypes.c_size_t()
        buf = None
        while addr < 0x7FFFFFFFFFFFFFFF:
            if kernel32.VirtualQueryEx(h_proc, ctypes.c_void_p(addr),
                                       ctypes.byref(mbi),
                                       ctypes.sizeof(mbi)) == 0:
                break
            region_size = mbi.RegionSize or 0
            base_addr = mbi.BaseAddress or 0
            if region_size == 0:
                break
            if (mbi.State == MEM_COMMIT and mbi.Protect in READABLE
                    and region_size >= 0x1000):
                # B3：不再设 50MB 区域上限——QQ 音乐是 CEF/Electron 系
                # 客户端，cookie 常落在数百 MB 的 V8 大堆里，旧上限会
                # 直接跳过导致偶发"未找到登录信息"；大区域分窗读取
                if buf is None or len(buf) < CHUNK + OVERLAP:
                    buf = (ctypes.c_char * (CHUNK + OVERLAP))()
                for w_off in range(0, region_size, CHUNK):
                    want = min(CHUNK + OVERLAP, region_size - w_off)
                    if kernel32.ReadProcessMemory(
                            h_proc, ctypes.c_void_p(base_addr + w_off),
                            buf, want, ctypes.byref(bytes_read)):
                        got = bytes_read.value
                        if got > 0:
                            found = _cookie_from_chunk(buf, got)
                            if found:
                                return {'ok': True, 'error': '',
                                        **found}
            nxt = base_addr + region_size
            if nxt <= addr:
                break
            addr = nxt
        return {'ok': False,
                'error': '未在 QQ 音乐进程内找到登录信息，请确认客户端已登录'}
    finally:
        kernel32.CloseHandle(h_proc)


# ══════════════════════════════════════════════════════════════════
# CgiGetVkey 接口取 ekey
# ══════════════════════════════════════════════════════════════════

def fetch_ekey(song_mid: str, filename: str, cookie: str, uin: str) -> str:
    """按 song_mid + 客户端文件名向官方接口换取 ekey。失败抛 KeyFetchError。

    filename 必须是文件尾部记录的客户端原始文件名（自带正确扩展名，
    如 AIM0xxx.mflac / F0M0xxx.mflac / 8xxx.mgg），原样传给接口——
    不同音质前缀（F0M0/AIM0/Q000…）对应不同扩展名，不能自行改写。"""
    if not song_mid or not filename:
        raise KeyFetchError('文件尾部缺少歌曲标识，无法换取密钥')
    file_name = filename
    if not file_name.lower().endswith(('.mflac', '.mgg')):
        # 兜底：尾部文件名缺扩展名时按前缀惯例补全
        file_name += '.mflac' if file_name.startswith('F0') else '.mgg'

    body = {
        'comm': {
            'cv': 4747474, 'ct': 24, 'format': 'json',
            'inCharset': 'utf-8', 'outCharset': 'utf-8',
            'notice': 0, 'platform': 'yqq.json', 'needNewCode': 1,
            'uin': int(uin) if uin.isdigit() else 0,
            'g_tk_new_20200303': 5381, 'g_tk': 5381,
        },
        'req_1': {
            'module': 'vkey.GetVkeyServer',
            'method': 'CgiGetVkey',
            'param': {
                'filename': [file_name],
                'guid': '10000',
                'songmid': [song_mid],
                'songtype': [0],
                'uin': uin,
                'loginflag': 1,
                'platform': '20',
            },
        },
    }
    req = urllib.request.Request(_API_URL, json.dumps(body).encode(),
                                 method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Cookie', cookie)
    req.add_header('User-Agent', 'QQMusic/21')
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            result = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise KeyFetchError(f'网络请求失败: {exc}')

    data = (result.get('req_1') or {}).get('data') or {}
    infos = data.get('midurlinfo') or []
    if infos:
        ekey = infos[0].get('ekey') or ''
        if ekey:
            return ekey
    code = (result.get('req_1') or {}).get('code')
    raise KeyFetchError(f'接口未返回密钥（code={code}），'
                        f'可能是登录态失效或账号无此歌权限')


# ══════════════════════════════════════════════════════════════════
# 歌曲信息补全（按 song_mid 搜索接口 + 封面图床）
# ══════════════════════════════════════════════════════════════════

def _fetch_cover(album_mid: str) -> bytes:
    """按 album_mid 从 QQ 音乐图床取 500x500 封面（无需登录）"""
    if not album_mid:
        return b''
    url = (f'https://y.gtimg.cn/music/photo_new/'
           f'T002R500x500M000{album_mid}.jpg')
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Mozilla/5.0')
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            data = resp.read()
        return data if len(data) > 1000 else b''
    except (urllib.error.URLError, TimeoutError, OSError):
        return b''


def fetch_track_info(song_mid: str, cookie: str = '', uin: str = '') -> dict:
    """按 song_mid 取歌曲信息 → {'title','artist','album','cover'}。

    走 get_song_detail 详情接口（按 mid 精确返回；搜索接口把 mid 当
    关键词、可能返回错歌，不可用）。登录态缺省时自动读本地密钥库缓存；
    任何失败（断网/无权限/未找到）都返回 {}，调用方按"无标签"降级处理。"""
    if not song_mid:
        return {}
    if not (cookie and uin):
        cookie, uin = EkeyStore().get_auth()
    body = {
        'comm': {'cv': 4747474, 'ct': 24, 'format': 'json',
                 'inCharset': 'utf-8', 'outCharset': 'utf-8',
                 'notice': 0, 'platform': 'yqq.json', 'needNewCode': 1,
                 'uin': int(uin) if uin.isdigit() else 0, 'g_tk': 5381},
        'req_1': {
            'module': 'music.pf_song_detail_svr',
            'method': 'get_song_detail',
            'param': {'song_mid': song_mid},
        },
    }
    req = urllib.request.Request(_API_URL, json.dumps(body).encode(),
                                 method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', 'QQMusic/21')
    if cookie:
        req.add_header('Cookie', cookie)
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            result = json.loads(resp.read())
        song = ((result.get('req_1') or {}).get('data') or {}) \
            .get('track_info') or {}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {}

    if not song or (song.get('mid') and song.get('mid') != song_mid):
        return {}
    singers = [x.get('title') or x.get('name') or ''
               for x in song.get('singer', [])]
    album = song.get('album') or {}
    return {
        'title': song.get('title') or song.get('name') or '',
        'artist': ' / '.join(s for s in singers if s),
        'album': album.get('title') or album.get('name') or '',
        'cover': _fetch_cover(album.get('mid', '')),
    }


# ══════════════════════════════════════════════════════════════════
# 高层流程：为一批 musicex 文件确保本地有密钥
# ══════════════════════════════════════════════════════════════════

def ensure_ekeys(paths, store: 'EkeyStore | None' = None,
                 progress=None, cancel_check=None) -> dict:
    """为 paths 中的 musicex 文件补齐本地密钥库。

    流程：解析尾部 → 跳过已有密钥的 → 取登录态（缓存优先，失效则从
    客户端内存重新提取并重试一轮）→ 逐首换取 ekey 并落盘。

    progress(i, total, name) 可选回调；cancel_check() 返回 True 时在
    下一首开始前停止（已获取的密钥照常落盘，结果带 'cancelled': True）。
    返回统计 dict：
    {'total', 'cached', 'fetched', 'failed': [(path, reason)], 'auth_ok',
     'cancelled'}
    """
    store = store or EkeyStore()
    cancelled = False

    def _cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    pending = []
    cached = 0
    for p in paths:
        info = parse_musicex_info(p)
        if not info:
            continue
        if store.get(info['song_mid'], info['filename']):
            cached += 1
        else:
            pending.append((p, info))

    result = {'total': cached + len(pending), 'cached': cached,
              'fetched': 0, 'failed': [], 'auth_ok': bool(pending),
              'cancelled': False}

    if not pending:
        return result

    cookie, uin = store.get_auth()
    if not (cookie and uin):
        if _cancelled():
            result['cancelled'] = True
            return result
        auth = extract_cookie_from_process()
        if not auth['ok']:
            result['auth_ok'] = False
            result['failed'] = [(p, auth['error']) for p, _ in pending]
            return result
        cookie, uin = auth['cookie'], auth['uin']
        store.set_auth(cookie, uin)

    retried = False
    idx = 0
    while idx < len(pending):
        if _cancelled():
            cancelled = True
            break
        p, info = pending[idx]
        if progress:
            progress(idx, len(pending), Path(p).name)
        try:
            ekey = fetch_ekey(info['song_mid'], info['filename'],
                              cookie, uin)
            store.put(ekey, info['song_mid'], info['filename'])
            result['fetched'] += 1
        except KeyFetchError as exc:
            result['failed'].append((p, str(exc)))
        idx += 1
        # 一轮结束：若有失败且尚未重试，重新提取登录态再试一轮
        if idx == len(pending) and result['failed'] and not retried:
            if _cancelled():
                cancelled = True
                break
            retried = True
            auth = extract_cookie_from_process()
            if auth['ok'] and auth['cookie'] != cookie:
                cookie, uin = auth['cookie'], auth['uin']
                store.set_auth(cookie, uin)
                failed_paths = {fp for fp, _ in result['failed']}
                pending = [(fp, fi) for fp, fi in pending
                           if fp in failed_paths]
                result['failed'] = []
                idx = 0

    result['cancelled'] = cancelled
    ok_save = store.save()
    # 保存状态供 UI 提示（B3）：写盘失败/登录态无法加密不再静默
    result['save_error'] = '' if ok_save else (
        '密钥库写入失败（磁盘或权限问题），已获取的密钥仅在本批次有效')
    result['cookie_dropped'] = bool(store.cookie_dropped)
    return result
