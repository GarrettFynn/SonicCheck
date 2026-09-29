#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SonicCheck 抖音图文卡片渲染器 v3：16 张 1080x1440 PNG。

v3 变化：
- 每张信息量加大，说明顺序重排（现象 → 概念 → 总原理 → 两家各自详解）
- 新增网易云 ncm 解锁原理专卡（卡 10）
- QQ 音乐解锁原理拆成三张讲透：三本密码本（卡 5）、ekey 的 TEA 壳（卡 6）、
  藏钥匙四代进化史（卡 7）
用 PIL 直接绘制；中文字体优先微软雅黑。
主题：深色音频风（声波装饰线 + 祖母绿强调色 + 琥珀警示色）。
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1440
OUT_DIR = Path(__file__).parent / "douyin_cards"

# ── 配色 ──
BG = (13, 17, 23)            # 深夜黑蓝
FG = (232, 237, 243)         # 主文字
FG_DIM = (148, 158, 170)     # 次要文字
ACCENT = (52, 211, 153)      # 祖母绿（品牌/亮点）
WARN = (251, 191, 36)        # 琥珀（警示）

# ── 字体 ──
def _load(size, bold=False):
    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    raise RuntimeError("找不到中文字体（msyh/simhei）")

F_KICKER = _load(30)
F_TITLE = _load(60, bold=True)
F_TITLE_L = _load(72, bold=True)
F_BODY = _load(38)
F_BODY_B = _load(38, bold=True)
F_BRAND = _load(30, bold=True)

MARGIN = 90
CONTENT_W = W - MARGIN * 2


def wrap(text, font, max_w):
    """按像素宽度对中文/混排文本折行"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if font.getlength(cur + ch) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def draw_wave(draw, y_base, color, alpha_h=26, seed_shift=0.0):
    """底部装饰声波（两层正弦叠加成点线）"""
    for x in range(0, W, 6):
        t = x / W * math.pi * 4 + seed_shift
        amp = math.sin(t) * 0.6 + math.sin(t * 2.7 + 1.3) * 0.4
        y = y_base + amp * alpha_h
        draw.ellipse([x, y - 2, x + 4, y + 2], fill=color)


def render_card(idx, total, kicker, title_lines, body, out_path,
                big_title=False):
    """body: list of (text, style) ; style: plain|accent|warn|dim|bold"""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 顶部装饰条
    d.rectangle([0, 0, W, 10], fill=ACCENT)

    y = 200 if big_title else 86
    # kicker + 页码
    d.text((MARGIN, y), kicker, font=F_KICKER, fill=ACCENT)
    page = f"{idx} / {total}"
    pw = F_KICKER.getlength(page)
    d.text((W - MARGIN - pw, y), page, font=F_KICKER, fill=FG_DIM)
    y += 30 + 42

    # 标题
    title_font = F_TITLE_L if big_title else F_TITLE
    for line in title_lines:
        for sub in wrap(line, title_font, CONTENT_W):
            d.text((MARGIN, y), sub, font=title_font, fill=FG)
            y += title_font.size + 20
    y += 14
    d.line([MARGIN, y, MARGIN + 120, y], fill=ACCENT, width=6)
    y += 44

    # 正文
    for text, style in body:
        font = F_BODY_B if style in ("accent", "warn", "bold") else F_BODY
        color = {"plain": FG, "dim": FG_DIM, "accent": ACCENT,
                 "warn": WARN, "bold": FG}[style]
        for sub in wrap(text, font, CONTENT_W):
            d.text((MARGIN, y), sub, font=font, fill=color)
            y += font.size + 20
        y += 10  # 段间距

    # 底部：声波 + 品牌
    draw_wave(d, H - 170, (35, 48, 60), seed_shift=idx * 0.9)
    draw_wave(d, H - 150, ACCENT, alpha_h=18, seed_shift=idx * 0.9 + 2.1)
    d.text((MARGIN, H - 96), "SonicCheck · 声鉴·曲库管家", font=F_BRAND,
           fill=FG_DIM)
    img.save(out_path)


CARDS = [
    # 1 封面
    dict(kicker="QQ 音乐 + 网易云 · 加密一篇讲清",
         title=["你下载的歌，", "为什么换个设备", "就放不了？"],
         body=[("mflac / mgg / ncm 是什么、怎么解锁，", "dim"),
               ("新版 STag 为什么目前无解", "dim"),
               ("外行能看懂，内行挑不出错", "dim")],
         big_title=True),

    # 2 那些文件是什么
    dict(kicker="先说清楚 · 那些文件是什么",
         title=["你下载的其实是", "一个「上锁的盒子」"],
         body=[("QQ 音乐下载的 xxxx.mflac / xxxx.mgg，", "plain"),
               ("网易云下载的 xxxx.ncm，都是同一种东西", "plain"),
               ("盒子里装的其实是标准音频：FLAC / MP3 / OGG", "plain"),
               ("但每个字节都被打乱过，只有官方客户端认识", "plain"),
               ("官方客户端能放，因为它播放时实时开锁", "accent"),
               ("你想拷到车上、HiFi 播放器、别的 App——", "plain"),
               ("没有钥匙，统统打不开", "bold")]),

    # 3 解锁是什么（核心概念）
    dict(kicker="核心概念 · 「解锁」到底是什么",
         title=["解锁 = 解密 + 换个标准盒子"],
         body=[("用正确的密码本，把打乱的字节逐字节还原", "plain"),
               ("再按标准格式重新封装，补上歌名、歌手和封面", "plain"),
               ("解锁不是下载：它一首新歌都不会帮你下", "dim"),
               ("解锁不是破解会员：只处理你已下载到本地的文件", "dim"),
               ("解锁不提升音质：盒子里的货原样取出，没变过", "dim"),
               ("一句话：把你已经拥有的歌，从平台的盒子里取出来", "accent")]),

    # 4 总原理（两家共用）
    dict(kicker="两家平台 · 同一个总原理",
         title=["加密 = 一本「密码本」"],
         body=[("QQ 音乐和网易云的加密是同一类思路：流密码", "plain"),
               ("音频第 1 个字节和密码本第 1 个字节做 XOR 运算", "plain"),
               ("第 2 个对第 2 个……一路对下去，原文就成了乱码", "plain"),
               ("XOR 有个很美的性质：", "plain"),
               ("同一个数异或两次，等于没动", "accent"),
               ("所以加密和解密是同一个动作", "plain"),
               ("区别只在于：你有没有那本密码本", "bold"),
               ("两家的差别不在锁，在于「钥匙怎么藏」", "accent")]),

    # 5 QQ 音乐：三本密码本
    dict(kicker="QQ 音乐 · 密码本有三本",
         title=["按钥匙长度自动选用"],
         body=[("钥匙不超过 300 字节 → map 密码：", "bold"),
               ("按「位置平方 + 71214」算坐标，取钥匙对应字节", "dim"),
               ("钥匙超过 300 字节 → 分段 RC4：", "bold"),
               ("每 5120 字节为一段，每段先空转几步再逐字节异或", "dim"),
               ("没有钥匙的远古文件 → static 固定盒：", "bold"),
               ("一本写死的 256 字节密码本，人人相同", "dim"),
               ("三本共同点：每个字节只看位置编号，不看上下文", "accent"),
               ("这一点后面会救我们一命（流式解密靠它）", "dim")]),

    # 6 QQ 音乐：钥匙的壳
    dict(kicker="QQ 音乐 · 钥匙还套着壳",
         title=["拿到 ekey 还不能直接用"],
         body=[("文件里存的、接口返回的钥匙，叫 ekey", "plain"),
               ("它外面裹着腾讯的 TEA 加密壳（16 轮块加密）", "plain"),
               ("要先剥壳才能得到真钥匙，才能推演密码本", "plain"),
               ("老版剥一层；新版 EncV2 要剥两层 TEA", "plain"),
               ("中间还夹一次 base64 解码", "plain"),
               ("完整链路：", "dim"),
               ("ekey → base64 →（新版：双 TEA）→ TEA 剥壳", "accent"),
               ("→ 真钥匙 → 密码本 → 逐字节还原音频", "accent")]),

    # 7 QQ 音乐：藏钥匙进化史
    dict(kicker="QQ 音乐 · 藏钥匙进化史",
         title=["四代，一代比一代藏得深"],
         body=[("第一代（远古）：没有钥匙，全民共用固定密码盒", "plain"),
               ("第二代：钥匙粘在文件尾巴上，附长度标记", "plain"),
               ("第三代 QTag：钥匙和歌名打包塞尾巴，换汤不换药", "plain"),
               ("第四代 musicex（现在的新版）：钥匙彻底不放文件里", "bold"),
               ("尾巴只剩歌曲身份证号（song_mid）和原始文件名", "dim"),
               ("钥匙放在腾讯服务器上，要凭登录态按歌领取", "accent"),
               ("前三代任何工具都能离线直接解开；", "plain"),
               ("第四代要先「领钥匙」，这就是密钥导入的由来", "plain")]),

    # 8 musicex 全流程
    dict(kicker="musicex 解锁全流程",
         title=["五步，全自动"],
         body=[("① 读文件尾巴，拿到歌曲身份证号和原始文件名", "plain"),
               ("② 查本地钥匙库：以前领过的直接用，不重复领", "plain"),
               ("③ 只读扫描正在运行的 QQ 音乐客户端内存，", "plain"),
               ("找到 qqmusic_key= 标记，取出你的登录 cookie", "plain"),
               ("④ 带着 cookie 调腾讯官方接口，按歌换回钥匙", "plain"),
               ("文件名必须原样上传：不同音质前缀对应不同钥匙", "dim"),
               ("⑤ 钥匙 DPAPI 加密存进本机钥匙库，随即开始解密", "plain"),
               ("实测 7 / 7 成功，失败的会自动重试一轮", "accent")]),

    # 9 STag 专卡
    dict(kicker="专门讲讲 · STag 是什么",
         title=["最新一代：为什么无解"],
         body=[("STag 是 QQ 音乐最新一代加密的尾部标记", "plain"),
               ("钥匙不仅不在文件里，连领取的通道都没留下", "plain"),
               ("解锁的前提是拿到密码本；没有钥匙，", "plain"),
               ("数学上就无法还原——", "plain"),
               ("这不是工具强不强的问题", "bold"),
               ("是钥匙根本不存在于你电脑里的问题", "bold"),
               ("唯一出路：用 19.43 及以下旧版客户端重新下载，", "warn"),
               ("旧版下载的还是「钥匙在文件里」的格式，就能解", "warn")]),

    # 10 网易云 ncm 原理
    dict(kicker="顺带讲讲 · 网易云 ncm",
         title=["它的锁更简单：", "钥匙就在文件头里"],
         body=[("ncm 的思路不同：每把锁的钥匙", "plain"),
               ("就藏在文件自己头部，用 AES-128 加密封存", "plain"),
               ("而开这层封存的「主钥匙」是公开的——", "plain"),
               ("所有 ncm 文件通用同一把，早已随逆向公开", "accent"),
               ("所以 ncm 解锁全程离线：不需要登录，不需要联网", "bold"),
               ("流程：读文件头 → 用主钥匙解开钥匙包", "plain"),
               ("→ 展开成 256 字节密码盒（KeyBox）", "plain"),
               ("→ 逐字节 XOR 还原音频", "plain"),
               ("头部自带歌名 / 歌手 / 专辑 / 封面，一并写回", "plain")]),

    # 11 安全
    dict(kicker="你可能担心 · 安全吗",
         title=["四件事，说清楚"],
         body=[("只读：扫描内存用的是 Windows 只读权限", "bold"),
               ("不注入、不修改 QQ 音乐的任何数据", "dim"),
               ("加密存：cookie 用 Windows DPAPI 加密落盘", "bold"),
               ("绑定你的系统账号，换电脑、换用户就是乱码", "dim"),
               ("不出门：钥匙和 cookie 只存在你自己电脑，不上传", "bold"),
               ("可查验：全部源码在 GitHub 公开，欢迎 review", "bold"),
               ("网络请求只发往官方接口，没有任何第三方服务器", "dim")]),

    # 12 流式解密
    dict(kicker="技术流高光",
         title=["300MB 的歌，", "峰值内存只有 76MB"],
         body=[("老工具把整个文件读进内存再解：", "plain"),
               ("300MB 的歌峰值要吃掉约 890MB，老电脑直接卡死", "dim"),
               ("SonicCheck 每次只处理 8MB，解完写出、再读下一块", "plain"),
               ("为什么能这样？还记得三本密码本的共同点吗：", "plain"),
               ("每个字节只看位置编号、不看上下文", "accent"),
               ("像翻书一样随翻随解", "accent"),
               ("不用像看录像带一样，必须从头放到尾", "dim")]),

    # 13 解锁之外
    dict(kicker="开完锁，还顺手做了这些",
         title=["不止是解锁"],
         body=[("认出真身：按文件头指纹识别 FLAC / MP3 / OGG", "plain"),
               ("补上名牌：ncm 头部自带标签直接写回；", "plain"),
               ("QQ musicex 则联网取回歌名 / 歌手 / 专辑", "plain"),
               ("并内嵌 500×500 专辑封面", "plain"),
               ("断网或失败自动降级为无标签，不影响解锁本身", "dim"),
               ("文件名自动清掉 _EM / _EG 之类的音质尾缀", "plain"),
               ("老实提示：有损内层转 FLAC 只是换盒子", "warn"),
               ("音质不会变好——解锁是还原，不是修复", "warn")]),

    # 14 自测
    dict(kicker="实用 · 你的文件能不能解",
         title=["一分钟自测"],
         body=[("网易云 .ncm：直接解，全程离线", "accent"),
               ("QQ 音乐老版本下载的（钥匙在文件里）：直接解", "accent"),
               ("QQ 音乐新版下载的 .mflac / .mgg（musicex）：", "plain"),
               ("先点「导入 QQ 音乐密钥」，之后即可离线解锁", "accent"),
               ("解锁时提示 STag 的：目前无解", "warn"),
               ("换 19.43 及以下旧版客户端重新下载，再回来解", "warn")]),

    # 15 边界
    dict(kicker="三条必须说的边界",
         title=["工具无罪，用途有责"],
         body=[("1. 只处理你已下载到本地的文件", "bold"),
               ("不绕过付费，不下载新歌，连搜歌功能都没有", "dim"),
               ("2. STag 格式解不了", "bold"),
               ("谁跟你说「全能解」，让他试试 STag", "dim"),
               ("3. 请只转换你合法获得的文件", "bold")]),

    # 16 结尾
    dict(kicker="SonicCheck · 声鉴·曲库管家",
         title=["祝你的曲库里，", "没有假无损"],
         body=[("本职：无损音乐真伪鉴定（揪出假无损）", "plain"),
               ("技能：网易云 / QQ 音乐加密格式解锁", "plain"),
               ("免费 · 开源 GPL v3 · Windows 解压即用", "accent"),
               ("GitHub 搜「SonicCheck」", "bold")],
         big_title=True),
]


def main():
    OUT_DIR.mkdir(exist_ok=True)
    total = len(CARDS)
    for i, card in enumerate(CARDS, 1):
        out = OUT_DIR / f"card_{i:02d}.png"
        render_card(i, total, card["kicker"], card["title"], card["body"],
                    out, big_title=card.get("big_title", False))
        print(f"OK {out.name}")
    print(f"\n共 {total} 张卡片 → {OUT_DIR}")


if __name__ == "__main__":
    main()
