# 打包说明（M5）—— 声鉴·曲库管家 SonicCheck

目标产物：`dist\SonicCheck\SonicCheck.exe`，整个目录即绿色版（预计 ~300MB），可整体拷贝分发。

## 一、打包前置：放置内置 ffmpeg（一次性）

1. 下载 Gyan 官方 release essentials 静态构建：
   https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
   （文件名形如 `ffmpeg-7.x-essentials_build.zip`，约 80MB）
2. 解压，从 `bin\` 里取出两个文件：
   - `ffmpeg.exe`
   - `ffprobe.exe`
3. 放到项目根目录的 `resources\ffmpeg\` 下，最终结构：

```
AudioQualityScanner/
├── resources/
│   ├── ffmpeg/
│   │   ├── ffmpeg.exe      ← 必须
│   │   └── ffprobe.exe     ← 必须（缺它无法分析）
│   ├── icon.ico
│   ├── icon.png
│   └── style.qss
├── build.spec
└── build.bat
```

> 忘记放也不阻断打包：exe 会退而去系统 PATH 找 ffmpeg；
> 也可以打包后手动复制到 `dist\SonicCheck\resources\ffmpeg\`，效果相同。

## 二、执行打包

双击 `build.bat`（或在项目根目录运行它）。脚本会依次：

1. 检查 Python（需 3.10+，已在 PATH）
2. 安装/升级依赖：PyQt6、numpy、PyInstaller
3. 检查 `resources\ffmpeg\` 是否就位（缺失会警告并暂停确认）
4. 执行 `PyInstaller --noconfirm --clean build.spec`

完成后产物在 `dist\SonicCheck\`。

## 三、打包后自检清单（用户本机）

- [ ] 双击 `SonicCheck.exe` 能启动，窗口标题为「声鉴·曲库管家 SonicCheck v0.2.0」
- [ ] 图标正常显示（任务栏 + exe 文件图标）
- [ ] 选一个含 FLAC/WAV 的文件夹完整扫描一遍，判定结果与开发环境一致
- [ ] 日志中没有 ffmpeg/ffprobe 找不到的报错
- [ ] 把 `dist\SonicCheck` 拷到**没有装 Python 和 ffmpeg 的机器**上复测一次

## 四、常见问题

| 问题 | 处理 |
|------|------|
| 杀毒软件报毒 | PyInstaller 引导程序常见误报，加白名单即可（本包未用 UPX 已是低误报配置） |
| 启动闪退 | 把 `build.spec` 里 `console=False` 临时改成 `True` 重打包，看控制台报错 |
| 找不到 ffmpeg | 确认 `resources\ffmpeg\ffmpeg.exe` 与 `ffprobe.exe` 都存在（exe 同级目录下） |
| 体积远超预期 | 确认没有装 scipy/pandas/matplotlib 等无关大包（spec 已显式排除） |
| 设置不生效 | 首次改名后 QSettings 重置过一次，此后正常记忆 |

## 五、后续改名

若要换软件英文名：改 `build.spec` 顶部 `APP_EXE_NAME` 与 `main_window.py` 的
`APP_NAME` / `DISPLAY_NAME` / `ORG_NAME`（四处保持一致），删除 `dist\`、`build\` 后重打。
注意改 `ORG_NAME` 会让用户设置（上次文件夹/窗口尺寸）重置一次。
