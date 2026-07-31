#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音频分析引擎（numpy 向量化版）

迁移自 audio_compare_多进程并行版.py，判定口径与评分公式逐字保留：
- 假无损三规则：断崖且 <20kHz；-60dB 截止 <16kHz；24bit 且 DR<4
- 评分六维：位深 15 / 采样率 10 / -60dB 截止 25 / DR 25 / 立体声相关 15 / 峰值 10；假无损 -20
- 断崖：15kHz–0.95Nyquist 内相邻窗落差 >30dB 且断后 50 bin 均值 <-70dB

与旧脚本的差异（已与用户对齐）：
1. 所有格式统一 ffmpeg 转码 pcm_s32le/48kHz（含 .wav），修复 WAV 频率轴 bug（决策 2）
2. 纯 Python 循环全部 numpy 向量化（决策 3，数值口径不变）
3. ffmpeg 转码加 60s 超时；子进程隐藏控制台窗口（⑦-6）
4. 砍掉 torch / scipy / 纯 Python 后端，仅保留 numpy（技术栈已锁定）
5. print 改为返回值/异常，由上层接入 GUI 日志

防坑备忘（AGENTS.md）：
- numpy 数组禁止直接当布尔值：一律用 len(...) == 0 / len(...) > 0 判断
- subprocess 一律传绝对路径 + 参数列表（中文/特殊符号路径安全）
- ffprobe FLAC 位深度 bits_per_sample == 0 时回退 bits_per_raw_sample
"""

import json
import math
import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from core.ffmpeg_locator import (find_ffmpeg, find_ffprobe,
                                 hidden_subprocess_kwargs)

# 统一转码规格：pcm_s32le / 48kHz（频率轴恒为 48k，与旧脚本非 WAV 路径一致）
TARGET_SAMPLE_RATE = 48000
FFPROBE_TIMEOUT = 10   # 秒（与旧脚本一致）
FFMPEG_TIMEOUT = 60    # 秒（旧脚本无超时，坏文件会挂死 worker，迁移时新增）
EPS = 1e-10            # 与旧脚本相同的 log 防零项

AUDIO_EXTS = {'.flac', '.wav', '.mp3', '.m4a', '.ogg',
              '.ape', '.wma', '.aac', '.opus'}


class ScanCancelled(Exception):
    """协作式取消：worker 在阶段边界或转码轮询中检测到取消标志时抛出"""


class AudioAnalyzer:
    """单文件分析器：构造即完成 probe → 转码 → 加载，analyze() 出结果"""

    def __init__(self, filepath, analyze_seconds: int = 30, cancel_check=None):
        # 坑2：中文+特殊符号路径，一律转绝对路径
        self.original_path = str(Path(filepath).resolve())
        self.analyze_seconds = int(analyze_seconds)
        self._cancel_check = cancel_check  # 可选：返回 True 表示请求取消
        self.wav_path = ""
        self.raw_meta = {}
        self.framerate = TARGET_SAMPLE_RATE
        self.nchannels = 0
        self.channels = []        # list[np.ndarray(float64)]，按声道去交错
        self._spectrum = None     # (freqs, db) 缓存，旧脚本重复算 4 次，这里算 1 次

        self._check_cancel()
        self._probe_original()
        self._check_cancel()
        self._transcode()
        self._check_cancel()
        self._load_wav()

    # ---------------- 基础设施 ----------------
    def _check_cancel(self) -> None:
        if self._cancel_check is not None and self._cancel_check():
            raise ScanCancelled()

    @staticmethod
    def is_audio_file(path) -> bool:
        return Path(path).suffix.lower() in AUDIO_EXTS

    @staticmethod
    def temp_dir() -> Path:
        """临时 WAV 目录：%TEMP%\\AudioQualityScanner\\（⑦-7）"""
        d = Path(tempfile.gettempdir()) / "AudioQualityScanner"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def sweep_temp_dir(max_age_seconds: int = 3600) -> int:
        """清理异常退出残留的临时 WAV（默认清 1 小时前的），返回清理数量"""
        removed = 0
        try:
            d = AudioAnalyzer.temp_dir()
            now = time.time()
            for f in d.glob("aqs_*.wav"):
                try:
                    if now - f.stat().st_mtime > max_age_seconds:
                        f.unlink()
                        removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        return removed

    # ---------------- 阶段 1：ffprobe 元数据 ----------------
    @staticmethod
    def _read_tag(tags: dict, key: str) -> str:
        """容器标签大小写不敏感读取（TITLE/Title/title 等）"""
        for k, v in (tags or {}).items():
            if k.lower() == key:
                return str(v).strip()
        return ""

    def _probe_original(self) -> None:
        ffprobe = find_ffprobe()
        if not ffprobe:
            raise RuntimeError("未找到 ffprobe（内置目录与系统 PATH 均不可用）")
        try:
            result = subprocess.run(
                [ffprobe, '-v', 'quiet', '-print_format', 'json',
                 '-show_streams', '-show_format', self.original_path],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=FFPROBE_TIMEOUT,
                **hidden_subprocess_kwargs()
            )
            if result.returncode == 0 and result.stdout:
                info = json.loads(result.stdout)
                s = info.get('streams', [{}])[0]
                fmt = info.get('format', {})
                bps = s.get('bits_per_sample', 0)
                if bps == 0:
                    # 坑3：FLAC 位深度 fallback（保留旧脚本逻辑）
                    bps = s.get('bits_per_raw_sample', 0)
                # M4.5：读取歌名/歌手标签（去重分组与标签改名依赖）
                # 优先 format.tags，缺失时回退 stream.tags
                fmt_tags = fmt.get('tags', {}) or {}
                st_tags = s.get('tags', {}) or {}
                title = (self._read_tag(fmt_tags, 'title')
                         or self._read_tag(st_tags, 'title'))
                artist = (self._read_tag(fmt_tags, 'artist')
                          or self._read_tag(st_tags, 'artist')
                          or self._read_tag(fmt_tags, 'album_artist'))
                self.raw_meta = {
                    'sample_rate': int(s.get('sample_rate', 0) or 0),
                    'channels': int(s.get('channels', 0) or 0),
                    'bit_depth': int(bps or 0),
                    'codec': s.get('codec_name', 'unknown'),
                    'bitrate': int(s.get('bit_rate', 0) or fmt.get('bit_rate', 0) or 0),
                    'duration': float(s.get('duration', 0) or fmt.get('duration', 0) or 0),
                    'title': title,
                    'artist': artist,
                }
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"ffprobe 解析超时({FFPROBE_TIMEOUT}s)")
        except json.JSONDecodeError:
            pass  # 与旧脚本一致：probe 失败不致命，后续用 wav 参数兜底

    # ---------------- 阶段 2：统一转码（决策 2，含 .wav） ----------------
    def _transcode(self) -> None:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg（内置目录与系统 PATH 均不可用）")
        fd, tmp_path = tempfile.mkstemp(suffix='.wav', prefix='aqs_',
                                        dir=str(self.temp_dir()))
        os.close(fd)
        cmd = [
            ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
            '-i', self.original_path,
            '-acodec', 'pcm_s32le', '-ar', str(TARGET_SAMPLE_RATE),
            '-t', str(self.analyze_seconds),
            tmp_path
        ]
        # Popen 轮询：转码期间也能响应取消（协作式取消，③-2）
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE,
                                **hidden_subprocess_kwargs())
        deadline = time.monotonic() + FFMPEG_TIMEOUT
        while True:
            self._check_cancel_or_kill(proc, tmp_path)
            ret = proc.poll()
            if ret is not None:
                break
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                self._silent_remove(tmp_path)
                raise RuntimeError(f"ffmpeg 转码超时({FFMPEG_TIMEOUT}s)")
            time.sleep(0.05)

        if ret != 0:
            err = proc.stderr.read().decode('utf-8', errors='replace') \
                if proc.stderr else ''
            self._silent_remove(tmp_path)
            raise RuntimeError(f"ffmpeg 转换失败: {err.strip()[:300]}")
        self.wav_path = tmp_path

    def _check_cancel_or_kill(self, proc, tmp_path: str) -> None:
        if self._cancel_check is not None and self._cancel_check():
            proc.kill()
            proc.wait()
            self._silent_remove(tmp_path)
            raise ScanCancelled()

    @staticmethod
    def _silent_remove(path: str) -> None:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    # ---------------- 阶段 3：numpy 向量化加载 ----------------
    def _load_wav(self) -> None:
        with wave.open(self.wav_path, 'rb') as w:
            self.nchannels = w.getnchannels()
            self.framerate = w.getframerate()
            width = w.getsampwidth()
            max_frames = min(w.getnframes(),
                             self.framerate * self.analyze_seconds)
            raw = w.readframes(max_frames)

        if width == 4:
            samples = np.frombuffer(raw, dtype='<i4').astype(np.float64) \
                / 2147483648.0
        elif width == 2:
            samples = np.frombuffer(raw, dtype='<i2').astype(np.float64) \
                / 32768.0
        elif width == 1:
            samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
                       - 128.0) / 128.0
        elif width == 3:
            # 统一转码后不会走到这里，保留兜底（与旧脚本 3 字节解析等价）
            b = np.frombuffer(raw, dtype=np.uint8)
            b = b[:len(b) // 3 * 3].reshape(-1, 3).astype(np.int32)
            vals = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            vals = np.where(vals & 0x800000, vals - 0x1000000, vals)
            samples = vals.astype(np.float64) / 8388608.0
        else:
            raise ValueError(f"不支持的位深度: {width * 8}bit")

        # 去交错分声道（替代旧脚本的列表推导）
        self.channels = [samples[ch::self.nchannels].copy()
                         for ch in range(self.nchannels)]

    # ---------------- 元数据 ----------------
    def get_metadata(self) -> dict:
        ext = os.path.splitext(self.original_path)[1].upper().lstrip('.')
        sr = self.raw_meta.get('sample_rate') or self.framerate
        bd = self.raw_meta.get('bit_depth', 0)
        ch = self.raw_meta.get('channels') or self.nchannels
        br = self.raw_meta.get('bitrate', 0)
        dur = self.raw_meta.get('duration', 0)
        return {
            'filename': os.path.basename(self.original_path),
            'format': ext,
            'codec': self.raw_meta.get('codec', ext.lower()),
            'sample_rate': sr,
            'bit_depth': bd,
            'channels': ch,
            'bitrate': br,
            'duration': dur,
            'title': self.raw_meta.get('title', ''),
            'artist': self.raw_meta.get('artist', ''),
        }

    # ---------------- 频谱（numpy 后端，与旧脚本数值口径一致） ----------------
    def compute_fft_spectrum(self):
        """返回 (freqs, db)。仅分析第 1 声道（与旧脚本一致）"""
        if self._spectrum is not None:
            return self._spectrum
        sig = self.channels[0]
        n = len(sig)
        if n < 1024:
            empty = np.array([], dtype=np.float64)
            self._spectrum = (empty, empty)
            return self._spectrum

        pad = 1 << (n - 1).bit_length()  # 补零到 2 的幂
        window = np.hanning(n)
        spectrum = np.fft.rfft(sig * window, pad)
        mag = np.abs(spectrum)
        freqs = np.fft.rfftfreq(pad, 1.0 / TARGET_SAMPLE_RATE)
        max_mag = float(np.max(mag))
        if max_mag <= 0:
            max_mag = 1.0
        db = 20 * np.log10(mag / max_mag + EPS)
        self._spectrum = (freqs, db)
        return self._spectrum

    def compute_fft_cutoff(self, threshold_db: float = -60) -> float:
        freqs, db = self.compute_fft_spectrum()
        if len(freqs) == 0:  # 坑1：numpy 数组不做布尔判断
            return 0
        mask = db > threshold_db
        if np.any(mask):
            return float(freqs[np.nonzero(mask)[0][-1]])
        return 0

    def compute_multi_cutoffs(self) -> dict:
        return {
            '-40dB': self.compute_fft_cutoff(-40),
            '-60dB': self.compute_fft_cutoff(-60),
            '-80dB': self.compute_fft_cutoff(-80),
        }

    def detect_spectral_cliff(self):
        """断崖检测（口径勿动）：15kHz–0.95Nyquist 相邻窗落差 >30dB
        且断后 50 bin 均值 <-70dB"""
        freqs, db = self.compute_fft_spectrum()
        if len(freqs) < 100:
            return False, 0
        nyq = self.framerate / 2
        search_start = 15000
        search_end = min(int(nyq * 0.95), float(freqs[-1]))

        start_idx = int(np.searchsorted(freqs, search_start))
        end_idx = int(np.searchsorted(freqs, search_end))
        if end_idx <= start_idx + 10:
            return False, 0

        # 向量化：before = mean(db[i-2:i])，after = mean(db[i+1:i+4])
        idx = np.arange(start_idx, end_idx - 5)
        if len(idx) == 0:
            return False, 0
        before = (db[idx - 2] + db[idx - 1]) / 2.0
        after = (db[idx + 1] + db[idx + 2] + db[idx + 3]) / 3.0
        drops = before - after
        j = int(np.argmax(drops))  # 首个最大值，与旧脚本严格大于循环一致
        max_drop = float(drops[j])
        cliff_freq = float(freqs[idx[j]])

        if max_drop > 30 and cliff_freq > 0:
            cliff_idx = int(np.searchsorted(freqs, cliff_freq))
            post = db[cliff_idx:min(cliff_idx + 50, len(db))]
            post_avg = float(np.mean(post)) if len(post) > 0 else 0.0
            if post_avg < -70:
                return True, cliff_freq
        return False, 0

    # ---------------- 时域指标 ----------------
    def compute_dynamic_range(self) -> float:
        """DR = 最响 5 个 3 秒块 RMS(dB) 均值的相反数（口径勿动）"""
        block_samples = int(self.framerate * 3)
        ch = self.channels[0]
        if block_samples <= 0 or len(ch) == 0:
            return 0

        n_blocks = len(ch) // block_samples
        rms_parts = []
        if n_blocks > 0:
            blocks = ch[:n_blocks * block_samples].reshape(n_blocks,
                                                           block_samples)
            rms_parts.append(np.sqrt(np.mean(blocks * blocks, axis=1)))
        tail = ch[n_blocks * block_samples:]
        if len(tail) > self.framerate:  # 旧脚本：尾块须长于 1 秒
            rms_parts.append(np.array([math.sqrt(
                float(np.mean(tail * tail)))]))
        if len(rms_parts) == 0:
            return 0

        rms = np.concatenate(rms_parts)
        rms = rms[rms > 0]
        if len(rms) < 2:  # 旧脚本：rms_list < 2 返回 0
            return 0
        rms_db = 20 * np.log10(rms)
        rms_db.sort()
        top = rms_db[::-1][:min(5, len(rms_db))]
        return -float(np.mean(top))

    def compute_stereo_correlation(self) -> float:
        if self.nchannels < 2:
            return 0
        L = self.channels[0]
        R = self.channels[1]
        n = min(len(L), len(R))
        if n == 0:
            return 0
        L = L[:n]
        R = R[:n]
        Lc = L - float(np.mean(L))
        Rc = R - float(np.mean(R))
        den_l = float(np.sum(Lc * Lc))
        den_r = float(np.sum(Rc * Rc))
        if den_l == 0 or den_r == 0:
            return 0
        return float(np.sum(Lc * Rc)) / math.sqrt(den_l * den_r)

    def compute_peak_level(self) -> float:
        peak = 0.0
        for ch in self.channels:
            if len(ch) > 0:
                peak = max(peak, float(np.max(np.abs(ch))))
        return 20 * math.log10(peak + EPS)

    # ---------------- 综合判定（口径勿动） ----------------
    def analyze(self) -> dict:
        meta = self.get_metadata()
        cutoffs = self.compute_multi_cutoffs()
        cliff, cliff_freq = self.detect_spectral_cliff()
        dr = self.compute_dynamic_range()
        corr = self.compute_stereo_correlation()
        peak = self.compute_peak_level()

        fake_lossless = False
        fake_reasons = []
        if cliff and cliff_freq < 20000:
            fake_lossless = True
            fake_reasons.append(f"频谱在 {cliff_freq:.0f}Hz 处断崖式截断")
        if cutoffs['-60dB'] < 16000:
            fake_lossless = True
            fake_reasons.append(f"-60dB截止仅 {cutoffs['-60dB']:.0f}Hz")
        if meta['bit_depth'] >= 24 and dr < 4:
            fake_lossless = True
            fake_reasons.append(f"24bit但DR仅{dr:.1f}dB，疑似有损源升频")

        return {
            'meta': meta,
            'cutoffs': cutoffs,
            'cliff': cliff,
            'cliff_freq': cliff_freq,
            'dr': dr,
            'correlation': corr,
            'peak': peak,
            'fake_lossless': fake_lossless,
            'fake_reasons': fake_reasons,
        }

    def cleanup(self) -> None:
        self._silent_remove(self.wav_path)
        self.wav_path = ""


def score_quality(result: dict) -> float:
    """六维评分（口径勿动，与旧脚本逐字一致）"""
    s = 0
    m = result['meta']
    cutoffs = result['cutoffs']
    if m['bit_depth'] >= 24:
        s += 15
    elif m['bit_depth'] >= 16:
        s += 10
    else:
        s += 5
    if m['sample_rate'] >= 96000:
        s += 10
    elif m['sample_rate'] >= 48000:
        s += 8
    else:
        s += 5
    cf = cutoffs['-60dB']
    if cf >= 20000:
        s += 25
    elif cf >= 18000:
        s += 20
    elif cf >= 16000:
        s += 15
    elif cf >= 12000:
        s += 10
    else:
        s += 5
    dr = result['dr']
    if dr >= 12:
        s += 25
    elif dr >= 8:
        s += 20
    elif dr >= 5:
        s += 15
    elif dr >= 3:
        s += 10
    else:
        s += 5
    corr = abs(result['correlation'])
    s += (1 - corr) * 15
    peak = result['peak']
    if peak > -1:
        s += 3
    elif peak > -3:
        s += 8
    else:
        s += 10
    if result['fake_lossless']:
        s = max(0, s - 20)
    return min(100, s)


def analyze_file(filepath, analyze_seconds: int = 30, cancel_check=None) -> dict:
    """对外门面：分析单文件，返回扁平结果字典（含评分），保证临时文件清理"""
    analyzer = AudioAnalyzer(filepath, analyze_seconds=analyze_seconds,
                             cancel_check=cancel_check)
    try:
        result = analyzer.analyze()
        result['score'] = score_quality(result)
        result['filepath'] = analyzer.original_path
        return result
    finally:
        analyzer.cleanup()
