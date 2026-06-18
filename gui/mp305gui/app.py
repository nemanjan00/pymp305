"""MP305 desktop dashboard — PyQt6 + pyqtgraph, Dracula-themed.

Design:
  * pointer-ONLY (trackball, no keyboard): tap a channel card → on-screen keypad (digits +
    units); one-click V+I presets (right-click to save). No scroll-to-change — a stray
    trackball scroll must never alter the output.
  * each quantity is ONE instrument card: big measured value + a tappable SET sub-row, so
    "set" and "measured" live together (no split, no duplicate setpoint readout).
  * the limiting channel highlights (border + tag) and a CV|CC indicator shows the mode.
  * output is one big green/red card-button. Disciplined layout; chart absorbs slack.
"""
from __future__ import annotations

import time
from collections import deque

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF, QTimer, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QPolygonF
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QGridLayout, QDialog, QPlainTextEdit, QSizePolicy,
)

from .theme import C, STYLESHEET
from .worker import DeviceWorker
from .backend import make_backend, SimBackend

WINDOW = 60.0
MONO = ["JetBrains Mono", "IBM Plex Mono", "DejaVu Sans Mono", "Consolas", "monospace"]


def mono(size, bold=True):
    f = QFont(); f.setFamilies(MONO); f.setPointSize(size)
    f.setBold(bold); f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def _lab(text, cls):
    w = QLabel(text); w.setProperty("class", cls); return w


def _rgb(hexcol):
    c = QColor(hexcol); return f"{c.red()},{c.green()},{c.blue()}"


# ---------------------------------------------------------------- widgets
class OutputButton(QPushButton):
    """The whole OUTPUT card is one button — green ON, red OFF. Huge trackball target."""
    def __init__(self):
        super().__init__()
        self.setCheckable(True); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(92)
        self.toggled.connect(self._restyle); self._restyle(False)

    def _restyle(self, on):
        col = C["on"] if on else C["danger"]; rgb = _rgb(col)
        self.setText("⏻   OUTPUT ON" if on else "⏻   OUTPUT OFF")
        self.setStyleSheet(
            f"QPushButton{{background:rgba({rgb},0.15);color:{col};border:2px solid {col};"
            f"border-radius:14px;font-size:23px;font-weight:800;letter-spacing:2px;}}"
            f"QPushButton:hover{{background:rgba({rgb},0.26);}}"
            f"QPushButton:disabled{{background:{C['card']};color:{C['off']};border:1px solid {C['stroke']};}}")


class Lamp(QWidget):
    def __init__(self, label):
        super().__init__()
        self.setFixedHeight(20); self.setMinimumWidth(28 + len(label) * 9)
        self._on = False; self._color = C["off"]; self._label = label

    def set(self, on, color):
        self._on, self._color = on, color; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(self._color) if self._on else QColor(C["stroke"]))
        p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QRectF(0, 4, 12, 12))
        p.setPen(QColor(C["text"] if self._on else C["muted"]))
        f = QFont(); f.setPointSize(9); f.setBold(True); p.setFont(f)
        p.drawText(self.rect().adjusted(20, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self._label)


class SegIndicator(QWidget):
    """Bootstrap-style segmented status group: joined cells, rounded outer corners. Each
    cell lights independently (it's a status display, not a mutually-exclusive control)."""
    def __init__(self, cells):                       # cells = [(label, color), ...]
        super().__init__(); self._cells = cells; self._active = set()
        self.setFixedSize(62 * len(cells), 40)

    def set_active(self, active):
        self._active = set(active); self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); r = 9; n = len(self._cells); cw = w / n
        outer = QRectF(0.75, 0.75, w - 1.5, h - 1.5)
        f = QFont(); f.setPointSize(14); f.setBold(True); p.setFont(f)
        for i, (label, col) in enumerate(self._cells):
            x = i * cw
            if label in self._active:
                p.save(); p.setClipRect(QRectF(x, 0, cw, h))
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(col)); p.drawRoundedRect(outer, r, r)
                p.restore(); p.setPen(QColor(C["panel"]))
            else:
                p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(x, 0, cw, h), Qt.AlignmentFlag.AlignCenter, label)
        p.setPen(QPen(QColor(C["stroke"]), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(outer, r, r)
        for i in range(1, n):
            p.drawLine(QPointF(i * cw, 5), QPointF(i * cw, h - 5))


class BatteryWidget(QWidget):
    """Battery glyph + % for the MP305B's internal cell: color by level, charging bolt,
    and a pulsing red when near-empty. Click toggles charge/discharge (sim)."""
    clicked = pyqtSignal()
    LOW = 15

    def __init__(self):
        super().__init__(); self.setFixedSize(82, 24)
        self._pct = None; self._charging = False; self._pulse = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Internal cell — click to toggle charge/discharge")
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(550)

    def set(self, pct, charging=False):
        self._pct = None if pct is None else max(0, min(100, int(pct)))
        self._charging = bool(charging); self.update()

    def _low(self):
        return self._pct is not None and self._pct <= self.LOW and not self._charging

    def _tick(self):
        if self._low():
            self._pulse = not self._pulse; self.update()
        elif self._pulse:
            self._pulse = False; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bw, bh = 28, 15; y = (self.height() - bh) / 2
        pct = self._pct if self._pct is not None else 0
        col = C["on"] if pct > 50 else C["warn"] if pct > self.LOW else C["danger"]
        fill = QColor(col)
        if self._low():
            fill.setAlpha(255 if self._pulse else 70)
        p.setPen(QPen(QColor(col if self._low() and self._pulse else C["muted"]), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush); p.drawRoundedRect(QRectF(1, y, bw, bh), 2, 2)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(C["muted"]))
        p.drawRect(QRectF(bw + 2, y + 4.5, 3, bh - 9))
        if self._pct is not None:
            p.setBrush(fill); p.drawRoundedRect(QRectF(3, y + 2, (bw - 4) * pct / 100.0, bh - 4), 1, 1)
        if self._charging:   # bolt over the fill
            bx, by = 11, y + 2
            pts = [(4, 0), (0, 7), (3, 7), (1, 12), (8, 4.5), (4.5, 4.5)]
            p.setBrush(QColor(C["panel"])); p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(bx + a, by + b) for a, b in pts]))
        p.setPen(QColor(C["text"] if self._pct is not None else C["muted"]))
        f = QFont(); f.setPointSize(9); f.setBold(True); p.setFont(f)
        p.drawText(QRectF(bw + 9, 0, self.width() - bw - 9, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"{pct}%" if self._pct is not None else "—")


class _TempBar(QWidget):
    def __init__(self):
        super().__init__(); self.setFixedHeight(6); self._f = 0.0; self._c = C["on"]

    def set(self, frac, col):
        self._f = max(0.0, min(1.0, frac)); self._c = col; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["stroke"])); p.drawRoundedRect(QRectF(0, 0, w, h), 3, 3)
        p.setBrush(QColor(self._c)); p.drawRoundedRect(QRectF(0, 0, w * self._f, h), 3, 3)


class TempGauge(QFrame):
    """Temperature stat as a colored bar gauge (green → warn → danger by zone)."""
    def __init__(self, vmax=80):
        super().__init__(); self.setProperty("class", "card"); self.setFixedHeight(72); self._max = vmax
        v = QVBoxLayout(self); v.setContentsMargins(16, 10, 16, 10); v.setSpacing(4)
        v.addWidget(_lab("TEMPERATURE", "cardTitle"))
        row = QHBoxLayout(); row.setSpacing(6); row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.val = QLabel("—"); self.val.setFont(mono(17)); row.addWidget(self.val)
        u = QLabel("°C"); u.setProperty("class", "unit"); row.addWidget(u, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1); v.addLayout(row)
        self.bar = _TempBar(); v.addWidget(self.bar)

    def set(self, t):
        col = C["on"] if t < 50 else C["warn"] if t < 65 else C["danger"]
        self.val.setText(f"{t}"); self.val.setStyleSheet(f"color:{col};")
        self.bar.set(t / self._max, col)


class Keypad(QDialog):
    """Big pointer-operable keypad: digits + unit buttons (e.g. 9→V, 1500→mA). No keyboard."""
    def __init__(self, title, value, unit, vmax, dec, units, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title); self.setModal(True)
        self._max, self._dec, self._mult = vmax, dec, units[0][1]
        self._s = f"{value:.{dec}f}" if dec else f"{int(value)}"
        v = QVBoxLayout(self); v.setContentsMargins(16, 16, 16, 16); v.setSpacing(10)
        v.addWidget(_lab(f"{title}  ({unit}, max {vmax:g})", "cardTitle"))
        self._disp = QLabel(self._s); self._disp.setFont(mono(30))
        self._disp.setStyleSheet(f"color:{C['accent']};background:{C['bg']};"
                                 f"border:1px solid {C['stroke']};border-radius:10px;padding:8px 12px;")
        self._disp.setAlignment(Qt.AlignmentFlag.AlignRight); v.addWidget(self._disp)
        grid = QGridLayout(); grid.setSpacing(8)
        for i, k in enumerate(["7", "8", "9", "4", "5", "6", "1", "2", "3", ".", "0", "⌫"]):
            b = QPushButton(k); b.setFixedSize(78, 56); b.setFont(mono(16))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, key=k: self._key(key)); grid.addWidget(b, i // 3, i % 3)
        v.addLayout(grid)
        bot = QHBoxLayout(); bot.setSpacing(8)
        cancel = QPushButton("Cancel"); cancel.setFixedHeight(46); cancel.clicked.connect(self.reject)
        bot.addWidget(cancel)
        for i, (lbl, mult) in enumerate(units):
            b = QPushButton(lbl); b.setFixedHeight(46)
            if i == 0:
                b.setObjectName("primary")
            b.clicked.connect(lambda _, m=mult: (setattr(self, "_mult", m), self.accept()))
            bot.addWidget(b)
        v.addLayout(bot)

    def _key(self, k):
        if k == "⌫":
            self._s = self._s[:-1]
        elif k == "." and "." in self._s:
            return
        else:
            self._s += k
        self._disp.setText(self._s or "0")

    def value(self):
        try:
            return max(0.0, min(self._max, float(self._s or 0) * self._mult))
        except ValueError:
            return 0.0


class ChannelCard(QFrame):
    """One instrument channel: big measured value + a tappable SET sub-row, in one card.
    Tap anywhere to edit the setpoint via the keypad. Highlights when it's the limiting one."""
    changed = pyqtSignal(float)
    _UNITS = {"V": [("V", 1.0), ("mV", 0.001)], "A": [("A", 1.0), ("mA", 0.001)]}

    def __init__(self, title, unit, vmax, color, dec, measured=True):
        super().__init__()
        self.setObjectName("chan")
        self._unit, self._title, self._max = unit, title, vmax
        self._dec, self._color, self._measured = dec, color, measured
        self._set = 0.0; self._active = False
        self._base = f"#chan{{background:{C['card']};border:1px solid {C['stroke']};border-radius:14px;}}"
        self._hover = f"#chan{{background:{C['card_hi']};border:1px solid {C['hover']};border-radius:14px;}}"
        self._act = f"#chan{{background:{C['card_hi']};border:2px solid {color};border-radius:14px;}}"
        self.setStyleSheet(self._base); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(98 if measured else 84)
        v = QVBoxLayout(self); v.setContentsMargins(16, 10, 16, 10); v.setSpacing(4)
        top = QHBoxLayout(); top.addWidget(_lab(title, "cardTitle")); top.addStretch(1)
        self.tag = QLabel(""); self.tag.setStyleSheet(f"color:{color};font-weight:800;")
        top.addWidget(self.tag); v.addLayout(top)
        if measured:
            mrow = QHBoxLayout(); mrow.setSpacing(6); mrow.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.meas = QLabel("—"); self.meas.setFont(mono(34)); self.meas.setStyleSheet(f"color:{color};")
            mrow.addWidget(self.meas)
            mu = QLabel(unit); mu.setProperty("class", "unit"); mrow.addWidget(mu, 0, Qt.AlignmentFlag.AlignBottom)
            mrow.addStretch(1); v.addLayout(mrow)
        srow = QHBoxLayout()
        self.setlab = QLabel(""); self.setlab.setFont(mono(28) if not measured else QFont())
        if not measured:
            self.setlab.setStyleSheet(f"color:{color};")
        srow.addWidget(self.setlab); srow.addStretch(1); srow.addWidget(_lab("tap ▸", "sub"))
        v.addLayout(srow)
        self.set_setpoint(0.0)

    def value(self): return self._set

    def set_measured(self, val):
        if self._measured:
            self.meas.setText(f"{val:.{self._dec}f}")

    def set_setpoint(self, v, emit=False):
        self._set = max(0.0, min(self._max, round(v, self._dec)))
        txt = f"{self._set:.{self._dec}f}"
        self.setlab.setText(txt if not self._measured else f"SET  {txt} {self._unit}")
        self.setlab.setStyleSheet(f"color:{self._color};" if not self._measured
                                  else f"color:{C['muted']};font-weight:700;")
        if emit:
            self.changed.emit(self._set)

    def set_active(self, active, tag):
        self._active = active
        self.setStyleSheet(self._act if active else self._base)
        self.tag.setText(("● " + tag) if active else "")

    def _open_keypad(self):
        units = self._UNITS.get(self._unit, [(self._unit, 1.0)])
        dlg = Keypad(self._title, self._set, self._unit, self._max, self._dec, units, self)
        if dlg.exec():
            self.set_setpoint(dlg.value(), emit=True)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self._open_keypad()

    # no wheelEvent by design: a stray trackball scroll must never change the output.

    def enterEvent(self, e):
        if not self._active:
            self.setStyleSheet(self._hover)

    def leaveEvent(self, e):
        self.setStyleSheet(self._act if self._active else self._base)


class Chip(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self.setFixedHeight(40); self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        f = QFont(); f.setPointSize(10); f.setBold(True); self.setFont(f)
        self.setStyleSheet("padding: 0 2px;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class PresetChip(Chip):
    """One-click V+I recall; right-click stores the current setpoint into the slot."""
    applied = pyqtSignal(float, float)
    saveReq = pyqtSignal(object)

    def __init__(self, v, a):
        super().__init__(""); self.v, self.a = v, a; self._refresh()
        self.clicked.connect(lambda: self.applied.emit(self.v, self.a))

    def _refresh(self):
        self.setText(f"{self.v:g}V")
        self.setToolTip(f"{self.v:g} V / {self.a:g} A — right-click to save current")

    def store(self, v, a):
        self.v, self.a = round(v, 2), round(a, 3); self._refresh()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.saveReq.emit(self)
        else:
            super().mousePressEvent(e)


def _readout(title, unit, color):
    card = QFrame(); card.setProperty("class", "card"); card.setFixedHeight(72)
    v = QVBoxLayout(card); v.setContentsMargins(16, 10, 16, 10); v.setSpacing(2)
    v.addWidget(_lab(title, "cardTitle"))
    row = QHBoxLayout(); row.setSpacing(6); row.setAlignment(Qt.AlignmentFlag.AlignLeft)
    val = QLabel("—"); val.setFont(mono(17)); val.setStyleSheet(f"color:{color};"); row.addWidget(val)
    if unit:
        u = QLabel(unit); u.setProperty("class", "unit"); row.addWidget(u, 0, Qt.AlignmentFlag.AlignBottom)
    row.addStretch(1); v.addLayout(row)
    return card, val


# ---------------------------------------------------------------- main window
class MainWindow(QWidget):
    reqConnect = pyqtSignal(); reqDisconnect = pyqtSignal()
    reqV = pyqtSignal(float); reqA = pyqtSignal(float); reqOut = pyqtSignal(bool)

    def __init__(self, prefer_real=True):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("MP305 — ISDT bench supply")
        self.resize(1180, 860)
        self._sync = False; self._init_sp = False
        self._t0 = time.monotonic()
        self._t = deque(maxlen=900); self._v = deque(maxlen=900); self._i = deque(maxlen=900)
        self.backend, self.is_real = make_backend(prefer_real)
        self._build_ui(); self._start_worker()
        self.batt.clicked.connect(self._toggle_charge)
        self.reqConnect.emit()

    def _toggle_charge(self):
        fn = getattr(self.backend, "toggle_charging", None)
        if fn is not None:
            self._logline(f"battery {'charging' if fn() else 'discharging'}", C["accent"])

    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._topbar())
        body = QWidget(); bl = QHBoxLayout(body); bl.setContentsMargins(14, 12, 14, 14); bl.setSpacing(14)
        bl.addWidget(self._left_column(), 0)
        bl.addLayout(self._right_column(), 1)
        root.addWidget(body, 1)

    def _topbar(self):
        bar = QFrame(); bar.setProperty("class", "topbar"); bar.setFixedHeight(62)
        h = QHBoxLayout(bar); h.setContentsMargins(18, 0, 16, 0); h.setSpacing(10)
        t = QVBoxLayout(); t.setSpacing(0)
        t.addWidget(_lab("⚡ MP305", "h1")); t.addWidget(_lab("smart bench power supply", "sub"))
        h.addLayout(t); h.addStretch(1)
        self.badge = QLabel(); self.devlabel = _lab("—", "sub"); self.status = QLabel()
        self._set_badge("SIM" if not self.is_real else "USB", C["warn"] if not self.is_real else C["on"])
        self._set_status("○ Disconnected", C["muted"], tint=False)
        self.btn_remote = QPushButton("Remote"); self.btn_remote.setCheckable(True); self.btn_remote.setChecked(True)
        self.btn_remote.setToolTip("Take / release remote control (front panel lockout)")
        self.btn_remote.toggled.connect(self._style_remote); self._style_remote(True)
        self.btn_conn = QPushButton("Connect"); self.btn_conn.setObjectName("primary")
        self.btn_conn.clicked.connect(self._toggle_conn)
        self.batt = BatteryWidget(); self.batt.setToolTip("Internal cell")
        for w in (self.batt, self.badge, self.devlabel, self.status, self.btn_remote, self.btn_conn):
            h.addWidget(w)
        return bar

    def _left_column(self):
        col = QFrame(); col.setFixedWidth(360)
        v = QVBoxLayout(col); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        self.out_btn = OutputButton()
        self.out_btn.toggled.connect(lambda on: None if self._sync else self.reqOut.emit(on))
        v.addWidget(self.out_btn)

        self.ch_v = ChannelCard("VOLTAGE", "V", 30.0, C["volt"], 2)
        self.ch_a = ChannelCard("CURRENT", "A", 5.0, C["curr"], 3)
        self.ch_v.changed.connect(lambda x: None if self._sync else self.reqV.emit(x))
        self.ch_a.changed.connect(lambda x: None if self._sync else self.reqA.emit(x))
        v.addWidget(self.ch_v); v.addWidget(self.ch_a)

        if isinstance(self.backend, SimBackend):
            self.ch_load = ChannelCard("SIM LOAD", "Ω", 100.0, C["pink"], 0, measured=False)
            self.ch_load.set_setpoint(self.backend.load)
            self.ch_load.changed.connect(self.backend.set_load)
            v.addWidget(self.ch_load)

        pc = QFrame(); pc.setProperty("class", "card"); pc.setFixedHeight(92)
        pv = QVBoxLayout(pc); pv.setContentsMargins(16, 10, 16, 12); pv.setSpacing(8)
        pv.addWidget(_lab("PRESETS  (right-click saves)", "cardTitle"))
        prow = QHBoxLayout(); prow.setSpacing(6)
        for volts, amps in ((3.3, 3), (5, 3), (9, 3), (12, 5), (20, 5)):
            b = PresetChip(volts, amps)
            b.applied.connect(self._apply_preset); b.saveReq.connect(self._save_preset)
            prow.addWidget(b)
        pv.addLayout(prow); v.addWidget(pc)

        lc = QFrame(); lc.setProperty("class", "card")
        lv = QVBoxLayout(lc); lv.setContentsMargins(14, 10, 14, 12); lv.setSpacing(6)
        lv.addWidget(_lab("EVENT LOG", "cardTitle"))
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setFont(mono(9, bold=False))
        self.log.setStyleSheet(f"background:{C['bg']};border:1px solid {C['stroke']};border-radius:8px;")
        lv.addWidget(self.log); v.addWidget(lc, 1)

        self.btn_off = QPushButton("◼  ALL OFF"); self.btn_off.setObjectName("danger"); self.btn_off.setFixedHeight(42)
        self.btn_off.clicked.connect(lambda: self.out_btn.setChecked(False))
        v.addWidget(self.btn_off)
        self._set_enabled(False)
        return col

    def _right_column(self):
        col = QVBoxLayout(); col.setSpacing(14)
        col.addWidget(self._charts(), 1)
        strip = QHBoxLayout(); strip.setContentsMargins(2, 0, 2, 0); strip.setSpacing(16)
        self.seg = SegIndicator([("CC", C["warn"]), ("OCP", C["danger"])])   # standalone, no card
        self.lamp_ovp = Lamp("OVP")
        strip.addWidget(self.seg); strip.addWidget(self.lamp_ovp); strip.addStretch(1)
        col.addLayout(strip)
        stats = QHBoxLayout(); stats.setSpacing(14)
        self.r_pow = _readout("POWER", "W", C["pow"]); self.r_energy = self._energy_card()
        self.temp_gauge = TempGauge(); self.r_time = _readout("RUNTIME", "", C["text"])
        stats.addWidget(self.r_pow[0], 1); stats.addWidget(self.r_energy[0], 1)
        stats.addWidget(self.temp_gauge, 1); stats.addWidget(self.r_time[0], 1)
        col.addLayout(stats)
        return col

    def _energy_card(self):
        ec = QFrame(); ec.setProperty("class", "card"); ec.setFixedHeight(72)
        v = QVBoxLayout(ec); v.setContentsMargins(16, 8, 12, 10); v.setSpacing(2)
        tr = QHBoxLayout(); tr.addWidget(_lab("ENERGY", "cardTitle")); tr.addStretch(1)
        btn = QPushButton("↻"); btn.setFixedSize(26, 22); btn.setToolTip("Reset energy")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"padding:0;font-weight:800;border-radius:6px;background:{C['card_hi']};"
                          f"border:1px solid {C['stroke']};")
        btn.clicked.connect(self._reset_energy); tr.addWidget(btn)
        v.addLayout(tr)
        row = QHBoxLayout(); row.setSpacing(6); row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        val = QLabel("—"); val.setFont(mono(17)); val.setStyleSheet(f"color:{C['text']};")
        row.addWidget(val)
        u = QLabel("Wh"); u.setProperty("class", "unit"); row.addWidget(u, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1); v.addLayout(row)
        return ec, val

    def _reset_energy(self):
        fn = getattr(self.backend, "reset_energy", None)
        if fn is not None:
            fn(); self._logline("energy reset", C["accent"])

    def _charts(self):
        pg.setConfigOptions(antialias=True)
        wrap = QFrame(); wrap.setProperty("class", "card"); lay = QVBoxLayout(wrap); lay.setContentsMargins(8, 8, 8, 8)
        glw = pg.GraphicsLayoutWidget(); glw.setBackground(C["card"])
        self.p_v = glw.addPlot(row=0, col=0); self.p_i = glw.addPlot(row=1, col=0)
        for p, unit in ((self.p_v, "V"), (self.p_i, "A")):
            p.showGrid(x=True, y=True, alpha=0.12); p.setMouseEnabled(x=False, y=False)
            for ax in ("left", "bottom"):
                p.getAxis(ax).setPen(C["muted"]); p.getAxis(ax).setTextPen(C["muted"])
            p.setLabel("left", unit, color=C["muted"]); p.setClipToView(True)
            p.setDownsampling(auto=True, mode="peak")
        self.p_i.setXLink(self.p_v); self.p_i.setLabel("bottom", "seconds", color=C["muted"])
        self.curve_v = self.p_v.plot(pen=pg.mkPen(C["volt"], width=2))
        self.curve_i = self.p_i.plot(pen=pg.mkPen(C["curr"], width=2))
        lay.addWidget(glw); return wrap

    # ---- worker
    def _start_worker(self):
        self.thread = QThread(); self.worker = DeviceWorker(self.backend, poll_ms=100)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.state.connect(self._on_state); self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected); self.worker.error.connect(self._on_error)
        self.reqConnect.connect(self.worker.connect_device); self.reqDisconnect.connect(self.worker.disconnect_device)
        self.reqV.connect(self.worker.set_voltage); self.reqA.connect(self.worker.set_current)
        self.reqOut.connect(self.worker.set_output)
        self.thread.start()

    # ---- helpers
    def _toggle_conn(self):
        (self.reqConnect if self.btn_conn.text() == "Connect" else self.reqDisconnect).emit()

    def _set_enabled(self, on):
        for w in (self.out_btn, self.ch_v, self.ch_a, self.btn_off):
            w.setEnabled(on)

    def _apply_preset(self, v, a):
        self.ch_v.set_setpoint(v, emit=True); self.ch_a.set_setpoint(a, emit=True)
        self._logline(f"preset {v:g}V / {a:g}A", C["accent"])

    def _save_preset(self, chip):
        chip.store(self.ch_v.value(), self.ch_a.value())
        self._logline(f"saved preset {chip.v:g}V / {chip.a:g}A", C["accent"])

    def _set_badge(self, text, hexcol):
        # flat colored label (no box) — matches the flat status text; only real buttons are boxed
        self.badge.setText(text)
        self.badge.setStyleSheet(f"background:transparent;border:none;color:{hexcol};"
                                 f"font-size:11px;font-weight:800;letter-spacing:2px;padding:0 2px;")

    def _set_status(self, text, hexcol, tint=True):
        # flat dot + text (no box) — clearly a status indicator, not a clickable button
        self.status.setText(text)
        self.status.setStyleSheet(f"background:transparent;border:none;color:{hexcol};"
                                  f"font-weight:700;padding:0 4px;")

    def _style_remote(self, held):
        if held:
            rgb = _rgb(C["accent"])
            self.btn_remote.setStyleSheet(f"background:rgba({rgb},0.9);color:{C['panel']};border:none;"
                                          f"border-radius:10px;padding:9px 16px;font-weight:700;")
            self.btn_remote.setText("● Remote")
        else:
            self.btn_remote.setStyleSheet(""); self.btn_remote.setText("Remote")

    def _logline(self, msg, color):
        self.log.appendHtml(f'<span style="color:{C["muted"]}">{time.strftime("%H:%M:%S")}</span> '
                            f'<span style="color:{color}">{msg}</span>')

    # ---- callbacks
    def _on_connected(self, info):
        self._set_status("● Connected", C["on"])
        self.devlabel.setText(f"{info.get('model','MP305')}  ·  {info.get('fw','')}")
        self._set_badge("SIM" if not self.is_real else "USB", C["warn"] if not self.is_real else C["on"])
        self.btn_conn.setText("Disconnect"); self._set_enabled(True); self._init_sp = False
        self._logline(f"connected — {info.get('model','MP305')} {info.get('fw','')}", C["on"])

    def _on_disconnected(self, _):
        self._set_status("○ Disconnected", C["muted"], tint=False)
        self.btn_conn.setText("Connect"); self._set_enabled(False); self._logline("disconnected", C["warn"])

    def _on_error(self, msg):
        self._logline(f"error: {msg}", C["danger"])

    def _on_state(self, st):
        on = bool(st["output"]); cc = st.get("mode") == "CC"
        self._sync = True
        if not self._init_sp:
            self.ch_v.set_setpoint(st["set_voltage"]); self.ch_a.set_setpoint(st["set_current"]); self._init_sp = True
        if self.out_btn.isChecked() != on:
            self.out_btn.setChecked(on); self._logline(f"output {'ON' if on else 'OFF'}", C["on"] if on else C["muted"])
        self._sync = False

        self.ch_v.set_measured(st["voltage"]); self.ch_a.set_measured(st["current"])
        self.ch_v.set_active(on and not cc, "CV"); self.ch_a.set_active(on and cc, "CC")

        self.r_pow[1].setText(f"{st['power']:.2f}"); self.r_energy[1].setText(f"{st['energy']:.3f}")
        self.temp_gauge.set(st["temperature"])
        self.batt.set(st.get("battery"), st.get("charging", False))
        h, rem = divmod(int(st["working_time"]), 3600); m, s = divmod(rem, 60)
        self.r_time[1].setText(f"{h:02d}:{m:02d}:{s:02d}")

        errs = st.get("errors", [])
        active = set()
        if on and cc:
            active.add("CC")
        if "errorDcOutOCP" in errs:
            active.add("OCP")
        self.seg.set_active(active)
        self.lamp_ovp.set("errorDcOutOVP" in errs, C["danger"])

        t = time.monotonic() - self._t0
        self._t.append(t); self._v.append(st["voltage"]); self._i.append(st["current"])
        while self._t and self._t[0] < t - WINDOW:
            self._t.popleft(); self._v.popleft(); self._i.popleft()
        self.curve_v.setData(self._t, self._v); self.curve_i.setData(self._t, self._i)

    def closeEvent(self, e):
        try:
            self.reqDisconnect.emit(); self.thread.quit(); self.thread.wait(800)
        except Exception:
            pass
        e.accept()


def run(prefer_real=True):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    win = MainWindow(prefer_real=prefer_real); win.show()
    return app.exec()
