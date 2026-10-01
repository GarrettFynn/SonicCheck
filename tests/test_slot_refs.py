#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""槽链静态审计（v2.0.1）：捕获"引用了不存在的方法/属性"类致命问题

背景：v1.3.0 设置面板闪退、v2.0.0 标签改名闪退同属一类——用户可触发
的槽在运行时引用了不存在的属性（重构搬移/删除时漏改）。本审计用 AST
解析 main_window + app_controllers，收集三个类的全部方法/类属性/实例
属性（含 __init__ 及任意方法中的 self.X = 赋值），然后逐函数校验每条
self.X.Y / mw.X.Y 引用链的首段与已知的本地类段。

纯 AST 实现，不导入 Qt，CI 安全。运行: python tests/test_slot_refs.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 本地类名 → 模块（用于链的第二段校验）
LOCAL_FILES = {
    'main_window.py': {'MainWindow'},
    'app_controllers.py': {'ScanController', 'FileOpsController',
                           '_FingerprintWorker', '_FpSignals'},
    'widgets/result_table.py': {'ResultTable'},
    'widgets/left_panel.py': {'LeftPanel'},
    'widgets/log_panel.py': {'LogPanel'},
    'widgets/progress_bar.py': {'ProgressWidget'},
    'widgets/summary_bar.py': {'SummaryBar'},
    'widgets/result_model.py': {'ResultTableModel', 'ResultProxyModel'},
}

# 实例属性 → 本地类名（MainWindow/控制器里持有的组件）
INSTANCE_CLASS = {
    'result_table': 'ResultTable',
    'left_panel': 'LeftPanel',
    'log_panel': 'LogPanel',
    'progress': 'ProgressWidget',
    'summary_bar': 'SummaryBar',
    'model': 'ResultTableModel',
    'proxy': 'ResultProxyModel',
    'scan': 'ScanController',
    'ops': 'FileOpsController',
}


def collect_class_attrs(tree, class_name):
    """类的方法 + 类属性 + 全部函数体内的 self.X = 实例属性"""
    cls = next((n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == class_name), None)
    if cls is None:
        return None
    methods, class_attrs, instance_attrs = set(), set(), set()
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.add(node.name)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) \
                else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    class_attrs.add(t.id)
                elif isinstance(t, ast.Attribute) and \
                        isinstance(t.value, ast.Name) and \
                        t.value.id == 'self':
                    instance_attrs.add(t.attr)
    return {'methods': methods, 'class_attrs': class_attrs,
            'instance_attrs': instance_attrs}


# Qt 基类（QObject/QWidget/QMainWindow/QDialog/QFrame）继承面白名单：
# 审计不解析 Qt 存根，这些名字视为合法首段。curated 自本项目实际用到
# 的继承方法；新增用到时补这里。
QT_BASE_METHODS = {
    'setWindowTitle', 'resize', 'setMinimumSize', 'setMaximumSize',
    'setFixedSize', 'setFixedWidth', 'setFixedHeight', 'setAcceptDrops',
    'setCentralWidget', 'statusBar', 'restoreGeometry', 'saveGeometry',
    'show', 'hide', 'close', 'exec', 'update', 'repaint', 'setStyleSheet',
    'width', 'height', 'size', 'pos', 'parent', 'parentWidget',
    'deleteLater', 'installEventFilter', 'setFocus', 'raise_',
    'adjustSize', 'setVisible', 'isVisible', 'isEnabled', 'setEnabled',
    'setUpdatesEnabled', 'grab', 'render', 'window', 'move', 'sizeHint',
    'setLayout', 'layout', 'setObjectName', 'objectName', 'setToolTip',
    'setWhatsThis', 'testAttribute', 'setAttribute', 'acceptDrops',
    'closeEvent', 'showEvent', 'hideEvent', 'keyPressEvent',
    'mousePressEvent', 'dragEnterEvent', 'dropEvent', 'dragLeaveEvent',
    'paintEvent', 'resizeEvent', 'focusOutEvent', 'changeEvent',
    'event', 'eventFilter', 'thread', 'blockSignals', 'connect',
    'disconnect', 'emit', 'signalsBlocked', 'findChildren', 'findChild',
    'setProperty', 'property', 'children', 'repolish',
}


def chains_rooted_at(node, roots):
    """收集函数体内以 roots 中的名字开头、最长 3 段的属性链"""
    found = []
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute):
            segs = []
            cur = n
            while isinstance(cur, ast.Attribute):
                segs.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id in roots:
                segs.append(cur.id)
                if len(segs) <= 4:
                    found.append(tuple(reversed(segs)))
    return found


def main():
    trees = {}
    classes = {}
    for rel, names in LOCAL_FILES.items():
        tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'), rel)
        trees[rel] = tree
        for name in names:
            info = collect_class_attrs(tree, name)
            if info:
                classes[name] = info

    errors = []
    for rel in ('main_window.py', 'app_controllers.py'):
        tree = trees[rel]
        for cls_node in tree.body:
            if not isinstance(cls_node, ast.ClassDef):
                continue
            cname = cls_node.name
            info = classes[cname]
            own = info['methods'] | info['class_attrs'] \
                | info['instance_attrs']
            for func in cls_node.body:
                if not isinstance(func, (ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                    continue
                for chain in chains_rooted_at(func, ('self', 'mw')):
                    root = 'MainWindow' if chain[0] in ('self', 'mw') \
                        and cname != 'MainWindow' and chain[0] == 'mw' \
                        else (cname if chain[0] == 'self' else 'MainWindow')
                    # 控制器里 self → 自身；self/mw 在 MainWindow → MainWindow
                    if cname == 'MainWindow':
                        root = 'MainWindow'
                    else:
                        root = cname if chain[0] == 'self' else 'MainWindow'
                    target = classes[root]
                    pool = target['methods'] | target['class_attrs'] \
                        | target['instance_attrs']
                    if chain[1] in QT_BASE_METHODS:
                        continue
                    if chain[1] not in pool:
                        errors.append(
                            f"{rel}:{func.lineno} {cname}.{func.name}: "
                            f"{'.'.join(chain)} —— {root} 缺少 '{chain[1]}'")
                        continue
                    # 第二段：解析到已知本地类则继续校验
                    if len(chain) >= 3:
                        sub = INSTANCE_CLASS.get(chain[1])
                        if sub and chain[2] not in (
                                classes[sub]['methods']
                                | classes[sub]['class_attrs']
                                | classes[sub]['instance_attrs']):
                            errors.append(
                                f"{rel}:{func.lineno} {cname}.{func.name}: "
                                f"{'.'.join(chain)} —— {sub} 缺少 "
                                f"'{chain[2]}'")

    if errors:
        print(f"✗ 槽链审计发现 {len(errors)} 处未解析引用：")
        for e in errors:
            print(' ', e)
        sys.exit(1)
    checked = sum(len(classes[c]['methods']) for c in classes)
    print(f"✓ 槽链静态审计通过：{len(classes)} 个类 {checked} 个方法，"
          "全部 self./mw. 引用链可解析")


if __name__ == '__main__':
    main()
