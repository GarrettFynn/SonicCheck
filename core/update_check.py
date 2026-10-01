#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动更新检查（V20-4）：启动后台查询 GitHub Latest，静默提示

- 任何失败（断网/超时/限流）静默放弃，绝不打扰
- tag_name 剥掉 semver 构建元数据后缀（+N）再比较——v1.2.0 的
  GitHub tag 是 v1.2.0+1 这类形态
- 只提示不自动下载；设置项 settings/check_updates 可关
"""

import json
import re
import urllib.request

LATEST_API = 'https://api.github.com/repos/GarrettFynn/SonicCheck/releases/latest'
_TIMEOUT = 8


def parse_version(tag: str) -> tuple:
    """'v1.2.0+1' → (1, 2, 0)——构建元数据不参与比较"""
    m = re.match(r'v?(\d+)\.(\d+)\.(\d+)', (tag or '').strip())
    if not m:
        return ()
    return tuple(int(x) for x in m.groups())


def fetch_latest_version() -> str:
    """返回 latest 的 tag_name；任何失败抛 RuntimeError（调用方静默）"""
    req = urllib.request.Request(LATEST_API, headers={
        'User-Agent': f'SonicCheck-UpdateCheck',
        'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read())
    tag = data.get('tag_name') or ''
    if not tag:
        raise RuntimeError('empty tag_name')
    return tag


def check(current_version: str) -> str:
    """有新版本返回新版本 tag 字符串，否则/失败返回 ''。永不抛异常。"""
    try:
        latest = fetch_latest_version()
    except Exception:
        return ''
    cur = parse_version(current_version)
    new = parse_version(latest)
    if not cur or not new:
        return ''
    return latest if new > cur else ''
