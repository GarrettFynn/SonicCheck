# SonicCheck 迭代任务书与计划书（v1.3.0 → v2.0）

> **性质**：三段版本的实施任务书（做什么/怎么改/怎么验收）+ 计划书（批次/顺序/依赖/风险）。
> **基线**：v1.2.0+1（main @ f018815，CI 全绿，128 项测试断言）。
> **使用方式**：每开一个新版本，从本文档对应章节取任务清单执行；执行者按 §5 发版清单收尾。
> **撰写日期**：2026-09-30。

---

## 0. 总纲

### 0.1 版本序列与主题

| 版本 | 主题 | 一句话目标 | 预估工作量 |
|---|---|---|---|
| **v1.3.0** | 可视化与体验 | 频谱图让判定"看得见"，设置面板落地，表格/报告/文件夹体验补齐 | 2-3 个业余日 |
| **v1.4.0** | 引擎能力 | 声纹指纹去重（能力质变），升频提示、CLI、可选 C 加速 | 3-4 个业余日（含前置验证） |
| **v2.0** | 架构还债 | 表格 Model/View 重构、主窗口拆分、CI 自动打包发版、自动更新 | 4-5 个业余日 |

### 0.2 全程不变的四条纪律

1. **判定口径勿动**：`analyzer.py` 的假无损三规则与六维评分公式逐字保留。新指标（升频提示、指纹相似度）只做**新增展示**，不进评分、不改老字段。
2. **批式交付**：每个任务独立 commit、独立可回退；每批结束全量测试必须绿。
3. **破坏性操作宁漏勿错**：去重相关的一切新能力（指纹分组）默认保守阈值，误报代价 > 漏报。
4. **发版草稿先行**：GitHub Release 必须先建草稿 → 传资产 → 再发布（不可变 release 机制，v1.2.0 的 `+1` 后缀教训，勿再犯）。

### 0.3 横切规范

- 每个"L"级任务动手前先在 DEV_LOG 里落一小节设计要点，避免实现中途改口径。
- 新增 core 模块保持 Qt-free（可测试性根基）；UI 层改动必须配离屏冒烟脚本进 `dev/`。
- 所有新 QSettings 键统一 `settings/` 前缀命名空间（v1.3.0 任务 2 一并收拢旧键）。

---

## 1. v1.3.0 —— 可视化与体验批

### V13-1 频谱图可视化【M，本批核心】

**做什么**：详情对话框显示频谱曲线（对数频率轴、dB 轴），标注断崖频率与 -60dB 截止线；对比对话框支持双曲线同屏叠加。

**现状与数据链**（实现依据）：
- `AudioAnalyzer._spectrum` 已缓存 `(freqs, db)`（约 105 万点，30s×48k 补零到 2^21 的 rfft），但 `analyze()` 结果 dict **没有**存频谱——需要补存储。
- `DetailDialog`（widgets/detail_dialog.py，83 行）纯 QFormLayout 文字行。
- `CompareDialog`（widgets/compare_dialog.py）分左右两栏展示对比组。

**实现要点**：
1. `core/analyzer.py`：`analyze()` 返回值新增 `spectrum` 字段——把 `(freqs, db)` 降采样为 **256 点对数频率分档**（每档取区间内 dB 最大值，保断崖形态）。**存储格式必须紧凑**：对数频率网格是常量（模块级定义，所有文件共享），`spectrum` 字段只存 256 个 dB 值，用 `array('f').tobytes()` 或 base64 字符串（约 1KB/文件，万级曲库 ~10MB）；**禁止用 Python list 存浮点**——对象开销会把内存放大一个数量级（~200MB）。**注意：此改动在 analyzer 内但不触碰判定/评分路径，仅附加字段。**
2. 新建 `widgets/spectrum_view.py`：`SpectrumView(QWidget)`，`paintEvent` 用 QPainter 画折线；X 轴 20Hz–24kHz 对数刻度（画 100/1k/10k 三条网格线），Y 轴 -100–0dB；接受可选标注参数 `cliff_freq`（红色竖虚线+文字）、`cutoff_60db`（黄色竖虚线）。颜色用 style.qss 现有 VSCode 色板（曲线 #4EC9B0，网格 #3C3C3C）。
3. `SpectrumView.set_data(spectrum, cliff=None, cutoff=None)`；支持 `overlay(spectrum2)` 画第二条半透明曲线（对比模式，第二曲线 #CE9178）。
4. `DetailDialog`：表单下方加 `SpectrumView`（高度 220px），数据取 `item.detail['spectrum']`；无数据（旧结果）时显示占位文字"频谱数据不可用（请重新扫描）"。
5. `CompareDialog`：选中两行时底部出现叠加频谱图（同曲不同音质的曲线差异是卖点）；**仅 MODE_COMPARE 模式启用，MODE_DEDUPE（去重清除预览）不加**——去重界面信息密度已高，且叠加图对"选谁保留"决策无帮助。

**验收标准**：
- 双击任一扫描结果行：详情窗口出现频谱曲线，断崖文件在断崖处有红色标注线，与判定原因的频率数值一致。
- 对比对话框选两首：两条曲线同屏可分辨；同内容不同码率的两文件曲线高度重合、截止位置不同肉眼可辨。
- 离屏冒烟 `dev/render_spectrum.py`：构造含断崖的合成频谱数据渲染并 savefig 验证不崩；截图入 `dev/`。
- 全量测试绿（analyzer 附加字段不破坏现有断言）。
- **`dev/compare_new_vs_legacy.py` 全字段比对须排除新增的 `spectrum` 字段后重跑，判定/评分各字段 diff 仍为 0**（迁移验证口径不被附加字段破坏）。

**风险与回退**：降采样丢失窄带特征 → 256 点对数分档已保高频细节（高频段点密）；仍嫌粗可提 512 点。回退 = 不读 `spectrum` 字段即回旧行为。

### V13-2 设置面板落地【M】

**做什么**：启用占位至今的 `btn_settings`，新建 `widgets/settings_dialog.py`，收拢所有配置；左栏同步瘦身。

**实现要点**：
1. `SettingsDialog` 三个分组：
   - **扫描**：线程数（1-32）、每首分析秒数（10-120）、默认安全模式开关；
   - **质量标记**：真/假标记文本 + 前后缀（吸收现有 `QualityMarkDialog`，原对话框删除或变为"标记设置"入口）；
   - **高级**：临时文件清理阈值展示（只读说明）、"打开配置文件夹"按钮（QSettings 无文件，改为打开 `~/.soniccheck/`）。
2. QSettings 键收拢到 `settings/` 命名空间：`settings/threads`、`settings/seconds`、`settings/safe`、`settings/marks/*`；**旧键读取兼容**——需迁移的现有键枚举：`window_geometry`、`last_folder`（并入 V13-4 的 `recent_folders`）、`safe/enabled`、`marks/true`、`marks/fake`、`marks/position`、`guide_seen`；启动时一次性迁移函数（读旧键→写新键→删旧键），迁移逻辑配离屏单测。
3. 左栏处理（**决策点 ①**）：建议**保留**左栏的线程数/分析时长控件与设置面板双向同步（扫描是最高频操作，入口不该藏进弹窗），设置面板是统一收纳处；质量标记按钮从左栏移除（低频操作归设置面板）。
4. `btn_settings` 解除 disabled，连接打开对话框。

**验收**：改设置 → 重启程序生效；旧版本升级后配置无丢失（迁移函数 + 用临时注册表冒烟验证）；左栏不再有质量标记按钮。

### V13-3 表格筛选与记忆增强【S/M】

**做什么**：
1. 工具栏加搜索框（QLineEdit，`筛选:` 下拉旁）：按文件名/歌名/歌手标签实时子串过滤（大小写不敏感），与现有真/假筛选可叠加。
2. 列宽、排序列/方向存 QSettings，启动恢复。
3. 右键菜单加两项："只看此歌手"、"只看此格式"（取右键行的标签/扩展名，设置到新搜索框）。

**实现要点**：过滤逻辑集中在 `ResultTable.apply_filter()` 扩展（组合条件函数）；列宽用 `header.saveState()/restoreState()`（QByteArray 直接进 QSettings）。

**验收**：搜索"周杰伦" + "只看假无损"叠加生效；重启后列宽排序保持；全表 1 万行下输入搜索无明显卡顿（代理过滤是 O(n) 遍历 setRowHidden，实测 <100ms）。

### V13-4 最近文件夹列表【S】

**做什么**：`last_folder` 单值改 `recent_folders` 列表（最多 5 个，去重，最新在前）；左栏文件夹显示行改为可点击下拉（QToolButton + 菜单），保留拖拽/选择按钮。

**验收**：扫描过的文件夹出现在菜单；选择历史文件夹即设为当前；5 个上限滚动淘汰。

### V13-5 HTML 报告导出【M】

**做什么**：导出 CSV 旁新增"导出报告(HTML)"——单文件自包含报告，可直接发送。

**实现要点**：
1. 新建 `core/html_report.py`（Qt-free）：纯字符串模板生成，**不引入模板库**。
2. 内容结构：报告头（时间/文件夹/版本）→ 汇总卡片（总数/真/假/失败/平均分）→ 明细表（可排序的纯 HTML table + 内嵌 `<style>`）→ 每首假无损一个"证据卡"：判定原因 + **内联 SVG 频谱图**（用 V13-1 的 256 点数据直接生成 `<polyline>`，无 Qt 依赖、无外链）。
3. 范围选择复用 `_pick_scope`（**决策点 ②**：建议默认"仅假无损"——证据卡才有意义，全部模式表格不带卡）。
4. 文件名 `SonicCheck报告_YYYYMMDD_HHMM.html`，UTF-8。

**验收**：报告用浏览器打开无外链依赖（断网可看）；假无损卡片频谱与详情对话框一致；含证据卡的报告（≤200 张卡）<2MB，千首全量明细表 <2MB。

### V13-6 批收尾与发版【S】

版本号 1.3.0 → DEV_LOG_v1.3.0.md → README 里程碑 M8 → §5 发版清单执行。

---

## 2. v1.4.0 —— 引擎能力批

### V14-0 前置验证：chromaprint 可用性【S，批内最先做】

**做什么**：把 `resources/ffmpeg/` 换成 **gyan full 版**（现 essentials 版无 chromaprint 滤镜，已实测确认），验证能力与代价。

**步骤**：下载 full 版 → 替换 ffmpeg.exe/ffprobe.exe → `ffmpeg -filters | grep chromaprint` 确认 → 跑全量 3 套件（128 项断言）→ 记录 zip 体积增量（预计 +30~50MB，123MB → ~160MB）→ **实测指纹输出**：分别试 `-af chromaprint -f null -` 与 `-af chromaprint=fp_format=raw -f null -`，抓取 stderr 确认输出行格式、指纹编码（raw/compressed/base64）与时长-指纹长度关系，实测结论写入 DEV_LOG 后再开 V14-1。

**决策点 ③（批内最大决策）**：+40MB 体积是否可接受？——**建议接受**（对桌面用户无感，能力质变值得）；不可接受则降级方案为"首次使用指纹去重时提示下载 fpcalc.exe 到 ~/.soniccheck/"。本任务验证结论写入 DEV_LOG 后再开 V14-1。

### V14-1 指纹采集管线【M】

**做什么**：新建 `core/fingerprint.py`，对单个音频产出 chromaprint 指纹。

**实现要点**：
1. `fingerprint_file(path, analyze_seconds=120) -> str | None`：调 `ffmpeg -i <path> -t <sec> -af chromaprint=fp_format=raw -f null -`，解析 stderr 中的指纹行（**具体过滤器选项与输出格式以 V14-0 实测为准**——ffmpeg 的 chromaprint 滤镜走 `fp_format` 参数，没有 fpcalc 的 `raw=1` 旗标，别照搬 fpcalc 文档）；指纹为 32bit 字序列，随时长线性增长（120s 约数 KB），ffmpeg 失败/超时返回 None（降级不阻塞）。
2. **采集时机（决策点 ④）**：建议**去重时按需采集**——用户点"去重清除"且勾选"声纹比对"时，后台 QRunnable 对已完成扫描的文件批量算指纹（进度条），结果存内存 dict（不进 detail，避免撑大扫描结果）。理由：扫描时顺带采集会让每首多一次 120 秒转码，扫描时长翻倍，收益只在去重场景。
3. 并发：QThreadPool（线程数同扫描设置），`hidden_subprocess_kwargs` 防黑窗。

**验收**：同一首歌不同码率两文件指纹汉明距离小、不同歌曲距离大（用 `_test_real` 或合成样本定量：同类 <0.25、异类 >0.35 的阈值区间存在）；采集 100 首 <2 分钟。

### V14-2 指纹比对与第三路并查【M】

**做什么**：指纹汉明距离比对接入 `find_duplicate_groups` 作为第三路证据。

**实现要点**：
1. 比对算法：raw 指纹按 32-bit 字分块（chromaprint 标准块），归一化汉明距离（0-1），对齐搜索 ±8 块滑移取最小值；numpy popcount 向量化（`np.bitwise_xor` + 256 项查表按字节累积）。
2. **候选生成（防漏检的关键设计）**：不能按"首块哈希"分桶——同一录音不同起点/裁剪的首块必然不同，会进不同桶导致永远不被比对（漏检）。采用两级：①时长预筛（±10%，沿用现有思路）；②**多锚点子指纹倒排索引**（Shazam 式 LSH）——每条指纹取 K 个固定相对位置（如第 5/50/100 个字）的 32bit 子指纹建倒排表，任一锚点碰撞即成为候选对；候选对再走全序列滑移精算。同曲变体（不同编码/轻微裁剪）锚点字完全一致的概率极高，不同歌碰撞率极低。
3. 阈值（**宁漏勿错**）：归一化距离 ≤0.30 判"同一录音"；结果在 DupGroup 标注来源：`同名` / `同内容签名` / `声纹相似(87%)`。
4. 接口：`find_duplicate_groups(items, fingerprints=None)`——fingerprints 传 None 时行为与现在完全一致（向后兼容，测试不破坏）。
5. `CompareDialog` 每组标注相似度百分比。

**验收**：同一首歌"live 版 vs 录音室版"（同名不同内容签名）在指纹路被合并且标注相似度；**"同曲不同起点/裁剪"变体对也能命中**（锚点倒排不依赖首块，此为分桶设计的回归锚点）；不同歌曲零误合并（回归用例 ≥10 对）；不勾选声纹时全量旧测试绿。

### V14-3 升频检测辅助指标【S】

**做什么**：新增"疑似升频"提示（只展示，不评分）。

**实现要点**：`core/analyzer.py` 新函数（不动现有函数）`upscale_hint(meta, cutoffs, dr) -> str`：满足 `sample_rate ≥ 88200` 且 `-60dB 截止 < 20000`，或 `bit_depth ≥ 24 且 DR < 6`（与既有第三规则不同阈值，仅提示）返回 `"疑似升频（高采样率但有效带宽不足）"` 等，否则空。结果存 `detail['upscale_hint']`，表格状态列 tooltip + HTML 报告展示，CSV 加列。

**验收**：44.1k 正常文件无提示；96k 但截止 16k 的合成样本有提示；评分与假无损判定输出与 v1.2.0 逐字节一致（用 compare 脚本核对）。

### V14-4 CLI 无头模式【S/M】

**做什么**：`SonicCheck.exe --scan <目录> [--threads N] [--seconds N] [--out 路径.csv|.html]`。

**实现要点**：`main.py` 参数分支（argparse）；不创建 QApplication——直接 `concurrent.futures.ThreadPoolExecutor` 并行跑 `analyze_file`，进度打印 `N/M`；复用 `csv_exporter`/`html_report`（v1.3.0 已就位）。**打包冲突（必须处理）**：现打包是 `--windowed`（无控制台），exe 从命令行跑 `--scan` 时 stdout 未附着、print 无处可去——方案：用 ctypes `AttachConsole(ATTACH_PARENT_PROCESS)` 把输出接回调用方控制台（win32 惯用法，`--windowed` 下可用），并在文档注明"CLI 完整体验建议源码运行 `python main.py --scan`"；若 AttachConsole 失败（双击启动场景）则日志同时落 `soniccheck_cli.log`。

**验收**：`SonicCheck.exe --scan D:\Music --out report.csv` 无窗口出 CSV；与 GUI 扫描同目录结果行数与判定一致；`--help` 可用。

### V14-5 RC4 可选 C 加速【S，条件触发】

**触发条件**：v1.2.0/v1.3.0 期间有用户反馈大文件解锁慢（≥50MB mflac）。否则本任务跳过。

**实现要点**：`requirements` 增加可选说明（不进 requirements.txt）；运行时 `try: from Crypto.Cipher import ARC4` 探测，可用则 `_QmcRc4Cipher` 走分段 ARC4 实例 + discard skip 字节（C 速度），不可用回退现有纯 Python；`dev/bench_rc4.py` 输出两种路径吞吐对比。

### V14-6 批收尾与发版【S】

版本号 1.4.0 → DEV_LOG → README 里程碑 M9 → §5 发版清单。

---

## 3. v2.0 —— 架构批

### V20-1 表格 Model/View 重构【L，本批核心】

**做什么**：`QTableWidget` → `QAbstractTableModel` + `QSortFilterProxyModel`，删除 B1 批量模式的全部特例代码。

**实现要点**：
1. 新建 `widgets/result_model.py`：`ResultTableModel`（rows: list[ResultItem]；role 暴露 score/cutoff/dr/status/path）；`ResultProxyModel`（真/假筛选 + 文本搜索合一的 `filterAcceptsRow`，V13-3 的手工过滤整体下沉）。
2. `ResultTable` 对外 API 保留外壳（`add_row/update_row_by_path/clear_rows/selected_paths/remove_rows_by_paths/begin_update/end_update...`），内部改走 model——调用方（main_window）**零改动**或极小改动；`begin_update/end_update` 变空实现（model 原生高效），后续版本删除。
3. 解锁能力：**扫描中允许排序**（proxy 排序实时）、十万行虚拟渲染、双列排序（评分+DR）。
4. 状态列渲染：`data()` 里按 status 返回文字/颜色（替代 `_make_status_item`）；`mark_unfinished_cancelled` 改数据层状态枚举（顺手修掉"按显示文本匹配"的旧脆弱实现，UserRole 状态枚举进 model）。

**验收**：功能回归 = 全部现有交互（排序/筛选/多选/右键/双击/还原按钮状态）逐项手测清单；性能基准 = 离屏 2 万行插入 <2s、排序点击 <300ms、滚动无可感卡顿（写成 `dev/bench_table.py` 持续跑）；全量测试绿。

**风险**：这是 UI 层最大手术——先在分支/独立提交序列完成，冒烟全过再合入；回退点 = 保留旧 ResultTable 为 `result_table_legacy.py` 一个版本周期。

### V20-2 main_window 拆分【M】

**做什么**：751 行主窗口拆三。

**实现要点**：`app_controllers.py`（或 threads/ 旁新包）：`ScanController`（扫描启停/进度/汇总/批量表格调度）、`FileOpsController`（重命名/标签改名/去重/还原/歌单/导出，含 `_run_rename_plan`、`_pick_scope`、安全模式映射）；`MainWindow` 只剩布局构建、信号接线和窗口状态。**纯搬移不改逻辑**，行为逐项对照旧代码验收。

**验收**：git diff 显示逻辑块整体迁移（非重写）；全量 GUI 冒烟 + 手测主流程四条（扫描/重命名/去重/歌单）。

### V20-3 CI 自动打包 + 自动草稿 Release【M】

**做什么**：GitHub Actions 在 tag 推送时自动出 zip 并挂到**草稿** Release，人工只点发布。

**实现要点**：
1. workflow 两 job：`test`（现 tests.yml 不动）+ `build`（`on: push: tags: ['v*']`）：windows-latest → pip install -r requirements.txt -r requirements-dev.txt → 下载 gyan full ffmpeg（URL 钉版本 + sha256 校验）→ 放 resources/ffmpeg → `python -m PyInstaller`（与本地命令一致，注意用 `python -m` 避免 PATH 解析歧义——v1.2.0 本机教训）→ Compress-Archive → 用 `GITHUB_TOKEN`（`permissions: contents: write`）创建**草稿** release（幂等：已有同名草稿则复用）→ 上传 zip 资产。
2. 资产命名与本地一致 `SonicCheck_v{版本}_win64.zip`，版本号从 tag 提取。
3. **决策点 ⑤**：CI 产物是否作为正式发布源？——建议 v2.0 起双轨一次（CI 打包 + 本地打包各出 zip，比对文件清单与大小，一致后 v2.1 起只走 CI），避免环境差异引入幽灵问题。
4. 遵守不可变纪律：**只建草稿**，发布动作永远留给人工。

**验收**：推一个试验 tag（如 `v1.4.0-ci`，注意 tag 名不可复用，用一次性名字）→ 10 分钟内草稿带 zip 出现；zip 在干净 Windows 解压可运行扫描。

### V20-4 自动更新检查【S】

**做什么**：启动 3 秒后后台 `GET api.github.com/repos/GarrettFynn/SonicCheck/releases/latest`（8s 超时，任何失败静默），版本高于当前 → 日志 + 状态栏横幅"新版本 vX.Y.Z 可用 → 打开下载页"（QDesktopServices，不打扰不弹窗）。

**注意**：读取 `tag_name` 去掉 `+N` 后缀再比语义版本（v1.2.0+1 的教训要写进解析函数注释）；提供设置项可关（进 V13-2 的设置面板"高级"组）。

### V20-5 版本收尾【S】

版本号 2.0.0 → DEV_LOG_v2.0.0 → README 里程碑 M10 + 目录结构更新（result_model/controllers）→ §5 发版清单。

---

## 4. 计划书：顺序、依赖与风险登记

### 4.1 执行顺序与依赖

```
v1.3.0: V13-1 频谱图 ──→ V13-5 HTML报告(依赖频谱数据)
        V13-2 设置面板 ─┐
        V13-3 表格筛选 ─┼─ 三者无依赖可并行
        V13-4 最近文件夹 ┘
        V13-6 收尾发版

v1.4.0: V14-0 ffmpeg验证 ──→ V14-1 指纹采集 ──→ V14-2 指纹比对(核心链)
        V14-3 升频提示(独立)
        V14-4 CLI(独立，依赖V13-5的html_report)
        V14-5 RC4加速(条件触发)
        V14-6 收尾发版

v2.0:   V20-1 Model/View ──→ V20-2 主窗口拆分(在表格稳定后动控制器)
        V20-3 CI打包(独立，可提前到v1.4.0后任意时点)
        V20-4 更新检查(依赖V13-2设置面板)
        V20-5 收尾发版
```

### 4.2 决策点汇总（开工前逐项拍板）

| # | 决策 | 建议 | 影响任务 |
|---|---|---|---|
| ① | 设置面板与左栏双入口 or 单入口 | 双入口同步（扫描高频不藏弹窗），质量标记归设置 | V13-2 |
| ② | HTML 报告默认范围 | 仅假无损（证据卡场景） | V13-5 |
| ③ | ffmpeg 换 full 版 +40MB | 接受；否则降级 fpcalc 按需下载 | V14-0 |
| ④ | 指纹采集时机 | 去重时按需（不让常规扫描翻倍耗时） | V14-1 |
| ⑤ | CI 打包转正节奏 | v2.0 双轨验证一次，v2.1 起仅 CI | V20-3 |

### 4.3 风险登记册

| 风险 | 概率 | 缓解 |
|---|---|---|
| chromaprint 在 full 版输出格式与预期不符 | 中 | V14-0 前置验证先行（实测输出格式后才开 V14-1），不通过则整条链降级 fpcalc 方案 |
| 指纹比对误报（不同歌判同） | 中 | 宁漏勿错阈值 + 相似度透明展示 + 默认可关；上线前 ≥10 对回归用例 |
| 指纹候选分桶漏检（同曲变体错开分桶） | 中 | 禁用首块分桶；多锚点子指纹倒排（任一锚点碰撞即候选），V14-2 回归用例须含"同曲不同裁剪"对 |
| windowed exe 无控制台，CLI 输出丢失 | 高（不处理必现） | AttachConsole(ATTACH_PARENT_PROCESS) + 日志文件兜底 + 文档引导源码运行 |
| Model/View 重构引入交互回归 | 中 | 保留 legacy 实现一个版本；性能与功能双基准脚本进 dev/ |
| CI 与本地打包产物不一致 | 低 | 双轨验证一次；ffmpeg 钉版本 + sha256 |
| analyzer 附加字段撑大内存（万级曲库） | 低 | 频率网格常量化 + dB 数组二进制存储（~1KB/文件）；禁止 Python list；CSV 不含频谱列 |
| 解锁 RC4 换 C 库引入依赖问题 | 低 | 条件触发 + 运行时探测回退，默认零依赖不动 |

### 4.4 每版固定收尾清单（§5，执行者照此走）

1. `python tests/run_all.py` 全绿；新增任务的本版回归用例已入套件
2. `pip freeze > requirements-freeze.txt`
3. 打包：`"C:/Program Files/Python311/python.exe" -m PyInstaller --noconfirm --clean --windowed --name SonicCheck --icon resources/icon.ico --add-data "resources;resources" --exclude-module tests main.py`（**显式解释器**，勿用 build.bat 直接跑——本机多 Python 歧义）
4. 启动冒烟：拉起 exe ≥10 秒确认进程存活、窗口标题版本正确（覆盖"有 last_folder 的重启"路径）
5. `Compress-Archive` 出 zip → 校验关键文件（exe/ffmpeg×2/qss）在 zip 内
6. 三远端推送 main + tag（**全新版本号 tag，永不复用历史名**）
7. GitHub **草稿** Release → 上传 zip → 核对文案（对比链接用 URL 编码的 tag 名）→ 勾 Latest → 发布
8. DEV_LOG（`DEV_LOG_v{版本}.md`）写入 **internal 分支**（仅推 codehub）+ README 里程碑 + 提交推送
9. **internal 分支维护**：`git checkout internal && git merge main`，推 origin（internal 绝不推 gitee/github）
10. 宣发素材（可选）：按 internal 分支 `docs/marketing/` 既有流程出抖音文案/卡片

---

## 5. 附：本文档维护规则

- 每完成一个任务，把对应小节标题加 `[已完成 vX.Y.Z @ commit]` 标记；决策点拍板后把结论写进对应小节。
- v2.0 发布后本文档归档（移至 internal 分支），新路线图另起。

> **分支模型（2026-09-30 起）**：`main` = 公开产品仓（推全部三远端）；
> `internal` = main + 内部物料（DEV_LOG/PROJECT_BIBLE/宣发素材/发布草稿，
> **仅推 codehub origin**）。写内部文档时在 internal 分支提交；
> main 侧完成新提交后 internal 需 `git merge main` 跟进。
