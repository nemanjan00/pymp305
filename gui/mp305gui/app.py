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


class SegToggle(QWidget):
    """A real mutually-exclusive segmented toggle (Bootstrap-style joined cells). Click a
    cell to select it; each shows a code + a smaller description (like WebLink's cells).
    Used for over-current behaviour: CC (current-limit) | OCP (trip) = `currentOver` 0/1."""
    selected = pyqtSignal(int)

    def __init__(self, cells):                       # cells = [(code, description, color), ...]
        super().__init__(); self._cells = cells; self._idx = 0
        self.setMinimumHeight(58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(110 * len(cells))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_index(self, i):
        self._idx = max(0, min(len(self._cells) - 1, int(i))); self.update()

    def mousePressEvent(self, e):
        i = int(e.position().x() // (self.width() / len(self._cells)))
        i = max(0, min(len(self._cells) - 1, i))
        if i != self._idx:
            self._idx = i; self.update(); self.selected.emit(i)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); r = 13; n = len(self._cells); cw = w / n   # card-shaped
        outer = QRectF(0.75, 0.75, w - 1.5, h - 1.5)
        code_pt = int(max(14, min(26, h * 0.20)))    # scale the code with the cell height
        sub_pt = int(max(8, min(10, h * 0.075)))      # cap: the descriptions are long, must not clip
        fcode = QFont(); fcode.setPointSize(code_pt); fcode.setBold(True)
        fsub = QFont(); fsub.setPointSize(sub_pt); fsub.setBold(True)
        mid = h / 2
        for i, (code, sub, col) in enumerate(self._cells):
            x = i * cw; active = i == self._idx
            if active:
                p.save(); p.setClipRect(QRectF(x, 0, cw, h))
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(col)); p.drawRoundedRect(outer, r, r)
                p.restore()
            code_col = QColor(C["panel"]) if active else QColor(C["muted"])
            sub_col = QColor(C["panel"]) if active else QColor(C["muted"])
            p.setFont(fcode); p.setPen(code_col)
            p.drawText(QRectF(x, mid - h * 0.30, cw, h * 0.32), Qt.AlignmentFlag.AlignCenter, code)
            p.setFont(fsub); p.setPen(sub_col)
            p.drawText(QRectF(x + 3, mid + h * 0.02, cw - 6, h * 0.26), Qt.AlignmentFlag.AlignCenter, sub)
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
        super().__init__(); self.setFixedHeight(72); self._max = vmax        # flat readout
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
                self.meas.setText(f"{val:.{self._dec}f}")

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
            self.meas.setText(f"{self._meas_val:.{self._dec}f}")
            self.setlab.setText(f"SET  {self._set:.{self._dec}f} {self._unit}")
        else:
            self.meas.setText(f"{self._set:.{self._dec}f}")
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

    def __init__(self, prefer_real=True):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("MP305 — ISDT bench supply")
        self.resize(1180, 860)
        self._sync = False; self._init_sp = False; self._last_errors = set()
        self._connected = False; self._remote_held = True
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

        # over-current behaviour is a SETTING → it lives with the controls (matches WebLink's
        # currentOver toggle in the control area): CC = current-limit, OCP = trip the output.
        oc = QFrame()        # no card: the toggle has its own border
        ov = QVBoxLayout(oc); ov.setContentsMargins(2, 4, 2, 0); ov.setSpacing(6)
        ov.addWidget(_lab("OVER-CURRENT", "cardTitle"))
        self.cov = SegToggle([("CC", "Constant Current", C["warn"]),
                              ("OCP", "Overcurrent Protection", C["danger"])])
        self.cov.selected.connect(lambda i: None if self._sync else self.reqCurrentOver.emit(i))
        ov.addWidget(self.cov, 1)
        v.addWidget(oc, 1)        # the toggle grows to fill, so the left column matches the right

        if isinstance(self.backend, SimBackend):
            self.ch_load = ChannelCard("SIM LOAD", "Ω", 100.0, C["pink"], 0, measured=False)
            self.ch_load.set_setpoint(self.backend.load)
            self.ch_load.changed.connect(self.backend.set_load)
            v.addWidget(self.ch_load)

        # Bootstrap-style card: a title header, a divider, then a flush edge-to-edge button
        # group (only the group's outer bottom corners are rounded).
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

        self._set_enabled(False)        # the over-current card (above) absorbs the slack now
        return col

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
        wrap = QFrame(); lay = QVBoxLayout(wrap); lay.setContentsMargins(0, 0, 0, 0)   # flat
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

    # ---- worker
    def _start_worker(self):
        self.thread = QThread(); self.worker = DeviceWorker(self.backend, poll_ms=100)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.state.connect(self._on_state); self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected); self.worker.error.connect(self._on_error)
        self.reqConnect.connect(self.worker.connect_device); self.reqDisconnect.connect(self.worker.disconnect_device)
        self.reqV.connect(self.worker.set_voltage); self.reqA.connect(self.worker.set_current)
        self.reqOut.connect(self.worker.set_output); self.reqCurrentOver.connect(self.worker.set_current_over)
        self.reqRemote.connect(self.worker.set_remote)
        self.thread.start()

    # ---- helpers
    def _toggle_conn(self):
        (self.reqConnect if self.btn_conn.text() == "Connect" else self.reqDisconnect).emit()

    def _set_enabled(self, on):
        self._connected = on
        self._refresh_controls()

    def _refresh_controls(self):
        # controls are live only while connected AND holding remote control (else the front
        # panel has the knob — exactly like the device's remoteCon lockout)
        live = self._connected and self._remote_held
        for w in (self.out_btn, self.ch_v, self.ch_a, self.cov):
            w.setEnabled(live)
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
        self._set_status("○ Disconnected", C["muted"], tint=False)
        self.btn_conn.setText("Connect"); self._set_enabled(False); self._logline("disconnected", C["warn"])

    def _on_error(self, msg):
        self._logline(f"error: {msg}", C["danger"])

    def _on_state(self, st):
        on = bool(st["output"])
        self._sync = True
        if not self._init_sp:
            self.ch_v.set_setpoint(st["set_voltage"]); self.ch_a.set_setpoint(st["set_current"]); self._init_sp = True
        if self.out_btn.isChecked() != on:
            self.out_btn.setChecked(on); self._logline(f"output {'ON' if on else 'OFF'}", C["on"] if on else C["muted"])
        self._sync = False

        # CV/CC is the device's regulation status (out_state: 2=CV, 1=CC) — read-only
        out_state = st.get("out_state", 0)
        is_cv, is_cc = out_state == 2, out_state == 1
        self.ch_v.set_live(on); self.ch_a.set_live(on)
        self.ch_v.set_measured(st["voltage"]); self.ch_a.set_measured(st["current"])
        self.ch_v.set_active(is_cv, "CV"); self.ch_a.set_active(is_cc, "CC")
        self._sync = True
        self.cov.set_index(st.get("current_over", 0))   # the over-current toggle (selectable)
        self._sync = False

        self.r_pow[1].setText(f"{st['power']:.2f}"); self.r_energy[1].setText(f"{st['energy']:.3f}")
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
        self._last_errors = errs

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
