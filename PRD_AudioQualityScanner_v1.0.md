# AudioQuality Scanner —— 技术需求文档
## 提交给 Kimi Code (K3) Agent

**文档版本**: v1.0
**目标**: 基于现有 Python 音频分析引擎，开发 PyQt6 桌面 GUI，打包为 Windows 绿色版
**要求**: K3 Agent 先完整审查本文档，与用户确认后再执行代码生成

---

## 一、项目定义（一句话）

一款面向 HiFi 爱好者的 Windows 绿色版桌面工具，用于批量检测本地音乐文件（FLAC/MP3 等）是否为"真无损"，并支持一键标记重命名。

---

## 二、目标用户与使用场景

用户是拥有大量本地音乐文件的 HiFi 爱好者，从 QQ 音乐、网易云等平台下载了所谓"无损"资源，但怀疑其中大量是 MP3 升频转 FLAC 的假无损。用户需要一个工具：
1. 选一个文件夹，自动扫描所有音频文件
2. 数据层面分析每首歌的频谱截止频率、动态范围、频谱断崖
3. 标记出假无损文件
4. 一键给文件重命名加后缀（真无损 / 假无损），方便后续管理或删除

用户不懂命令行，也不懂 Python，需要解压即用的 .exe 绿色版。

---

## 三、技术栈（已确定，不可更改）

| 层级 | 选型 | 版本/说明 |
|------|------|-----------|
| GUI 框架 | PyQt6 | 6.x，支持高 DPI，深色主题 |
| 打包工具 | PyInstaller | --onedir 模式，生成绿色文件夹 |
| FFT 后端 | numpy | 已验证可用，用户环境有 numpy |
| 音频解码 | ffmpeg + ffprobe | 内置 Gyan essentials 精简版 |
| 多线程 | QThreadPool + QRunnable | 禁止用 Python multiprocessing，避免 PyInstaller 打包后重复弹窗 |
| 样式 | QSS (Qt StyleSheet) | 手写深色主题，不依赖外部样式库 |

**关键约束**: 用户电脑是 AMD Ryzen 9 9950X + RTX 4090，Windows 11，已装 ffmpeg 8.1。但软件必须内置 ffmpeg，做到解压即用，不依赖用户环境。

---

## 四、功能需求（按优先级）

### P0 —— 必须有（MVP 闭环）

1. **文件夹选择**: 点击按钮选文件夹，或拖拽文件夹到窗口
2. **批量扫描**: 自动识别文件夹内所有音频文件（FLAC/WAV/MP3/M4A/OGG/APE），忽略 LRC/歌词等非音频文件
3. **实时结果展示**: 表格显示每首歌的文件名、评分、频谱截止(-60dB)、动态范围(DR)、假无损状态
4. **实时进度**: 进度条 + 当前分析文件名 + 已完成的数量
5. **扫描控制**: 开始扫描、停止扫描
6. **一键重命名**: 扫描完成后，根据结果给文件加后缀（_真无损 / _假无损），同步改对应的 .lrc 文件，先预览再确认执行
7. **导出 CSV**: 扫描结果导出为 CSV，编码 UTF-8-BOM，方便 Excel 打开

### P1 —— 重要（提升体验）

8. **结果筛选**: 表格上方加筛选器，可只看"真无损"、"只看假无损"、"全部"
9. **排序**: 点击表头按评分/截止频率/DR 排序
10. **汇总统计**: 扫描完成后底部显示"共 X 首，假无损 Y 首 (Z%)，平均评分 W"
11. **日志面板**: 底部可折叠的日志区，显示 ffmpeg 转换、分析过程中的详细信息

### P2 —— 锦上添花（二期再做）

12. **频谱迷你图**: 每行显示一个缩略频谱图
13. **设置面板**: 可调整并行线程数、分析时长（默认 30 秒）
14. **拖拽单个文件**: 支持直接拖音频文件到窗口分析

---

## 五、界面设计

### 整体布局（三栏式）

窗口尺寸默认 1200×800，最小 900×600，可缩放。

```
┌─────────────────────────────────────────────────────────────┐
│  [icon] AudioQuality Scanner    v0.1.0          [设置] [X]  │  ← 标题栏（深色）
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│  左侧面板     │              右侧主面板                       │
│  (宽度 220px)│              (表格 + 进度 + 汇总)              │
│              │                                              │
│  📁 来源      │  ┌──────────────────────────────────────┐   │
│  [选择文件夹] │  │  筛选: [全部 ▼]  [导出CSV] [重命名 ▼] │   │
│  [拖拽到此处] │  ├──────────────────────────────────────┤   │
│              │  │  文件名      │ 评分 │ 截止 │ DR  │ 状态 │   │
│  ───────────  │  ├──────────────────────────────────────┤   │
│              │  │ 七里香...   │ 65.2 │ 20k  │ 8.5 │ ✅真 │   │
│  ⚙️ 控制      │  │ Battle...   │ 44.6 │ 15k  │ 6.2 │ 🚨假 │   │
│  线程数: 16   │  │ ...         │ ...  │ ...  │ ... │ ...  │   │
│  分析时长:30s │  └──────────────────────────────────────┘   │
│              │                                              │
│  [开始扫描]   │  进度: [████████░░░░░░░░] 67% (108/162)      │
│  [停止]       │  当前: "黑色星期五 - 马思唯..."               │
│              │                                              │
│  ───────────  │  📊 汇总: 共162首 | 假无损98首(60.5%) | 均分56.2   │
│              │                                              │
│  📄 导出      │                                              │
│  [导出CSV]   │                                              │
│              │                                              │
└──────────────┴──────────────────────────────────────────────┘
│  📋 日志 (可折叠)                                             │
│  [14:32:01] 开始扫描 G:\Music\...                             │
│  [14:32:02] ffmpeg 转换: 1stp Klosr.flac → temp.wav           │
│  [14:32:03] 分析完成: 1stp Klosr - 假无损                     │
└─────────────────────────────────────────────────────────────┘
```

### 配色方案（深色 HiFi 风格）

参考 foobar2000 DarkOne 主题：
- 背景色: #1E1E1E（主背景）、#252526（面板背景）
- 文字色: #D4D4D4（主文字）、#808080（次要文字）
- 强调色: #007ACC（蓝色，按钮/进度条）、#4EC9B0（青色，真无损标记）
- 警告色: #F44747（红色，假无损标记）
- 边框色: #3C3C3C
- 表格交替行: #1E1E1E / #252526

### 状态标记

- 真无损: 绿色圆点 + "真无损" 文字
- 假无损: 红色圆点 + "假无损" 文字
- 分析中: 灰色转圈动画或 "..."
- 失败: 橙色感叹号

---

## 六、架构设计

### 模块划分

```
audio_quality_scanner/
├── main.py              # 入口，创建 QApplication 和主窗口
├── main_window.py       # 主窗口类，布局管理
├── widgets/
│   ├── left_panel.py    # 左侧面板（文件夹选择、控制按钮）
│   ├── result_table.py  # 结果表格（QTableWidget）
│   ├── progress_bar.py  # 进度条 + 当前文件名显示
│   ├── summary_bar.py   # 底部汇总统计栏
│   └── log_panel.py     # 日志面板（QTextEdit）
├── core/
│   ├── analyzer.py      # 音频分析引擎（从现有脚本迁移）
│   ├── ffmpeg_utils.py  # ffmpeg/ffprobe 调用封装
│   ├── scanner.py       # 扫描调度器（文件遍历 + 任务分发）
│   └── renamer.py       # 重命名逻辑（预览 + 执行）
├── models/
│   └── result_item.py   # 数据模型：单首歌曲的分析结果
├── threads/
│   ├── scan_worker.py   # QRunnable：单首歌曲分析任务
│   └── scan_manager.py  # 管理线程池、任务队列、进度回传
└── resources/
    ├── style.qss        # 深色主题样式表
    └── icon.png         # 软件图标
```

### 核心类设计

**MainWindow** (main_window.py)
- 职责: 主窗口布局、菜单栏、信号连接
- 持有: LeftPanel, ResultTable, ProgressBar, SummaryBar, LogPanel
- 信号: `scan_started()`, `scan_progress(int, int, str)`, `scan_finished(list)`, `rename_preview(list)`

**ScanManager** (threads/scan_manager.py)
- 职责: 管理整个扫描生命周期
- 方法: `start_scan(folder_path)`, `stop_scan()`
- 内部: 用 `QThreadPool.globalInstance()` 调度 ScanWorker
- 关键: 通过 `pyqtSignal` 把结果从工作线程回传到主线程
- 信号: `result_ready(ResultItem)`, `progress_updated(int, int, str)`, `scan_completed()`

**ScanWorker** (threads/scan_worker.py) —— 继承 QRunnable
- 职责: 分析单首歌曲
- 方法: `run()` —— 调用 ffmpeg 转码 → 加载 WAV → FFT 分析 → 返回 ResultItem
- 注意: 不操作 UI，只计算，通过信号发结果

**Analyzer** (core/analyzer.py)
- 职责: 纯计算，无 Qt 依赖，复用现有脚本逻辑
- 方法: `analyze(filepath) -> dict` —— 返回频谱截止、DR、假无损判定等
- FFT 后端: 优先 numpy，回退纯 Python

**ResultItem** (models/result_item.py) —— 数据类
```python
class ResultItem:
    filename: str       # 原文件名
    stem: str           # 无扩展名
    filepath: str       # 完整路径
    score: float        # 综合评分 0-100
    cutoff_60db: float  # -60dB 截止频率
    dr: float           # 动态范围
    is_fake: bool       # 是否假无损
    fake_reasons: list  # 假无损原因列表
    status: str         # "pending" / "analyzing" / "done" / "error"
```

**Renamer** (core/renamer.py)
- 职责: 根据 ResultItem 列表生成重命名计划
- 方法: `build_plan(results) -> list[(old, new, reason)]`
- 安全: 防重复后缀、目标存在检测、LRC 同步
- 预览: 返回计划列表但不执行
- 执行: `execute_plan(plan) -> (success_count, fail_count)`

---

## 七、数据流与信号槽（关键）

```
用户点击[开始扫描]
    ↓
MainWindow.start_scan() —— 清空表格，重置进度
    ↓
ScanManager.start_scan(folder)
    ├── 遍历文件夹，收集音频文件列表（主线程）
    ├── 对每个文件创建 ScanWorker，提交到 QThreadPool
    └── 连接信号: worker.signals.result → on_result_ready()
    ↓
ScanWorker.run()（在子线程中执行）
    ├── ffmpeg 转码前 30 秒为 temp WAV
    ├── Analyzer.analyze() 计算频谱/DR/假无损
    └── signals.result.emit(result_item)
    ↓
ScanManager.on_result_ready()（槽函数，通过信号跨线程）
    ├── 收集结果到列表
    └── 转发: self.result_ready.emit(result_item)
    ↓
MainWindow.on_result_received()（主线程更新 UI）
    ├── ResultTable.add_row(result_item)
    ├── ProgressBar.set_value(current, total)
    └── SummaryBar.update_stats()
    ↓
全部完成 → ScanManager.scan_completed.emit()
    ↓
MainWindow 弹出"扫描完成"提示，启用[导出CSV]和[重命名]按钮
```

**关键规则**: 任何 UI 更新必须在主线程执行。工作线程只能通过 pyqtSignal 发数据，不能直接操作 QWidget。

---

## 八、ffmpeg 内置策略

软件目录结构（打包后）:
```
AudioQualityScanner/
├── AudioQualityScanner.exe
├── _internal/           # PyInstaller 生成的依赖目录
└── ffmpeg/
    ├── ffmpeg.exe       # 内置精简版 (~25MB)
    └── ffprobe.exe      # 内置精简版 (~20MB)
```

**运行时查找逻辑** (ffmpeg_utils.py):
```python
def get_ffmpeg_path():
    # 1. 检查内置路径
    bundled = Path(sys.executable).parent / "ffmpeg" / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    # 2. 检查系统 PATH
    system = shutil.which("ffmpeg")
    if system:
        return system
    # 3. 未找到
    return None
```

**错误处理**: 如果内置和系统都找不到 ffmpeg，弹窗提示用户下载，并提供 Gyan 官网链接。

---

## 九、打包规范

### PyInstaller 参数
```bash
pyinstaller   --name "AudioQualityScanner"   --onedir   --windowed   --icon=resources/icon.ico   --add-data "resources;resources"   --add-data "ffmpeg;ffmpeg"   --hidden-import=numpy   --hidden-import=PyQt6   main.py
```

**为什么用 --onedir 而不是 --onefile**:
- 启动速度快（onefile 每次启动要解压到 temp）
- 减少杀毒软件误报
- 方便用户替换内置 ffmpeg 版本
- 绿色版体验：复制整个文件夹就能用

### 输出物
- `dist/AudioQualityScanner/` 文件夹
- 用户解压后双击 `AudioQualityScanner.exe` 即可运行
- 无需安装 Python，无需配置环境变量

---

## 十、开发顺序（里程碑）

**M1: 骨架与界面（Day 1）**
- 创建项目结构
- 实现 MainWindow 三栏布局
- 实现 LeftPanel（选文件夹按钮、拖拽区域、控制按钮）
- 实现 ResultTable（空表格，带表头）
- 实现 ProgressBar 和 SummaryBar（静态占位）
- 应用 QSS 深色主题
- 可运行：窗口能打开，有基本布局

**M2: 分析引擎集成（Day 2）**
- 迁移现有 Analyzer 到 core/analyzer.py（去 GUI 依赖）
- 实现 ffmpeg_utils（内置 ffmpeg 查找 + 转码）
- 实现 ScanWorker（QRunnable，单首分析）
- 实现 ScanManager（线程池调度 + 信号回传）
- 可运行：选文件夹 → 开始扫描 → 表格逐行填充结果

**M3: 重命名与导出（Day 3）**
- 实现 Renamer（预览 + 执行）
- 实现 CSV 导出
- 实现重命名预览对话框（模态窗口，显示旧名→新名列表，确认按钮）
- 可运行：扫描完成后可导出 CSV，可一键重命名

**M4: 细节打磨（Day 4）**
- 结果筛选器（全部/真无损/假无损）
- 表头排序
- 日志面板
- 停止扫描功能
- 错误处理（文件损坏、ffmpeg 失败等）

**M5: 打包测试（Day 5）**
- PyInstaller 打包调通
- 在无 Python 环境的 Windows 虚拟机测试
- 处理路径中文/特殊符号问题
- 处理 Defender 误报（加版本信息、图标）

---

## 十一、已知风险与对策

| 风险 | 对策 |
|------|------|
| PyInstaller 打包后 numpy 加载失败 | 用 `--hidden-import=numpy.core._dtype_ctypes` 等参数显式包含 |
| 多线程下 ffmpeg 临时文件冲突 | 每个 ScanWorker 用独立 temp 目录，或文件名加随机后缀 |
| 表格数据量大（>1000首）卡顿 | 用 QTableView + QAbstractTableModel 替代 QTableWidget（MVP 阶段先用 QTableWidget，卡了再优化） |
| 用户拖拽非文件夹对象 | 判断路径是否为目录，不是则提示 |
| 扫描中途关闭窗口 | 重写 closeEvent，先停止线程池，等所有 worker 完成再退出 |

---

## 十二、给 K3 Agent 的审查清单

K3 Agent 在开始写代码前，必须逐条确认以下内容，并与用户讨论后再执行：

- [ ] 技术栈是否确认（PyQt6 + numpy + 内置 ffmpeg，不用 multiprocessing）？
- [ ] 界面布局是否认可（三栏式、深色主题、1200×800）？
- [ ] P0 功能是否完整（选文件夹、扫描、表格、进度、重命名、导出 CSV）？
- [ ] P1/P2 功能是否接受延期到二期？
- [ ] 打包方式是否认可（--onedir 绿色版，150MB 左右）？
- [ ] 开发顺序（M1→M5，5天）是否合理？
- [ ] 是否有额外的功能需求或约束需要补充？

**确认方式**: K3 逐条列出审查结论，用户回复确认后，K3 从 M1 开始执行代码生成。

---

## 附录：现有分析引擎核心代码（供迁移参考）

现有 `audio_compare_多进程并行版.py` 中的核心类 `AudioAnalyzer` 已实现：
- ffprobe 元数据抓取（含 FLAC 位深度修正）
- ffmpeg 转码前 30 秒为 WAV
- numpy FFT 频谱分析
- 多阈值频谱截止检测（-40/-60/-80 dB）
- 频谱断崖检测
- 动态范围 DR 估算
- 假无损综合判定
- 综合评分 0-100

迁移时只需剥离 GUI 无关代码，保持 `analyze(filepath) -> dict` 接口不变。
