"""MARK  -  single-page holographic command center (PySide6).

Layout idea
-----------
* ONE page, no scrolling.  The holographic core lives in the centre of the stage.
* Always in front: conversation, command bar, state / mic / stop (header).
* Everything else (tasks, verification, live info, system, camera, vision) lives
  behind the  ≡  (three-line) menu as ON / OFF switches.  Panels can also open
  themselves automatically when work happens (Auto-show).
* Every panel is a floating glass window: drag it by its title bar, resize it with the
  corner grip, put it anywhere.  Layout + switches are saved and restored.

Public hooks used by main.py / plugins are unchanged and safe to call from any thread.
"""
from __future__ import annotations

import html
import json
import math
import sys
import time
import warnings
from pathlib import Path
from typing import Any

# This module can also be started directly, so initialize COM before importing
# PySide6 or any automation-dependent code.
if sys.platform == "win32":
    sys.coinit_flags = 2  # pywinauto: COINIT_APARTMENTTHREADED (STA)
    warnings.filterwarnings(
        "ignore",
        message=r"Apply externally defined coinit_flags: 2",
        category=UserWarning,
        module=r"pywinauto",
    )
    try:
        import ctypes
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass

import numpy as np

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QCloseEvent, QColor, QFont, QImage, QKeySequence, QPainter, QPen, QPixmap, QPolygonF,
                           QRadialGradient, QShortcut, QShowEvent)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPlainTextEdit, QPushButton,
    QSlider, QTextEdit, QVBoxLayout, QWidget, QWidgetAction,
)

from core.telemetry import TELEMETRY

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"

STATE_RGB = {
    "LISTENING": (73, 243, 179),
    "THINKING": (155, 140, 255),
    "SPEAKING": (255, 155, 94),
    "MUTED": (251, 113, 133),
    "SLEEPING": (88, 166, 199),
}
STATE_NAMES = {"SLEEPING": "OFFLINE", "LISTENING": "LISTENING", "THINKING": "WORKING",
               "SPEAKING": "SPEAKING", "MUTED": "MIC MUTED"}

# key -> (title, default visible, default geometry as fractions of the stage: x, y, w, h)
PANELS: dict[str, tuple[str, bool, tuple[float, float, float, float]]] = {
    "chat":    ("◈  CONVERSATION",   True,  (0.012, 0.03, 0.26, 0.56)),
    "command": ("➤  COMMAND",        True,  (0.290, 0.87, 0.42, 0.11)),
    "info":    ("⌁  LIVE INFO",      False, (0.012, 0.61, 0.26, 0.27)),
    "tasks":   ("▤  TASKS & ALERTS", False, (0.728, 0.03, 0.26, 0.20)),
    "verify":  ("✓  VERIFICATION",   False, (0.728, 0.25, 0.26, 0.20)),
    "system":  ("⌗  SYSTEM STATUS",  False, (0.728, 0.71, 0.26, 0.19)),
    "vision":  ("⌖  VISION MONITOR", False, (0.300, 0.03, 0.40, 0.34)),
}
CORE_PANELS = ("chat", "command")

_STYLE = """
QMainWindow, #central { background: #050B14; }
QWidget { color: #DCEAF5; font-family: 'Segoe UI'; font-size: 10pt; }
QLabel { background: transparent; }
QFrame#panel { background: rgba(9, 21, 34, 214); border: 1px solid rgba(34, 211, 238, 95); border-radius: 12px; }
QFrame#phead { background: rgba(14, 50, 72, 150); border: 0; border-top-left-radius: 11px; border-top-right-radius: 11px; }
QLabel#section { color: #79DFFF; font-size: 9pt; font-weight: 700; letter-spacing: 1px; }
QLabel#brand { color: #F8FAFC; font-size: 18pt; font-weight: 700; letter-spacing: 3px; }
QLabel#subtitle, QLabel#muted { color: #94A3B8; }
QLabel#state { color: #67E8F9; font-weight: 700; padding: 5px 12px; border: 1px solid #164E63; border-radius: 10px; background: #0B2030; }
QLabel#metric { color: #B8D3E4; background: #0B1C2A; border: 1px solid #17384E; border-radius: 9px; padding: 7px 10px; }
QLabel#grip { color: #22D3EE; background: transparent; }
QProgressBar { background: #07101F; border: 1px solid #26364D; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #22D3EE; border-radius: 3px; }
QFrame#llmStatus { background: rgba(6, 16, 27, 170); border: 1px solid #1C3549; border-radius: 9px; }
QLabel#llmName { color: #FCA5A5; font-size: 8pt; font-weight: 700; padding: 2px 6px; }
QPlainTextEdit, QTextEdit, QLineEdit { background: rgba(6, 16, 27, 170); border: 1px solid #1C3549; border-radius: 9px; padding: 7px; selection-background-color: #0E7490; }
QLineEdit:focus { border: 1px solid #22D3EE; }
QPushButton { background: #0D2030; color: #DCE8F5; border: 1px solid #24445A; border-radius: 9px; padding: 7px 12px; font-weight: 600; }
QPushButton:hover { background: #123249; border-color: #22D3EE; }
QPushButton:pressed { background: #0E7490; }
QPushButton#icon { min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0; font-size: 15pt; color: #9AEAFF; }
QPushButton#primary { background: #0B7087; color: #F0FDFA; border: 1px solid #22D3EE; }
QPushButton#danger { color: #FCA5A5; border-color: #7F1D1D; }
QPushButton#close { min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; padding: 0; border-radius: 6px; font-size: 9pt; }
QMenu { background: #091522; border: 1px solid #22D3EE; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: #123249; }
QMenu::separator { height: 1px; background: #15344A; margin: 5px 8px; }
QCheckBox { spacing: 10px; padding: 5px 12px; background: transparent; }
QCheckBox::indicator { width: 34px; height: 16px; border-radius: 8px; background: #1E293B; border: 1px solid #334155; }
QCheckBox::indicator:checked { background: #0E7490; border: 1px solid #22D3EE; }
QScrollBar:vertical { width: 9px; background: transparent; }
QScrollBar::handle:vertical { background: #334155; border-radius: 4px; min-height: 25px; }
"""


# ──────────────────────────────────────────────────────────────────────────────
# floating glass panel (drag by title, resize by corner grip)
# ──────────────────────────────────────────────────────────────────────────────
class _Header(QFrame):
    def __init__(self, title: str, panel: "FloatPanel"):
        super().__init__(panel)
        self.panel = panel
        self._off = None
        self.setObjectName("phead")
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        self.title = QLabel(title)
        self.title.setObjectName("section")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.title, 1)
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("close")
        self.close_btn.setToolTip("Hide this panel (switch it back on from ≡)")
        lay.addWidget(self.close_btn)

    def mousePressEvent(self, e):
        self.panel.raise_()
        if e.button() == Qt.MouseButton.LeftButton and not self.panel.locked:
            self._off = e.globalPosition().toPoint() - self.panel.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._off is None:
            return
        par = self.panel.parentWidget()
        pos = e.globalPosition().toPoint() - self._off
        x = max(-self.panel.width() + 80, min(par.width() - 80, pos.x()))
        y = max(0, min(par.height() - 34, pos.y()))
        self.panel.move(x, y)

    def mouseReleaseEvent(self, e):
        e.accept()
        if self._off is not None:
            self._off = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.panel.geometry_changed.emit(self.panel.key)


class _Grip(QLabel):
    def __init__(self, panel: "FloatPanel"):
        super().__init__("◢", panel)
        self.panel = panel
        self.setObjectName("grip")
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._start = None

    def mousePressEvent(self, e):
        self.panel.raise_()
        if e.button() == Qt.MouseButton.LeftButton and not self.panel.locked:
            self._start = (e.globalPosition().toPoint(), self.panel.size())

    def mouseMoveEvent(self, e):
        if self._start is None:
            return
        g0, s0 = self._start
        d = e.globalPosition().toPoint() - g0
        par = self.panel.parentWidget()
        w = max(200, min(par.width() - self.panel.x(), s0.width() + d.x()))
        h = max(120, min(par.height() - self.panel.y(), s0.height() + d.y()))
        self.panel.resize(w, h)

    def mouseReleaseEvent(self, e):
        e.accept()
        if self._start is not None:
            self._start = None
            self.panel.geometry_changed.emit(self.panel.key)


class FloatPanel(QFrame):
    geometry_changed = Signal(str)
    closed = Signal(str)

    def __init__(self, key: str, title: str, body: QWidget, parent: QWidget):
        super().__init__(parent)
        self.key = key
        self.locked = False
        self.setObjectName("panel")
        self.setMinimumSize(200, 110)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = _Header(title, self)
        self.header.close_btn.clicked.connect(lambda: self.closed.emit(self.key))
        lay.addWidget(self.header)
        inner = QVBoxLayout()
        inner.setContentsMargins(10, 8, 10, 12)
        inner.addWidget(body)
        lay.addLayout(inner, 1)
        self.body = body
        self.grip = _Grip(self)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.grip.move(self.width() - 20, self.height() - 20)
        self.grip.raise_()


# ──────────────────────────────────────────────────────────────────────────────
# stage: the Jarvis-style particle hologram + home of all floating panels
# ──────────────────────────────────────────────────────────────────────────────
def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], np.float32)


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], np.float32)


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float32)


def _splat(x, y, w, ox, oy, Wb, Hb, sc):
    """sub-pixel (bilinear) splat of weighted points into a flat Wb*Hb float buffer."""
    x = (x - ox) * sc
    y = (y - oy) * sc
    xi = np.floor(x).astype(np.int32)
    yi = np.floor(y).astype(np.int32)
    m = (xi >= 0) & (xi < Wb - 1) & (yi >= 0) & (yi < Hb - 1)
    xi, yi, w = xi[m], yi[m], w[m]
    fx = (x[m] - xi).astype(np.float32)
    fy = (y[m] - yi).astype(np.float32)
    i = yi * Wb + xi
    return np.bincount(
        np.concatenate([i, i + 1, i + Wb, i + Wb + 1]),
        np.concatenate([w * (1 - fx) * (1 - fy), w * fx * (1 - fy), w * (1 - fx) * fy, w * fx * fy]),
        minlength=Wb * Hb).astype(np.float32)


# glow, spin, breathe, swirl, scan, wave   (same targets as the Jarvis hologram)
TARGETS = {
    "idle":      (0.30, 0.12, 0.015, 0.00, 0.25, 0.00),
    "listening": (0.85, 0.25, 0.070, 0.15, 0.25, 0.35),
    "thinking":  (0.65, 1.10, 0.030, 1.00, 1.00, 0.00),
    "speaking":  (0.80, 0.30, 0.020, 0.10, 0.20, 1.00),
}
ANIM = {"LISTENING": "listening", "SPEAKING": "speaking", "THINKING": "thinking",
        "SLEEPING": "idle", "MUTED": "idle"}


class Stage(QWidget):
    """Full-page canvas: Jarvis-style particle hologram + home of all floating panels."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.state = "SLEEPING"
        self.level = 0.0
        self._level_ts = 0.0
        self._lv = 0.0
        self.t = 0.0
        self.ang = 0.0
        self.burst = 0.0
        self.fade = 0.0
        self.q = 0
        self._slow = self._fast = 0.0
        self._last = time.monotonic()
        self.fracs: dict[str, list[float]] = {}
        self.panels: dict[str, FloatPanel] = {}

        rng = self.rng = np.random.default_rng(7)
        N = 7000
        k = np.arange(N) + .5
        ph = np.arccos(1 - 2 * k / N)
        th = np.pi * (1 + 5 ** .5) * k
        sph = np.stack([np.sin(ph) * np.cos(th), np.cos(ph), np.sin(ph) * np.sin(th)], 1).astype(np.float32)
        self.sph = sph[rng.permutation(N)]
        self.sph_ph = rng.uniform(0, 6.283, N).astype(np.float32)
        self.sph_w = rng.uniform(.4, 1, N).astype(np.float32)
        g, t = [], np.linspace(0, 2 * np.pi, 150, endpoint=False)
        for la in np.linspace(-1.25, 1.25, 13):
            g.append(np.stack([np.cos(la) * np.cos(t), np.full_like(t, np.sin(la)), np.cos(la) * np.sin(t)], 1))
        for lo in np.linspace(0, np.pi, 9, endpoint=False):
            g.append(np.stack([np.cos(t) * np.cos(lo), np.sin(t), np.cos(t) * np.sin(lo)], 1))
        g = np.concatenate(g)
        seg = np.arange(len(g)) // 12
        self.grid = (g[rng.random(seg.max() + 1)[seg] < .7] * 1.04).astype(np.float32)
        self.rings = [(1.40, .30, .0, .50), (1.64, 1.15, .5, -.35), (1.90, .70, -.9, .25)]
        tt = np.linspace(0, 2 * np.pi, 900, endpoint=False)
        self.ring_pts = np.stack([np.cos(tt), np.zeros_like(tt), np.sin(tt)], 1).astype(np.float32)
        self.ring_keep = np.sin(tt * 7) > -.55
        d = rng.normal(size=(420, 3))
        d /= np.linalg.norm(d, axis=1)[:, None]
        self.dust = (d * rng.uniform(1.25, 2.9, (420, 1))).astype(np.float32)
        self.dust_ph = rng.uniform(0, 6.28, 420)
        r = rng.normal(size=(420, 3))
        r /= np.linalg.norm(r, axis=1)[:, None]
        self.rays = r.astype(np.float32)
        self.ray_v = rng.uniform(.5, 1.6, 420).astype(np.float32)

        b = np.linspace(0, 4, 1024)
        self._ramp = (1 - np.exp(-1.7 * b)).astype(np.float32)
        self.tint = np.array(STATE_RGB["SLEEPING"], np.float32)
        self.lut = np.zeros((1024, 3), np.uint8)
        self._lut_sig = None
        self.p = np.array(TARGETS["idle"], np.float32)
        self._spec = np.zeros(72, np.float32)
        self._spec_ph = rng.uniform(0, 6.283, 72).astype(np.float32)
        self._bufsig = None
        self._outlines: list = []
        self._himg = self._simg = None
        self._harr = self._sarr = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # ---- inputs ------------------------------------------------------------------
    def set_level(self, v: float) -> None:
        self.level = max(0.0, min(1.0, v))
        self._level_ts = time.monotonic()

    def trigger(self) -> None:
        self.burst = 1.0

    @property
    def anim(self) -> str:
        return ANIM.get(self.state, "idle")

    # ---- animation ---------------------------------------------------------------
    def _tick(self):
        now = time.monotonic()
        dt = min(.1, now - self._last)
        self._last = now
        self.t += dt
        self.p += (np.array(TARGETS[self.anim], np.float32) - self.p) * (1 - math.exp(-dt * 4))
        self.ang += dt * float(self.p[1])
        self.burst = max(0.0, self.burst - dt)
        if now - self._level_ts > .35:
            self.level = 0.0
        rate = 22 if self.level > self._lv else 6
        self._lv += (self.level - self._lv) * (1 - math.exp(-dt * rate))
        f = min(1.0, self.t / 2.5)
        self.fade = f * f * (3 - 2 * f)
        target = np.array(STATE_RGB.get(self.state, STATE_RGB["SLEEPING"]), np.float32)
        rate = 11.0 if self.state == "MUTED" else 3.6
        self.tint += (target - self.tint) * (1 - math.exp(-dt * rate))
        sig = tuple(int(v) >> 2 for v in self.tint)
        if sig != self._lut_sig:
            self._lut_sig = sig
            rmp = self._ramp[:, None]
            base = np.clip(rmp * 1.4, 0, 1)
            self.lut = np.clip(self.tint[None, :] * base + (255.0 - self.tint)[None, :] * (rmp ** 5) * .55,
                               0, 255).astype(np.uint8)
        if self.isVisible():
            self.update()

    # ---- panel geometry ----------------------------------------------------------
    def apply_fracs(self) -> None:
        w, h = self.width(), self.height()
        if w < 100 or h < 100:
            return
        for key, panel in self.panels.items():
            fx, fy, fw, fh = self.fracs[key]
            panel.setGeometry(round(fx * w), round(fy * h), max(200, round(fw * w)), max(110, round(fh * h)))

    def remember(self, key: str) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        g = self.panels[key].geometry()
        self.fracs[key] = [g.x() / w, g.y() / h, g.width() / w, g.height() / h]

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.apply_fracs()

    # ---- particle hologram (numpy port of the Jarvis renderer) -------------------
    def _render(self, cx: float, cy: float, R: float, w: int, h: int) -> None:
        t0 = time.perf_counter()
        t = self.t
        glow, _, breathe, swirl, _, wave = [float(v) for v in self.p]
        lv, anim = self._lv, self.anim
        pulse = lv * (.14 * wave + .06 * (anim == "listening"))
        shock = 0.0
        if self.burst > 0:
            pr = 1 - self.burst
            shock = .22 * math.sin(pr * math.pi) * (1 - pr)
        bright = (.5 + .6 * glow) * self.fade
        S = R / 165.0
        persp = 900 * S
        M = rot_x(.32 + .02 * math.sin(t * .5)) @ rot_y(self.ang)
        sh, so = [], []
        self._outlines = []

        def add(dst, P, wt):
            sc = persp / (persp - P[:, 2])
            wt = np.broadcast_to(np.asarray(wt, np.float32), (len(P),)) * bright
            dst.append(((cx + P[:, 0] * sc).astype(np.float32), (cy + P[:, 1] * sc).astype(np.float32), wt))

        def depth(Pr, rad):
            zn = Pr[:, 2] / (rad + 1e-6)
            return zn, .28 + .72 * np.clip((zn + 1) / 2, 0, 1) ** 1.2

        n = (7000, 5200, 3600)[self.q]
        ph = self.sph_ph[:n]
        r = R * (1 + breathe * np.sin(t * 5 + ph) + pulse + shock)
        P = self.sph[:n] * r[:, None]
        if swirl > .01:
            P = P + swirl * .05 * R * np.stack([np.sin(t * 7 + ph), np.cos(t * 6 + ph * 1.7), np.sin(t * 5 + ph * .6)], 1)
        Pr = P @ M.T
        zn, wd = depth(Pr, r)
        wt = self.sph_w[:n] * 1.05 * wd * (.6 + .8 * np.clip(1 - np.abs(zn), 0, 1) ** 1.6)
        if swirl > .05:
            wt = wt * (1 + 1.4 * swirl * np.exp(-((self.sph[:n, 1] - math.sin(t * 1.7)) / .08) ** 2))
        add(sh, Pr, wt)
        Gr = (self.grid * R * (1 + pulse * .6 + shock * .8)) @ M.T
        add(sh, Gr, .55 * depth(Gr, R)[1])
        ri = R * .52 * (1 + pulse * 1.6 + shock)
        Ir = (self.sph[:1800] * ri) @ (rot_y(-self.ang * 2.2) @ M).T
        add(sh, Ir, 1.25 * depth(Ir, ri)[1])

        for k, (rr, tx, tz, sp) in enumerate(self.rings):
            m = rot_z(tz) @ rot_x(tx) @ rot_y(self.ang * sp * 3 + k)
            rad = R * rr * (1 + .03 * lv * (anim == "listening") + shock * .5 + .01 * math.sin(t * 2 + k))
            add(so, (self.ring_pts[self.ring_keep] * rad) @ m.T @ M.T, .42)
            if k != 1:
                full = (self.ring_pts @ m.T * rad) @ M.T
                sc = persp / (persp - full[:, 2])
                xs, ys = cx + full[::6, 0] * sc[::6], cy + full[::6, 1] * sc[::6]
                self._outlines.append(QPolygonF([QPointF(float(a), float(b)) for a, b in zip(xs, ys)]))
            for q in range(3):
                a = t * (.9 + .4 * k) * (1 if sp > 0 else -1) + q * 2.094 + k
                add(so, (np.array([[math.cos(a), 0, math.sin(a)]], np.float32) * rad) @ m.T @ M.T, 2.2)
        if self.q < 2:
            D = self.dust * R + np.stack([np.zeros(420), np.sin(t * .6 + self.dust_ph) * 8 * S, np.zeros(420)], 1)
            add(so, D.astype(np.float32) @ (rot_y(self.ang * .4) @ M).T,
                (.2 + .4 * np.abs(np.sin(t * 2 + self.dust_ph))).astype(np.float32))
        if self.burst > 0:
            pr = 1 - self.burst
            e = 1 - (1 - pr) ** 3
            add(so, self.rays * (R * .3 + e * R * 3.4 * self.ray_v)[:, None], 1.4 * (1 - pr))

        cat = lambda L, j: np.concatenate([a[j] for a in L]).astype(np.float32)
        RO = int(R * 1.5)
        SW = 2 * RO
        WH, HH = max(2, w // 2), max(2, h // 2)
        sig = (SW, WH, HH)
        if sig != self._bufsig:
            self._bufsig = sig
            self.sbuf = np.zeros(SW * SW, np.float32)
            self.hbuf = np.zeros(WH * HH, np.float32)
        ox, oy = int(cx) - RO, int(cy) - RO
        self.sbuf = self.sbuf * .3 + _splat(cat(sh, 0), cat(sh, 1), cat(sh, 2), ox, oy, SW, SW, 1.0)
        self.hbuf = self.hbuf * .62 + _splat(cat(so, 0), cat(so, 1), cat(so, 2), 0, 0, WH, HH, .5)
        lut = lambda buf: self.lut[np.minimum(np.nan_to_num(buf) * 255.75, 1023).astype(np.uint16)]
        self._sarr = np.ascontiguousarray(lut(self.sbuf).reshape(SW, SW, 3))
        self._harr = np.ascontiguousarray(lut(self.hbuf).reshape(HH, WH, 3))
        self._simg = QImage(self._sarr.data, SW, SW, SW * 3, QImage.Format.Format_RGB888)
        self._himg = QImage(self._harr.data, WH, HH, WH * 3, QImage.Format.Format_RGB888)
        self._origin = (ox, oy)
        # adaptive quality: shed detail if the frame is too slow, restore with headroom
        ft = time.perf_counter() - t0
        if ft > .026:
            self._slow += 1
            self._fast = 0
        elif ft < .012:
            self._fast += 1
            self._slow = max(0, self._slow - 1)
        if self._slow > 20 and self.q < 2:
            self.q += 1
            self._slow = 0
        elif self._fast > 200 and self.q > 0:
            self.q -= 1
            self._fast = 0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(3, 7, 13))
        if w < 200 or h < 200:
            p.end()
            return
        col = tuple(int(v) for v in self.tint)
        fade = self.fade

        def C(alpha: float, k: float = 1.0) -> QColor:
            return QColor(col[0], col[1], col[2], max(0, min(255, int(alpha * fade * k))))

        cx, cy = w / 2, h / 2
        R = min(w, h) * .19
        glow, _, _, _, scan, wave = [float(v) for v in self.p]
        lv = self._lv
        pulse = lv * (.14 * wave + .06 * (self.anim == "listening"))
        shock = 0.0
        if self.burst > 0:
            pr = 1 - self.burst
            shock = .22 * math.sin(pr * math.pi) * (1 - pr)

        # particle layers (additive, like the Jarvis bloom)
        self._render(cx, cy, R, w, h)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.drawImage(QRectF(0, 0, w, h), self._himg)
        p.drawImage(self._origin[0], self._origin[1], self._simg)
        gr = R * (1.1 + .5 * glow + .6 * pulse + shock * 1.5)
        g1 = QRadialGradient(cx, cy, gr)
        g1.setColorAt(0, C(70 + 90 * glow))
        g1.setColorAt(.5, C(22))
        g1.setColorAt(1, C(0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g1)
        p.drawEllipse(QPointF(cx, cy), gr, gr)
        cr = R * (.3 + .3 * glow + .7 * pulse + shock * 1.5)
        g2 = QRadialGradient(cx, cy, cr)
        hot = QColor(min(255, col[0] + 120), min(255, col[1] + 120), min(255, col[2] + 120), int((80 + 90 * glow) * fade))
        g2.setColorAt(0, hot)
        g2.setColorAt(1, C(0))
        p.setBrush(g2)
        p.drawEllipse(QPointF(cx, cy), cr, cr)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # glass bubble rim + ring outlines
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rr, kk in ((R * (1.02 + pulse), 56), (R * .52 * (1 + pulse * 1.6), 30)):
            p.setPen(QPen(C(kk), 1))
            p.drawEllipse(QPointF(cx, cy), rr, rr)
        p.setPen(QPen(C(75 + 65 * glow), 1))
        for poly in self._outlines:
            p.drawPolyline(poly)

        # HUD: tick ring, arcs, cross marks, waveform, spectrum
        RT = R * 2.06
        p.setPen(QPen(C(56), 1))
        p.drawEllipse(QPointF(cx, cy), RT, RT)
        for i in range(120):
            ai = i * math.tau / 120 + self.ang * .1
            ln = R * (.073 if i % 10 == 0 else .03)
            c, s = math.cos(ai), math.sin(ai)
            p.setPen(QPen(C(115 if i % 10 == 0 else 50), 1))
            p.drawLine(QPointF(cx + RT * c, cy + RT * s), QPointF(cx + (RT + ln) * c, cy + (RT + ln) * s))
        spin = 3.0 if self.anim == "thinking" else 1.0
        for rad, sp, ln in ((1.82, 1.9, 1.3), (1.93, -1.4, 1.0), (1.98, 2.6, .7)):
            pen = QPen(C(50 + 190 * scan), 1.6)
            rr = R * rad
            start = int(math.degrees(self.t * sp * spin) * 16)
            p.setPen(pen)
            p.drawArc(QRectF(cx - rr, cy - rr, rr * 2, rr * 2), start, int(math.degrees(ln) * 16))
        outer = R * 2.2
        p.setPen(QPen(C(75), 1))
        p.drawArc(QRectF(cx - outer, cy - outer, outer * 2, outer * 2), int(math.degrees(self.t * .15) * 16), int(math.degrees(1.2) * 16))
        p.drawArc(QRectF(cx - outer, cy - outer, outer * 2, outer * 2), int(math.degrees(self.t * .15 + 3.14) * 16), int(math.degrees(.9) * 16))
        for sx in (-1, 1):
            p.setPen(QPen(C(90), 1))
            p.drawLine(QPointF(cx + sx * R * 2.3, cy), QPointF(cx + sx * R * 2.42, cy))
            p.drawLine(QPointF(cx, cy + sx * R * 2.3), QPointF(cx, cy + sx * R * 2.42))

        # waveform ring (reacts to voice)
        a_ = np.linspace(0, 2 * np.pi, 220, endpoint=False)
        d = (np.sin(a_ * 6 + self.t * 5) + .6 * np.sin(a_ * 13 - self.t * 7) + .4 * np.sin(a_ * 23 + self.t * 3)) / 2
        rr = R * 1.73 + (R * .02 + R * .2 * lv * wave + R * .02 * wave) * d
        p.setPen(QPen(C(90 + 150 * wave * (.5 + lv)), 1))
        p.drawPolygon(QPolygonF([QPointF(float(cx + r_ * math.cos(a)), float(cy + r_ * math.sin(a))) for r_, a in zip(rr, a_)]))

        # radial audio spectrum (real mic / speaker level)
        n = 72
        base, amp = R * 2.25, R * .16
        drive = lv * (.45 + .55 * wave + (.25 if self.anim == "listening" else 0.0))
        ang = np.arange(n) * (2 * np.pi / n) - np.pi / 2 + self.ang * .05
        wob = np.abs(np.sin(self.t * 5.0 + self._spec_ph) * np.sin(self.t * 2.3 + self._spec_ph * 1.7))
        idle = 1.5 + 1.2 * (.5 + .5 * np.sin(self.t * 1.4 + ang * 3)) * (.35 + .65 * glow)
        self._spec += ((amp * min(1.2, drive) * (.2 + .8 * wob) + idle).astype(np.float32) - self._spec) * .3
        cs, sn = np.cos(ang), np.sin(ang)
        for i in range(n):
            hh = float(self._spec[i])
            p.setPen(QPen(C(56 + 150 * min(1.0, hh / amp)), 2))
            p.drawLine(QPointF(cx + base * cs[i], cy + base * sn[i]), QPointF(cx + (base + hh) * cs[i], cy + (base + hh) * sn[i]))

        # command shockwave
        if self.burst > 0:
            pr = 1 - self.burst
            p.setBrush(Qt.BrushStyle.NoBrush)
            for dl in (0, .12):
                q = max(0, pr - dl)
                if q > 0:
                    p.setPen(QPen(C(220 * (1 - q) ** 1.5), 1))
                    rad = R * .36 + q * R * 2.6
                    p.drawEllipse(QPointF(cx, cy), rad, rad)

        # state caption
        f2 = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        p.setFont(f2)
        p.setPen(C(235))
        p.drawText(QRectF(cx - 200, 12, 400, 24), Qt.AlignmentFlag.AlignCenter, "●  " + STATE_NAMES.get(self.state, self.state))
        p.end()


# ──────────────────────────────────────────────────────────────────────────────
class MarkUI(QMainWindow):
    """Single-page holographic MARK UI; keeps every runtime hook main.py / plugins expect."""
    _bridge = Signal(object)

    def __init__(self, face_path: str = "", size=None):
        self.app = QApplication.instance() or QApplication(sys.argv)
        super().__init__()
        self._face_path = face_path
        self._bridge.connect(self._dispatch_ui, Qt.ConnectionType.QueuedConnection)
        self.name = "MARK"
        self.assistant_name = self.name
        try:
            from core.secrets import get_secret
            self._ready = bool(get_secret("gemini"))  # credential gate; connection state is tracked separately
        except Exception:
            self._ready = False
        self._gemini_live_state = "CONFIGURED" if self._ready else "NOT CONFIGURED"
        self._gemini_text_state = "CONFIGURED"
        self._gemini_memory_state = "CONFIGURED"
        self._llm_provider = "sleep"
        self._llm_labels: dict[str, QLabel] = {}
        try:
            from core.llm import load_config
            llm_cfg = load_config()
            self._llm_provider = "sleep"
            gemini_cfg = llm_cfg.get("gemini", {})
            self._gemini_text_state = "CONFIGURED" if gemini_cfg.get("text_model") else "NOT CONFIGURED"
            self._gemini_memory_state = "CONFIGURED" if gemini_cfg.get("memory_model") else "NOT CONFIGURED"
        except Exception:
            self._gemini_text_state = self._gemini_memory_state = "UNAVAILABLE"
        self.muted = False
        self._file: str | None = None
        self._camera_monitor = None
        self._closing = False
        self.on_desktop_share_stop = None
        self._task_items: dict[int, Any] = {}
        self._state = "SLEEPING"
        self.on_screen_monitor_toggle = None
        self._screen_monitor_enabled = True
        self.on_gesture_action = None
        self.on_text_command = None
        self.on_interrupt = None
        self.on_call_action = None
        self.plugin_manager = None
        self.announce_vision = None
        self._win = self
        self.root = self
        self.vision_dashboard = None
        self._unread = 0
        self.setWindowTitle(f"{self.name}  |  MARK Runtime")
        self.resize(*(size or (1420, 900)))
        self.setMinimumSize(980, 660)
        self.setAcceptDrops(True)
        self.setStyleSheet(_STYLE)

        cfg = self._read_cfg()
        prefs = cfg.get("mark_prefs", {})
        try:
            self.setWindowOpacity(max(0.3, min(1.0, float(prefs.get("opacity", 1.0)))))
        except (TypeError, ValueError):
            self.setWindowOpacity(1.0)
        self.auto_panels = bool(prefs.get("auto", True))
        self.layout_locked = bool(prefs.get("lock", False))
        self._build_ui(cfg)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._persist)
        self._opacity_shortcut = QShortcut(QKeySequence("F8"), self)
        self._opacity_shortcut.activated.connect(self._adjust_opacity)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(1500)
        TELEMETRY.start()
        self._refresh_status()
        self._paint_state("SLEEPING")

    async def confirm_action(self, tool, arguments):
        import asyncio
        loop = asyncio.get_running_loop()
        result = loop.create_future()
        def resolve(approved):
            if not result.done():
                result.set_result(approved)
        def display():
            if result.done() or self._closing:
                loop.call_soon_threadsafe(resolve, False)
                return
            box = QMessageBox(self)
            box.setWindowTitle("Approve MARK action")
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(f"Allow {tool}?")
            box.setInformativeText(json.dumps({k:v for k,v in arguments.items() if k != "confirmed"},
                                              ensure_ascii=False, indent=2))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            box.finished.connect(lambda answer: loop.call_soon_threadsafe(resolve, answer == QMessageBox.StandardButton.Yes))
            result.add_done_callback(lambda _: self._post(box.close))
            box.open()
        self._post(display)
        try:
            return await asyncio.wait_for(result, timeout=120)
        except asyncio.TimeoutError:
            return False

    def set_desktop_sharing(self, active: bool) -> None:
        self._post(lambda: self.desktop_share_stop.setVisible(active))

    def set_gemini_live_state(self, state: str) -> None:
        """Update the UI's Live indicator from actual session lifecycle events."""
        self._gemini_live_state = state if state in {"NOT CONFIGURED", "CONFIGURED", "TESTING", "READY", "IDLE", "DEGRADED", "UNAVAILABLE"} else "UNAVAILABLE"
        self._post(lambda: (self._paint_llm_provider(self._llm_provider), self._refresh_status()))

    # ---- config ----------------------------------------------------------------
    @staticmethod
    def _read_cfg() -> dict:
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _save_cfg(**values: Any) -> None:
        try:
            cfg = MarkUI._read_cfg()
            cfg.update(values)
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"[UI] config save failed: {exc}")

    def _persist(self) -> None:
        self._save_cfg(
            mark_layout={k: [round(v, 4) for v in f] for k, f in self.stage.fracs.items()},
            mark_panels={k: p.isVisible() for k, p in self.stage.panels.items()},
            mark_prefs={"auto": self.auto_panels, "lock": self.layout_locked, "opacity": self.windowOpacity()},
        )

    def _schedule_save(self, *_):
        self._save_timer.start(500)

    # ---- construction ------------------------------------------------------------
    def _icon_button(self, glyph: str, tip: str, cb) -> QPushButton:
        b = QPushButton(glyph)
        b.setObjectName("icon")
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.clicked.connect(cb)
        return b

    def _build_ui(self, cfg: dict) -> None:
        central = QWidget(self)
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 10, 16, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        box = QVBoxLayout()
        box.setSpacing(0)
        title = QLabel(self.name.upper())
        title.setObjectName("brand")
        sub = QLabel("HOLOGRAPHIC ASSISTANT  /  LIVE COMMAND CENTER")
        sub.setObjectName("subtitle")
        box.addWidget(title)
        box.addWidget(sub)
        header.addLayout(box)
        header.addStretch(1)
        self._build_llm_status(header)
        header.addWidget(self._icon_button("▧", "Attach a file", self._choose_file))
        self.state_label = QLabel("OFFLINE")
        self.state_label.setObjectName("state")
        header.addWidget(self.state_label)
        self.voice_level = QProgressBar()
        self.voice_level.setRange(0, 100)
        self.voice_level.setTextVisible(False)
        self.voice_level.setFixedWidth(100)
        self.voice_level.setToolTip("Live microphone / speaker level")
        header.addWidget(self.voice_level)
        self.mic_button = self._icon_button("\u25d6", "Microphone on", self._toggle_mic)
        header.addWidget(self.mic_button)
        header.addWidget(self._icon_button("⌘", "API credentials", self._configure_key))
        stop = self._icon_button("■", "Stop current task", self._interrupt)
        stop.setObjectName("danger")
        stop.setStyleSheet("QPushButton{color:#FCA5A5;border-color:#7F1D1D;}")
        header.addWidget(stop)
        self.desktop_share_stop = QPushButton("Stop sharing")
        self.desktop_share_stop.setObjectName("danger")
        self.desktop_share_stop.setToolTip("Your desktop is being shared via LiveKit")
        self.desktop_share_stop.clicked.connect(
            lambda: self.on_desktop_share_stop and self.on_desktop_share_stop())
        self.desktop_share_stop.hide()
        header.addWidget(self.desktop_share_stop)
        self.menu_button = self._icon_button("⋮", "Menu", self._open_menu)
        header.addWidget(self.menu_button)
        root.addLayout(header)

        gesture_row = QHBoxLayout()
        self.gesture_status_label = QLabel("GESTURE OFF")
        self.gesture_status_label.setToolTip("Gesture mouse status")
        gesture_row.addWidget(self.gesture_status_label)
        self.gesture_enable_button = QPushButton("Enable")
        self.gesture_disable_button = QPushButton("Disable")
        self.gesture_enable_button.clicked.connect(lambda: self._gesture_action("enable"))
        self.gesture_disable_button.clicked.connect(lambda: self._gesture_action("disable"))
        gesture_row.addWidget(self.gesture_enable_button)
        gesture_row.addWidget(self.gesture_disable_button)
        gesture_row.addStretch(1)
        root.addLayout(gesture_row)

        self.stage = Stage(self.name)
        root.addWidget(self.stage, 1)
        self.setCentralWidget(central)

        # --- panel bodies
        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        self.chat.document().setMaximumBlockCount(2000)
        self.chat.setPlaceholderText("MARK messages and task updates will appear here.")

        self.command = QLineEdit()
        self.command.setPlaceholderText("Tell MARK what you need…  (Enter to send)")
        self.command.returnPressed.connect(self._submit_command)
        send = self._icon_button("➤", "Send command", self._submit_command)
        send.setObjectName("primary")
        send.setStyleSheet("QPushButton{background:#0B7087;border:1px solid #22D3EE;color:#F0FDFA;}")
        cmd_body = QWidget()
        cl = QHBoxLayout(cmd_body)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.command, 1)
        cl.addWidget(send)
        from core.llm import load_config
        self.local_voice = None
        if load_config()["profile"] in {"offline", "low_resource"}:
            from actions.local_voice import LocalVoice
            self.local_voice = LocalVoice()
            dictate = QPushButton("Hold to dictate")
            dictate.setToolTip("Release to transcribe locally. Review the text and press Enter to send.")
            dictate.pressed.connect(lambda: self.local_voice.press(self))
            dictate.released.connect(self.local_voice.release)
            cl.addWidget(dictate)

        self.tasks = QPlainTextEdit()
        self.tasks.setReadOnly(True)
        self.tasks.setMaximumBlockCount(500)

        verify_body = QWidget()
        vl = QVBoxLayout(verify_body)
        vl.setContentsMargins(0, 0, 0, 0)
        self.verify_image = QLabel("No screenshot evidence attached")
        self.verify_image.setObjectName("muted")
        self.verify_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.verify_image.setMinimumHeight(40)
        self.verify = QPlainTextEdit()
        self.verify.setReadOnly(True)
        self.verify.setMaximumBlockCount(500)
        vl.addWidget(self.verify_image, 1)
        vl.addWidget(self.verify, 2)

        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)

        sys_body = QWidget()
        sl = QVBoxLayout(sys_body)
        sl.setContentsMargins(0, 0, 0, 0)
        self.cpu_metric = QLabel("CPU  —")
        self.ram_metric = QLabel("MEMORY  —")
        self.runtime_metric = QLabel("TOOLS  —  ·  PLUGINS  —")
        for m in (self.cpu_metric, self.ram_metric, self.runtime_metric):
            m.setObjectName("metric")
            sl.addWidget(m)
        sl.addStretch(1)

        self.vision_holder = QWidget()
        vh = QVBoxLayout(self.vision_holder)
        vh.setContentsMargins(0, 0, 0, 0)

        bodies = {"chat": self.chat, "command": cmd_body, "info": self.info, "tasks": self.tasks,
                  "verify": verify_body, "system": sys_body, "vision": self.vision_holder}

        saved_layout = cfg.get("mark_layout", {})
        saved_vis = cfg.get("mark_panels", {})
        self._checks: dict[str, QCheckBox] = {}
        for key, (ttl, default_vis, frac) in PANELS.items():
            panel = FloatPanel(key, ttl, bodies[key], self.stage)
            panel.locked = self.layout_locked
            panel.geometry_changed.connect(self._on_geometry)
            panel.closed.connect(lambda k: self.set_panel_visible(k, False))
            self.stage.panels[key] = panel
            f = saved_layout.get(key)
            self.stage.fracs[key] = list(f) if isinstance(f, list) and len(f) == 4 else list(frac)
            if key in CORE_PANELS and key not in saved_vis:
                visible = True
            else:
                visible = bool(saved_vis.get(key, default_vis))
            panel.setVisible(visible)
        self.stage.apply_fracs()
        self.stage.panels["command"].raise_()
        self.statusBar().showMessage("AI starting…  ·  Camera initializing")
        if self.stage.panels["vision"].isVisible():
            self._ensure_vision()

    def _build_llm_status(self, header: QHBoxLayout) -> None:
        frame = QFrame()
        frame.setObjectName("llmStatus")
        frame.setToolTip("Text requests and Gemini Live connection status")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(0)
        for provider in ("groq", "gemini"):
            label = QLabel(provider.upper())
            label.setObjectName("llmName")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._llm_labels[provider] = label
            layout.addWidget(label)
        self.gemini_live_label = QLabel("GEMINI LIVE")
        self.gemini_live_label.setObjectName("llmName")
        self.gemini_live_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.gemini_live_label)
        header.addWidget(frame)
        self._paint_llm_provider(self._llm_provider)

    def set_llm_provider(self, provider: str) -> None:
        """Mark the provider currently serving the active LLM request."""
        provider = str(provider).lower().strip()
        if provider not in self._llm_labels and provider != "sleep":
            return
        previous = self._llm_provider
        self._llm_provider = provider
        self._post(lambda: self._paint_llm_provider(provider))
        if provider != "sleep" and provider != previous:
            self.write_log(f"SYS: {provider.capitalize()} kaam kar raha hai.")

    def _paint_llm_provider(self, provider: str) -> None:
        for name, label in self._llm_labels.items():
            color = "#4ADE80" if name == provider else "#FCA5A5"
            label.setText(f"{name.upper()} TEXT - {'WORKING' if name == provider else 'IDLE'}")
            label.setStyleSheet(f"color:{color};")
        live_state = self._gemini_live_state
        live_text = {"READY": "CONNECTED", "TESTING": "CONNECTING", "IDLE": "IDLE",
                     "DEGRADED": "RECONNECTING", "UNAVAILABLE": "UNAVAILABLE",
                     "NOT CONFIGURED": "NOT CONFIGURED", "CONFIGURED": "OFFLINE"}.get(live_state, "OFFLINE")
        self.gemini_live_label.setText(f"GEMINI LIVE - {live_text}")
        self.gemini_live_label.setStyleSheet(f"color:{'#4ADE80' if live_state == 'READY' else '#FCA5A5'};")

    # ---- panel switching / menu --------------------------------------------------
    def _on_geometry(self, key: str) -> None:
        self.stage.remember(key)
        self._schedule_save()

    def set_panel_visible(self, key: str, on: bool, raise_: bool = True) -> None:
        panel = self.stage.panels.get(key)
        if panel is None:
            return
        panel.setVisible(on)
        if on:
            self.stage.apply_fracs()
            if raise_:
                panel.raise_()
            if key == "vision":
                self._ensure_vision()
        chk = self._checks.get(key)
        if chk is not None and chk.isChecked() != on:
            chk.blockSignals(True)
            chk.setChecked(on)
            chk.blockSignals(False)
        self._schedule_save()

    def _auto(self, key: str) -> None:
        if self.auto_panels and not self.stage.panels[key].isVisible():
            self.set_panel_visible(key, True, raise_=False)

    def _toggle_widget(self, text: str, checked: bool, cb) -> QWidgetAction:
        chk = QCheckBox(text)
        chk.setChecked(checked)
        chk.toggled.connect(cb)
        act = QWidgetAction(self)
        act.setDefaultWidget(chk)
        act._chk = chk
        return act

    def _clear_history(self):
        if QMessageBox.question(self, "Clear history", "Delete the saved voice and typed conversation turns?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            from memory.db import clear_conversation_turns
            clear_conversation_turns()
            self.write_log("SYS: Saved conversation turns cleared.")

    def _open_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Plugins", self._open_plugins)
        menu.addAction("Window opacity (F8)", self._adjust_opacity)
        menu.addSeparator()
        head = QLabel("  PANELS  —  switch on only what you need")
        head.setObjectName("section")
        ha = QWidgetAction(menu)
        ha.setDefaultWidget(head)
        menu.addAction(ha)
        self._checks = {}
        for key, (ttl, _, _) in PANELS.items():
            act = self._toggle_widget(ttl.split("  ", 1)[-1].title(), self.stage.panels[key].isVisible(),
                                      lambda on, k=key: self.set_panel_visible(k, on))
            self._checks[key] = act._chk
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(self._toggle_widget("Local screen monitoring", self._screen_monitor_enabled, self._set_screen_monitor))
        menu.addAction(self._toggle_widget("Auto-show panels when work happens", self.auto_panels, self._set_auto))
        menu.addAction(self._toggle_widget("Lock layout (no drag / resize)", self.layout_locked, self._set_lock))
        menu.addSeparator()
        menu.addAction("Focus mode  (chat + command only)", self._focus_mode)
        menu.addAction("Reset layout", self._reset_layout)
        menu.addAction("Clear saved conversation history", self._clear_history)
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomRight()) - self._menu_offset(menu))

    def _adjust_opacity(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Window opacity")
        layout = QVBoxLayout(dialog)
        label = QLabel()
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(30, 100)
        slider.setValue(round(self.windowOpacity() * 100))
        def update(value):
            label.setText(f"Opacity: {value}%")
            self.setWindowOpacity(value / 100)
            self._schedule_save()
        slider.valueChanged.connect(update)
        label.setText(f"Opacity: {slider.value()}%")
        layout.addWidget(label)
        layout.addWidget(slider)
        dialog.exec()

    def _open_plugins(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("MARK Plugins")
        layout = QVBoxLayout(dialog)
        title = QLabel("PLUGINS")
        title.setObjectName("brand")
        search = QLineEdit()
        search.setPlaceholderText("Search installed plugins")
        details = QPlainTextEdit()
        details.setReadOnly(True)
        def refresh():
            manager = self.plugin_manager
            if manager is None:
                details.setPlainText("Plugin manager is not available yet.")
                return
            needle = search.text().strip().lower()
            rows = manager.describe().splitlines()
            details.setPlainText("\n".join(row for row in rows if needle in row.lower()) or "No matching plugins.")
        search.textChanged.connect(refresh)
        refresh_button = QPushButton("Refresh status")
        refresh_button.clicked.connect(refresh)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.close)
        layout.addWidget(title)
        layout.addWidget(search)
        layout.addWidget(details, 1)
        layout.addWidget(refresh_button)
        layout.addWidget(close_button)
        refresh()
        dialog.showMaximized()
        dialog.exec()

    @staticmethod
    def _menu_offset(menu: QMenu):
        from PySide6.QtCore import QPoint
        return QPoint(menu.sizeHint().width(), 0)

    def _set_screen_monitor(self, on: bool) -> None:
        self._screen_monitor_enabled = on
        if self.on_screen_monitor_toggle:
            self.on_screen_monitor_toggle(on)
        self.write_log("SYS: Local screen monitoring " + ("ON" if on else "OFF"))

    def _set_auto(self, on: bool) -> None:
        self.auto_panels = on
        self._schedule_save()

    def _set_lock(self, on: bool) -> None:
        self.layout_locked = on
        for p in self.stage.panels.values():
            p.locked = on
        self._schedule_save()

    def _focus_mode(self) -> None:
        for key in PANELS:
            self.set_panel_visible(key, key in CORE_PANELS)

    def _reset_layout(self) -> None:
        for key, (_, vis, frac) in PANELS.items():
            self.stage.fracs[key] = list(frac)
            self.set_panel_visible(key, vis)
        self.stage.apply_fracs()
        self._schedule_save()

    def _ensure_vision(self) -> None:
        if self.vision_dashboard is not None:
            return
        try:
            from vision_department.dashboard import VisionDashboard
            self.vision_dashboard = VisionDashboard(self)
            self.vision_holder.layout().addWidget(self.vision_dashboard)
        except Exception as exc:
            lab = QLabel(f"Vision monitor unavailable: {type(exc).__name__}: {exc}")
            lab.setObjectName("muted")
            lab.setWordWrap(True)
            self.vision_holder.layout().addWidget(lab)
            self.vision_dashboard = lab

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.stage.apply_fracs()

    def mainloop(self) -> int:
        self.show()
        return self.app.exec()

    # ---- thread bridge -----------------------------------------------------------
    def _dispatch_ui(self, callback) -> None:
        try:
            callback()
        except RuntimeError:
            pass

    def _post(self, callback) -> None:
        if self._closing:
            return
        if QThread.currentThread() == self.thread():
            self._dispatch_ui(callback)
        else:
            self._bridge.emit(callback)

    @property
    def current_file(self) -> str | None:
        return self._file

    # ---- state ---------------------------------------------------------------------
    def set_state(self, state: str) -> None:
        state = str(state).upper()
        if state == "LISTENING" and self.muted:
            state = "MUTED"
        self._state = state
        self._post(lambda: self._paint_state(state))
        try:
            TELEMETRY.set_assistant(state=state, mic_muted=self.muted, session="offline" if state == "SLEEPING" else "live")
        except Exception:
            pass

    def _paint_state(self, state: str) -> None:
        self.state_label.setText(STATE_NAMES.get(state, state))
        self.stage.state = state
        c = STATE_RGB.get(state, (103, 232, 249))
        hexc = "#%02x%02x%02x" % c
        self.state_label.setStyleSheet(f"color:{hexc}; border-color:{hexc};")

    def set_level(self, value: float) -> None:
        try:
            level = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return

        def apply():
            self.voice_level.setValue(round(level * 100))
            self.stage.set_level(level)
        self._post(apply)

    # ---- chat / tasks ----------------------------------------------------------------
    def write_log(self, message: str) -> None:
        text = str(message)
        self._post(lambda: self._append_chat(text))

    def _append_chat(self, text: str) -> None:
        ts = time.strftime("%H:%M")
        low = text.lower()
        if low.startswith("you:"):
            color, label, body = "#67E8F9", "YOU", text[4:].strip()
        elif low.startswith(("mark:", "jarvis:", "ultron:")):
            color, label, body = "#FDBA74", self.name, text.split(":", 1)[1].strip()
        elif text.startswith("ERR"):
            color, label, body = "#FCA5A5", "ERR", text
        elif text.startswith(("NOTIF", "BRAIN", "[brain")):
            color, label, body = "#A5B4FC", "SYS", text
        else:
            color, label, body = "#94A3B8", "SYS", text
        self.chat.append(f'<span style="color:{color};font-weight:700">{html.escape(label)}</span> '
                         f'<span style="color:#64748B">{ts}</span><br>{html.escape(body).replace(chr(10), "<br>")}')
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())
        if text.startswith(("ERR", "NOTIF", "BRAIN", "[brain")):
            self.tasks.appendPlainText(text)

    def task_start(self, name: str, args: dict | None = None) -> int:
        from uuid import uuid4
        tid = uuid4().int
        short = ", ".join(f"{k}={str(v)[:36]}" for k, v in (args or {}).items() if k not in {"code", "content", "data"})[:130]
        self._post(lambda: self._task_start_ui(tid, name, short))
        return tid

    def _task_start_ui(self, tid: int, name: str, detail: str) -> None:
        try:
            from core.agents import for_tool
            agent_name = for_tool(name).name
        except Exception:
            agent_name = "Executor"
        line = f"RUNNING  {name}  ·  {agent_name}" + (f"  ·  {detail}" if detail else "")
        self._task_items[tid] = line
        self.tasks.appendPlainText(line)
        self._auto("tasks")

    def task_end(self, tid: int, result: str) -> None:
        lines = [ln.strip() for ln in str(result).splitlines() if ln.strip()]
        first = lines[0] if lines else "Completed"
        status = next((s for s in ("DONE", "UNVERIFIED", "FAILED") if first.startswith(f"[{s}]")), "INFO")
        detail = first[:250]
        self._post(lambda: self.tasks.appendPlainText(f"{status}  {self._task_items.pop(tid, 'Task')}  —  {detail}"))
        try:
            log = BASE_DIR / "memory" / "work_log.jsonl"
            with log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "task": tid, "status": status}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def show_verification(self, name: str, status: str, detail: str = "", evidence: str = "", image: bytes | None = None) -> None:
        entry = f"{str(status).upper()}  {name}\n{detail}\n{evidence}".strip()

        def update():
            self.verify.appendPlainText(entry)
            self._auto("verify")
            if image:
                pm = QPixmap()
                if pm.loadFromData(image):
                    self.verify_image.setText("")
                    self.verify_image.setPixmap(pm.scaled(self.verify_image.width() or 400, 160, Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation))
        self._post(update)
        try:
            TELEMETRY.add_verification({"tool": str(name)[:80], "status": str(status).upper()[:12], "detail": str(detail)[:400]})
        except Exception:
            pass

    def show_content(self, title: str, text: str) -> None:
        def update():
            self.info.setPlainText(f"{title}\n\n{text}")
            self._auto("info")
        self._post(update)

    def show_work(self, title: str, lines: list[str] | None = None, seconds: float = 8.0) -> None:
        del seconds
        self.write_log(f"WORK: {title} — {'; '.join(lines or [])}")

    def work_log(self, line: str) -> None:
        self.write_log(f"WORK: {line}")

    def show_work_image(self, image: bytes | str, caption: str = "", seconds: float = 12.0) -> None:
        del seconds
        def show():
            pm = QPixmap()
            if isinstance(image, (bytes, bytearray)):
                pm.loadFromData(bytes(image))
            else:
                pm.load(str(image))
            if pm.isNull():
                self.info.setPlainText(f"{caption}\n\nCould not open generated visualization.")
                self._auto("info")
                return
            dlg = QDialog(self)
            dlg.setWindowTitle(caption or "MARK visualization")
            dlg.resize(900, 650)
            lay = QVBoxLayout(dlg)
            lab = QLabel()
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setPixmap(pm.scaled(860, 580, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            lay.addWidget(lab)
            dlg.show()
        self._post(show)

    def show_screen_preview(self, img_bytes: bytes, seconds: float = 6.0) -> None:
        del seconds
        self.show_camera_frame(img_bytes)

    def add_notification(self, note: dict) -> None:
        text = json.dumps(note, ensure_ascii=False, default=str)

        def update():
            self.info.appendPlainText(text)
            self._auto("info")
        self._post(update)

    def set_state_info(self, **values) -> None:
        def update_metrics():
            tools = values.get("tools_total", "—")
            plugins = values.get("plugins_ok", "—")
            failed = values.get("plugins_bad", 0)
            session = str(values.get("session", "offline")).upper()
            self.runtime_metric.setText(f"TOOLS  {tools}   ·   PLUGINS  {plugins}   ·   {session}")
            if failed:
                self.runtime_metric.setToolTip(f"{failed} plugin(s) failed to load")
        self._post(update_metrics)
        try:
            TELEMETRY.set_assistant(**values)
        except Exception:
            pass

    def start_speaking(self) -> None:
        self.set_state("SPEAKING")

    def stop_speaking(self) -> None:
        if not self.muted:
            self.set_state("LISTENING")

    # ---- camera --------------------------------------------------------------------------
    def show_camera_frame(self, img_bytes: bytes | None) -> None:
        # Camera capture remains available without a live preview panel.
        del img_bytes

    def show_camera_status(self, status: str) -> None:
        if any(word in str(status).upper() for word in ("ERROR", "FAILED")):
            self.write_log("CAMERA: " + str(status))

    def _gesture_action(self, action: str) -> None:
        if self.on_gesture_action:
            self.on_gesture_action(action)
        else:
            self.write_log("Gesture control is still starting.")

    def show_gesture_status(self, status: str) -> None:
        self._post(lambda: self.gesture_status_label.setText("GESTURE " + str(status)))

    def start_camera_stream(self) -> None:
        if self._closing or self._camera_monitor is not None:
            return
        try:
            from actions.camera_monitor import CameraMonitor
            from actions.whatsapp_alert import send_alert

            def on_frame(frame, status):
                self.show_camera_frame(frame)
                text = "ERROR: " + status.error if not status.connected and status.error else (
                    "UNKNOWN PERSON" if status.unknown else (", ".join(status.names) if status.names else "NO FACE"))
                self.show_camera_status(f"{text} | {status.pending_seconds:.1f}s")

            def on_alert(snapshot):
                try:
                    send_alert(snapshot)
                except Exception as exc:
                    self.show_camera_status(f"ALERT FAILED: {type(exc).__name__}: {exc}")

            self._camera_monitor = CameraMonitor(on_frame=on_frame, on_alert=on_alert)
            self._camera_monitor.start()
        except Exception as exc:
            self._camera_monitor = None
            self.show_camera_status(f"CAMERA ERROR: {type(exc).__name__}: {exc}")

    def stop_camera_stream(self) -> None:
        monitor = self._camera_monitor
        self._camera_monitor = None
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                pass


    def _toggle_camera(self) -> None:
        if self._camera_monitor is None:
            self.start_camera_stream()
        else:
            self.stop_camera_stream()

    # ---- credentials -----------------------------------------------------------------------
    def prompt_reconfig(self) -> None:
        self._ready = False
        self._post(self._prompt_reconfig_ui)

    def _prompt_reconfig_ui(self) -> None:
        key, accepted = QInputDialog.getText(self, "Voice API key", "Gemini key (voice only; typed commands use the configured AI provider):", QLineEdit.EchoMode.Password)
        if accepted and key.strip():
            try:
                from core.secrets import set_secret
                set_secret("gemini", key)
            except Exception as exc:
                QMessageBox.critical(self, "Credential store unavailable", str(exc))
        self._ready = True

    def _configure_key(self) -> None:
        key, accepted = QInputDialog.getText(self, "API credentials", "Gemini API key:", QLineEdit.EchoMode.Password)
        if not accepted:
            return
        try:
            from core.secrets import set_secret
            set_secret("gemini", key)
            self._ready = bool(key.strip())
            QMessageBox.information(self, "Saved", "Credential saved.")
        except Exception as exc:
            QMessageBox.critical(self, "Credential store unavailable", str(exc))

    def wait_for_api_key(self) -> None:
        while not self._ready:
            time.sleep(0.1)

    # ---- status / input ---------------------------------------------------------------------
    def _refresh_status(self) -> None:
        try:
            snap = TELEMETRY.snapshot()
            if not snap.get("ok", False):
                reason = str(snap.get("why", "telemetry unavailable"))
                self.cpu_metric.setText("CPU  —")
                self.ram_metric.setText("MEMORY  —")
                self.cpu_metric.setToolTip(reason)
                self.ram_metric.setToolTip(reason)
                self.statusBar().showMessage(f"System telemetry unavailable: {reason}")
                return
            cpu = snap.get("cpu_percent", snap.get("cpu", "—"))
            ram = snap.get("memory_percent", snap.get("ram_percent", "—"))
            self.cpu_metric.setText(f"CPU  {cpu}%")
            self.ram_metric.setText(f"MEMORY  {ram}%")
            self.cpu_metric.setToolTip("Current CPU usage")
            self.ram_metric.setToolTip("Current memory usage")
            self.statusBar().showMessage(f"CPU {cpu}%   ·   RAM {ram}%   ·   Gemini Text {self._gemini_text_state} · Live {self._gemini_live_state} · Memory {self._gemini_memory_state}   ·   {self._state}")
        except (AttributeError, TypeError, ValueError) as exc:
            self.cpu_metric.setText("CPU  —")
            self.ram_metric.setText("MEMORY  —")
            self.statusBar().showMessage(f"System status error: {type(exc).__name__}: {exc}")

    def _submit_command(self) -> None:
        text = self.command.text().strip()
        if not text:
            return
        self.command.clear()
        self.stage.trigger()
        self.write_log(f"You: {text}")
        if self.on_text_command:
            self.on_text_command(text)

    def _interrupt(self) -> None:
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mic(self) -> None:
        self.muted = not self.muted
        self.mic_button.setText("◌" if self.muted else "◖")
        self.mic_button.setToolTip("Microphone off" if self.muted else "Microphone on")
        self.mic_button.setAccessibleName(self.mic_button.toolTip())
        if self._gemini_live_state == "READY":
            self.set_state("MUTED" if self.muted else "LISTENING")

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file")
        if path:
            self._file = path
            self.write_log(f"SYS: selected file {Path(path).name}")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._file = paths[0]
            self.write_log(f"SYS: selected file {Path(paths[0]).name}")
            event.acceptProposedAction()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            orb = getattr(self, "orb", None)
            if orb:
                if self.isMinimized():
                    orb.show()
                else:
                    orb.hide()

    def restore_from_orb(self) -> None:
        def restore():
            if not self._closing:
                self.showNormal()
                self.raise_()
                self.activateWindow()
        self._post(restore)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        if self.local_voice:
            self.local_voice.release()
        if getattr(self, "on_runtime_stop", None):
            self.on_runtime_stop()
        self._persist()
        TELEMETRY.stop()
        self.stop_camera_stream()
        dash = self.vision_dashboard
        if dash is not None and hasattr(dash, "shutdown"):
            try:
                dash.shutdown()
            except Exception:
                pass
        event.accept()


HoloUI = MarkUI
UltronUI = MarkUI


if __name__ == "__main__":
    window = MarkUI("assets/face.png")
    sys.exit(window.mainloop())
