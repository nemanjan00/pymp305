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
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QRectF, QTimer, QPointF, QObject, QVariantAnimation, QEasingCurve,
    QMetaObject,
)
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QPolygonF, QPainterPath
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QGridLayout, QDialog, QPlainTextEdit, QSizePolicy, QStackedWidget, QButtonGroup,
)

from .theme import C, STYLESHEET
from .worker import DeviceWorker
from .backend import make_backend, SimBackend, CHEMS, MODE_DC, MODE_PROG, MODE_PD, MODE_CHARGE

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


def _blend(a, b, t):
    """Linear color interpolation a→b for t in [0,1]."""
    ca, cb = QColor(a), QColor(b); t = max(0.0, min(1.0, t))
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t),
                  int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t))


class EasedValue(QObject):
    """Eases a float toward a target (OutCubic, ~180 ms) and pushes each step to a setter,
    so numeric read-outs and gauges glide instead of snapping."""
    def __init__(self, setter, dur=180, parent=None):
        super().__init__(parent)
        self._setter = setter; self._cur = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(dur); self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)

    def _step(self, v):
        self._cur = float(v); self._setter(self._cur)

    def to(self, target):
        target = float(target)
        if abs(target - self._cur) < 1e-6:
            return
        self._anim.stop(); self._anim.setStartValue(self._cur)
        self._anim.setEndValue(target); self._anim.start()


# ---------------------------------------------------------------- widgets
class OutputButton(QPushButton):
    """The whole OUTPUT card is one button — green ON, red OFF. Huge trackball target."""
    def __init__(self, on_text="⏻   OUTPUT ON", off_text="⏻   OUTPUT OFF"):
        super().__init__()
        self._on_text, self._off_text = on_text, off_text
        self.setText(off_text)        # initial label (toggled only fires on change, not at start)
        self.setCheckable(True); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(92)
        self._mix = 0.0                       # 0 = OFF (red), 1 = ON (green)
        self._anim = QVariantAnimation(self); self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self._restyle(float(v)))
        self.toggled.connect(self._on_toggled); self._restyle(0.0)

    def _on_toggled(self, on):
        self.setText(self._on_text if on else self._off_text)
        self._anim.stop(); self._anim.setStartValue(self._mix)
        self._anim.setEndValue(1.0 if on else 0.0); self._anim.start()

    def _restyle(self, mix):
        self._mix = mix
        col = _blend(C["danger"], C["on"], mix).name(); rgb = _rgb(col)
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


class SegToggle(QWidget):
    """A real mutually-exclusive segmented toggle (Bootstrap-style joined cells). Click a
    cell to select it; each shows a code + a smaller description (like WebLink's cells).
    Used for over-current behaviour: CC (current-limit) | OCP (trip) = `currentOver` 0/1."""
    selected = pyqtSignal(int)

    def __init__(self, cells):                       # cells = [(code, description, color), ...]
        super().__init__(); self._cells = cells; self._idx = 0
        self.setFixedHeight(66)        # fixed like the other cards (don't absorb column slack)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(110 * len(cells))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 0.0                       # animated fill position (float cell index)
        self._slide = QVariantAnimation(self); self._slide.setDuration(180)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.valueChanged.connect(lambda v: (setattr(self, "_pos", float(v)), self.update()))
        self._pulse = 0.0                     # brief red flash (OCP trip)
        self._pulse_anim = QVariantAnimation(self); self._pulse_anim.setDuration(480)
        for k, val in ((0.0, 0.0), (0.5, 1.0), (1.0, 0.0)):
            self._pulse_anim.setKeyValueAt(k, val)
        self._pulse_anim.valueChanged.connect(lambda v: (setattr(self, "_pulse", float(v)), self.update()))

    def _glide_to(self, i):
        self._slide.stop(); self._slide.setStartValue(self._pos)
        self._slide.setEndValue(float(i)); self._slide.start()

    def set_index(self, i):
        i = max(0, min(len(self._cells) - 1, int(i)))
        if i != self._idx:
            self._idx = i; self._glide_to(i)

    def pulse(self):
        self._pulse_anim.stop(); self._pulse_anim.start()

    def mousePressEvent(self, e):
        i = int(e.position().x() // (self.width() / len(self._cells)))
        i = max(0, min(len(self._cells) - 1, i))
        if i != self._idx:
            self._idx = i; self._glide_to(i); self.selected.emit(i)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); r = 13; n = len(self._cells); cw = w / n   # card-shaped
        outer = QRectF(0.75, 0.75, w - 1.5, h - 1.5)
        # sliding fill: a cell-wide block at the animated position, clipped to the card shape,
        # its colour blended between the cells it's travelling between
        lo = max(0, min(n - 1, int(self._pos))); hi = min(n - 1, lo + 1)
        fill = _blend(self._cells[lo][2], self._cells[hi][2], self._pos - lo)
        p.save(); path = QPainterPath(); path.addRoundedRect(outer, r, r); p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(fill); p.drawRect(QRectF(self._pos * cw, 0, cw, h))
        p.restore()
        code_pt = int(max(14, min(26, h * 0.20)))    # scale the code with the cell height
        sub_pt = int(max(8, min(10, h * 0.075)))      # cap: the descriptions are long, must not clip
        fcode = QFont(); fcode.setPointSize(code_pt); fcode.setBold(True)
        fsub = QFont(); fsub.setPointSize(sub_pt); fsub.setBold(True)
        mid = h / 2; lit = round(self._pos)
        for i, (code, sub, col) in enumerate(self._cells):
            x = i * cw
            tcol = QColor(C["panel"]) if i == lit else QColor(C["muted"])
            p.setFont(fcode); p.setPen(tcol)
            p.drawText(QRectF(x, mid - h * 0.30, cw, h * 0.32), Qt.AlignmentFlag.AlignCenter, code)
            p.setFont(fsub); p.setPen(tcol)
            p.drawText(QRectF(x + 3, mid + h * 0.02, cw - 6, h * 0.26), Qt.AlignmentFlag.AlignCenter, sub)
        p.setPen(QPen(QColor(C["stroke"]), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(outer, r, r)
        for i in range(1, n):
            p.drawLine(QPointF(i * cw, 5), QPointF(i * cw, h - 5))
        if self._pulse > 0:                   # OCP trip flash
            pc = QColor(C["danger"]); pc.setAlpha(int(220 * self._pulse))
            p.setPen(QPen(pc, 3)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(outer, r, r)


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
        super().__init__(); self.setFixedHeight(72); self._max = vmax        # flat readout
        v = QVBoxLayout(self); v.setContentsMargins(16, 10, 16, 10); v.setSpacing(4)
        v.addWidget(_lab("TEMPERATURE", "cardTitle"))
        row = QHBoxLayout(); row.setSpacing(6); row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.val = QLabel("—"); self.val.setFont(mono(17)); row.addWidget(self.val)
        u = QLabel("°C"); u.setProperty("class", "unit"); row.addWidget(u, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1); v.addLayout(row)
        self.bar = _TempBar(); v.addWidget(self.bar)
        self._eased = EasedValue(self._render)

    def _render(self, t):
        col = C["on"] if t < 50 else C["warn"] if t < 65 else C["danger"]
        self.val.setText(f"{t:.0f}"); self.val.setStyleSheet(f"color:{col};")
        self.bar.set(t / self._max, col)

    def set(self, t):
        self._eased.to(float(t))


class Keypad(QDialog):
    """Big pointer-operable keypad: digits + unit buttons (e.g. 9→V, 1500→mA). No keyboard."""
    def __init__(self, title, value, unit, vmax, dec, units, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title); self.setModal(True)
        self._max, self._dec, self._mult = vmax, dec, units[0][1]
        self._s = f"{value:.{dec}f}" if dec else f"{int(value)}"
        v = QVBoxLayout(self); v.setContentsMargins(16, 16, 16, 16); v.setSpacing(10)
        self._unit = unit; self._capped = False
        self._hdr_text = f"{title}  ({unit}, max {vmax:g})"
        self._hdr = _lab(self._hdr_text, "cardTitle"); v.addWidget(self._hdr)
        self._disp = QLabel(self._s); self._disp.setFont(mono(30))
        self._disp_css = (f"color:{C['accent']};background:{C['bg']};"
                          f"border:1px solid {C['stroke']};border-radius:10px;padding:8px 12px;")
        self._warn_css = (f"color:{C['danger']};background:{C['bg']};"
                          f"border:1px solid {C['danger']};border-radius:10px;padding:8px 12px;")
        self._disp.setStyleSheet(self._disp_css)
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
            b.clicked.connect(lambda _, m=mult: self._commit(m))
            bot.addWidget(b)
        v.addLayout(bot)

    def _key(self, k):
        if self._capped:                       # editing after a cap → start fresh
            self._capped = False; self._s = ""
            self._disp.setStyleSheet(self._disp_css); self._hdr.setText(self._hdr_text)
        if k == "⌫":
            self._s = self._s[:-1]
        elif k == "." and "." in self._s:
            return
        else:
            self._s += k
        self._disp.setText(self._s or "0")

    def _commit(self, mult):
        try:
            raw = float(self._s or 0) * mult
        except ValueError:
            raw = 0.0
        if raw > self._max + 1e-9 and not self._capped:
            # over the rail — make it visible: snap to max (red) and require a confirming tap
            self._capped = True; self._mult = 1.0
            self._s = f"{self._max:.{self._dec}f}" if self._dec else f"{int(self._max)}"
            self._disp.setText(self._s); self._disp.setStyleSheet(self._warn_css)
            self._hdr.setText(f"⚠  capped to max {self._max:g} {self._unit} — tap a unit to accept")
            return
        self._mult = 1.0 if self._capped else mult
        self.accept()

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
        self._out_on = False; self._meas_val = 0.0
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
            self._big = EasedValue(lambda val: self.meas.setText(f"{val:.{self._dec}f}"))
        srow = QHBoxLayout()
        self.setlab = QLabel(""); self.setlab.setFont(mono(34) if not measured else QFont())
        if not measured:
            self.setlab.setStyleSheet(f"color:{color};")
        srow.addWidget(self.setlab); srow.addStretch(1); srow.addWidget(_lab("tap ▸", "sub"))
        v.addLayout(srow)
        self.set_setpoint(0.0)

    def value(self): return self._set

    def set_measured(self, val):
        if self._measured:
            self._meas_val = val
            if self._out_on:
                self._big.to(val)

    def set_live(self, on):
        if self._measured and on != self._out_on:
            self._out_on = on
            self._refresh_primary()

    def _refresh_primary(self):
        # the BIG number is the live measurement while output is ON, but the SET-POINT while
        # it's OFF — so the configured V/A stays big and readable instead of a meaningless 0.
        if not self._measured:
            return
        if self._out_on:
            self._big.to(self._meas_val)
            self.setlab.setText(f"SET  {self._set:.{self._dec}f} {self._unit}")
        else:
            self._big.to(self._set)
            self.setlab.setText("output off")
            self.tag.setText("SET"); self.tag.setStyleSheet(f"color:{C['muted']};font-weight:800;letter-spacing:1px;")
        self.meas.setStyleSheet(f"color:{self._color};")
        self.setlab.setStyleSheet(f"color:{C['muted']};font-weight:700;")

    def set_setpoint(self, v, emit=False):
        self._set = max(0.0, min(self._max, round(v, self._dec)))
        if self._measured:
            self._refresh_primary()
        else:
            self.setlab.setText(f"{self._set:.{self._dec}f}")
            self.setlab.setStyleSheet(f"color:{self._color};")
        if emit:
            self.changed.emit(self._set)

    def set_active(self, active, tag):
        self._active = active
        self.setStyleSheet(self._act if active else self._base)
        if self._out_on:                       # CV/CC tag only when live; when off the tag is "SET"
            self.tag.setText(("● " + tag) if active else "")
            self.tag.setStyleSheet(f"color:{self._color};font-weight:800;")

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


class Picker(QDialog):
    """Pointer-friendly dropdown: a vertical list of big option buttons — tap one to pick."""
    def __init__(self, title, options, current, parent=None):
        super().__init__(parent); self.setModal(True); self._val = current
        v = QVBoxLayout(self); v.setContentsMargins(16, 16, 16, 16); v.setSpacing(8)
        v.addWidget(_lab(title, "cardTitle"))
        for i, opt in enumerate(options):
            b = QPushButton(str(opt)); b.setFixedHeight(44); b.setFont(mono(14))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if i == current:
                b.setObjectName("primary")
            b.clicked.connect(lambda _, idx=i: (setattr(self, "_val", idx), self.accept()))
            v.addWidget(b)

    def value(self):
        return self._val


class ChoiceCard(QFrame):
    """A whole-card button showing the current choice; tap opens a dropdown (Picker)."""
    changed = pyqtSignal(int)

    def __init__(self, title, options, color):
        super().__init__(); self.setObjectName("chan")
        self._title, self._options, self._color, self._idx = title, options, color, 0
        self._base = f"#chan{{background:{C['card']};border:1px solid {C['stroke']};border-radius:14px;}}"
        self._hover = f"#chan{{background:{C['card_hi']};border:1px solid {C['hover']};border-radius:14px;}}"
        self.setStyleSheet(self._base); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(72)
        v = QVBoxLayout(self); v.setContentsMargins(16, 10, 16, 10); v.setSpacing(2)
        v.addWidget(_lab(title, "cardTitle"))
        row = QHBoxLayout()
        self.val = QLabel(str(options[0]) if options else "—"); self.val.setFont(mono(22))
        self.val.setStyleSheet(f"color:{color};")
        row.addWidget(self.val); row.addStretch(1); row.addWidget(_lab("tap ▾", "sub"))
        v.addLayout(row)

    def value(self):
        return self._idx

    def set_index(self, i):
        if self._options:
            self._idx = max(0, min(len(self._options) - 1, int(i)))
            self.val.setText(str(self._options[self._idx]))

    def _open(self):
        dlg = Picker(self._title, self._options, self._idx, self)
        if dlg.exec():
            self.set_index(dlg.value()); self.changed.emit(self._idx)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self._open()

    def enterEvent(self, e):
        self.setStyleSheet(self._hover)

    def leaveEvent(self, e):
        self.setStyleSheet(self._base)


class Chip(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self.setFixedHeight(40); self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        f = QFont(); f.setPointSize(10); f.setBold(True); self.setFont(f)
        self.setStyleSheet("padding: 0 2px;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_group_style(self, i, n):
        """Style as a member of a flush button group (no gaps; only the group's outer
        bottom corners are rounded, to sit edge-to-edge in a card)."""
        self.setFixedHeight(42)
        # pin ALL four corners (the global QPushButton border-radius would otherwise round the
        # tops): only the group's outer bottom corners are rounded, the rest square.
        bl = 13 if i == 0 else 0
        br = 13 if i == n - 1 else 0
        radii = (f"border-top-left-radius:0px;border-top-right-radius:0px;"
                 f"border-bottom-left-radius:{bl}px;border-bottom-right-radius:{br}px;")
        right = f"border-right:1px solid {C['stroke']};" if i < n - 1 else ""
        self.setStyleSheet(
            f"QPushButton {{ background:{C['card_hi']}; border:none; {right}{radii}"
            f" padding:0; font-weight:700; color:{C['text']}; }}"
            f"QPushButton:hover {{ background:{C['hover']}; color:{C['accent']}; }}"
            f"QPushButton:disabled {{ color:{C['off']}; }}")


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
    card = QFrame(); card.setFixedHeight(72)        # flat: read-only, not a card
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
    reqCurrentOver = pyqtSignal(int); reqRemote = pyqtSignal(bool)
    reqMode = pyqtSignal(int); reqCharge = pyqtSignal(dict); reqCharging = pyqtSignal(bool)
    reqSelectPDO = pyqtSignal(int); reqPdOutput = pyqtSignal(bool); reqProgram = pyqtSignal(bool)

    def __init__(self, prefer_real=True):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("MP305 — ISDT bench supply")
        self.resize(1180, 860)
        self.setMinimumSize(1060, 800)        # floor: stops anyone cramping it into nonsense
        self._sync = False; self._init_sp = False; self._last_errors = set()
        self._connected = False; self._remote_held = True; self._output_on = False
        self._t0 = time.monotonic()
        self._t = deque(maxlen=900); self._v = deque(maxlen=900); self._i = deque(maxlen=900)
        self.backend, self.is_real = make_backend(prefer_real)
        self._build_ui(); self._start_worker()
        self._pow_eased = EasedValue(lambda v: self.r_pow[1].setText(f"{v:.2f}"))
        self.batt.clicked.connect(self._toggle_charge)
        self._build_overlay(); self._show_overlay()   # cover the UI until the first reading lands
        self.reqConnect.emit()

    # ---- startup / connecting overlay -----------------------------------
    def _build_overlay(self):
        self._overlay = QWidget(self); self._overlay.setObjectName("overlay")
        self._overlay.setStyleSheet(f"#overlay {{ background: {C['bg']}; }}")
        lay = QVBoxLayout(self._overlay)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.setSpacing(16)
        title = QLabel("⚡ MP305"); title.setFont(mono(44)); title.setStyleSheet(f"color:{C['accent']};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overlay_msg = QLabel("connecting…"); self._overlay_msg.setFont(mono(15, bold=False))
        self._overlay_msg.setStyleSheet(f"color:{C['muted']};")
        self._overlay_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title); lay.addWidget(self._overlay_msg)
        self._spin_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"; self._spin_i = 0
        self._spin_timer = QTimer(self); self._spin_timer.setInterval(90)
        self._spin_timer.timeout.connect(self._spin)
        self._overlay.hide()

    def _spin(self):
        self._spin_i = (self._spin_i + 1) % len(self._spin_frames)
        self._overlay_msg.setText(f"{self._spin_frames[self._spin_i]}   connecting…")

    def _show_overlay(self):
        self._overlay.setGeometry(self.rect()); self._overlay.show(); self._overlay.raise_()
        self._spin_i = 0; self._spin_timer.start()

    def _hide_overlay(self):
        self._spin_timer.stop()
        if self._overlay.isVisible():
            self._overlay.hide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        ov = getattr(self, "_overlay", None)
        if ov is not None:
            ov.setGeometry(self.rect())

    def _toggle_charge(self):
        fn = getattr(self.backend, "toggle_charging", None)
        if fn is not None:
            self._logline(f"battery {'charging' if fn() else 'discharging'}", C["accent"])

    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._topbar())
        body = QWidget(); bl = QVBoxLayout(body); bl.setContentsMargins(14, 12, 14, 14); bl.setSpacing(12)
        cols = QHBoxLayout(); cols.setSpacing(14)
        cols.addWidget(self._left_column(), 0)
        cols.addLayout(self._right_column(), 1)
        bl.addLayout(cols, 1)
        bl.addWidget(self._log_card())          # event log spans the full width, under both columns
        root.addWidget(body, 1)

    def _topbar(self):
        bar = QFrame(); bar.setProperty("class", "topbar"); bar.setFixedHeight(62)
        h = QHBoxLayout(bar); h.setContentsMargins(18, 0, 16, 0); h.setSpacing(10)
        h.addWidget(_lab("⚡ MP305", "h1")); h.addStretch(1)
        self.badge = QLabel(); self.devlabel = _lab("—", "sub"); self.status = QLabel()
        self._set_badge("SIM" if not self.is_real else "USB", C["warn"] if not self.is_real else C["on"])
        self._set_status("○ Disconnected", C["muted"], tint=False)
        self.btn_remote = QPushButton("Remote"); self.btn_remote.setCheckable(True); self.btn_remote.setChecked(True)
        self.btn_remote.setToolTip("Take / release remote control (front panel lockout)")
        self.btn_remote.toggled.connect(self._on_remote_toggle); self._style_remote(True)
        self.btn_conn = QPushButton("Connect"); self.btn_conn.setObjectName("primary")
        self.btn_conn.clicked.connect(self._toggle_conn)
        self.batt = BatteryWidget(); self.batt.setToolTip("Internal cell — click to start / stop charging")
        for w in (self.batt, self.badge, self.devlabel, self.status, self.btn_remote, self.btn_conn):
            h.addWidget(w)
        return bar

    def _left_column(self):
        col = QFrame(); col.setFixedWidth(360)
        v = QVBoxLayout(col); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        # mode tabs — the device runs ONE mode at a time (its `model` field), so switching a
        # tab switches the device mode and the control panel below.
        tabs = QHBoxLayout(); tabs.setSpacing(6)
        self._mode_grp = QButtonGroup(self); self._mode_grp.setExclusive(True)
        self._mode_tabs = {}
        for label, model, page in (("DC PSU", MODE_DC, 0), ("Program", MODE_PROG, 3),
                                   ("Charge", MODE_CHARGE, 1), ("USB-PD", MODE_PD, 2)):
            b = QPushButton(label); b.setProperty("class", "tab"); b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, m=model, p=page: self._switch_mode(m, p))
            self._mode_grp.addButton(b); tabs.addWidget(b); self._mode_tabs[model] = (b, page)
        self._mode_tabs[MODE_DC][0].setChecked(True)
        v.addLayout(tabs)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._dc_page())          # 0
        self.stack.addWidget(self._charge_page())      # 1
        self.stack.addWidget(self._pd_page())          # 2
        self.stack.addWidget(self._program_page())     # 3  (model 1)
        v.addWidget(self.stack, 1)
        self._set_enabled(False)
        return col

    def _dc_page(self):
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        self.out_btn = OutputButton()
        self.out_btn.toggled.connect(lambda on: None if self._sync else self.reqOut.emit(on))
        v.addWidget(self.out_btn)

        self.ch_v = ChannelCard("VOLTAGE", "V", 30.0, C["volt"], 2)
        self.ch_a = ChannelCard("CURRENT", "A", 5.0, C["curr"], 3)
        self.ch_v.changed.connect(lambda x: None if self._sync else self.reqV.emit(x))
        self.ch_a.changed.connect(lambda x: None if self._sync else self.reqA.emit(x))
        v.addWidget(self.ch_v); v.addWidget(self.ch_a)

        oc = QFrame()        # no card: the toggle has its own border
        ov = QVBoxLayout(oc); ov.setContentsMargins(2, 4, 2, 0); ov.setSpacing(6)
        ov.addWidget(_lab("OVER-CURRENT", "cardTitle"))
        self.cov = SegToggle([("CC", "Constant Current", C["warn"]),
                              ("OCP", "Overcurrent Protection", C["danger"])])
        self.cov.selected.connect(lambda i: None if self._sync else self.reqCurrentOver.emit(i))
        ov.addWidget(self.cov)
        v.addWidget(oc)

        if isinstance(self.backend, SimBackend):
            self.ch_load = ChannelCard("SIM LOAD", "Ω", 100.0, C["pink"], 0, measured=False)
            self.ch_load.set_setpoint(self.backend.load)
            self.ch_load.changed.connect(self.backend.set_load)
            v.addWidget(self.ch_load)

        pc = QFrame(); pc.setProperty("class", "card"); pc.setFixedHeight(80)
        pv = QVBoxLayout(pc); pv.setContentsMargins(0, 0, 0, 0); pv.setSpacing(0)
        title = _lab("PRESETS  ·  right-click saves", "cardTitle"); title.setContentsMargins(16, 11, 16, 9)
        pv.addWidget(title)
        sep = QFrame(); sep.setFixedHeight(1); sep.setStyleSheet(f"background:{C['stroke']};border:none;")
        pv.addWidget(sep)
        grp = QWidget(); gl = QHBoxLayout(grp); gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(0)
        presets = ((3.3, 3), (5, 3), (9, 3), (12, 5), (20, 5)); n = len(presets)
        for i, (volts, amps) in enumerate(presets):
            b = PresetChip(volts, amps)
            b.applied.connect(self._apply_preset); b.saveReq.connect(self._save_preset)
            b.set_group_style(i, n)
            gl.addWidget(b, 1)
        pv.addWidget(grp); v.addWidget(pc)
        v.addStretch(1)        # controls keep natural height; extra space falls to the bottom
        return page

    def _charge_page(self):
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        self.chg_btn = OutputButton("⚡   CHARGING", "⚡   START CHARGE")
        self.chg_btn.toggled.connect(lambda on: None if self._sync else self.reqCharging.emit(on))
        v.addWidget(self.chg_btn)
        self.ch_chem = ChoiceCard("CHEMISTRY", CHEMS, C["accent"])
        self.ch_chem.changed.connect(lambda i: None if self._sync else self.reqCharge.emit({"chem": i}))
        v.addWidget(self.ch_chem)
        self.ch_cells = ChannelCard("CELLS", "S", 24, C["pow"], 0, measured=False)
        self.ch_cells.set_setpoint(3)
        self.ch_cells.changed.connect(lambda x: None if self._sync else self.reqCharge.emit({"cells": int(x)}))
        v.addWidget(self.ch_cells)
        self.ch_chgA = ChannelCard("CHARGE CURRENT", "A", 10.0, C["pow"], 2, measured=False)
        self.ch_chgA.set_setpoint(1.0)
        self.ch_chgA.changed.connect(lambda x: None if self._sync else self.reqCharge.emit({"current": x}))
        v.addWidget(self.ch_chgA)
        sc = QFrame(); sc.setProperty("class", "card"); sc.setFixedHeight(64)
        scl = QVBoxLayout(sc); scl.setContentsMargins(16, 8, 16, 8); scl.setSpacing(4)
        scl.addWidget(_lab("CHARGE", "cardTitle"))
        self.chg_stat = QLabel("idle"); self.chg_stat.setFont(mono(15)); self.chg_stat.setStyleSheet(f"color:{C['pow']};")
        scl.addWidget(self.chg_stat); v.addWidget(sc)
        v.addStretch(1)
        return page

    def _pd_page(self):
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.pd_out_btn = OutputButton("⚡   PD OUTPUT ON", "⚡   ENABLE PD OUTPUT")
        self.pd_out_btn.toggled.connect(self._on_pd_output)
        v.addWidget(self.pd_out_btn)
        v.addWidget(_lab("ADVERTISED PROFILES  ·  tap to toggle", "cardTitle"))
        self._pdo_wrap = QVBoxLayout(); self._pdo_wrap.setSpacing(6); v.addLayout(self._pdo_wrap)
        self._pdo_btns = []
        v.addStretch(1)
        em = QFrame(); em.setFixedHeight(58)        # flat: e-marker is read-only cable info
        eml = QVBoxLayout(em); eml.setContentsMargins(2, 8, 2, 0); eml.setSpacing(4)
        eml.addWidget(_lab("USB-C CABLE", "cardTitle"))
        self._emark_lab = QLabel("—"); self._emark_lab.setFont(mono(10, bold=False))
        self._emark_lab.setStyleSheet(f"color:{C['curr']};"); self._emark_lab.setWordWrap(True)
        eml.addWidget(self._emark_lab); v.addWidget(em)
        return page

    # ---- Programmable (timed DC steps) mode ------------------------------
    def _program_page(self):
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.prog_btn = OutputButton("■   RUNNING  ·  STOP", "▶   START SEQUENCE")
        self.prog_btn.toggled.connect(self._on_program)
        v.addWidget(self.prog_btn)
        self.prog_name_lbl = _lab("SEQUENCE", "cardTitle"); v.addWidget(self.prog_name_lbl)
        self._prog_wrap = QVBoxLayout(); self._prog_wrap.setSpacing(6); v.addLayout(self._prog_wrap)
        self._prog_lbls = []
        v.addStretch(1)
        return page

    def _on_program(self, on):
        if self._sync:
            return
        self.reqProgram.emit(on)
        self._logline(f"sequence {'started' if on else 'stopped'}", C["on"] if on else C["muted"])

    def _prog_row_css(self, active):
        if active:
            return (f"background:{C['accent']};color:{C['panel']};border:none;"
                    f"border-radius:8px;padding:7px 13px;font-weight:800;")
        return (f"background:{C['card_hi']};color:{C['text']};border:1px solid {C['stroke']};"
                f"border-radius:8px;padding:7px 13px;")

    def _rebuild_program(self, steps):
        for l in self._prog_lbls:
            l.setParent(None)
        self._prog_lbls = []
        for i, (vv, aa, ss) in enumerate(steps):
            lbl = QLabel(f"{i + 1}.    {vv:g} V   ·   {aa:g} A   ·   {ss:g} s")
            lbl.setFixedHeight(36); lbl.setFont(mono(13, bold=False))
            lbl.setStyleSheet(self._prog_row_css(False))
            self._prog_wrap.addWidget(lbl); self._prog_lbls.append(lbl)

    _PDO_TOGGLE_CSS = (
        "QPushButton{{background:{card};border:1px solid {stroke};border-radius:9px;"
        "font-weight:700;padding:9px;text-align:left;padding-left:14px;color:{text};}}"
        "QPushButton:hover{{background:{hover};}}"
        "QPushButton:checked{{background:{accent};color:{panel};border:none;}}"
        "QPushButton:disabled{{background:{accent};color:{panel};border:none;}}"
    )

    def _rebuild_pdos(self, pdos):
        for b in self._pdo_btns:
            b.setParent(None)
        self._pdo_btns = []
        css = self._PDO_TOGGLE_CSS.format(card=C['card_hi'], stroke=C['stroke'], hover=C['hover'],
                                          accent=C['accent'], panel=C['panel'], text=C['text'])
        for i, item in enumerate(pdos):
            b = QPushButton(item["label"]); b.setCheckable(True); b.setFixedHeight(40)
            b.setChecked(bool(item["checked"])); b.setStyleSheet(css)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if i == 0:                    # the 5 V fixed PDO is mandatory for a PD source
                b.setChecked(True); b.setEnabled(False)
            else:
                b.toggled.connect(lambda _=False: self._apply_pdos())   # auto-apply on toggle
            self._pdo_wrap.addWidget(b); self._pdo_btns.append(b)

    def _apply_pdos(self):
        if self._sync:
            return
        mask = 0
        for i, b in enumerate(self._pdo_btns):
            if i == 0 or b.isChecked():
                mask |= (1 << i)
        self.reqSelectPDO.emit(mask)
        self._logline(f"USB-PD advertise set → 0x{mask:02X}", C["accent"])

    def _on_pd_output(self, on):
        if self._sync:
            return
        self.reqPdOutput.emit(on)
        self._logline(f"USB-PD output {'ON' if on else 'OFF'}", C["on"] if on else C["muted"])

    def _switch_mode(self, model, page):
        self.stack.setCurrentIndex(page)
        if not self._sync:
            self.reqMode.emit(model)
            self._logline(f"mode → {['DC PSU', 'Program', 'USB-PD', 'Charge'][model]}", C["accent"])

    def _right_column(self):
        col = QVBoxLayout(); col.setSpacing(14)
        col.addWidget(self._charts(), 1)
        line = QFrame(); line.setFixedHeight(1); line.setStyleSheet(f"background:{C['stroke']};border:none;")
        col.addWidget(line)
        stats = QHBoxLayout(); stats.setSpacing(14)
        self.r_pow = _readout("POWER", "W", C["pow"]); self.r_energy = self._energy_card()
        self.temp_gauge = TempGauge(); self.r_time = _readout("RUNTIME", "", C["text"])
        stats.addWidget(self.r_pow[0], 1); stats.addWidget(self.r_energy[0], 1)
        stats.addWidget(self.temp_gauge, 1); stats.addWidget(self.r_time[0], 1)
        col.addLayout(stats)
        return col

    def _log_card(self):
        self._log_box = QFrame()
        lv = QVBoxLayout(self._log_box); lv.setContentsMargins(0, 6, 0, 0); lv.setSpacing(6)
        self._log_hdr = QPushButton("▾  EVENT LOG"); self._log_hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        self._log_hdr.setStyleSheet(
            f"QPushButton {{ text-align:left; background:transparent; border:none; padding:2px 0;"
            f" color:{C['muted']}; font-size:12px; font-weight:700; letter-spacing:1px; }}"
            f"QPushButton:hover {{ color:{C['text']}; }}")
        self._log_hdr.clicked.connect(self._toggle_log)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setFont(mono(9, bold=False))
        self.log.setStyleSheet(f"background:{C['bg']};border:1px solid {C['stroke']};border-radius:8px;")
        lv.addWidget(self._log_hdr); lv.addWidget(self.log)
        self._log_box.setFixedHeight(120)
        return self._log_box

    def _toggle_log(self):
        vis = not self.log.isVisible()
        self.log.setVisible(vis)
        self._log_hdr.setText(("▾" if vis else "▸") + "  EVENT LOG")
        self._log_box.setFixedHeight(120 if vis else 30)

    def _energy_card(self):
        ec = QFrame(); ec.setFixedHeight(72)        # flat readout; only the ↻ reset is a button
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
        wrap = QFrame(); lay = QVBoxLayout(wrap); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)  # flat
        hdr = QHBoxLayout(); hdr.setContentsMargins(0, 0, 2, 0); hdr.addStretch(1)
        self.btn_chart_reset = QPushButton("↻"); self.btn_chart_reset.setFixedSize(26, 22)
        self.btn_chart_reset.setToolTip("Clear chart history"); self.btn_chart_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_chart_reset.setStyleSheet(f"padding:0;font-weight:800;border-radius:6px;"
                                           f"background:{C['card_hi']};border:1px solid {C['stroke']};")
        self.btn_chart_reset.clicked.connect(self._reset_chart)
        hdr.addWidget(self.btn_chart_reset); lay.addLayout(hdr)
        glw = pg.GraphicsLayoutWidget(); glw.setBackground(C["bg"])
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

    def _reset_chart(self):
        self._t.clear(); self._v.clear(); self._i.clear(); self._t0 = time.monotonic()
        self.curve_v.setData([], []); self.curve_i.setData([], [])
        self._logline("chart cleared", C["accent"])

    # ---- worker
    def _start_worker(self):
        # 500 ms: the MP305 firmware stops answering if polled much faster (100 ms
        # over-polls and the device chokes after ~1-2 s); ISDT's own app polls ~3 s.
        self.thread = QThread(); self.worker = DeviceWorker(self.backend, poll_ms=500)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.state.connect(self._on_state); self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected); self.worker.error.connect(self._on_error)
        self.reqConnect.connect(self.worker.connect_device); self.reqDisconnect.connect(self.worker.disconnect_device)
        self.reqV.connect(self.worker.set_voltage); self.reqA.connect(self.worker.set_current)
        self.reqOut.connect(self.worker.set_output); self.reqCurrentOver.connect(self.worker.set_current_over)
        self.reqRemote.connect(self.worker.set_remote)
        self.reqMode.connect(self.worker.set_mode); self.reqCharge.connect(self.worker.set_charge)
        self.reqCharging.connect(self.worker.set_charging); self.reqSelectPDO.connect(self.worker.select_pdo)
        self.reqPdOutput.connect(self.worker.set_pd_output)
        self.reqProgram.connect(self.worker.run_program)
        self.thread.start()

    # ---- helpers
    def _toggle_conn(self):
        if self.btn_conn.text() == "Connect":
            self._show_overlay(); self.reqConnect.emit()
        else:
            self.reqDisconnect.emit()

    def _set_enabled(self, on):
        self._connected = on
        self._refresh_controls()

    def _refresh_controls(self):
        # controls are live only while connected AND holding remote control (else the front
        # panel has the knob — exactly like the device's remoteCon lockout)
        live = self._connected and self._remote_held
        self.stack.setEnabled(live)                       # all per-mode control panels
        for b, _ in self._mode_tabs.values():
            # can switch mode when connected, but NOT while the output is on (the device
            # won't switch modes with a live output — mirror that lockout in the UI)
            b.setEnabled(self._connected and not self._output_on)
        self.btn_remote.setEnabled(self._connected)

    def _on_remote_toggle(self, held):
        self._style_remote(held)
        self._remote_held = held
        self._refresh_controls()
        if not self._sync:
            self.reqRemote.emit(held)
            self._logline("remote control " + ("taken" if held else "released → front panel"),
                          C["accent"] if held else C["muted"])

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
        self._hide_overlay()
        self._set_status("○ Disconnected", C["muted"], tint=False)
        self.btn_conn.setText("Connect"); self._set_enabled(False); self._logline("disconnected", C["warn"])

    def _on_error(self, msg):
        self._hide_overlay()   # don't trap the user behind the loader if connecting failed
        self._logline(f"error: {msg}", C["danger"])

    def _on_state(self, st):
        on = bool(st["output"])
        self._hide_overlay()                 # first reading landed — reveal the dashboard
        if on != self._output_on:            # output on/off gates mode switching
            self._output_on = on
            self._refresh_controls()
        self._sync = True
        if not self._init_sp:
            self.ch_v.set_setpoint(st["set_voltage"]); self.ch_a.set_setpoint(st["set_current"]); self._init_sp = True
        if self.out_btn.isChecked() != on:
            self.out_btn.setChecked(on); self._logline(f"output {'ON' if on else 'OFF'}", C["on"] if on else C["muted"])
        self._sync = False

        # CV/CC is the device's regulation status (out_state: 1=CV, 2=CC, verified on
        # MP305B hardware) — read-only
        out_state = st.get("out_state", 0)
        is_cv, is_cc = out_state == 1, out_state == 2
        self.ch_v.set_live(on); self.ch_a.set_live(on)
        self.ch_v.set_measured(st["voltage"]); self.ch_a.set_measured(st["current"])
        self.ch_v.set_active(is_cv, "CV"); self.ch_a.set_active(is_cc, "CC")
        self._sync = True
        self.cov.set_index(st.get("current_over", 0))   # the over-current toggle (selectable)
        self._sync = False

        self._pow_eased.to(st["power"]); self.r_energy[1].setText(f"{st['energy']:.3f}")
        self.temp_gauge.set(st["temperature"])
        self.batt.set(st.get("battery"), st.get("charging", False))
        h, rem = divmod(int(st["working_time"]), 3600); m, s = divmod(rem, 60)
        self.r_time[1].setText(f"{h:02d}:{m:02d}:{s:02d}")

        # protections (OVP/OCP) surface as error alerts, not LEDs — exactly like the WebLink
        errs = set(st.get("errors", []))
        labels = {"errorDcOutOVP": "OVER-VOLTAGE PROTECTION tripped",
                  "errorDcOutOCP": "OVER-CURRENT PROTECTION tripped"}
        for e in errs - self._last_errors:
            self._logline(f"⚠ {labels.get(e, e)}", C["danger"])
            if e == "errorDcOutOCP":
                self.cov.pulse()              # brief red flash on the over-current toggle
        self._last_errors = errs

        self._sync_modes(st)

        t = time.monotonic() - self._t0
        self._t.append(t); self._v.append(st["voltage"]); self._i.append(st["current"])
        while self._t and self._t[0] < t - WINDOW:
            self._t.popleft(); self._v.popleft(); self._i.popleft()
        self.curve_v.setData(self._t, self._v); self.curve_i.setData(self._t, self._i)

    def _sync_modes(self, st):
        """Reflect the device's current mode + the Charge/USB-PD panels (read-back, no emit)."""
        self._sync = True
        model = st.get("model", MODE_DC)
        if model in self._mode_tabs:
            btn, page = self._mode_tabs[model]
            if not btn.isChecked():
                btn.setChecked(True)
            if self.stack.currentIndex() != page:
                self.stack.setCurrentIndex(page)
        # Charge panel
        self.ch_chem.set_index(st.get("chem", 0))
        self.ch_cells.set_setpoint(st.get("cells", 1))
        self.ch_chgA.set_setpoint(st.get("charge_current", 0.0))
        charging = bool(st.get("charging_ext", False))
        if self.chg_btn.isChecked() != charging:
            self.chg_btn.setChecked(charging)
        pct = st.get("charge_pct", 0)
        if model == MODE_CHARGE and charging:
            self.chg_stat.setText(f"{pct}%   ·   {st['voltage']:.1f} V   ·   {st['current']:.2f} A")
        else:
            self.chg_stat.setText(f"{pct}%   ·   idle" if model == MODE_CHARGE else "idle")
        # USB-PD panel: toggle-button advertise-set. Only (re)seed on/off when the row set
        # changes — don't overwrite the user's in-progress toggles on every poll.
        pdos = st.get("pdos", [])
        if len(self._pdo_btns) != len(pdos):
            self._rebuild_pdos(pdos)
            self.pd_out_btn.setChecked(bool(st.get("output", 0)))   # seed once on entering PD
        elif pdos:
            for i, item in enumerate(pdos):     # keep labels fresh (values), not toggle state
                self._pdo_btns[i].setText(item["label"])
        self._emark_lab.setText(str(st.get("emarker", "—")))
        # Program panel: step list, current step highlighted, run state on the button
        steps = st.get("program_steps", [])
        if len(self._prog_lbls) != len(steps):
            self._rebuild_program(steps)
        name = st.get("program_name", "")
        self.prog_name_lbl.setText(f"SEQUENCE  ·  {name}" if name else "SEQUENCE")
        running = bool(st.get("program_running", False)); idx = st.get("program_index", 0)
        for i, lbl in enumerate(self._prog_lbls):
            lbl.setStyleSheet(self._prog_row_css(running and i == idx))
        if self.prog_btn.isChecked() != running:
            self.prog_btn.setChecked(running)
        self._sync = False

    def closeEvent(self, e):
        # Run the disconnect (which turns output off + releases remote back to the front
        # panel) synchronously in the worker thread BEFORE quitting it — a queued
        # reqDisconnect would be dropped when the thread's event loop stops.
        try:
            if getattr(self, "thread", None) is not None and self.thread.isRunning():
                if getattr(self, "_connected", False):
                    QMetaObject.invokeMethod(self.worker, "disconnect_device",
                                             Qt.ConnectionType.BlockingQueuedConnection)
                self.thread.quit(); self.thread.wait(3000)
        except Exception:
            pass
        e.accept()


def run(prefer_real=True):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    win = MainWindow(prefer_real=prefer_real); win.show()
    return app.exec()
