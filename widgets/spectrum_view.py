#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""频谱概要渲染（V13-1）：QPainter 折线，对数频率轴 + dB 轴

数据来自 core.analyzer.compute_spectrum_summary() 的 256 点 float32
（分档网格见 spectrum_grid()）。纯展示组件，不参与任何判定。
"""

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget

from core.analyzer import decode_spectrum_summary, spectrum_grid

# 频率网格常量与 analyzer 共享定义（20Hz–24kHz 对数）
_F_MIN, _F_MAX = 20.0, 24000.0
_DB_MIN, _DB_MAX = -100.0, 0.0

COLOR_CURVE = QColor("#4EC9B0")     # 主曲线（与表格真无损同色系）
COLOR_CURVE2 = QColor("#CE9178")    # 叠加对比曲线
COLOR_GRID = QColor("#3C3C3C")
COLOR_TEXT = QColor("#808080")
COLOR_CLIFF = QColor("#F44747")     # 断崖标注（与假无损同色）
COLOR_CUTOFF = QColor("#D7BA7D")    # -60dB 截止标注

_GRID_TICKS = (100, 1000, 10000)    # 对数轴主刻度


class SpectrumView(QWidget):
    """单条或双条频谱曲线；可标注断崖频率与 -60dB 截止线"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._db1 = None     # np.ndarray float32 / None
        self._db2 = None
        self._cliff_freq = 0.0
        self._cutoff_freq = 0.0
        self.setMinimumHeight(180)

    # ---------------- 数据 ----------------
    def set_data(self, spectrum_bytes: bytes, cliff_freq: float = 0.0,
                 cutoff_60db: float = 0.0) -> None:
        """设置主曲线（bytes 为 compute_spectrum_summary 输出）与标注"""
        self._db1 = decode_spectrum_summary(spectrum_bytes)
        self._db2 = None
        self._cliff_freq = float(cliff_freq or 0)
        self._cutoff_freq = float(cutoff_60db or 0)
        self.update()

    def set_overlay(self, spectrum_bytes: bytes) -> None:
        """设置/清除（传 b''）第二条对比曲线"""
        self._db2 = decode_spectrum_summary(spectrum_bytes) \
            if spectrum_bytes else None
        self.update()

    # ---------------- 坐标映射 ----------------
    @staticmethod
    def _freq_to_x(freq: float, left: float, width: float) -> float:
        """对数频率 → 画布 x（20Hz–24kHz 全域）"""
        t = (math.log10(max(freq, _F_MIN)) - math.log10(_F_MIN)) \
            / (math.log10(_F_MAX) - math.log10(_F_MIN))
        return left + t * width

    @staticmethod
    def _db_to_y(db: float, top: float, height: float) -> float:
        t = (min(max(db, _DB_MIN), _DB_MAX) - _DB_MIN) / (_DB_MAX - _DB_MIN)
        return top + (1.0 - t) * height

    # ---------------- 绘制 ----------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        margin_l, margin_b, margin_t = 34, 20, 8
        plot_w = w - margin_l - 8
        plot_h = h - margin_b - margin_t
        if plot_w <= 10 or plot_h <= 10:
            return
        left, top = float(margin_l), float(margin_t)

        painter.fillRect(QRectF(left, top, plot_w, plot_h),
                         QColor("#252526"))

        # 网格：对数频率主刻度 + dB 每 20dB
        painter.setPen(QPen(COLOR_GRID, 1))
        for f in _GRID_TICKS:
            x = self._freq_to_x(f, left, plot_w)
            painter.drawLine(QPointF(x, top),
                             QPointF(x, top + plot_h))
            painter.setPen(QPen(COLOR_TEXT, 1))
            label = f"{f // 1000:.0f}k" if f >= 1000 else f"{f:.0f}"
            painter.drawText(QRectF(x - 20, top + plot_h + 2, 40, 14),
                             Qt.AlignmentFlag.AlignCenter, label)
            painter.setPen(QPen(COLOR_GRID, 1))
        for db in range(-100, 1, 20):
            y = self._db_to_y(db, top, plot_h)
            painter.drawLine(QPointF(left, y), QPointF(left + plot_w, y))
            painter.setPen(QPen(COLOR_TEXT, 1))
            painter.drawText(QRectF(0, y - 7, margin_l - 4, 14),
                             Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter, str(db))
            painter.setPen(QPen(COLOR_GRID, 1))

        # 曲线（阶梯：每档画平台，保断崖的垂直落差形态）
        if self._db1 is not None and len(self._db1):
            self._paint_curve(painter, self._db1, left, top, plot_w,
                              plot_h, COLOR_CURVE)
        if self._db2 is not None and len(self._db2):
            self._paint_curve(painter, self._db2, left, top, plot_w,
                              plot_h, COLOR_CURVE2)

        # 标注竖线
        for freq, color, dash in ((self._cliff_freq, COLOR_CLIFF, True),
                                  (self._cutoff_freq, COLOR_CUTOFF, False)):
            if freq > 0:
                x = self._freq_to_x(freq, left, plot_w)
                pen = QPen(color, 1, Qt.PenStyle.DashLine if dash
                           else Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.drawLine(QPointF(x, top),
                                 QPointF(x, top + plot_h))

        if self._db1 is None or not len(self._db1):
            painter.setPen(QPen(COLOR_TEXT, 1))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "频谱数据不可用（请重新扫描）")

    def _paint_curve(self, painter: QPainter, db, left: float, top: float,
                     plot_w: float, plot_h: float, color: QColor) -> None:
        """256 档阶梯多边形：每个分档画 [档左, 档右] 的水平平台"""
        grid = spectrum_grid(len(db))
        poly = QPolygonF()
        started = False
        for i, v in enumerate(db):
            if v < _DB_MIN:      # 空档（-120 哨兵）跳过
                continue
            x1 = self._freq_to_x(float(grid[i]), left, plot_w)
            x2 = self._freq_to_x(float(grid[i + 1]), left, plot_w)
            y = self._db_to_y(float(v), top, plot_h)
            if not started:
                poly.append(QPointF(x1, y))
                started = True
            else:
                poly.append(QPointF(x1, poly.last().y()))
            poly.append(QPointF(x2, y))
        pen = QPen(color, 1.2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if poly.count() >= 2:
            painter.drawPolyline(poly)  # 上轮廓；下沿省略，视觉即面积图上缘
