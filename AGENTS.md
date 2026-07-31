# AGENTS.md —— 声鉴·曲库管家 SonicCheck 项目记录（原 AudioQuality Scanner）

## 当前状态
- 2026-07-31 M1 ✅ → M4.6 ✅ 均已验收 → 导出 CSV 加「仅真无损」✅、指南对话框加「迭代计划」页 ✅ →
  M5 打包材料已出（build.spec + build.bat + BUILDING.md），待用户本机执行打包与实测

## M5 打包材料（2026-07-31，待用户本机执行）
- build.spec：--onedir、console=False、icon=resources/icon.ico、datas 含 style.qss/双 icon，
  resources/ffmpeg/ 存在则一并打入（缺失不阻断）；显式排除 PyQt5/PySide/matplotlib/pandas/PIL/tkinter 等；
  改英文名只需改顶部 APP_EXE_NAME（配合 main_window 的 APP_NAME/DISPLAY_NAME/ORG_NAME 四处一致）
- build.bat：检查 python → pip 装依赖+pyinstaller → 检查 resources/ffmpeg/ → PyInstaller --clean
- BUILDING.md：ffmpeg 下载（Gyan essentials）与放置说明、自检清单、报错排查表
- ffmpeg_locator 双兜底：exe 同级 resources/ffmpeg/ 优先，_internal(_MEIPASS) 兜底——打包前后放入皆可

## M4.6 增量（2026-07-31，已验收后追加）
- 导出 CSV 范围弹窗新增「仅真无损」（_pick_scope 加 allow_true_filter 参数，顺序：全部/仅选中行/仅假无损/仅真无损/取消）
- GuideDialog 改 QTabWidget 双页：使用指南 + 迭代计划（ROADMAP_TEXT，维护新功能想法时同步更新）
- **测试坑（再次命中）：m3_gui_test 的 _pick_scope lambda 补丁必须 **_kwargs——
  产品代码新增关键字参数后，旧 lambda 在槽内 TypeError → PyQt6 直接终止进程 EXIT=127**
- **顺序防抖修复：renamer.build_rename_plan 与 tag_renamer.build_tag_rename_plan 入口按 filepath 排序——
  撞名"谁先谁后"不再随线程完成顺序抖动（曾致 m45_gui_test 撞名跳过断言偶发 FAIL）**
- build.bat 开头加 `chcp 65001`（UTF-8 中文 echo 防乱码）

## M4.6 增强批交付（2026-07-30，已验收）
- 软件定名（2026-07-31 用户拍板）：APP_NAME="SonicCheck"，DISPLAY_NAME="声鉴·曲库管家"，
  ORG_NAME="SonicCheck"，APP_VERSION="0.2.0"
  **坑：ORG_NAME 变了，老用户 QSettings 会整体重置一次（上次文件夹/窗口尺寸丢失，属预期）**
- 自定义质量标记（core/quality_marks.py）：文本+前/后缀位置，左栏「质量标记…」对话框（widgets/quality_mark_dialog.py），
  QSettings 记忆；`set_config(mark_true, mark_fake, position)` **注意参数顺序**；
  判定列与 CSV 的「真无损/假无损」文字不变（那是判定口径，非文件名标记）；
  renamer.strip_quality_suffix 委托 strip_quality_mark，前后缀+自定义标记都能剥
- 表格多选 + 右键菜单（打开所在文件夹 os.startfile / 复制完整路径）；
  重命名/导出 CSV 经 `_pick_scope()` 范围弹窗：全部/仅选中行/（仅导出时有）仅假无损/取消；
  无选中且无假无损筛选时不弹窗直接全部
- 去重还原（core/deduper.py）：清除时写 `_待清除/.restore_map.json` manifest（新路径→原路径），
  「还原清除」按钮 build_restore_plan 移回原位；占用冲突跳过；还原后 prune 死条目；安全模式下按钮禁用
- 对比表手动保留项：dedupe 模式处置行可点击改保留（DupGroup.keep_override，keep 属性优先它；
  **契约：DupGroup.items 必须按评分降序**）
- 歌单未匹配清单：写 `未匹配清单.txt` + 一键复制到剪贴板（自动复制）
- 详情对话框加「打开所在文件夹」
- 安全模式：左栏 checkbox；开时所有写操作重定向到 `<目标文件夹同级>/<文件夹名>_安全输出/`——
  重命名/标签改名=复制产出（execute_copy_plan，**目标已存在→失败条不覆盖**）、
  去重=仅索引清除（不碰文件）、歌单文件夹建在安全目录；
  scanner 排除规则 `_excluded()`：精确名 `_待清除` 或后缀 `_安全输出`
- 新手引导 widgets/guide_dialog.py：左栏「使用指南」+ 首启非模态 show()（**offscreen 测试里 exec 型对话框会永久阻塞，首启必须 show()**）
- 测试：dev/m46_core_test.py 6 组 + dev/m46_gui_test.py 7 段全过（C:\Python314 跑）；M2/M3/M4/M4.5 全量回归通过
- **测试坑（新）：Fake 对话框类必须带产品代码引用的枚举（如 ButtonRole/DialogCode）——
  缺了会在 click() 槽里抛 AttributeError，PyQt6 对槽内未捕获异常直接终止进程（EXIT=127 无 traceback），
  直接调槽函数则是正常 AttributeError；m3_gui_test 里 `window._pick_scope = lambda ...` 补丁可绕过范围弹窗**
- **测试坑（旧，再次命中）：QApplication(sys.argv) 必须赋值持有引用，临时对象被 GC 会让 Qt 硬崩（EXIT=127 无输出）**
- 中文名/英文名为 Agent 拟定，用户可在验收时要求改

## M4.5 二期功能批交付（2026-07-30，已验收）
- 功能入口：结果表工具栏第二排 4 按钮（同名对比/去重清除/标签改名/歌单导入）+ 双击行看详情
- A 详情：widgets/detail_dialog.py；B/C 对比+去重：widgets/compare_dialog.py（MODE_COMPARE/MODE_DEDUPE，去重模式表尾带处置行）
- C 去重（core/deduper.py）：两路分组——标签/规范化文件名 + 内容签名（时长±0.3s、DR±0.2、corr±0.01、cutoff±50Hz，并查集合并）；
  清除=C1 移 `_待清除/`（可恢复），保留组内最高分；scanner 排除 `_待清除`
- D 标签改名（core/tag_renamer.py）：默认 歌名-歌手、可选 歌手-歌名（QInputDialog.getItem 选）；缺标签跳过；
  **坑：音频撞名跳过时 .lrc 必须一并跳过（曾出 lrc 单独被改成别人目标名的张冠李戴 bug，已修）**
- E 歌单（core/playlist.py + widgets/playlist_dialog.py）：md/csv/txt/手动粘贴解析，两级匹配（标签精确→文件名包含），
  复制（非移动）到 `目标文件夹/<歌单名>/`，幂等，未匹配写 `未匹配清单.txt`；**Markdown `#` 标题行不算歌曲条目（已修）**
- F 网易云链接解析：**零登录全量方案（2026-07-30 已上线并联网验证 832/832）**——
  v6 详情接口 `api/v6/playlist/detail?id=&n=100000` 匿名即返回**完整 trackIds**（tracks 虽被限流截断约前 10 首），
  再用 trackIds 分批调 `api/song/detail?ids=[...]`（**单批上限 ~200，实测 500 只返 201**）补全，保持歌单原顺序。
  对话框「高级」栏 MUSIC_U 保留为私密歌单等场景的可选兜底（仅本机用、不保存不上传）；
  粘贴解析支持 tab 分隔（网页表格复制格式）。**坑：id 判断必须 `is not None`（id=0 假值被过滤曾丢歌）**。
  QQ 音乐后置；私密歌单匿名必然失败，提示手动粘贴
- analyzer 扩展：ffprobe 读 title/artist 标签（format.tags 优先，stream.tags 回退，album_artist 再回退）
- 已知行为：歌单副本在目标文件夹内，重扫会被扫到（符合扫描语义，可用去重清理）；
  不同音质且无标签不同名的重复识别不了（需声纹，二期以后）
- dev/m45_core_test.py 36 项 + dev/m45_gui_test.py（C:\Python314 跑）全过；M2/M3/M4 回归全过
- **测试坑：QApplication(sys.argv) 必须赋值持有引用，临时对象被 GC 会让 Qt 硬崩（EXIT=127 无输出）**

## M4 交付（2026-07-30，已验证）
- core/scanner.py：os.walk(onerror) 容错，无权限/损坏子目录跳过并计数（返回 tuple：files, skipped_dirs）
- main_window：on_start_scan 前置检查 ffmpeg/ffprobe 缺失直接拒绝（不整批进 error 行）；
  扫描中锁定整窗拖拽；扫描结束汇总日志含真/假/失败明细
- left_panel：扫描中锁定线程数/分析时长 spin；log_panel：日志上限 5000 行（在 document 上，
  坑：setMaximumBlockCount 不是 QTextEdit 的方法）
- dev/m4_boundary_test.py 13 项全过；M2/M3 全部回归通过

## M3 交付（2026-07-30，已验证）
- core/renamer.py：内存结果直接建计划（不经 CSV）；按文件路径推导（递归安全）；
  剥旧后缀再按判定加（支持改判/防套娃）；只同步 .lrc；目标已存在→跳过；单条失败不中断
- core/csv_exporter.py：旧脚本全列+完整路径列（末列），UTF-8-BOM，只导出 done 行，
  默认名 AudioQuality_YYYYMMDD_HHmmss.csv
- widgets/rename_dialog.py：预览确认两段式（计划项+跳过项同表展示，确认才执行）
- main_window：导出/重命名接线；音频改名后同步内存路径+表格行；LogPanel.log_error 标红
- 测试（全部沙盒副本上进行，不动 samples 原件）：
  dev/m3_core_test.py 30+ 项全过（套娃剥离/改判/冲突跳过/二次无操作/CSV BOM 与全列）
  dev/m3_gui_test.py 端到端全过（打补丁对话框自动确认，落盘/表格/内存三方一致）
- 测试后清理 dev/tmp_m3_* 沙盒
- 用户实测抓到 M1 遗留 bug：btn_export/btn_rename 的 clicked 从未接到对外信号（占位期未暴露），
  M3 已修复并补"真实按钮点击"回归路径到 m3_gui_test.py（原测试直接调槽函数绕过了按钮）

## M2 交付（2026-07-30，已验证）
- core/ffmpeg_locator.py：内置目录 → PATH → chocolatey/C:\ffmpeg 兜底；隐藏子进程窗口
- core/analyzer.py：统一转码 pcm_s32le/48k（含 .wav，WAV 频率轴 bug 已修）、numpy 全向量化、
  ffmpeg 60s 超时、临时 WAV 放 %TEMP%\AudioQualityScanner\（启动清扫残留）、ScanCancelled 协作式取消
- core/scanner.py：rglob 递归；threads/scan_worker.py + scan_manager.py：QThreadPool 调度
- 取消设计：只置标志位、**不调 pool.clear()**（被清任务无回调会丢计数），每个 worker 保证恰好一次终结回调
- 验证（dev/compare_new_vs_legacy.py）：3 个 FLAC 样本与旧脚本全字段 diff=0.00e+00（含评分）；
  44.1k WAV 差异为 bug 修复预期（旧报 16218Hz → 新报真实 14900Hz）；单首 2.7s → 0.43s
- GUI 集成测试（dev/gui_smoke_test.py，C:\Python314 离屏跑）20 项全过：含排序回归、停止、二次扫描
- 旧脚本权威副本在 dev/legacy/（从 G:\Music 复制，比对基准，勿改）

## 技术栈（已锁定，不可更改）
PyQt6 + numpy（唯一 FFT 后端）+ 内置 ffmpeg（Gyan essentials 静态版）
多线程用 QThreadPool + QRunnable，**禁止 multiprocessing**；样式为手写 QSS。

## 已确认决策（2026-07-30 与用户对齐，共 20 条）
1. 打包：--onedir，保留 ffprobe，预期 ~300MB；icon.png/icon.ico 双备；.spec 管理
2. WAV 频率轴 bug 修复：**所有格式统一 ffmpeg 转码 pcm_s32le/48kHz（含 .wav）**
3. numpy 向量化：替换前必须用同批文件与旧脚本逐字段比对一致
4. CSV 按钮只放表格工具栏；线程数/分析时长放左栏（默认 8 / 30 秒，上限 16 / 120 秒）
5. 状态列 QSS 色点+文字（●/○/!），不用 emoji
6. 递归扫描子文件夹（rglob）
7. 停止 = 协作式取消（标志位 + 清队列），M2 内建，不后置
8. 重命名：先剥离旧后缀 → 按新判定加后缀（支持改判）；只同步 .lrc；不同步 .cue
9. CSV 列 = 旧脚本全列 + 完整路径列，UTF-8-BOM
10. P1（筛选/排序/汇总/日志）并入一期；P2（迷你频谱图/设置面板/拖单文件）二期
11. 分工：Agent 出源码 + build.spec + build.bat，用户 Win 本机打包与实测
12. 分析取段：从头 30 秒（与旧脚本一致，保证历史结果可比）
13. fake_reasons 放状态列 tooltip
14. 重复开始 = 清空重扫；QSettings 记忆上次文件夹与窗口尺寸
15. 扫描中锁定"选择文件夹"与拖拽区；"停止"仅扫描中可用

## 用户提醒的三个坑（迁移必守）
1. numpy 数组禁止直接当布尔值：`if len(mag) == 0:` / `if len(freqs) > 0:`（旧脚本已修过两次）
2. 中文+特殊符号路径（如 `【66-破解-乐库-】`）：subprocess 一律传绝对路径，参数用列表形式
3. ffprobe FLAC 位深度 fallback：`bits_per_sample == 0` 时用 `bits_per_raw_sample`（保留）

## 判定口径（源自旧脚本，勿动）
- 假无损三规则：断崖且 <20kHz；-60dB 截止 <16kHz；24bit 且 DR<4
- 评分六维：位深 15 / 采样率 10 / -60dB 截止 25 / DR 25 / 立体声相关 15 / 峰值 10；假无损 -20
- 断崖：15kHz–0.95Nyquist 内相邻窗落差 >30dB 且断后 50 bin 均值 <-70dB
