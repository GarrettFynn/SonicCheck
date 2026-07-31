# 声鉴·曲库管家 SonicCheck（原 AudioQuality Scanner）

面向 HiFi 爱好者的 Windows 绿色版桌面工具：批量鉴定本地音乐是"真无损"还是"假无损"，
并完成去重、规范命名、按歌单整理等曲库管理工作。

## 运行（开发环境）

```bash
pip install -r requirements.txt
python main.py
```

运行需要 ffmpeg / ffprobe：放 `resources/ffmpeg/` 下，或装入系统 PATH。

## 打包（绿色版）

见 [BUILDING.md](BUILDING.md)：`build.bat` 一键打包，产物 `dist/SonicCheck/` 整个目录可分发。

## 里程碑状态

- [x] M1 界面骨架
- [x] M2 分析引擎集成（ffmpeg 内置查找、QThreadPool、协作式停止）
- [x] M3 重命名与导出（预览/执行、CSV UTF-8-BOM）
- [x] M4 错误处理与边界打磨
- [x] M4.5 二期功能批：详情 / 同名对比 / 去重清除 / 标签改名 / 歌单导入 / 网易云链接全量解析
- [x] M4.6 增强批：自定义质量标记、多选与筛选导出、去重还原、安全模式、使用指南、改名 SonicCheck
- [x] M5 打包材料：`build.spec` + `build.bat` + BUILDING.md（用户本机执行）

## 目录结构

```
AudioQualityScanner/
├── main.py              # 入口
├── main_window.py       # 主窗口（布局、拖拽、状态记忆、写操作调度）
├── widgets/             # 左栏 / 结果表格 / 进度 / 汇总 / 日志 / 各对话框
├── core/                # 分析引擎、ffmpeg 封装、扫描、重命名、去重、歌单、质量标记
├── models/              # ResultItem 数据模型
├── threads/             # ScanWorker / ScanManager
├── resources/           # style.qss / icon.png / icon.ico / ffmpeg（打包前放入）
└── dev/                 # 各里程碑测试（沙盒副本上进行，不动 samples 原件）
```
