# QQ 音乐加密格式（QMC / musicex）解锁技术说明

> **本文定位**：面向开发者的技术交流文档，对应 SonicCheck v1.1.0 的实现（`core/unlock.py`、`core/qqmusic_key.py`）。
>
> **合法用途声明**：本项目的解密能力仅用于将用户**已合法获得（已购买/已下载）**的本地文件转换为标准格式以实现互操作（导入播放器、车载设备等）。软件不绕过付费、不下载任何新内容。请勿将本文信息用于侵犯版权或违反平台服务条款的用途。
>
> **参考实现致谢**：QMC 算法常量与流程逐字节对照 [unlock-music CLI v0.2.12](https://git.unlock-music.dev/um/cli)（MIT）；musicex 内存扫描与接口参数移植自 [qmdec](https://github.com/Sophomoresty/qmdec)（MIT）。本文是站在这些开源工作之上的工程化说明。

---

## 0. TL;DR

- QQ 音乐的加密是**流密码 XOR**，不是整体块加密。密钥（ekey）经 TEA-CBC 派生出真实密钥后，按密钥长度分流到三种流密码之一（map / RC4 / static）。
- 密钥的存放位置随客户端版本演进：**内嵌在文件尾部**（旧版、QTag 版）→ **不在文件内、需联网按歌换取**（musicex 版）→ **完全离线不可得**（STag 版，无解）。
- 对 musicex 文件，本项目从**运行中的 QQ 音乐客户端进程内存只读提取登录 cookie**，调腾讯官方 `CgiGetVkey` 接口按歌换回 ekey，DPAPI 加密缓存在本机，之后即可离线解密。
- 三种流密码的 keystream 只依赖**流内绝对偏移**，天然支持随机访问，因此可以实现 8MB 分块的流式解密，大文件内存占用恒定。

---

## 1. 版本演进与格式识别

QQ 音乐桌面客户端下载的加密文件，按尾部结构分为四代：

| 代际 | 尾部特征 | 密钥位置 | 本项目支持 |
|---|---|---|---|
| QMCv1（远古） | 无尾部 | 无密钥，固定密码盒 | ✅ static 密码 |
| EKey 内嵌版 | 末 4 字节 = LE u32 密钥长度 | 文件尾部内嵌 ekey | ✅ |
| QTag 版 | 末 4 字节 = `QTag` | 尾部元数据内嵌 ekey | ✅ |
| musicex 版 | 末 8 字节 = `musicex\x00`（尾部解析时按末 4 字节 `cex\x00` 命中） | **不在文件内**，需按 song_mid 联网换取 | ✅（需先导入密钥） |
| STag 版 | 末 4 字节 = `STag` | 不在文件内且**无取回通道** | ❌ 无解 |

扩展名识别集合（`QMC_EXTS`，共 30+）：

```
qmc, qmc0, qmc2, qmc3, qmc4, qmc6, qmc8, qmcflac, qmcogg, tkm,
bkcmp3, bkcm4a, bkcflac, bkcwav, bkcape, bkcogg, bkcwma,
666c6163, 6d7033, 6f6767, 6d3461, 776176, mmp4,
mflac, mflac0, mflac1, mflaca, mflach, mflacl, mflacm,
mgg, mgg0, mgg1, mgga, mggh, mggl, mggm
```

文件级判定（`detect_encrypted_type`）不读全文：网易云看头部 magic `CTENFDAM`，QMC 系按扩展名段匹配。

### 1.1 尾部解析决策树（`_qmc_parse_footer`）

```
读文件末尾 ~1MB+4KB+32 字节（覆盖 QTag 大元数据与 musicex 尾块）
├─ 末 4 字节 == "QTag"
│    元数据长度 = BE u32 @ (n-8)
│    元数据内容 = "songmid,ekey"（逗号分隔；单段时按纯 ekey 兼容）
│    音频长度 = 文件大小 - 8 - 元数据长度
├─ 末 4 字节 == "STag"
│    → 直接抛错：密钥不在文件内且无取回通道，建议 ≤19.43 旧版客户端重下
├─ 末 4 字节 == "cex\x00"（即 musicex）
│    → 解析 musicex 尾块（见 §4.1），返回 meta，转密钥库查 ekey
├─ 末 4 字节 解读为 LE u32 key_len 且 1 ≤ key_len ≤ 0xFFFF
│    → ekey = 尾部倒数 4+key_len 起的 key_len 字节（去尾零）
│      音频长度 = 文件大小 - 4 - key_len
└─ 以上都不是 → 无尾部旧版，全文即音频，走 static 固定密码盒
```

---

## 2. EKey 派生：TEA-CBC → 真实密钥

文件内嵌或接口返回的 **ekey 不是直接可用的密钥**，需要派生。管线（`_qmc_derive_key`）：

```
ekey (bytes)
 → base64 decode
 → 若前缀为 "QQMusic EncV2,Key:" → 走 v2 派生（§2.2），结果回到主线
 → v1 派生（§2.1）
 → real_key（长度决定用哪种流密码）
```

### 2.1 腾讯 TEA-CBC 框架（`_tencent_tea_decrypt`）

这是腾讯系经典的 `oi_symmetry_decrypt2` 框架：

- **TEA 单块**：8 字节一块，大端双字 (v0, v1)，DELTA = `0x9E3779B9`，**16 轮**解密，初始 sum = `0xE3779B90`（= DELTA×16 mod 2³²）。
- **帧结构**：`PadLen(低3位) | Pad | Salt(2字节) | Body | Zero(7字节)`。
- **CBC 链**：首块解密后，后续每块先与当前密文块 XOR 再解密，输出时再与前一个密文块 XOR；末尾 7 字节必须与前密文块相等（Zero 校验），不等则判定"密钥校验失败"。

### 2.2 v1 派生（`_qmc_derive_key_v1`）

```python
# dec = base64 解码后的 ekey（≥16 字节）
tea_key = 交错拼接(_QMC_SIMPLE_KEY[i], dec[i])   # 16 字节
# _QMC_SIMPLE_KEY = 69 56 46 38 2B 20 15 0B
rs = tencent_tea_decrypt(dec[8:], tea_key)
real_key = dec[:8] + rs
```

### 2.3 v2 派生（`_qmc_derive_key_v2`）

新版 ekey 在 base64 解码后带前缀 `QQMusic EncV2,Key:`，去掉前缀后做**两轮 TEA-CBC**：

```python
buf = tencent_tea_decrypt(raw, _QMC_DERIVE_V2_KEY1)  # 33 38 36 5A 4A 59 ...
buf = tencent_tea_decrypt(buf, _QMC_DERIVE_V2_KEY2)  # 2A 2A 23 21 28 23 ...
dec = base64_decode(buf)   # 注意：解完还要再 base64 解码一次
# 之后回到 v1 派生主线
```

即：**base64 →（v2：双 TEA + 再 base64）→ v1 TEA 交错密钥 → real_key**。

---

## 3. 三种流密码（按 real_key 长度分流）

分流逻辑（`_qmc_cipher_for`）：

```python
if len(real_key) > 300:  RC4 分段密码
elif real_key 非空:      map 密码
else:                    static 固定密码盒（旧版无密钥文件）
```

三种密码的共同性质：**keystream 的每个字节只依赖明文/密文在流内的绝对偏移**，不依赖前后文——这是能流式分块解密的根本原因。

### 3.1 Map 密码（密钥 1..300 字节，`_QmcMapCipher`）

对每个绝对偏移 `o`（`o > 0x7FFF` 时先取 `o % 0x7FFF`）：

```
idx  = (o² + 71214) % n          # n = 密钥长度
v    = key[idx]
r    = ((idx & 0x07) + 4) % 8
mask = (v <<< r) 循环左移         # ((v << r) & 0xFF) | (v >> r)
plain[o] = cipher[o] XOR mask
```

本项目用 numpy 向量化：一次性生成整个分块的偏移数组 → 索引数组 → 旋转掩码数组 → 整分块 XOR。

### 3.2 Static 固定密码盒（无密钥旧版，`_QmcStaticCipher`）

256 字节固定盒（来自 unlock-music `cipher_static.go`），索引：

```
idx = (o² + 27) & 0xFF
plain[o] = cipher[o] XOR box[idx]
```

### 3.3 分段 RC4（密钥 >300 字节，`_QmcRc4Cipher`）

结构：

- **KSA**：`box[i] = i & 0xFF` 初始化（注意按字节截断），标准 RC4 密钥调度。
- **密钥哈希**：`h` 从 1 开始连乘非零密钥字节（mod 2³²），遇 0 跳过、不增长则停。
- **分段结构**：首 128 字节特殊处理；之后每 **5120 字节为一段**。段 `seg_id` 的跳过量：

  ```
  seed = key[seg_id % n]
  skip = (seed == 0) ? 0 : floor(hash / ((seg_id+1) * seed) * 100.0) % n
  ```

  段内从 RC4 状态机先空转 `(offset % 5120) + skip` 步丢弃 keystream，再逐字节 XOR。

- 首 128 字节：`cipher[i] ^= key[segment_skip(i)]`（每字节按 `i` 直接算 skip）。

这个实现保留了参考实现的分段语义，同时因为每段的 skip 可由段号直接计算，所以任意偏移都可以**按需定位**——流式解密时每个 8MB 分块独立处理即可。

---

## 4. musicex：密钥不在文件内怎么办

### 4.1 musicex 尾部结构（`parse_musicex_info_from_bytes`）

```
文件末尾布局：
  [ ... 音频数据 ... ][ 尾块 tail_size 字节 ][ LE u32 tail_size @ -16 ][ "musicex\x00" @ -8 ]

尾块内（184 ≤ tail_size ≤ 4096）：
  [28:88]   song_mid       UTF-16-LE（去尾零）
  [88:184]  客户端原始文件名  UTF-16-LE（如 AIM0xxx.mflac / F0M0xxx.mflac / 8xxx.mgg）

audio_size = 文件总大小 - 16 - tail_size
```

> ⚠️ **文件名不能改写**：`fetch_ekey` 要求把尾部记录的原始文件名原样传给接口——不同音质前缀（F0M0/AIM0/Q000…）对应不同扩展名，自行拼改会取错密钥。仅当尾部文件名缺扩展名时按前缀惯例兜底（`F0` 开头补 `.mflac`，否则补 `.mgg`）。

### 4.2 登录态提取：Win32 只读内存扫描（`extract_cookie_from_process`）

流程：

1. `psapi.EnumProcesses` 枚举 PID，`GetModuleBaseNameA` 找 `QQMusic.exe`。
2. `OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION)`——**只读权限，不注入、不写**。
3. `VirtualQueryEx` 遍历进程内存区域，过滤条件：`MEM_COMMIT` + 可读保护页 + 区域大小 4KB~50MB。
4. `ReadProcessMemory` 读入后在字节流中查找标记 **`qqmusic_key=`**。
5. 命中后截取至 NUL（≤512 字节），正则提取 `qqmusic_uin=(\d+)` 得到 QQ 号。

### 4.3 按歌换取 ekey：官方 `CgiGetVkey` 接口（`fetch_ekey`）

```
POST https://u.y.qq.com/cgi-bin/musicu.fcg
Headers: Content-Type: application/json; Cookie: <提取的 cookie>; User-Agent: QQMusic/21
Body:
{
  "comm":  { "cv": 4747474, "ct": 24, "format": "json", ..., "uin": <QQ号>, ... },
  "req_1": {
    "module": "vkey.GetVkeyServer",
    "method": "CgiGetVkey",
    "param": {
      "filename": ["<尾部原始文件名>"], "guid": "10000",
      "songmid":  ["<song_mid>"],       "songtype": [0],
      "uin": "<QQ号>", "loginflag": 1, "platform": "20"
    }
  }
}
响应取 req_1.data.midurlinfo[0].ekey
```

工程要点：

- **登录态缓存优先**：先读本地密钥库缓存的 cookie；失效时重新内存扫描，**失败自动重试一轮**（`ensure_ekeys`）。
- **双键索引**：ekey 同时按 `mid:<song_mid>` 和 `file:<文件名小写>` 落盘，任一命中即可用。
- **持久化安全**：密钥库存放 `~/.soniccheck/qqmusic_ekeys.json`；cookie 落盘前经 **Windows DPAPI**（`CryptProtectData`，当前用户绑定）加密并 base64，前缀 `dpapi:`；换机/换用户解密失败自动回退为重新导入。ekey 本身不上传任何第三方。
- 实测一批 7 首全部成功（7/7）。

### 4.4 联网补齐标签与封面（`fetch_track_info`）

musicex 文件本身不带歌名/歌手/封面，解锁后可选联网补齐：

- 走 **`music.pf_song_detail_svr` / `get_song_detail`** 详情接口（按 mid 精确返回）。**刻意不用搜索接口**——搜索接口把 mid 当关键词，可能返回错歌。
- 返回校验：若返回的 `track_info.mid` 与请求的 song_mid 不一致，按失败降级。
- 封面：按 `album.mid` 从官方图床取 `T002R500x500M000<album_mid>.jpg`（500×500，无需登录）。
- **任何失败（断网/无权限/未找到）静默降级为无标签输出，不影响解锁本身**；用户可在界面一键关闭走纯离线。

---

## 5. 流式解密与输出管线

### 5.1 为什么能流式

§3 三种流密码的 keystream 只依赖绝对偏移（RC4 分段的 skip 也可由段号直接算出），因此：

```
对分块 [done, done+len)：
  keystream 由偏移数组 arange(done, done+len) 直接生成
  无需知道前面任何字节的内容
```

实现（`_decrypt_qmc_stream`）：只读文件尾部定位密钥与音频边界 → 回到文件头，按 **8MB 分块**（`_STREAM_CHUNK`）读 → `cipher.decrypt_into(buf, done)` → 写出。实测 **296.2MB 文件峰值内存约 75.9MB**（旧整体读入方案约 890MB），内存占用与文件大小解耦。

### 5.2 内层格式识别（`detect_audio_format`）

解密后按 magic 识别，与参考实现同口径：

| magic | 格式 |
|---|---|
| `FF Ex`（帧同步） / `ID3` | mp3 |
| `fLaC` | flac |
| `OggS` | ogg |
| `RIFF` | wav |
| `MAC ` | ape |
| `????ftyp`（偏移 4） | aac (m4a) |

首分块解密后先取前 4100 字节做识别，识别不出直接判失败（"密钥可能已失效或文件损坏"），避免白写几百 MB。

### 5.3 输出管线（`_write_output`）

```
解密裸流临时文件（在输出目录，保证同盘原子 rename）
├─ target=auto 且无需标签/封面 → 直接 rename 落盘（零转码）
├─ 仅打标签/内嵌封面：
│    ffmpeg -i raw -i meta.txt [-i cover.jpg] -c copy -map_metadata ...
│    失败 → 清半截 → 保底输出无标签文件
└─ 强制 FLAC / WAV：
     ffmpeg -c:a flac / pcm_s24le 转码
     内层有损（mp3/ogg/aac）时明确提示"换容器不提升音质"
```

工程细节：

- **中文标签不乱码**：标签写入走 `ffmetadata1` 临时文件 + `-map_metadata`，绕开 Windows 命令行 ANSI 编码问题（转义规则：`\ = ; #` 与换行需反斜杠转义）。
- **封面内嵌**：仅 flac/mp3 支持，`-disposition:v:0 attached_pic`。
- **超时按文件大小缩放**：基础 120s，超出部分按 2MB/s 保守速率追加（300MB → ~150s；1GB → ~512s），慢盘/大文件不再误杀。
- **半截文件零容忍**：任何异常/取消都清掉半截输出；裸流临时文件 `finally` 兜底删除。
- **命名**：优先 `歌名 - 歌手`；无标签时剥掉加密/音频扩展段与 QQ 音乐音质尾缀（`_EM`/`_EG`/`_EA` 等 `_E[A-Z]` 正则）；撞名加序号 `(1)`，**绝不覆盖已有文件**。

### 5.4 协作式取消

流式循环每块检查 `cancel_check`；ffmpeg 用 Popen 轮询（50ms 间隔），取消即 kill。取消不是失败——不产错误行，临时文件与半截输出全部清理。

---

## 6. 安全设计一览

| 行为 | 设计 |
|---|---|
| 进程内存 | **只读**扫描（`PROCESS_VM_READ`），不注入、不修改客户端任何数据 |
| cookie 落盘 | Windows DPAPI 加密（当前用户绑定），换机/换用户自动失效 |
| 密钥存储 | 仅本机 `~/.soniccheck/qqmusic_ekeys.json`，不上传 |
| 源文件 | 全程只读；输出撞名加序号，绝不覆盖 |
| 网络请求 | 仅腾讯官方接口（`u.y.qq.com`）与官方图床（`y.gtimg.cn`），无第三方 |
| 可审计 | 全项目 GPL v3 开源，算法对照 MIT 参考实现逐字节验证 |

---

## 7. 能力边界（明确不支持）

1. **STag 文件**：密钥完全不在本地且无取回通道，无法离线解密。唯一办法：用 ≤19.43 旧版客户端重新下载（旧版下载的是密钥内嵌格式）。
2. **音质不提升**：内层有损（mp3/ogg/m4a）的文件，转 FLAC/WAV 只是换容器。解锁 = 解密 + 重封装，不是音质修复。
3. **仅处理本地已有文件**：不提供任何搜索、下载、绕过付费的能力。

---

## 8. 验证与测试

- **逐字节对照**：合成文件加密→解密往返，与 unlock-music CLI v0.2.12 / qmdec 参考实现输出逐字节一致。
- **流式等价**：8MB 分块流式解密结果与整体解密逐字节一致（测试把分块改小以强制跨块边界）。
- **回归套件**：`python tests\run_all.py` 一键全跑，100+ 项断言（含密钥库双键、DPAPI 往返、文件操作回归）。

---

## 9. 关键常量索引（便于 code review）

| 常量 | 值 | 用途 |
|---|---|---|
| `_QMC_KEY_THRESHOLD` | 300 | map / RC4 分流阈值 |
| `_QMC_SIMPLE_KEY` | `69 56 46 38 2B 20 15 0B` | v1 派生交错密钥 |
| `_QMC_DERIVE_V2_KEY1/2` | 16 字节 ×2 | v2 双 TEA |
| `_QMC_V2_PREFIX` | `QQMusic EncV2,Key:` | v2 ekey 识别 |
| `_QMC_STATIC_BOX` | 256 字节 | QMCv1 固定盒 |
| TEA DELTA / 轮数 | `0x9E3779B9` / 16 | TEA 单块 |
| map 密码 | `idx=(o²+71214)%n`，循环左移 `((idx&7)+4)%8` | 短密钥流密码 |
| static 密码 | `idx=(o²+27)&0xFF` | 无密钥旧版 |
| RC4 分段 | 首 128B 特殊，段长 5120B | 长密钥流密码 |
| `MUSICEX_MAGIC` | `musicex\x00` | musicex 识别 |
| `_COOKIE_MARKER` | `qqmusic_key=` | 内存 cookie 定位 |
| `_STREAM_CHUNK` | 8 MB | 流式分块 |
| 偏移回绕 | `o > 0x7FFF → o % 0x7FFF` | 三种密码共用 |

---

> **再次强调**：以上能力仅用于你已合法获得的文件的本地格式转换。如对实现有疑问或改进建议，欢迎在 Issue / Discussion 交流。
