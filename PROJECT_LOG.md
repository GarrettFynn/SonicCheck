

---

## 附录 A：项目技术原理与程序工作原理（极详细版）

> 本节写给未来维护代码的人，也写给想深入理解这个项目是如何运转的人。
> 所有描述基于 v1.0.0 代码状态。

---

### A.1 整体架构与模块划分

SonicCheck 采用经典的 **"GUI 前端 + 分析引擎 + 文件管理"** 三层架构。所有模块围绕 Qt 的信号-槽机制组织，确保界面响应不会阻塞分析流程。

```
┌─────────────────────────────────────────────────────────────┐
│                         GUI 层                               │
│  main_window.py  ── 主窗口（布局、状态管理、调度）            │
│  widgets/        ── 左栏、表格、进度条、日志、对话框           │
│  threads/        ── ScanWorker(QRunnable) + ScanManager       │
└──────────────────────┬──────────────────────────────────────┘
                       │ 信号/槽（跨线程安全）
┌──────────────────────▼──────────────────────────────────────┐
│                      业务逻辑层                              │
│  core/analyzer.py      ── 单文件音频分析（FFT/频谱/指标）     │
│  core/scanner.py       ── 文件系统扫描（递归/容错）            │
│  core/ffmpeg_locator.py── ffmpeg/ffprobe 自动定位             │
│  core/renamer.py       ── 智能重命名计划                       │
│  core/tag_renamer.py   ── 按标签改名                          │
│  core/deduper.py       ── 去重引擎（并查集）                   │
│  core/playlist.py      ── 歌单解析与匹配                       │
│  core/csv_exporter.py  ── CSV 导出                           │
│  core/quality_marks.py ── 质量标记配置                         │
└──────────────────────┬──────────────────────────────────────┘
                       │ subprocess / 文件 IO
┌──────────────────────▼──────────────────────────────────────┐
│                      外部依赖层                              │
│  ffmpeg / ffprobe    ── 音频解码、格式转换、元数据读取         │
│  numpy               ── FFT、向量化数值运算                   │
│  PyQt6               ── GUI 框架、线程调度、信号槽             │
└─────────────────────────────────────────────────────────────┘
```

**模块依赖原则**：
- `core/` 模块**不依赖** `widgets/` 或 `main_window.py`，确保分析引擎可以独立测试
- `threads/` 只依赖 `core/` 和 `models/`，是 GUI 与引擎之间的胶水层
- `widgets/` 只通过 `main_window.py` 间接调用 `core/`，不直接引用分析引擎

---

### A.2 完整数据流：从"拖文件夹"到"显示结果"

以下是一个用户完成一次完整扫描操作后，数据在系统内部的全流程。

**Phase 1：用户触发**
1. 用户拖拽文件夹到窗口，或点击"选择文件夹"按钮
2. `main_window.py::MainWindow._on_browse()` 或 `dragEnterEvent/dropEvent` 捕获路径
3. 路径通过 `QSettings` 记忆（`settings.setValue("last_dir", path)`）
4. 调用 `_start_scan()`

**Phase 2：前置检查**
5. `_start_scan()` 调用 `ffmpeg_locator.find_ffmpeg()` 和 `find_ffprobe()`
6. **如果找不到**：弹 `QMessageBox.critical`，直接返回，不进入扫描流程
7. 如果找到：清空上一次的内存结果列表和表格数据

**Phase 3：文件扫描**
8. 调用 `scanner.scan_folder(folder_path)`
9. `scanner.py` 使用 `os.walk(onerror=...)` 递归遍历
10. 对每个文件检查 `is_audio_file()`（后缀匹配 `{'.flac', '.wav', '.mp3', '.m4a', '.ogg', '.ape', '.wma', '.aac', '.opus'}`）
11. 同时检查 `_excluded()` 规则：排除 `_待清除` 文件夹和 `_安全输出` 后缀
12. 返回 `(audio_files, skipped_dirs)`

**Phase 4：任务分派**
13. `main_window.py` 创建 `ScanManager`，传入文件列表
14. `ScanManager` 维护：
    - `self.total = len(audio_files)`
    - `self.done_count = 0`
    - `self.cancelled = False`
15. 对每个文件创建一个 `ScanWorker(QRunnable)`，提交到 `QThreadPool.globalInstance()`

**Phase 5：Worker 执行（并发）**
16. 每个 `ScanWorker` 在其独立线程中执行：
    ```
    analyzer = AudioAnalyzer(filepath, analyze_seconds=30, cancel_check=...)
    result = analyzer.analyze()
    result['score'] = score_quality(result)
    result['filepath'] = filepath
    analyzer.cleanup()  # 删除临时 WAV
    ```

**Phase 5a：AudioAnalyzer 内部四阶段**
- **阶段 1 Probe**：`ffprobe -v quiet -print_format json -show_streams -show_format <filepath>`
  - 读取 sample_rate、channels、bit_depth、codec、bitrate、duration
  - 读取 tags（title/artist），优先 format.tags，回退 stream.tags，再回退 album_artist
  - bit_depth 为 0 时 fallback 到 bits_per_raw_sample（FLAC 旧编码器 bug）
  
- **阶段 2 Transcode**：`ffmpeg -i <input> -acodec pcm_s32le -ar 48000 -t 30 <temp.wav>`
  - 统一转成 48kHz/32bit PCM
  - Popen + 轮询，支持取消（`proc.kill()`）
  - 60 秒超时保护
  
- **阶段 3 Load**：`wave.open(temp_wav)`
  - 用 `wave` 标准库读取 PCM 数据
  - 支持 1/2/3/4 byte 采样宽度，统一归一化到 float64 [-1, 1]
  - 去交错（deinterleave）分声道
  
- **阶段 4 Analyze**：
  - `compute_fft_spectrum()`：numpy `rfft` + Hann 窗，4096 窗口/2048 hop
  - `detect_spectral_cliff()`：15kHz~0.95Nyquist 区间向量化搜索 >30dB 落差
  - `compute_fft_cutoff(-60)`：从 Nyquist 往低频扫，找 -60dB 点
  - `compute_dynamic_range()`：3 秒分块 RMS，取 Top 20% vs Bottom 20%
  - `compute_stereo_correlation()`：归一化互相关
  - `compute_peak_level()`：最大 dBFS
  - 综合判定：三条规则检查

**Phase 6：结果回传（线程安全）**
17. `ScanWorker` 通过 `pyqtSignal` 发射 `result_ready(ResultItem)`
18. `ScanManager` 的槽函数在主线程中被调用（Qt 的 queued connection 保证线程安全）
19. `ScanManager` 更新 `done_count`，发射 `progress_updated(int)`

**Phase 7：GUI 更新**
20. `MainWindow` 的 `_on_result_ready()` 槽被调用：
    - 创建 `ResultItem` 对象
    - 添加到 `self.results` 内存列表
    - 调用 `self.result_table.add_item(item)` 更新表格
    - 更新汇总标签（真/假/失败计数）
21. `MainWindow` 的 `_on_progress()` 更新进度条：`progress_bar.setValue(int(100 * done / total))`

**Phase 8：扫描结束**
22. 当 `done_count == total` 或用户点击"停止"：
    - 停止按钮置灰
    - 左栏恢复可用
    - 日志面板输出汇总信息
    - 拖拽区恢复

---

### A.3 核心模块技术详解

#### A.3.1 ffmpeg_locator.py —— 外部工具自动发现

SonicCheck 本身不内嵌 ffmpeg（版权和体积原因），但需要在各种安装环境下都能找到它。定位策略设计为**四级兜底**，按优先级从高到低：

**优先级 1：内置目录**
检查 `sys.executable` 同级目录下的 `resources/ffmpeg/ffmpeg.exe` 和 `ffprobe.exe`。这是"绿色版"推荐方式——用户把 ffmpeg 静态版解压到 resources 里，和 exe 一起分发。

**优先级 2：PyInstaller 解压目录**
打包后的 exe 运行时，PyInstaller 会把 bundled 的数据解压到临时目录（`_MEIPASS` 或 `_internal`）。如果打包时把 ffmpeg 打进了 `datas`，这里能找到。

**优先级 3：系统 PATH**
用 `shutil.which('ffmpeg')` 和 `shutil.which('ffprobe')` 搜索。这是"开发者模式"或"已装 ffmpeg"用户的路径。

**优先级 4：常见安装位置**
- Chocolatey 默认路径：`C:\ProgramData\chocolatey\bin\ffmpeg.exe`
- 手动安装路径：`C:\ffmpeg\bin\ffmpeg.exe`

**子进程窗口隐藏**：`hidden_subprocess_kwargs()` 返回 `{'creationflags': subprocess.CREATE_NO_WINDOW}`（Windows）或空字典（其他平台）。这确保 ffmpeg 转码时不会弹出烦人的黑色控制台窗口。

#### A.3.2 analyzer.py —— 音频分析引擎（最核心）

`AudioAnalyzer` 是一个**"构造即完成准备"**的类。实例化时会依次执行 probe → transcode → load，之后调用 `analyze()` 出结果。这种设计保证了：
- 如果文件损坏或 ffmpeg 出错，异常在构造阶段就抛出，不会留下半成品
- `cleanup()` 统一清理临时文件，用 `try/finally` 保证不泄露

**统一转码决策的深层原因**：
除了修复 WAV 频率轴 bug 外，另一个隐藏原因是 **Python 标准库 `wave` 只支持 PCM**。FLAC、APE 等压缩格式无法直接用 `wave` 读取。所以必须有一个"解压到 PCM"的步骤。与其为每种格式写独立的解码逻辑（需要 `flac` 命令行、`mac` 命令行等），不如让 ffmpeg 一站式搞定。

**FFT 参数选择**：
- 窗口大小 4096：在 48kHz 采样率下，频率分辨率 = 48000/4096 ≈ 11.7 Hz/bin，足够精细
- Hop length 2048（50% 重叠）：平衡时间分辨率和计算量
- Hann 窗：频谱泄漏最小，适合音乐信号
- 补零到 2 的幂：`pad = 1 << (n - 1).bit_length()`，加速 FFT

**断崖检测的向量化实现**：
旧脚本是逐 bin 循环，伪代码：
```python
for i in range(start_idx, end_idx):
    before = mean(db[i-2:i])
    after = mean(db[i+1:i+4])
    if before - after > 30:
        # 检查断后 50 bin
```

新版用 numpy 向量化：
```python
idx = np.arange(start_idx, end_idx - 5)
before = (db[idx - 2] + db[idx - 1]) / 2.0
after = (db[idx + 1] + db[idx + 2] + db[idx + 3]) / 3.0
drops = before - after
j = int(np.argmax(drops))
```

优势：一次操作处理整个区间，C 级速度，避免 Python 循环开销。

**DR（动态范围）计算**：
这是音频工程领域的标准算法，参考 DR Meter 的实现：
1. 按 3 秒分块（`block_samples = framerate * 3`）
2. 每块计算 RMS：`sqrt(mean(samples^2))`
3. 所有块排序，取最响的 5 个块（Top 20% 的近似）
4. DR = -mean(top_5_rms_in_db)

> 为什么是负的？因为 dB 是负值（0 dBFS 是满幅），"更响"意味着更接近 0（数值更大），所以取负数后，DR 越大表示动态范围越好。

**临时文件管理**：
临时 WAV 路径：`%TEMP%\AudioQualityScanner\aqs_XXXXXX.wav`
- 用 `tempfile.mkstemp` 生成，保证唯一性
- 实例析构或 `cleanup()` 时删除
- 启动时 `sweep_temp_dir()` 清理 1 小时前的残留（防崩溃后堆积）

#### A.3.3 scanner.py —— 文件系统扫描

非常轻量的模块，核心就一个函数：

```python
def scan_folder(folder_path):
    files = []
    skipped_dirs = 0
    for root, dirs, filenames in os.walk(folder_path, onerror=_on_walk_error):
        # 排除 _待清除 和 _安全输出
        dirs[:] = [d for d in dirs if not _excluded(d)]
        for f in filenames:
            if AudioAnalyzer.is_audio_file(f):
                files.append(os.path.join(root, f))
    return files, skipped_dirs
```

关键点：
- `os.walk` 的 `onerror` 回调捕获权限错误，跳过无权限目录
- `dirs[:] = ...` 是原地修改，控制 `os.walk` 不进入被排除的目录
- 返回绝对路径，后续所有模块统一使用绝对路径（防中文路径问题）

#### A.3.4 deduper.py —— 去重引擎（并查集）

去重是 M4.5 最复杂的算法模块，分两路：

**路 A：同名对比**
规范化文件名后分组：
```python
def _normalize(name):
    return name.lower().replace('_', '').replace('-', '').replace(' ', '')
```
`周杰伦-晴天.flac`、`周杰伦_晴天.FLAC`、`周杰伦 晴天.flac` → 同一个组。

**路 B：内容签名去重（并查集）**

这是真正的"硬核算法"。

**Step 1：提取签名**
每个已分析的文件生成一个四元组签名：
- `duration`：时长（秒）
- `dr`：动态范围
- `correlation`：立体声相关度（绝对值）
- `cutoff_60dB`：-60dB 截止频率

**Step 2：两两比较**
对于 N 个文件，理论上需要 N*(N-1)/2 次比较。当 N=1000 时约 50 万次比较，但每个比较只是 4 个数值的范围检查，所以实际很快。

比较容差：
```python
TIME_TOLERANCE = 0.3      # 秒
DR_TOLERANCE = 0.2        # dB
CORR_TOLERANCE = 0.01     # 归一化
CUTOFF_TOLERANCE = 50     # Hz
```

**Step 3：并查集合并**
```python
class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
    
    def find(self, x):
        # 路径压缩
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]
    
    def union(self, x, y):
        # 按秩合并
        px, py = self.find(x), self.find(y)
        if px != py:
            self.parent[px] = py
```

时间复杂度：接近 O(N × α(N))，其中 α 是反阿克曼函数，实际应用中可视为常数。1000 首歌的内容签名去重，在普通电脑上 < 1 秒。

**Step 4：分组展示**
每个连通分量（同一 parent）构成一个 `DupGroup`。
- `DupGroup.items` 按评分降序排列（契约）
- 默认保留最高分（items[0]）
- `keep_override` 允许用户手动指定保留项

**清除与还原**：
- 清除时：物理移动文件到 `_待清除/`，写入 `.restore_map.json`
- 还原时：读取 manifest，按"新路径 → 原路径"映射移回
- 还原后 `prune` 死条目（文件已不存在的从 manifest 删除）

#### A.3.5 playlist.py —— 网易云歌单解析

这是 M4.5 中技术含量最高的功能之一，涉及对网易云 API 行为的逆向理解。

**问题**：网易云的歌单详情接口有反爬策略。`tracks` 字段（歌曲详情）被限流到只返回前 10 首左右，但 `trackIds`（ID 列表）是全的。

**解决方案**：
```
Step 1: GET api/v6/playlist/detail?id=<playlist_id>&n=100000
        Response: { trackIds: [1,2,3,...,832], tracks: [/* only ~10 songs */] }
        
Step 2: Split trackIds into batches of 200
        
Step 3: For each batch:
        GET api/song/detail?ids=[id1,id2,...,id200]
        Response: { songs: [/* full metadata */] }
        
Step 4: Merge all batches, maintain original order
```

**关键发现**：
- `n=100000` 参数不是返回数量上限，而是"最多取多少首"。对 trackIds 来说，这个值足够大就能拿到完整列表。
- `api/song/detail` 的 `ids` 参数有长度限制。实测 500 个 ID 只返回 201 首，所以保守用 200。
- 整个流程**不需要登录、不需要 Cookie、不需要 MUSIC_U**。

**私密歌单**：`trackIds` 也不返回，匿名完全拿不到数据。此时提示用户手动粘贴歌曲列表。

**匹配算法**：
1. **精确匹配**：歌单里的 `"歌名 - 歌手"` vs 曲库文件的 `title`/`artist` tags
2. **模糊匹配**：歌单歌名是否出现在某个文件名中（`filename.contains(song_name)`）
3. 复制（非移动）到目标文件夹，幂等执行

#### A.3.6 renamer.py —— 智能重命名

**计划构建流程**：
1. 输入：分析结果列表 + 质量标记配置
2. 按 `filepath` 排序（**顺序防抖**，防止撞名时顺序不确定）
3. 对每个文件：
   a. 剥离旧标记（正则匹配前缀/后缀位置的 `[真无损]`、`[假无损]` 等）
   b. 按判定结果加新标记
   c. 检查目标路径是否已存在
   d. 检查同名 `.lrc` 是否需要同步改
4. 输出：`RenamePlan`（操作列表 + 跳过列表）

**剥离逻辑**：
```python
# 前缀剥离
re.sub(r'^\s*' + re.escape(mark) + r'\s*', '', filename)
# 后缀剥离
re.sub(r'\s*' + re.escape(mark) + r'\s*$', '', filename)
```

**同步规则**：
- 音频文件改名 → 检查同目录下同名的 `.lrc`，一并改名
- `.cue` 文件**不动**（cue 内部引用了音轨文件名，改了会破坏索引）

#### A.3.7 csv_exporter.py —— CSV 导出

非常直白的模块，值得注意的细节：
- 编码用 `utf-8-sig`（带 BOM），这是 Excel 正确识别 UTF-8 中文的"魔法字节"
- 只导出 `STATUS_DONE` 的行，失败的跳过（和旧脚本行为一致）
- 文件名带时间戳，避免覆盖

---

### A.4 GUI 与后台的协作机制

#### A.4.1 线程模型

SonicCheck 使用 **单主线程 + QThreadPool Worker 线程** 模型：

- **主线程**：跑 Qt 事件循环，处理用户交互、界面更新
- **Worker 线程**：每个音频文件一个 `ScanWorker(QRunnable)`，由 `QThreadPool` 调度，默认最多 8 个并发

**为什么不用多进程？**
1. Qt 的 QThreadPool 与 multiprocessing 在 Windows 上交互有已知问题
2. `multiprocessing.freeze_support()` 在 PyInstaller 打包后行为不稳定
3. GIL 不是瓶颈——分析是 CPU 密集但 numpy 在 C 层释放 GIL，FFT 能并行
4. ffmpeg 转码本身是多线程的（内部用 SIMD），外部再加多进程收益递减

#### A.4.2 信号与槽的跨线程通信

```python
# ScanWorker（在 Worker 线程中）
class ScanWorker(QRunnable):
    result_ready = pyqtSignal(object)  # 发射 ResultItem
    
    def run(self):
        try:
            result = analyze_file(self.filepath, ...)
            self.result_ready.emit(result)
        except Exception as e:
            self.error.emit(str(e))
```

Qt 的 `pyqtSignal` 在跨线程时自动使用 **Queued Connection**：发射信号的线程（Worker）把事件放入接收线程（主线程）的事件队列，主线程在下一轮事件循环中调用槽函数。这保证了：**GUI 更新总是在主线程执行，不会出现竞争条件**。

#### A.4.3 协作式取消机制

**为什么不直接 pool.clear()？**

`QThreadPool.clear()` 会清空待执行的任务队列，但已经开始执行的任务不受影响。更致命的是：被 clear 掉的任务不会调用它们的 `run()` 方法，也就不会发射 `finished` 信号 → `done_count` 永远到不了 `total_count` → 进度条永远卡住。

**SonicCheck 的方案**：
1. `ScanManager.cancelled = True`
2. 每个 `ScanWorker` 传入一个 `cancel_check` 回调：
   ```python
   def cancel_check():
       return self.manager.cancelled
   ```
3. Worker 在关键阶段检查：
   - 转码前（避免启动不必要的 ffmpeg）
   - 转码中（通过 Popen 轮询检查）
   - analyze 前
4. 检测到取消 → 抛 `ScanCancelled` → `finally` 块清理临时文件 → 发射 `finished`

**关键保证**：每个 worker 无论成功、失败、取消，都会**恰好发射一次** `finished` 信号。`ScanManager` 用 `done_count == total_count` 判断扫描是否真正结束。

#### A.4.4 扫描中锁定

扫描期间，以下操作被禁用：
- "选择文件夹"按钮（`btn_browse.setEnabled(False)`）
- 拖拽区（`setAcceptDrops(False)`）
- 左栏的线程数/分析时长 spin（`spin_threads.setEnabled(False)`）
- "导出 CSV"、"重命名"等功能按钮（防止在扫描中途操作半成品数据）

同时启用的：
- "停止"按钮（`btn_stop.setEnabled(True)`）
- 进度条（`progress_bar.setRange(0, total)`）

---

### A.5 关键设计决策及原因

#### 决策 1：所有格式统一 ffmpeg 转码（含 WAV）

**选项 A**：每种格式独立解码（flac 用 `subprocess` 调 `flac -d`，ape 用 `mac`，wav 直接用 `wave.open`）
**选项 B**：全部走 ffmpeg 转码成统一格式

**选了 B**。原因：
- 代码路径唯一化，测试覆盖面最小
- 修复 WAV 频率轴 bug（旧脚本独立解析 WAV 时 Nyquist 算错）
- 未来支持新格式（如 DSD、MQA）无需改分析引擎，只要 ffmpeg 支持就行

代价：多了一次有损/无损转换？**不，pcm_s32le 是无损 PCM，flac → pcm_s32le 是解码不是转码，没有信息损失。**

#### 决策 2：numpy 向量化替代纯 Python 循环

**选项 A**：保留旧脚本的纯 Python 循环（可读性高、易调试）
**选项 B**：全部 numpy 向量化

**选了 B**。原因：
- FFT 后处理（断崖检测、截止频率、DR）涉及大量数组操作，Python 循环是瓶颈
- 实测单首分析从 2.7s 降到 0.43s，提升 ~6 倍
- 验证方式：同批样本新旧脚本全字段 diff=0.00e+00，保证数值一致

#### 决策 3：不重命名直接改，而是"预览 → 确认 → 执行"

**选项 A**：点"重命名"直接执行
**选项 B**：先展示预览，用户确认后再执行

**选了 B**。原因：
- 文件操作不可逆（虽然有安全模式，但默认不是安全模式）
- 让用户看到"哪些会改、哪些会跳过"，心里有底
- 跳过项（目标已存在）需要用户知情

#### 决策 4：去重清除用"移动"而非"删除"

**选项 A**：直接删除重复文件
**选项 B**：移到 `_待清除/` 文件夹

**选了 B**。原因：
- "假阳性"风险：不同版本的同一首歌（现场版 vs 录音室版）可能被误判为重复
- 给用户反悔的机会
- 生成 `.restore_map.json` 支持一键还原

#### 决策 5：歌单导入用"复制"而非"移动"

**选项 A**：把匹配到的歌曲移动到歌单文件夹
**选项 B**：复制到歌单文件夹，原文件不动

**选了 B**。原因：
- 原曲库结构不应被歌单操作破坏
- 同一首歌可能出现在多个歌单里
- 用户可能想保留原文件夹的分类方式

#### 决策 6：安全模式默认关闭，但醒目提示

**选项 A**：默认开启安全模式
**选项 B**：默认关闭，但左栏有醒目的 checkbox

**选了 B**。原因：
- 安全模式会让所有操作变成"复制产出"，曲库里一堆 `_安全输出` 文件夹，对熟练用户很烦
- 新手会仔细看界面，左栏的 checkbox 足够醒目
- 第一次用的人自然会勾选，用熟了再关掉

---

### A.6 业务流程状态机

#### 扫描流程状态机

```
[Idle] --选择文件夹/拖拽--> [Ready]
[Ready] --开始扫描--> [Scanning]
[Scanning] --完成--> [Done]
[Scanning] --停止--> [Stopped]
[Scanning] --出错--> [Error]
[Done] --选择新文件夹--> [Ready]
[Stopped] --选择新文件夹--> [Ready]
```

状态转换时：
- `Idle → Ready`：启用"开始扫描"按钮
- `Ready → Scanning`：锁定界面、初始化进度条、启动 QThreadPool
- `Scanning → Done`：解锁界面、汇总日志、启用导出/重命名按钮
- `Scanning → Stopped`：解锁界面、部分结果可用、日志提示"用户取消"

#### 重命名流程状态机

```
[Idle] --点击重命名--> [Planning]
[Planning] --用户确认--> [Executing]
[Planning] --用户取消--> [Idle]
[Executing] --完成--> [Done]
[Executing] --出错--> [Error]
```

- `Planning`：弹 RenameDialog，展示计划和跳过项
- `Executing`：逐个执行 rename 操作，单条失败不中断
- 完成后刷新表格中的文件路径（因为文件名变了，内存中的 filepath 要同步更新）

---

### A.7 内存管理

SonicCheck 的内存使用主要集中在扫描阶段：

1. **分析期间**：每个 worker 同时持有原始音频数据（PCM）+ FFT 结果
   - 48kHz × 30秒 × 2声道 × 4字节 = ~11.5 MB 每文件
   - 8 线程并发 ≈ 92 MB 峰值
   - 加上 numpy 内部缓冲区，实际峰值约 150-200 MB

2. **结果存储**：所有分析结果保存在内存中（`List[ResultItem]`）
   - 1000 首歌 × 每条约 1KB = ~1 MB
   - 可以忽略不计

3. **临时文件**：ffmpeg 转码产生的临时 WAV
   - 48kHz × 30秒 × 2声道 × 4字节 ≈ 11.5 MB 每文件
   - 但临时文件在 `analyze()` 返回后由 `cleanup()` 立即删除
   - 磁盘峰值 = 并发数 × 11.5 MB ≈ 92 MB

**内存优化空间**：
- 目前分析只取前 30 秒，如果未来支持"全曲分析"，内存会线性增长
- 可以考虑流式处理（分 chunk 分析），但会显著增加代码复杂度

---

### A.8 可扩展性设计

SonicCheck 在以下位置预留了扩展点：

1. **新格式支持**：
   - 只需改 `AUDIO_EXTS` 集合（analyzer.py）
   - 只要 ffmpeg 支持解码，分析引擎无需改动

2. **新判定规则**：
   - 在 `AudioAnalyzer.analyze()` 里添加新规则检查
   - 在 `fake_reasons` 里追加原因描述
   - `score_quality()` 里调整评分

3. **新导出格式**：
   - 模仿 `csv_exporter.py` 写 `json_exporter.py` / `xlsx_exporter.py`
   - 在 `main_window.py` 的导出按钮槽里加分支

4. **新歌词源**：
   - 目前只支持网易云
   - 可以仿照 `playlist.py` 写 `qq_music.py`、`spotify.py` 等
   - 统一接口：输入链接/文本 → 返回 `List[PlaylistTrack]` → `match_tracks()`

5. **迷你频谱图（二期）**：
   - 已有 `compute_fft_spectrum()` 返回 `(freqs, db)` 数据
   - 只需加一个可视化层（matplotlib 或 Qt 自带的 QPainter）
   - 性能考虑：1000 首歌每首画一张缩略图，可以在扫描时预计算缓存

---

> 附录 A 完。如有新增模块或重大重构，请同步更新本节。
