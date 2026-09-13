# SonicCheck v1.0.0 打包清单

> 本文档说明：如何把 SonicCheck 打包成一个可以直接发给别人的压缩包。

---

## 一、打包前准备（开发者端）

### Step 1：确认 ffmpeg 已就位

在打包之前，必须确认 `resources/ffmpeg/` 下面有这两个文件：

```
resources/
├── ffmpeg/
│   ├── ffmpeg.exe      ← 必须有
│   └── ffprobe.exe     ← 必须有
├── icon.ico
├── icon.png
└── style.qss
```

如果没有，去 https://www.gyan.dev/ffmpeg/builds/ 下载 `ffmpeg-release-essentials.zip`，解压后把 `bin/ffmpeg.exe` 和 `bin/ffprobe.exe` 复制到 `resources/ffmpeg/` 下。

### Step 2：执行打包

在项目根目录运行：

```
build.bat
```

脚本会自动：
1. 检查 Python 环境
2. 安装 PyQt6、numpy、PyInstaller
3. 调用 PyInstaller 打包
4. 生成 `dist/SonicCheck/` 目录

打包完成后，你会在 `dist/` 下看到：

```
dist/
└── SonicCheck/
    ├── SonicCheck.exe          ← 主程序
    ├── resources/
    │   ├── ffmpeg/             ← ffmpeg 会被自动包含（如果你提前放了）
    │   │   ├── ffmpeg.exe
    │   │   └── ffprobe.exe
    │   ├── icon.ico
    │   ├── icon.png
    │   └── style.qss
    ├── _internal/              ← PyInstaller 运行时依赖
    └── ...（其他 PyInstaller 生成的文件）
```

### Step 3：自检

1. 双击 `dist/SonicCheck/SonicCheck.exe`，确认能正常启动
2. 拖一个 FLAC 文件夹进去扫描，确认结果正常
3. 把 `dist/SonicCheck` 文件夹复制到另一台**没有装 Python 和 ffmpeg** 的电脑上，确认能正常运行

---

## 二、最终压缩包内容（发给用户的版本）

把下面这个文件夹整体压缩成 zip/rar/7z：

```
SonicCheck/                     ← 整个文件夹压缩
├── SonicCheck.exe              ← 主程序，双击运行
├── resources/
│   ├── ffmpeg/
│   │   ├── ffmpeg.exe          ← 音频解码器（必须）
│   │   └── ffprobe.exe         ← 音频信息读取（必须）
│   ├── icon.ico
│   ├── icon.png
│   └── style.qss
├── _internal/                  ← PyInstaller 运行时文件（不要删）
└── 使用说明.txt                 ← 下面这份说明文档
```

**压缩包体积**：约 300MB（主要是 ffmpeg 占了 80MB + PyInstaller 依赖库）

---

## 三、附带说明文档（使用说明.txt）

压缩包里建议放一份 `使用说明.txt`，内容如下（直接复制粘贴）：

---

═══════════════════════════════════════════════
  声鉴·曲库管家 SonicCheck v1.0.0 使用说明
═══════════════════════════════════════════════

【这是什么？】
一个无损音频真伪检测工具。拖进一个音乐文件夹，自动帮你找出
所有"假无损"——那些看起来是 FLAC、其实是 MP3 转过来的赝品。

【支持格式】
FLAC、APE、WAV、AIFF（以及同目录下的 .lrc 歌词文件）

【系统要求】
- Windows 10 / Windows 11（64位）
- 内存 4GB 以上
- 不需要安装 Python，解压即用

【使用方法】

1. 解压
   把压缩包解压到任意位置，比如 D:\Tools\SonicCheck\

2. 运行
   双击 SonicCheck.exe

3. 扫描
   把音乐文件夹拖到窗口上，或点"选择文件夹"按钮
   点"开始扫描"，等进度跑完

4. 看结果
   ● 真无损（绿色）—— 放心听
   ○ 假无损（红色）—— 鼠标悬停看原因
   ! 分析失败（黄色）—— 文件损坏或读不了

5. 批量操作（扫描完后可用）
   - 导出 CSV：把结果导出成 Excel 表格
   - 智能重命名：自动给文件名加 [真无损]/[假无损] 标记
   - 去重：找出曲库里的重复歌曲
   - 标签改名：按歌名/歌手标签规范化文件名
   - 歌单导入：粘贴网易云歌单链接，自动匹配本地歌曲

6. 安全模式
   左下角勾选"安全模式"后，所有写操作变成"复制"而不是"直接改"
   新手建议先开这个试几次，确认没问题再关

【常见问题】

Q：提示找不到 ffmpeg？
A：正常情况下压缩包已经内置了 ffmpeg。如果还是提示找不到，
   说明你解压时可能漏了 resources/ffmpeg/ 文件夹。重新解压一遍。

Q：杀毒软件报毒？
A：这是 PyInstaller 打包的通病，属于误报。加白名单即可。
   本软件开源，源码在 GitHub 上可以查验。

Q：扫描很慢？
A：默认 8 线程，可以在左栏调线程数（1~16）。
   分析时长默认 30 秒，意思是只分析每首歌的前 30 秒。
   调大这个值会更准但更慢。

Q：假无损检测结果可信吗？
A：判"假"的可信度 > 95%（断崖检测是物理层面的硬证据）。
   判"真"的可信度约 85-90%（"没找到假证据"，极少数可能漏网）。
   详细原理见 GitHub。

【GitHub 开源】
https://github.com/GarrettFynn/SonicCheck

【联系作者】
邮箱：feifeifynn@qq.com
有问题可以发邮件，业余项目，回复可能慢但一定回。

═══════════════════════════════════════════════
  祝你的曲库里，没有假无损。🎧
═══════════════════════════════════════════════

---

## 四、可选：附带源码包

因为本项目以 GPL v3 开源，按照许可证要求，分发二进制版本时
必须同时提供源代码（或提供获取源码的方式）。

两种做法：

**做法 A（推荐）**：
在压缩包里放一张文本文件 `源码获取方式.txt`，内容：
```
本软件以 GPL v3 许可证开源。
完整源代码获取地址：
https://github.com/GarrettFynn/SonicCheck
```

**做法 B**：
如果收件人没有 GitHub 访问条件，可以额外打一个源码压缩包：
```
SonicCheck-source-v1.0.0.zip
├── main.py
├── main_window.py
├── widgets/
├── core/
├── models/
├── threads/
├── resources/
├── requirements.txt
├── build.spec
├── build.bat
├── BUILDING.md
├── LICENSE
└── README.md
```

---

## 五、最终分发文件清单

如果要发给朋友，建议准备以下文件：

| 文件名 | 说明 | 大小 |
|---|---|---|
| `SonicCheck-v1.0.0.zip` | 主程序压缩包（绿色版，解压即用） | ~300MB |
| `源码获取方式.txt` | GPL v3 合规文件（放在压缩包内） | 1KB |
| `使用说明.txt` | 面向最终用户的使用指南（放在压缩包内） | 3KB |
| `SonicCheck-source-v1.0.0.zip`（可选） | 完整源码包 | ~500KB |

---

## 六、注意事项

1. **不要删掉 `_internal/` 文件夹**：这是 PyInstaller 运行时必需的依赖库
2. **不要单独运行 `SonicCheck.exe` 而不带周围文件夹**：exe 依赖同目录下的 `_internal/` 和 `resources/`
3. **ffmpeg 必须放在 `resources/ffmpeg/` 下**：如果用户手动移动了 exe 位置，ffmpeg 的相对路径会失效
4. **首次运行会在注册表写 QSettings**：存储上次打开的文件夹和窗口尺寸，这是正常的

---

打包完成，可以发了。
