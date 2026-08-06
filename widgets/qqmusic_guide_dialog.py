#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ 音乐密钥导入教程对话框：针对 musicex（新版加密）文件的专用引导"""

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QTextBrowser,
                             QVBoxLayout, QWidget)

_HTML = """
<style>
  body { font-size: 13px; line-height: 1.6; }
  h3 { margin: 4px 0; }
  ol, ul { margin: 4px 0 10px 0; }
  li { margin: 3px 0; }
  code { background: rgba(128,128,128,0.15); padding: 1px 4px; }
  .warn { color: #c0392b; }
</style>

<h3>适用场景</h3>
<p>QQ 音乐<b>新版客户端</b>下载的 <code>.mflac</code> / <code>.mgg</code>
文件（musicex 加密）。这类文件的解密密钥<b>不保存在文件里</b>，
而是绑在下载它的客户端账号下，所以需要先从客户端把密钥取回来，
之后才能离线解锁。</p>

<h3>操作步骤</h3>
<ol>
  <li>打开 <b>QQ 音乐 Windows 客户端</b>，登录<b>下载过这些歌曲的账号</b>
      （VIP 歌曲需要 VIP 账号）。</li>
  <li>让客户端<b>保持运行</b>——不用播放，最小化也可以。</li>
  <li>回到本软件，把 <code>.mflac</code> / <code>.mgg</code>
      文件加入解锁列表。</li>
  <li>点击「<b>导入 QQ 音乐密钥</b>」：软件会从客户端进程读取登录态，
      为列表中缺密钥的歌曲逐一换取密钥（每首不到一秒）。</li>
  <li>看到「密钥已就绪」后，点「<b>开始解锁</b>」即可。</li>
</ol>

<h3>常见问题</h3>
<ul>
  <li><b>提示「未检测到运行中的 QQ 音乐客户端」</b><br>
      → 确认 QQ 音乐已经打开并且已登录。</li>
  <li><b>提示「无法读取 QQ 音乐进程」</b><br>
      → 关闭本软件，右键图标选择「以管理员身份运行」再试。</li>
  <li><b>提示「账号无此歌权限」或全部换取失败</b><br>
      → 换成当初下载这些歌的账号登录；客户端里退出重登后，
      再点一次「导入 QQ 音乐密钥」。</li>
  <li><b>密钥要每次导入吗？</b><br>
      → 不用。密钥缓存于本机
      <code>~/.soniccheck/qqmusic_ekeys.json</code>，不会过期；
      已经导入过的歌曲以后解锁无需再打开客户端。</li>
  <li><b>还是不行（客户端版本太新、接口变动）</b><br>
      → 备选方案：用 <b>≤19.43 的旧版客户端</b>重新下载这些歌，
      旧版文件密钥内嵌，本软件可直接离线解锁，无需任何导入。</li>
</ul>

<h3>安全说明</h3>
<ul>
  <li>软件对 QQ 音乐进程只做<b>只读</b>扫描，不注入、不修改客户端。</li>
  <li>登录态（cookie）与密钥<b>只保存在你自己的电脑上</b>，
      不会上传到任何第三方服务器。</li>
  <li class="warn">请仅处理你本人账号合法下载的文件，
      勿用于侵犯版权的用途。</li>
</ul>
"""


class QQMusicGuideDialog(QDialog):
    """QQ 音乐 musicex 密钥导入教程（只读说明，无操作）"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle("QQ 音乐密钥导入教程")
        self.resize(520, 560)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)

        text = QTextBrowser(self)
        text.setHtml(_HTML)
        text.setOpenExternalLinks(False)
        lay.addWidget(text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close,
                                   self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
