# v1.1.0 提交计划（COMMIT PLAN）

> 当前工作区 = v1.0.0（b5d4b15）之后的全部改动。建议拆 5 个提交，审阅/回滚都方便；
> 赶时间也可一把梭（见文末）。**切勿提交**：`*.zip`、`_test_real/`、`resources/ffmpeg/`、
> `dist/`、`build/`、`*.spec` —— .gitignore 已全部覆盖，`git status` 复核应为空。

---

## 提交 1：格式解锁核心（新功能主体）

```bash
git add core/unlock.py core/qqmusic_key.py widgets/unlock_dialog.py widgets/qqmusic_guide_dialog.py
git commit -m "feat: 加密音乐格式解锁（ncm/QMC 流式解密 + QQ 密钥导入）

- 网易云 ncm / QQ 音乐 QMC 全系（mflac/mgg/qmc*）→ flac/wav，纯 Python 零新依赖
- 流式解密：8MB 分块，296MB 文件峰值内存约 76MB（旧方案约 890MB）
- 三种 QMC 流密码（map/RC4/static）+ TEA-CBC EKey 派生，与 unlock-music 参考逐字节一致
- QQ 新版 musicex：从运行中客户端只读提取登录态，CgiGetVkey 换密钥，本地密钥库缓存
- 解锁后联网补齐标签封面（get_song_detail 详情接口）；ffmetadata 写标签中文无乱码
- 全程可取消（分块检查 + ffmpeg 轮询 kill），异常/取消自动清理半截产物
- 转码超时按文件大小缩放；QQ 登录 cookie 落盘 DPAPI 加密
- 解锁对话框：字节级进度、密钥导入自动续跑开关、内置图文教程"
```

## 提交 2：扫描与曲库管理增强

```bash
git add core/analyzer.py core/playlist.py main_window.py widgets/left_panel.py widgets/result_table.py
git commit -m "feat: 扫描与曲库管理稳定性增强

- 分析失败兜底清理临时 WAV（修复 %TEMP% 堆积 82GB 的泄漏）
- 扫描停止后未完结行统一标记「已取消」，不再悬挂「分析中…」
- 非安全模式去重后被清除项移出结果表（与安全模式一致），防误操作 _待清除/ 文件
- 歌单文件名模糊匹配加最小长度阈值（≥2 字符），防短歌名误配
- 左栏安全模式开关与输出目录提示
- 版本号 APP_VERSION = 1.1.0"
```

## 提交 3：测试套件

```bash
git add tests/
git commit -m "test: 全量回归套件（101 项断言，run_all 一键跑）

- test_unlock.py：AES/TEA 向量、NCM/QMC 合成文件加密解密逐字节往返、
  双段 QTag、流式跨块边界 + 进度单调性（28 项）
- test_qqmusic_key.py：musicex 尾部解析、密钥库双键索引、接口流程（41 项）
- test_file_ops.py：renamer/tag_renamer/deduper/playlist 文件操作真实往返（32 项）
- run_all.py：自动发现 test_*.py 逐个子进程执行，失败退出码非 0"
```

## 提交 4：发布物料与文档

```bash
git add build.bat BUILDING.md README.md release_notes_v1.1.0.md RELEASE_DRAFT_v1.1.0.md .gitignore
git commit -m "docs: v1.1.0 打包链路与发布物料

- build.bat 一键 PyInstaller 打包（onedir 绿色版，ffmpeg 内置）
- BUILDING.md 打包指南 + 发版检查清单
- release_notes_v1.1.0.md 完整更新日志；RELEASE_DRAFT_v1.1.0.md Release 页面文案
- .gitignore 增补：*.zip / resources/ffmpeg/ / _test_real/"
```

## 提交 5：清理过期文档

```bash
git add -A AGENTS.md PRD_AudioQualityScanner_v1.0.md
git commit -m "chore: 移除过期设计文档（AGENTS.md / 一期 PRD）"
```

---

## 一把梭备选

```bash
git add -A
git commit -m "feat: SonicCheck v1.1.0 —— 格式解锁 + 稳定性与安全加固

详见 release_notes_v1.1.0.md"
```

## 提交前自检

1. `python tests\run_all.py` 全绿
2. `git status` 确认无 zip / _test_real / resources/ffmpeg / dist
3. `git push` 后打 tag：`git tag v1.1.0 && git push origin v1.1.0`，
   再到 GitHub 按 RELEASE_DRAFT_v1.1.0.md 发 Release（Asset 传 zip，不传源码 zip 以外的文件）
