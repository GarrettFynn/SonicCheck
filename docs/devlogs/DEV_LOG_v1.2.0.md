# SonicCheck v1.2.0 开发日志（批次 B 性能与响应性 + 批次 C 打磨）

> **版本**：v1.1.1 → **v1.2.0**
> **性质**：全项目代码审查后的第二批落地项（性能/响应性 6 项 + 打磨 5 项），判定口径零改动
> **前置**：v1.1.1 工程地基批（测试入库、CI、去重误报加固）见 `DEV_LOG_v1.1.1.md`

---

## 一、本轮一句话

万级曲库 UI 性能（表格批量渲染 + 行号映射）、解锁吞吐（并行 + 并发安全）、QQ 密钥安全（cookie 永不明文落盘 + 大堆分块扫描）、三处 GUI 线程重活后台化、网络熔断，以及歌单解析三修 / 文件名安全 / 重复代码收敛 / 根目录整理。

## 二、提交清单（批次 B）

| # | 提交 | 要点 |
|---|---|---|
| B1 | `perf: 结果表格批量渲染模式` | begin/end_update 包裹扫描周期，排序与全表过滤推迟到结束统一处理，插行/回填 O(n²)→O(n)；批量期间 filepath→行号映射 O(1) 回填。离屏冒烟：3000 行插行 0.06s + 回填 0.07s |
| B2 | `perf: 解锁吞吐` | 线程池 1→min(4,cpu)；RC4 PRGA 微优化（取模→条件减法，单线程 6.0→6.3MB/s，段循环 1.2×，输出与旧实现逐字节一致）；EkeyStore 锁+合并落盘防 last-writer-wins；_unique_path 预留集合防并行同名覆盖（TOCTOU）。新增 `dev/bench_rc4.py`。**诚实修正**：审查报告预估"向量化 50×"不可行——RC4 状态推进存在数据依赖链，无法向量化，真实收益=并行 4× |
| B3 | `fix: QQ 密钥导入安全加固` | DPAPI 失败时 cookie 整段不入库（旧版静默明文落盘）+ UI 弹窗说明；save() 失败上报；ctypes 全量 argtypes/restype（64 位 HANDLE 截断隐患）；内存扫描去 50MB 区域上限改 64MB 分窗 + 1KB 重叠（CEF 大堆漏扫致偶发"未找到登录信息"） |
| B4 | `perf: 网易云歌单解析后台化` | 串行分批请求移入 QThreadPool，进度"已获取 N/M 首"+批间可取消；core 层新增 progress_cb/cancel_check 直通参数（签名向后兼容） |
| B5 | `perf: 解锁窗口文件夹扫描后台化` | rglob+逐文件读头移后台；先按扩展名过滤再读头（省 90%+ open）；损坏 .ncm 计数上报不再静默跳过 |
| B6 | `fix: 批量解锁网络熔断` | 连续 3 次取不到标签即跳闸，本批剩余跳过联网（解锁不受影响），列表追加一次性说明；_API_TIMEOUT 15→8s |

## 三、提交清单（批次 C）

| # | 提交 | 要点 |
|---|---|---|
| C1 | `fix: 歌单解析三修` | `01 - 晴天 - 周杰伦` 编号短横剥离（限 1~3 位防误剥年份型歌名）+裸编号兜底；GBK 歌单回退 gb18030；copy_matched 同名异大小自动 (n) 消歧（同名曲不同音质不再静默丢歌）+ 半截文件清理；歌单目录创建失败不再炸槽 |
| C2 | `fix: 文件名安全` | sanitize_filename：Windows 保留设备名（CON/NUL/COM1-9…，含带扩展名形态）加下划线；名字分量截断 120 字符 |
| C3 | `refactor: 重复代码收敛` | 新增 core/fsutil.companion_lrc（4 份拷贝合一）；renamer.execute_ops 通用执行器（3 个同构循环合一）；main_window._run_rename_plan（两条 20 行平行流程合一）；清死代码 |
| C4 | `test: 套件 assert 化` | 三个套件 check() 失败即带栈中断（原打印 ✗ 后继续跑，CI 看不出错点）；四个后台 worker 兜底 except 补 logging.exception |
| C5 | `chore: 根目录整理` | 宣发物料→docs/marketing/（卡片生成脚本随产物放一处，未按计划进 dev/）、发布说明→docs/release/、开发日志→docs/devlogs/ |

## 四、验证记录

- `python tests/run_all.py` 全绿：3 套件 128 项断言（file_ops 51 + unlock 41 + qqmusic_key 36）
- 新增回归：去重误报防护 4 项、歌单解析 9 项、文件名安全 6 项
- 并发冒烟：4 线程解锁 4 个同名文件→4 个独立输出；EkeyStore 双实例并发 save 键全保留；熔断阈值/跳闸零请求/批次重置
- 离屏 GUI 冒烟：主窗口构造 + 批量表格 3000 行 + 歌单/解锁对话框构造
- RC4 基准（`dev/bench_rc4.py`，i7/32G）：优化前 6.0MB/s → 优化后 6.3MB/s 单线程；并行 4 线程批量吞吐约 4×

## 五、遗留与后续建议

- RC4 若需再提速：可选依赖 pycryptodome 走 C 实现（分段 ARC4 + discard），已按决策②暂不引入，实测不够快再议
- result_table 完整 Model/View 重构按决策⑤延后至 2.0（B1 已解决当前量级）
- match_entries 优先级 1 的线性扫（O(条目×曲库)）在万级曲库+千条歌单下仍可优化为 dict 索引，本轮未动
- `waitForDone(-1)` 关闭等待上限、CT 上限换 runner 平台等小项未动
