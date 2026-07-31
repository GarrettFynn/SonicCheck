# 声鉴·曲库管家 SonicCheck（原 AudioQuality Scanner）

面向 HiFi 爱好者的 Windows 绿色版桌面工具：批量鉴定本地音乐是"真无损"还是"假无损"，并完成去重、规范命名、按歌单整理等曲库管理工作。

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/PyQt6-6.6+-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)

---

## 许可证

**本项目以 GNU General Public License v3.0（GPL v3）发布。**

> 由于本项目使用了 PyQt6 作为 GUI 框架，而 PyQt6 采用 GPL v3 许可证，根据 GPL 的传染性条款，本项目必须以相同许可证发布。
>
> 你可以自由使用、修改、分发本软件，但必须遵守 GPL v3 的要求。如果你 Fork 后希望闭源商用，需要将 PyQt6 替换为许可证兼容的框架（如 PySide6/LGPL），或购买 Riverbank Computing 的 PyQt6 商业许可证。

完整许可证文本见仓库根目录 [`LICENSE`](LICENSE) 文件。

---

## 运行（开发环境）

```bash
pip install -r requirements.txt
python main.py
```

运行需要 ffmpeg / ffprobe：放 `resources/ffmpeg/` 下，或装入系统 PATH。

---

## 打包（绿色版）

见 [BUILDING.md](BUILDING.md)：`build.bat` 一键打包，产物 `dist/SonicCheck/` 整个目录可分发。

---

## 功能特性

### 核心：无损真伪鉴定

- **六维评分体系**：位深 / 采样率 / -60dB 截止频率 / 动态范围 DR / 立体声相关性 / 峰值
- **三条假无损判定规则**：
  - 频谱断崖检测（15kHz~Nyquist 间 >30dB 落差）
  - -60dB 截止频率 < 16kHz
  - 24bit 但 DR < 4（位深造假）
- **批量扫描**：递归扫描子文件夹，多线程并发（QThreadPool），默认 8 线程可调
- **协作式取消**：扫描中可随时停止，不残留临时文件

### 曲库管理

- **智能重命名**：按质量判定自动加 `[真无损]`/`[假无损]` 标记，自动剥离旧标记防套娃
- **标签改名**：按 audio metadata（ID3/Vorbis Comment）规范化文件名，`歌名 - 歌手` 或 `歌手 - 歌名`
- **去重与对比**：
  - 同名对比（规范化文件名分组）
  - 内容签名去重（时长/DR/相关度/截止频率四指标 + 并查集，跨文件名识别重复）
  - 清除操作移至 `_待清除/`（可还原），生成 `.restore_map.json`
- **歌单导入**：支持 `.md`/`.csv`/`.txt` 本地歌单、手动粘贴、**网易云链接零登录全量解析**
- **导出 CSV**：UTF-8-BOM 编码，范围可选（全部/选中/仅假无损/仅真无损）

### 安全与易用

- **安全模式**：所有写操作变为复制/只读，输出到 `<文件夹名>_安全输出/`，目标已存在不覆盖
- **拖拽支持**：直接拖文件夹到窗口
- **表格排序/多选/右键菜单**：打开所在文件夹、复制路径
- **新手引导**：首启非模态引导窗口，内置使用指南与迭代计划

---

## 技术栈

| 组件 | 用途 | 许可证 |
|---|---|---|
| Python 3.10+ | 运行时 | PSF License |
| PyQt6 ≥ 6.6.0 | GUI 框架 | GPL v3 |
| numpy ≥ 1.26.0 | FFT 与向量化数值计算 | BSD 3-Clause |
| ffmpeg / ffprobe | 音频解码、转码、元数据读取 | LGPL v2.1+ / GPL v2+ |
| PyInstaller | 打包工具（构建时使用）| 特殊例外条款 |

---

## 算法来源与致谢

### 核心分析引擎

本项目的音频分析引擎（`core/analyzer.py`）**基于早期命令行脚本 `audio_compare_多进程并行版.py` 进行工程化重写**。

- **保留**：判定口径（断崖检测、-60dB 截止、24bit+低 DR 三条规则）、六维评分权重、DR 计算算法——均与原始脚本逐字一致
- **迁移验证**：通过 `dev/compare_new_vs_legacy.py` 用同批 FLAC 样本做全字段比对，diff 为 0.00e+00
- **主要改进**：GUI（PyQt6）、多线程调度（QThreadPool）、numpy 向量化（替代纯 Python 循环）、ffmpeg 统一转码（修复 WAV 频率轴 bug）、文件管理功能

### 社区资源

| 来源 | 贡献 |
|---|---|
| **Hydrogenaudio 论坛** | 无损音频检测的方法论讨论，频谱断崖作为有损编码痕迹的判定共识 |
| **What.CD** | 黄金时代的音乐共享社区，其内部检测标准影响了 cutoff 阈值和 DR 容差设定 |
| **DR Meter** | 动态范围（DR）的计算算法参考，分块 RMS 取 Top 20% vs Bottom 20% 的差值 |
| **ffmpeg（Gyan builds）** | 音频解码、格式转换、元数据读取 |
| **numpy** | FFT 计算（`numpy.fft.rfft`）、向量化数值运算 |
| **PyQt6** | 跨平台 GUI 框架 |

---

## 里程碑状态

- [x] M1 界面骨架
- [x] M2 分析引擎集成（ffmpeg 内置查找、QThreadPool、协作式停止）
- [x] M3 重命名与导出（预览/执行、CSV UTF-8-BOM）
- [x] M4 错误处理与边界打磨
- [x] M4.5 二期功能批：详情 / 同名对比 / 去重清除 / 标签改名 / 歌单导入 / 网易云链接全量解析
- [x] M4.6 增强批：自定义质量标记、多选与筛选导出、去重还原、安全模式、使用指南、改名 SonicCheck
- [x] M5 打包材料：`build.spec` + `build.bat` + BUILDING.md（用户本机执行）

---

## 目录结构

```
SonicCheck/
├── main.py              # 入口
├── main_window.py       # 主窗口（布局、拖拽、状态记忆、写操作调度）
├── widgets/             # 左栏 / 结果表格 / 进度 / 汇总 / 日志 / 各对话框
├── core/                # 分析引擎、ffmpeg 封装、扫描、重命名、去重、歌单、质量标记
│   ├── analyzer.py      # 音频分析引擎（numpy 向量化版）
│   ├── scanner.py       # 文件扫描器（递归、容错）
│   ├── deduper.py       # 去重引擎（并查集内容签名）
│   ├── playlist.py      # 歌单解析与匹配
│   ├── renamer.py       # 智能重命名
│   ├── tag_renamer.py   # 标签改名
│   ├── csv_exporter.py  # CSV 导出
│   ├── quality_marks.py # 质量标记配置
│   └── ffmpeg_locator.py# ffmpeg 自动查找
├── models/              # ResultItem 数据模型
├── threads/             # ScanWorker / ScanManager（QThreadPool 调度）
├── resources/           # style.qss / icon.png / icon.ico / ffmpeg（打包前放入）
├── dev/                 # 各里程碑测试（沙盒副本上进行，不动 samples 原件）
├── requirements.txt     # Python 依赖
├── build.spec           # PyInstaller 打包配置
├── build.bat            # 一键打包脚本
├── BUILDING.md          # 打包说明
├── LICENSE              # GPL v3 许可证
└── README.md            # 本文件
```

---

## 贡献

欢迎提交 Issue 和 Pull Request。由于是业余项目，响应可能不及时，但会逐一查看。

提交 PR 前请注意：
- 代码风格保持与现有文件一致
- 核心分析引擎（`analyzer.py` 中的判定口径与评分公式）的修改需附带新旧脚本比对验证
- 新增功能请补充对应的 `dev/` 测试

---

## 免责声明

本软件按"原样"提供，不提供任何明示或暗示的担保。软件的分析结果基于频谱特征和统计指标，仅供参考。对于因使用本软件导致的任何数据丢失或文件损坏，作者不承担责任。建议在安全模式下首次使用，或先备份重要数据。

---

> **祝你的曲库里，没有假无损。** 🎧
