#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同一首歌数据层面音质对比工具 - 极速版 v3
自动选择最快 FFT 后端：torch(GPU) > numpy > scipy > python
支持多进程并行扫描

用法:
  python audio_compare.py --scan "文件夹" --csv "结果.csv"
  python audio_compare.py --scan "文件夹" --workers 16
"""

import wave
import struct
import math
import os
import sys
import subprocess
import tempfile
import json
import csv
from pathlib import Path
from multiprocessing import Pool, cpu_count
from functools import partial

# ========== 自动选择 FFT 后端 ==========

HAS_NUMPY = False
HAS_SCIPY = False
HAS_TORCH = False
HAS_CUDA = False

# 检测 numpy
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    pass

# 检测 scipy
try:
    import scipy.fft
    HAS_SCIPY = True
except ImportError:
    pass

# 检测 torch
try:
    import torch
    HAS_TORCH = True
    HAS_CUDA = torch.cuda.is_available()
    if HAS_CUDA:
        _FFT_DEVICE = torch.device('cuda')
        _FFT_DTYPE = torch.float32
        print(f"[系统] 检测到 CUDA GPU: {torch.cuda.get_device_name(0)}")
    else:
        print(f"[系统] torch 可用但无 CUDA，使用 CPU")
except ImportError:
    pass

if HAS_NUMPY:
    print(f"[系统] numpy FFT 后端已加载")
elif HAS_SCIPY:
    print(f"[系统] scipy FFT 后端已加载")
else:
    print(f"[系统] 警告: 无 numpy/scipy，回退到纯 Python FFT（极慢）")

# 只分析前 N 秒
ANALYZE_SECONDS = 30


def fast_fft(signal):
    """
    统一 FFT 接口，自动选择最快后端
    输入: list[float] 或 numpy array
    输出: (magnitudes, freqs)
    """
    n = len(signal)
    if n < 1024:
        return [], []

    # 补零到2的幂
    pad = 1
    while pad < n:
        pad *= 2

    # === 后端1: torch GPU ===
    if HAS_CUDA:
        # 汉宁窗
        window = torch.hann_window(n, device='cpu').numpy()
        sig = np.array(signal, dtype=np.float32) * window
        # 补零
        sig_padded = np.zeros(pad, dtype=np.float32)
        sig_padded[:n] = sig
        # 转 GPU
        t = torch.from_numpy(sig_padded).to(_FFT_DEVICE)
        spectrum = torch.fft.rfft(t)
        mag = torch.abs(spectrum).cpu().numpy()
        freqs = np.fft.rfftfreq(pad, 1.0 / 48000)
        return mag, freqs

    # === 后端2: numpy ===
    if HAS_NUMPY:
        window = np.hanning(n)
        sig = np.array(signal, dtype=np.float64) * window
        sig_padded = np.zeros(pad)
        sig_padded[:n] = sig
        spectrum = np.fft.rfft(sig_padded)
        mag = np.abs(spectrum)
        freqs = np.fft.rfftfreq(pad, 1.0 / 48000)
        return mag, freqs

    # === 后端3: scipy ===
    if HAS_SCIPY:
        window = np.hanning(n)
        sig = np.array(signal, dtype=np.float64) * window
        sig_padded = np.zeros(pad)
        sig_padded[:n] = sig
        spectrum = scipy.fft.rfft(sig_padded)
        mag = np.abs(spectrum)
        freqs = scipy.fft.rfftfreq(pad, 1.0 / 48000)
        return mag, freqs

    # === 后端4: 纯 Python（兜底） ===
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]
    sig = [signal[i] * window[i] for i in range(n)]
    sig += [0.0] * (pad - n)

    def _iterative_fft(x):
        N = len(x)
        if N <= 1: return x
        j = 0
        for i in range(1, N):
            bit = N >> 1
            while j & bit:
                j ^= bit
                bit >>= 1
            j ^= bit
            if i < j:
                x[i], x[j] = x[j], x[i]
        length = 2
        while length <= N:
            half = length // 2
            for start in range(0, N, length):
                for k in range(half):
                    angle = -2 * math.pi * k / length
                    w = complex(math.cos(angle), math.sin(angle))
                    u = x[start + k]
                    v = w * x[start + k + half]
                    x[start + k] = u + v
                    x[start + k + half] = u - v
            length *= 2
        return x

    spectrum = _iterative_fft([complex(s, 0) for s in sig])
    mag = [abs(c) for c in spectrum[:pad//2]]
    freqs = [i * 48000 / pad for i in range(len(mag))]
    return mag, freqs


# ========== 音频分析核心 ==========

class AudioAnalyzer:
    AUDIO_EXTS = {'.flac', '.wav', '.mp3', '.m4a', '.ogg', '.ape', '.wma', '.aac', '.opus'}

    def __init__(self, filepath):
        self.original_path = filepath
        self.wav_path = filepath
        self.is_temp = False
        self.raw_meta = {}
        self.params = None
        self.frames = None
        self.samples = []
        self.channels = []
        self._probe_original()
        self._ensure_wav()
        self._load_wav()

    @classmethod
    def is_audio_file(cls, path):
        return Path(path).suffix.lower() in cls.AUDIO_EXTS

    def _probe_original(self):
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                 '-show_streams', '-show_format', self.original_path],
                capture_output=True, text=True, encoding='utf-8',
                errors='ignore', timeout=10
            )
            if result.returncode == 0:
                info = json.loads(result.stdout)
                s = info.get('streams', [{}])[0]
                fmt = info.get('format', {})
                bps = s.get('bits_per_sample', 0)
                if bps == 0:
                    bps = s.get('bits_per_raw_sample', 0)
                self.raw_meta = {
                    'sample_rate': int(s.get('sample_rate', 0)),
                    'channels': int(s.get('channels', 0)),
                    'bit_depth': int(bps),
                    'codec': s.get('codec_name', 'unknown'),
                    'bitrate': int(s.get('bit_rate', 0) or fmt.get('bit_rate', 0)),
                    'duration': float(s.get('duration', 0) or fmt.get('duration', 0)),
                }
        except Exception:
            pass

    def _ensure_wav(self):
        ext = os.path.splitext(self.original_path)[1].lower()
        if ext == '.wav':
            return
        fd, tmp_path = tempfile.mkstemp(suffix='.wav')
        os.close(fd)
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', self.original_path,
            '-acodec', 'pcm_s32le', '-ar', '48000',
            '-t', str(ANALYZE_SECONDS),
            tmp_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='ignore')
        if result.returncode != 0:
            os.remove(tmp_path)
            raise RuntimeError(f"ffmpeg 转换失败: {result.stderr}")
        self.wav_path = tmp_path
        self.is_temp = True

    def _load_wav(self):
        with wave.open(self.wav_path, 'rb') as w:
            self.params = w.getparams()
            max_frames = min(self.params.nframes, self.params.framerate * ANALYZE_SECONDS)
            self.frames = w.readframes(max_frames)

        width = self.params.sampwidth
        nchannels = self.params.nchannels
        nframes = max_frames
        total_samples = nframes * nchannels

        if width == 1:
            data = struct.unpack(f'{total_samples}B', self.frames)
            self.samples = [(x - 128) / 128.0 for x in data]
        elif width == 2:
            data = struct.unpack(f'{total_samples}h', self.frames)
            self.samples = [x / 32768.0 for x in data]
        elif width == 3:
            self.samples = []
            for i in range(total_samples):
                b = self.frames[i*3:(i+1)*3]
                val = b[0] | (b[1] << 8) | (b[2] << 16)
                if val & 0x800000:
                    val -= 0x1000000
                self.samples.append(val / 8388608.0)
        elif width == 4:
            data = struct.unpack(f'{total_samples}i', self.frames)
            self.samples = [x / 2147483648.0 for x in data]
        else:
            raise ValueError(f"不支持的位深度: {width*8}bit")

        self.channels = []
        for ch in range(nchannels):
            self.channels.append(
                [self.samples[i] for i in range(ch, len(self.samples), nchannels)]
            )

    def get_metadata(self):
        ext = os.path.splitext(self.original_path)[1].upper().lstrip('.')
        sr = self.raw_meta.get('sample_rate') or self.params.framerate
        bd = self.raw_meta.get('bit_depth') or self.params.sampwidth * 8
        ch = self.raw_meta.get('channels') or self.params.nchannels
        br = self.raw_meta.get('bitrate', 0)
        dur = self.raw_meta.get('duration', 0)
        if dur == 0:
            dur = self.params.nframes / self.params.framerate
        return {
            'filename': os.path.basename(self.original_path),
            'format': ext,
            'codec': self.raw_meta.get('codec', ext.lower()),
            'sample_rate': sr,
            'bit_depth': bd,
            'channels': ch,
            'bitrate': br,
            'duration': dur,
        }

    def compute_fft_spectrum(self):
        mag, freqs = fast_fft(self.channels[0])
        if len(mag) == 0:
            return [], []
        if HAS_NUMPY or HAS_SCIPY or HAS_CUDA:
            max_mag = np.max(mag) if np.max(mag) > 0 else 1
            db = 20 * np.log10(mag / max_mag + 1e-10)
            return freqs, db
        else:
            max_mag = max(mag) if max(mag) > 0 else 1
            db = [20 * math.log10(m / max_mag + 1e-10) for m in mag]
            return freqs, db

    def compute_fft_cutoff(self, threshold_db=-60):
        freqs, db = self.compute_fft_spectrum()
        if len(freqs) == 0:
            return 0
        if HAS_NUMPY or HAS_SCIPY or HAS_CUDA:
            mask = db > threshold_db
            if np.any(mask):
                return freqs[np.where(mask)[0][-1]]
            return 0
        else:
            for i in range(len(db) - 1, -1, -1):
                if db[i] > threshold_db:
                    return freqs[i]
            return 0

    def compute_multi_cutoffs(self):
        return {
            '-40dB': self.compute_fft_cutoff(-40),
            '-60dB': self.compute_fft_cutoff(-60),
            '-80dB': self.compute_fft_cutoff(-80),
        }

    def detect_spectral_cliff(self):
        freqs, db = self.compute_fft_spectrum()
        if len(freqs) < 100:
            return False, 0
        nyq = self.params.framerate / 2
        search_start = 15000
        search_end = min(int(nyq * 0.95), freqs[-1] if len(freqs) > 0 else 0)

        if HAS_NUMPY or HAS_SCIPY or HAS_CUDA:
            freqs_arr = np.array(freqs)
            db_arr = np.array(db)
            start_idx = np.searchsorted(freqs_arr, search_start)
            end_idx = np.searchsorted(freqs_arr, search_end)
            if end_idx <= start_idx + 10:
                return False, 0
            max_drop = 0
            cliff_freq = 0
            for i in range(start_idx, end_idx - 5):
                before = np.mean(db_arr[i-2:i]) if i >= 2 else db_arr[i]
                after = np.mean(db_arr[i+1:i+4])
                drop = before - after
                if drop > max_drop:
                    max_drop = drop
                    cliff_freq = freqs_arr[i]
            if max_drop > 30 and cliff_freq > 0:
                cliff_idx = np.searchsorted(freqs_arr, cliff_freq)
                post_avg = np.mean(db_arr[cliff_idx:min(cliff_idx+50, len(db_arr))])
                if post_avg < -70:
                    return True, float(cliff_freq)
            return False, 0
        else:
            start_idx = next((i for i, f in enumerate(freqs) if f >= search_start), 0)
            end_idx = next((i for i, f in enumerate(freqs) if f >= search_end), len(freqs))
            if end_idx <= start_idx + 10:
                return False, 0
            max_drop = 0
            cliff_freq = 0
            for i in range(start_idx, end_idx - 5):
                before = sum(db[i-2:i]) / 2 if i >= 2 else db[i]
                after = sum(db[i+1:i+4]) / 3
                drop = before - after
                if drop > max_drop:
                    max_drop = drop
                    cliff_freq = freqs[i]
            if max_drop > 30 and cliff_freq > 0:
                cliff_idx = next((i for i, f in enumerate(freqs) if f >= cliff_freq), len(freqs)-1)
                post_avg = sum(db[cliff_idx:min(cliff_idx+50, len(db))]) / 50
                if post_avg < -70:
                    return True, cliff_freq
            return False, 0

    def compute_dynamic_range(self):
        block_samples = int(self.params.framerate * 3)
        rms_list = []
        ch = self.channels[0]
        for i in range(0, len(ch), block_samples):
            block = ch[i:i+block_samples]
            if len(block) > self.params.framerate:
                rms = math.sqrt(sum(x*x for x in block) / len(block))
                if rms > 0:
                    rms_list.append(20 * math.log10(rms))
        if len(rms_list) < 2:
            return 0
        rms_list.sort(reverse=True)
        avg_top = sum(rms_list[:min(5, len(rms_list))]) / min(5, len(rms_list))
        return -avg_top

    def compute_stereo_correlation(self):
        if self.params.nchannels < 2:
            return 0
        L = self.channels[0]
        R = self.channels[1]
        n = min(len(L), len(R))
        mean_l = sum(L[:n]) / n
        mean_r = sum(R[:n]) / n
        num = sum((L[i] - mean_l) * (R[i] - mean_r) for i in range(n))
        den_l = sum((L[i] - mean_l)**2 for i in range(n))
        den_r = sum((R[i] - mean_r)**2 for i in range(n))
        if den_l == 0 or den_r == 0:
            return 0
        return num / math.sqrt(den_l * den_r)

    def compute_peak_level(self):
        peak = max(max(abs(x) for x in ch) for ch in self.channels)
        return 20 * math.log10(peak + 1e-10)

    def analyze(self):
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

    def cleanup(self):
        if self.is_temp and os.path.exists(self.wav_path):
            os.remove(self.wav_path)


def score_quality(result):
    s = 0
    m = result['meta']
    cutoffs = result['cutoffs']
    if m['bit_depth'] >= 24: s += 15
    elif m['bit_depth'] >= 16: s += 10
    else: s += 5
    if m['sample_rate'] >= 96000: s += 10
    elif m['sample_rate'] >= 48000: s += 8
    else: s += 5
    cf = cutoffs['-60dB']
    if cf >= 20000: s += 25
    elif cf >= 18000: s += 20
    elif cf >= 16000: s += 15
    elif cf >= 12000: s += 10
    else: s += 5
    dr = result['dr']
    if dr >= 12: s += 25
    elif dr >= 8: s += 20
    elif dr >= 5: s += 15
    elif dr >= 3: s += 10
    else: s += 5
    corr = abs(result['correlation'])
    s += (1 - corr) * 15
    peak = result['peak']
    if peak > -1: s += 3
    elif peak > -3: s += 8
    else: s += 10
    if result['fake_lossless']:
        s = max(0, s - 20)
    return min(100, s)


# ========== 多进程并行扫描 ==========

def get_audio_files(folder):
    files = []
    for f in sorted(os.listdir(folder)):
        path = os.path.join(folder, f)
        if os.path.isfile(path) and AudioAnalyzer.is_audio_file(path):
            files.append(path)
    return files


def analyze_single_file(args):
    """多进程工作函数：分析单首歌曲"""
    idx, total, filepath = args
    name = Path(filepath).stem
    try:
        a = AudioAnalyzer(filepath)
        r = a.analyze()
        s = score_quality(r)
        flag = "🚨假" if r['fake_lossless'] else "✅真"

        # 打印进度（多进程下可能乱序，但能看到在跑）
        msg = f"[{idx}/{total}] {flag} {s:.1f}分 | {name}"
        if r['fake_lossless']:
            msg += f"\n   原因: {'; '.join(r['fake_reasons'])}"
        print(msg)

        result = {
            '文件名': name, '格式': r['meta']['format'],
            '编码': r['meta']['codec'],
            '采样率': r['meta']['sample_rate'],
            '位深度': r['meta']['bit_depth'],
            '比特率_kb': r['meta']['bitrate'] // 1000 if r['meta']['bitrate'] else '',
            '截止_40dB': round(r['cutoffs']['-40dB']),
            '截止_60dB': round(r['cutoffs']['-60dB']),
            '截止_80dB': round(r['cutoffs']['-80dB']),
            '动态范围_dB': round(r['dr'], 1),
            '立体声相关': round(r['correlation'], 3),
            '假无损': '是' if r['fake_lossless'] else '否',
            '假无损原因': '; '.join(r['fake_reasons']) if r['fake_lossless'] else '',
            '综合评分': round(s, 1),
        }
        a.cleanup()
        return result
    except Exception as e:
        print(f"❌ [{idx}/{total}] {name} 分析失败: {e}")
        return None


def scan_folder_parallel(folder, csv_path=None, workers=None):
    files = get_audio_files(folder)
    if not files:
        print(f"❌ 文件夹中没有找到音频文件")
        return

    if workers is None:
        workers = max(1, cpu_count() - 2)  # 留2核给系统

    print(f"\n🔥 扫描 {len(files)} 首音频，每首分析前{ANALYZE_SECONDS}秒...")
    print(f"   FFT后端: {'torch CUDA' if HAS_CUDA else 'numpy' if HAS_NUMPY else 'scipy' if HAS_SCIPY else '纯Python'}")
    print(f"   并行进程: {workers} 个\n")

    # 准备参数列表
    args_list = [(i+1, len(files), f) for i, f in enumerate(files)]

    results = []
    with Pool(processes=workers) as pool:
        for res in pool.imap_unordered(analyze_single_file, args_list):
            if res is not None:
                results.append(res)

    if csv_path and results:
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"\n📄 结果已导出: {csv_path}")

    if results:
        fake_count = sum(1 for r in results if r['假无损'] == '是')
        avg_score = sum(r['综合评分'] for r in results) / len(results)
        print("\n" + "="*60)
        print("【扫描汇总】")
        print(f"  扫描总数: {len(results)}")
        print(f"  假无损: {fake_count} 首 ({fake_count/len(results)*100:.1f}%)")
        print(f"  平均评分: {avg_score:.1f}")
        print("="*60)
    return results


# ========== 单文件对比（串行，保持原样） ==========

def compare_two(path1, path2):
    print("\n" + "🔥"*30)
    print("     同一首歌 · 数据层面音质对比")
    print("🔥"*30)
    a1 = AudioAnalyzer(path1)
    r1 = a1.analyze()
    a2 = AudioAnalyzer(path2)
    r2 = a2.analyze()
    s1 = score_quality(r1)
    s2 = score_quality(r2)

    meta1 = r1['meta']
    print(f"\n{'='*60}")
    print(f"📄 {meta1['filename']} ({meta1['format']})")
    print(f"  频谱截止: {r1['cutoffs']['-60dB']:.0f}Hz | DR: {r1['dr']:.1f}dB | {'假' if r1['fake_lossless'] else '真'}")
    meta2 = r2['meta']
    print(f"📄 {meta2['filename']} ({meta2['format']})")
    print(f"  频谱截止: {r2['cutoffs']['-60dB']:.0f}Hz | DR: {r2['dr']:.1f}dB | {'假' if r2['fake_lossless'] else '真'}")
    print(f"\n【综合评分】{meta1['filename']}: {s1:.1f} 分 | {meta2['filename']}: {s2:.1f} 分")
    if s1 > s2:
        print(f"🏆 胜出: {meta1['filename']} (+{s1-s2:.1f}分)")
    elif s2 > s1:
        print(f"🏆 胜出: {meta2['filename']} (+{s2-s1:.1f}分)")
    else:
        print(f"⚖️ 平局")
    a1.cleanup()
    a2.cleanup()
    return s1, s2


def batch_compare(folder_a, folder_b, workers=None):
    files_a = {Path(f).stem: f for f in get_audio_files(folder_a)}
    files_b = {Path(f).stem: f for f in get_audio_files(folder_b)}
    common = sorted(set(files_a.keys()) & set(files_b.keys()))
    if not common:
        print(f"❌ 未找到同名音频")
        return

    if workers is None:
        workers = max(1, cpu_count() - 2)

    print(f"\n🔥 找到 {len(common)} 首同名歌曲，{workers} 进程并行对比...\n")

    def worker(args):
        idx, total, name = args
        try:
            a1 = AudioAnalyzer(files_a[name])
            r1 = a1.analyze()
            a2 = AudioAnalyzer(files_b[name])
            r2 = a2.analyze()
            s1, s2 = score_quality(r1), score_quality(r2)
            winner = "A" if s1 > s2 else "B" if s2 > s1 else "平"
            print(f"[{idx}/{total}] [{winner}] {name} | A:{s1:.0f} B:{s2:.0f}")
            a1.cleanup()
            a2.cleanup()
            return {'name': name, 'winner': winner, 's1': s1, 's2': s2,
                    'fake_a': r1['fake_lossless'], 'fake_b': r2['fake_lossless']}
        except Exception as e:
            print(f"❌ [{idx}/{total}] {name} 失败: {e}")
            return None

    args_list = [(i+1, len(common), n) for i, n in enumerate(common)]
    results = []
    with Pool(processes=workers) as pool:
        for res in pool.imap_unordered(worker, args_list):
            if res:
                results.append(res)

    if results:
        a_wins = sum(1 for r in results if r['winner'] == 'A')
        b_wins = sum(1 for r in results if r['winner'] == 'B')
        fake_a = sum(1 for r in results if r['fake_a'])
        fake_b = sum(1 for r in results if r['fake_b'])
        print(f"\n汇总: A胜{a_wins} B胜{b_wins} | A假{fake_a} B假{fake_b}")
    return results


# ========== 主入口 ==========

if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 0:
        print("""用法:
  单文件:     python audio_compare.py 文件A.flac 文件B.flac
  批量对比:   python audio_compare.py --batch "文件夹A" "文件夹B" --workers 16
  单文件夹:   python audio_compare.py --scan "文件夹" --csv "结果.csv" --workers 16
""")
        sys.exit(0)

    workers = None
    if '--workers' in args:
        idx = args.index('--workers')
        if idx + 1 < len(args):
            workers = int(args[idx + 1])
            args = args[:idx] + args[idx+2:]

    if args[0] == '--scan':
        folder = args[1]
        csv_path = None
        if '--csv' in args:
            idx = args.index('--csv')
            if idx + 1 < len(args):
                csv_path = args[idx + 1]
        scan_folder_parallel(folder, csv_path, workers)
        sys.exit(0)

    if args[0] == '--batch':
        if len(args) < 3:
            print("❌ --batch 需要两个文件夹路径")
            sys.exit(1)
        batch_compare(args[1], args[2], workers)
        sys.exit(0)

    if len(args) >= 2:
        compare_two(args[0], args[1])
        sys.exit(0)

    print("❌ 参数错误")
