# SonicCheck v1.1.0 开发日志（本轮开发全记录）

> **用途**：供撰写其他文档（发布稿/宣传稿/教程）的 agent 使用的一手素材。
> **时间**：2026-08-06 18:30 ~ 2026-08-07 02:50（约 8 小时，含上一 compacted 会话）
> **版本**：v1.0.0 → **v1.1.0**（git 提交 `b5d4b15` → `381d9a3`，共 4 个提交 + tag v1.1.0，已推送 GitHub）
> **项目路径**：`D:\Dev\SoundProject\SonicCheck`（GitHub: GarrettFynn/SonicCheck）

---

## 一、本轮开发全景（一句话版）

在 v1.0.0（无损鉴定+曲库管理）基础上，**新增"加密音乐格式解锁"完整功能链路**（网易云 ncm、QQ 音乐 QMC 全系 → 标准 flac/wav），随后进行了一轮**全项目代码审查**，修复 3 个 P1 bug、5 个 P2 问题、完成 4 项 P3 加固，测试扩充到 **101 项断言双环境全绿**，最终完成 PyInstaller 打包（123 MB zip）并推送 GitHub。

## 二、开发时间线与需求来源

按用户对话顺序（每项都是用户明确提出后实施）：

1. **安全模式疑问**（附件 `[182510] 安全模式已开启：所有产.txt`）→ 解释安全模式机制
2. **音质影响疑问** → 确认：解锁/鉴定全程只读源文件或无损重封装，对音质零影响
3. **发版前自查要求** → 用户要更新 GitHub 发布版本，要求先自查潜在问题
4. **提出解锁功能**：网易云 ncm、QQ 音乐 mflac 转无损 flac/wav
5. **提供测试样本**：`D:\Dev\SoundProject\测试文件`（13 个真实文件，复制到 `_test_real/`）
6. **QQ 密钥导入方案确认**：参考 qmdec 的 MmkvParser 思路 → 改为从运行中的 QQ 音乐客户端内存提取登录态；确认需要打开 Win 端 QQ 音乐并登录下载过这些歌的（VIP）账号
7. **确认开工 + 要求软件内置教程入口** → 新增 `QQMusicGuideDialog` 图文教程
8. **"mflac 解出的文件不带标签什么意思"** → 解释 musicex 文件本身不携带 ID3/封面 → 用户明确要求联网补标签功能
9. **全面测试 + 潜在问题排查 + 改进建议**
10. **指定四项优先改进**：大文件内存占用、密钥导入后一键续跑、单文件进度、测试一键 runner
11. **全项目完成度审查** → 产出 P1/P2/P3 分级问题清单
12. **确认修复全部三级问题 + 全量回归**
13. **确认打包** → PyInstaller 出 exe + 冒烟
14. **确认发布物料** → zip + Release 文案
15. **确认 git 提交计划** → 5→4 个提交（tests/ 应用户要求移出公开仓库）
16. **推送 GitHub**（遇网络阻断，配代理后成功）

---

## 三、新功能：格式解锁（核心交付）

### 3.1 模块构成

| 文件 | 行数 | 职责 |
|---|---|---|
| `core/unlock.py` | 1071 | 解密算法 + 流式管线 + 输出管线（打标签/转码） |
| `core/qqmusic_key.py` | 474 | musicex 尾部解析、密钥库、客户端内存提取 cookie、CgiGetVkey 接口、DPAPI |
| `widgets/unlock_dialog.py` | 396 | 解锁对话框 UI（文件列表/输出设置/密钥导入/进度） |
| `widgets/qqmusic_guide_dialog.py` | 85 | QQ 音乐解锁图文教程对话框 |

### 3.2 支持的格式

- **网易云 `.ncm`**：AES-128-ECB（纯 Python 实现，含 S 盒/逆 S 盒运行时生成）解密钥 → 自定义 KeyBox XOR 流（注意：不是标准 RC4 KSA，含 last_byte 链）
- **QQ 音乐 QMC 全系**：`.mflac .mflac0/1/a/h/l/m .mgg .qmc0-8 .qmcflac .qmcogg .tkm .bkc*` 等 30+ 扩展名；三种流密码按密钥长度分流：
  - 密钥 1~300 字节 → **map 密码**（`idx=(o²+71214)%n`，循环左移 `((idx&7)+4)%8` 位）
  - 密钥 >300 字节 → **分段 RC4**（首 128 字节特殊处理，之后 5120 字节一段，段内 skip 由 hash 与段种子决定）
  - 无密钥（旧版）→ **static 静态密码盒**（256 字节常量，`idx=(o²+27)&0xFF`）
- **密钥来源三种尾部**：数字长度前缀 EKey（TEA-CBC 双层派生，v2 前缀 `QQMusic EncV2,Key:` 再走双层 TEA）、`QTag` 双段尾部（`songmid,ekey` 逗号分隔，兼容单段纯 ekey）、`musicex` 尾块（密钥不在文件内，见 3.3）

算法逐字节对照 unlock-music CLI v0.2.12 与 qmdec（MIT）参考实现；全部测试用合成文件做加密→解密往返验证逐字节一致。

### 3.3 QQ 新版 musicex 加密与密钥导入

- **识别**：文件末 8 字节 magic `musicex\0`；-16 处 LE u32 为尾块长度；尾块 [28:88] UTF-16-LE = song_mid，[88:184] = 客户端原始文件名（`F0M0xxx.mflac` 等前缀决定音质，不可改写）
- **取密钥链路**：`parse_musicex_info` 解析尾部 → 本地密钥库 `EkeyStore`（`~/.soniccheck/qqmusic_ekeys.json`，按 `mid:`/`file:` 双键索引）→ 无缓存则从 **QQMusic.exe 进程内存**只读扫描（Win32 EnumProcesses + VirtualQueryEx + ReadProcessMemory，找 `qqmusic_key=` 标记，区域上限 50MB）提取 cookie → 官方接口 `POST https://u.y.qq.com/cgi-bin/musicu.fcg`（module=`vkey.GetVkeyServer`, method=`CgiGetVkey`）逐首换 ekey → 双键落盘
- **容错**：一轮失败自动重新提取登录态再试一轮（仅当 cookie 变化）；实测 7/7 成功
- **无密钥文件**：抛出带操作引导的 `UnlockError`（指引用户点"导入密钥"或看"教程"）

### 3.4 联网补标签

musicex 文件本身不含歌名/歌手/封面。解锁后按 song_mid 调 **`get_song_detail` 详情接口**（module=`music.pf_song_detail_svr`）精确取回标签；封面走图床 `y.gtimg.cn/music/photo_new/T002R500x500M000{album_mid}.jpg`。
**关键决策**：不能用搜索接口——会把 mid 当关键词返回错歌（踩过坑，已定型为详情接口）。失败一律静默降级为"无标签输出"，不影响解锁本身。

### 3.5 标签/封面写入方案

中文标签走 **ffmetadata1 文件**（`;FFMETADATA1` 头 + `\ = ; #` 转义规则，UTF-8 落盘）+ ffmpeg `-map_metadata` 输入，绕开 Windows 命令行 ANSI 编码乱码；封面以 `-map 1:v -disposition:v:0 attached_pic` 内嵌（flac/mp3）。打标签失败保底输出无标签文件，不让解锁失败。

### 3.6 流式解密（本轮重点改进）

**问题**：原方案整个文件读进内存，峰值 ≈ 3×文件大小（300MB 文件 → ~900MB）。
**方案**：
- NCM：`_ncm_read_header(f)` 文件对象顺序读头（内存/流式共用），音频按 `_STREAM_CHUNK = 8MB` 分块 XOR（`_ncm_xor_audio` 加 `offset` 参数，keystream 按流内绝对偏移 `j=(i+1)&0xFF` 计算）
- QMC：只读尾部 `_QMC_TAIL_READ = 1MB+4096+32` 定位密钥/边界（`_qmc_parse_footer` 签名改为尾部切片+真实大小）；三种流密码 keystream 只依赖绝对偏移，天然支持随机访问分块
- 解密产物先写输出目录内 `unlock_*.bin` 临时文件（同盘 rename 保证原子性），`_write_output` 接管清理，`unlock_file` finally 兜底
**实测**（296.2MB「大石碎胸口.ncm」）：峰值内存 **75.9MB ≈ 文件 25.6%**（tracemalloc），其中约 32MB 来自封面上限；进度回调 36 次（296MB/8MB），单调递增收尾 100%。

### 3.7 解锁对话框 UX

- 文件列表（添加文件/文件夹/清空，自动识别格式并打 `[NCM]`/`[QMC]` 前缀）、输出目录（默认首个文件同级 `已解锁/`）、输出格式（自动保留内层/强制 FLAC/强制 WAV）
- 「联网补齐 QQ 音乐标签」开关（默认开）
- 「导入 QQ 音乐密钥」按钮 + 「教程」按钮 + 密钥库缓存计数显示
- 「导入后自动开始解锁」开关（默认开）：导入成功（fetched+cached>0）且有文件且未在跑 → 自动 `_on_start()`
- 状态栏双级进度：整体 `(3/13)` + 单文件字节级 `— 45%`（`file_progress(int,int,int)` 信号）
- 有损内层（mp3/ogg/m4a）强制转无损容器时明确提示"只是换容器，音质不变"

---

## 四、全项目审查与修复（P1/P2/P3 全记录）

审查范围：全部 6524 行 Python（17 个 core/widgets/threads 文件逐一精读）。

### 🔴 P1（3 个真 bug，发版必修）

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | ffmpeg 转码失败留半截输出文件，重跑生成 `xxx (1).flac` 垃圾累积 | `_write_output` 转码失败只抛错不清理 dst | 拆 `_write_output`/`_write_output_inner` 两层，外层捕获 BaseException 删 dst（dst 由 `_unique_path` 保证调用前不存在，删除安全） |
| 2 | 解锁单文件期间无法取消，关窗 `waitForDone(-1)` 假死最长 2 分钟 | 取消标志只在文件之间检查；ffmpeg 用 `subprocess.run` 不可中断 | `unlock_file` 加 `cancel_check`：流式循环每块检查；ffmpeg 改 Popen 轮询+kill（复用 analyzer 模式）；新增 `UnlockCancelled` 异常，worker 捕获后静默收尾不算失败 |
| 3 | 密钥导入无法取消 | `ensure_ekeys` 无中断机制 | 加 `cancel_check`（逐首+提取登录态前检查），已获取密钥照常落盘，结果带 `cancelled` 标记；对话框独立 `_cancel_import` 事件，取消后不自动续跑 |

### 🟡 P2（5 项小 bug/一致性）

| # | 问题 | 修复 |
|---|---|---|
| 4 | 扫描停止后"分析中…"行永久悬挂 | `ResultTable.mark_unfinished_cancelled()` 统一标记「已取消」，日志带数量 |
| 5 | 非安全模式去重后清除项以 `_待清除/` 路径留在结果表，后续重命名会误操作垃圾桶文件 | 移动成功后从 `_results` 和表格移除（与安全模式一致） |
| 6 | 解锁完成行丢失 `[NCM]/[QMC]` 前缀 | `_fmt_tags` 字典保留格式标签（修完冒烟又抓到小写 bug：`[ncm]`→`[NCM]` 经 `_FMT_TAG` 映射） |
| 7 | `_MUX_TIMEOUT=120s` 对 300MB+ hi-res 转码不够且超时报错误导 | `_mux_timeout_for()` 按 2MB/s 保守速率随大小缩放（300MB→150s，1GB→512s）；`_run_ffmpeg` 返回 `ok/error/timeout` 三态，超时文案区分 |
| 8 | 歌单短名误配（如「笑」匹配任何含"笑"文件名） | 文件名模糊匹配要求双方规范化后 ≥2 字符 |

### 🔵 P3（4 项加固）

| # | 事项 | 实施 |
|---|---|---|
| 9 | cookie 明文落盘风险 | **Windows DPAPI**（ctypes CryptProtectData/UnprotectData）加密 cookie 字段，前缀 `dpapi:` 标识；换机/换用户/乱码自动失效回退重导；ekeys 本身保持明文（单文件密钥，敏感度低） |
| 10 | README 引用的 `BUILDING.md`/`build.bat` 不存在 | 补齐两文件：build.bat（PyInstaller onedir + windowed + 图标 + add-data resources + exclude tests）；BUILDING.md 含前置条件、PyInstaller 6 的 `_internal` 布局说明、发版检查清单、GPL 分发注意 |
| 11 | `release_notes_v1.0.0.md` 与 APP_VERSION=0.3.0 不一致 | **版本号决策**：GitHub 已发布 v1.0.0，再发 0.3.0 会版本倒挂 → `APP_VERSION` 提为 **1.1.0**，新写 `release_notes_v1.1.0.md` |
| 12 | analyzer/deduper/renamer/playlist 零测试 | 新增 `tests/test_file_ops.py`（32 项，真实文件计划→执行→验证往返） |

---

## 五、测试体系（101 项断言）

| 套件 | 项数 | 覆盖 |
|---|---|---|
| `tests/test_unlock.py` | 28 | AES-128 ECB 标准向量、TEA-CBC 往返（测试侧实现 encrypt）、NCM 全结构合成解密逐字节一致、QMC map/RC4/static 三密码、数字尾部 EKey 全链路、STag 引导错误、双段 QTag、端到端 ffmpeg 标签、**第 9 节流式专项**（`_STREAM_CHUNK` 强制 64KB 跨块：NCM/QMC 流式逐字节一致、进度单调收尾 100%、XOR offset 分段等价、小分块端到端） |
| `tests/test_qqmusic_key.py` | 41 | musicex 尾部解析（文件版/内存版）、EkeyStore 双键索引/去重计数、cookie 提取 mock、fetch_ekey 流程、ensure_ekeys 重试逻辑 |
| `tests/test_file_ops.py` | 32 | renamer（标记加剥/套娃防护/撞名/lrc 同步/安全复制不覆盖）、tag_renamer（非法字符净化、音频跳过时 lrc 跟随跳过防张冠李戴）、deduper（标签+内容签名并查集分组、清除→manifest→还原往返）、playlist（标签精确>文件名模糊、单字歌名不误配） |
| `tests/run_all.py` | — | 自动发现 `test_*.py` 逐个子进程跑，任一失败退出码 1 |

**验证矩阵**：托管 Python 3.12.13 ✓ / 生产 Python 3.11.9（`$DAIMON_USER_PYTHON`，Qt 6.11）✓，三套件全绿。
**专项冒烟**：取消路径（296MB 第 3 块取消 → UnlockCancelled + 输出目录 0 残留）、立即取消、DPAPI 往返/乱码容错/落盘无明文、UI 离屏（QT_QPA_PLATFORM=offscreen）实例化 UnlockDialog/ResultTable。
**真实样本**：13 个用户文件（`_test_real/`）30 秒鉴定 8 真 6 假；解锁产物 13 个带标签文件留 `_test_real/已解锁/` 供用户检查。

---

## 六、打包与发布

### 6.1 打包

- 工具：PyInstaller 6.21.0（生产 Python 3.11），命令等价于 build.bat：`--noconfirm --clean --windowed --name SonicCheck --icon resources/icon.ico --add-data "resources;resources" --exclude-module tests main.py`
- 坑：git bash 会吞反斜杠，`resources\icon.ico` 被读成 `resourcesicon.ico` → 改用正斜杠
- 产物：`dist/SonicCheck/` 314MB（onedir），exe 4.7MB；ffmpeg/ffprobe 在 `_internal/resources/ffmpeg/`（`sys._MEIPASS` 兼容逻辑已内置在 `ffmpeg_locator` 与 `resource_path`）
- 冒烟：离屏模式启动存活 10 秒，依赖加载正常
- 发布包：`SonicCheck_v1.1.0_win64.zip` **123.3MB**（zip 压缩 314MB→123MB，10 秒）

### 6.2 git 提交（推送 GitHub 的 45 文件清单）

远程有两个：`github`（目标）与 `huawei`（未动）。4 个提交：

```
381d9a3 chore: 移除过期设计文档（AGENTS.md / 一期 PRD）
ce42436 docs: v1.1.0 打包链路与发布物料
51e2ee1 feat: 扫描与曲库管理稳定性增强
1496540 feat: 加密音乐格式解锁（ncm/QMC 流式解密 + QQ 密钥导入）
```

**用户明确要求不推送**：tests/（从已提交历史中 soft-reset 移除 + .gitignore 永久本地保留，历史零痕迹）、`_test_real/`（4GB 私有样本）、`resources/ffmpeg/`（193MB 二进制）、dist/build/*.spec/*.zip、`COMMIT_PLAN_v1.1.0.md`、`RELEASE_DRAFT_v1.1.0.md`，以及用户自己放进文件夹的一批文档（`douyin_article*.md`、`PROJECT_LOG.md`、`PACKAGING_GUIDE.md`、`三目录合并整理指南.md`、`使用说明.txt`、`源码获取方式.txt`，均未跟踪）。
注意：AGENTS.md 和旧 PRD 在回退操作期间一度被还原回磁盘，已按用户批准重新删除提交。

### 6.3 推送（网络插曲）

GitHub 直连被重置（两次 21s 超时）；扫描本机代理端口初为空 → 用户开启代理后 7890 端口开放、curl 连通 → `git config http(s).proxy http://127.0.0.1:7890` → `git push github main`（`b5d4b15..381d9a3`）+ `git push github v1.1.0` 均成功。**代理配置已写入本地 git 配置，后续推送自动生效。**

### 6.4 待用户手工完成

1. 人工冒烟：双击 exe 扫描+解锁验证渲染（离屏验证不了）
2. 浏览器创建 Release：tag 选 v1.1.0，描述粘 `RELEASE_DRAFT_v1.1.0.md`，Asset 传 zip
3. 可选清理：dist/zip/build 缓存约 440MB

---

## 七、关键设计决策与口径（写文档时勿违背）

1. **源文件只读**：解锁/鉴定全程不动原文件；输出一律撞名加序号 `(n)`，不覆盖
2. **"自动"输出格式是推荐项**：保留内层原始格式（无损重封装），强制 FLAC/WAV 对有损内层只是换容器——UI 与文案必须明确提示，避免"音质提升"误解
3. **音质零影响**：解锁是解密+重封装（位对位），鉴定只读，安全模式全部产出走副本
4. **安全模式语义**：所有写操作变复制/只读，输出到 `<文件夹名>_安全输出/`；扫描排除 `_待清除` 与 `*_安全输出` 目录
5. **判定口径冻结**：假无损三规则（断崖<20kHz / -60dB截止<16kHz / 24bit+DR<4）与六维评分权重为 v1.0.0 与用户拍板的口径，注释标"口径勿动"
6. **判定逻辑是排除法**：判"假"可信度>95%（物理证据），判"真"≈85-90%（没找到假证据）
7. **取消语义**：协作式取消（标志位+阶段边界检查），取消≠失败，不产错误行、不留垃圾
8. **单文件失败不中断整批**：任何批量操作（扫描/解锁/重命名/移动）单条失败转错误行继续
9. **临时文件零容忍**：82.7GB 泄漏事故的教训——所有临时文件（转码 WAV、解锁裸流、ffmetadata、封面）都有 finally 兜底 + 启动时 `sweep_temp_dir()` 清扫
10. **免责边界**：解锁功能定位为用户已合法获得文件的本地格式转换；cookie/ekey 不出本机（DPAPI 落盘）；STag 格式（密钥完全不离线）无法本地解密，只能旧版客户端重下

## 八、环境与依赖

- 开发/生产 Python：3.11.9（`C:\Program Files\Python311`）；托管验证 Python：3.12.13
- 运行时依赖仅 2 个：PyQt6≥6.6（实际 6.11）、numpy≥1.26；ffmpeg/ffprobe 外置二进制（打包内置）
- 零新增第三方依赖：AES/TEA/RC4 全部纯 Python 实现；网络请求全部 urllib 标准库；DPAPI 走 ctypes
- 打包：PyInstaller 6.21.0；图标 `resources/icon.ico`；QSettings 组织/应用名均为 SonicCheck
- 密钥库路径：`~/.soniccheck/qqmusic_ekeys.json`（含 ekeys 双键索引、DPAPI 加密 cookie、uin）

## 九、已知边界与二期候选（未做）

- 设置面板按钮仍为 disabled 占位（线程数/分析时长在左栏）
- 声纹指纹去重（chromaprint）：跨音质版本同曲识别，二期候选
- 迷你频谱图缩略列、单文件拖拽分析、QQ 音乐歌单解析、Excel/JSON 导出（旧 Roadmap）
- 迷你频谱图/设置面板阈值自定义均在一期 Roadmap 列出但未承诺本期
- 杀软可能对 PyInstaller exe 误报（通病，发布文案已提示加白名单）

---

*本日志由开发 agent 在发版完成后凭会话记录与代码实况撰写，所有数据（内存实测、测试项数、提交哈希、文件大小）均可在仓库与本地环境复现验证。*
