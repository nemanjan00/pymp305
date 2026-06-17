"""MP305 desktop dashboard — PyQt6 + pyqtgraph, Dracula-themed.

Design priorities (v2):
  * pointer-ONLY operation (target lab PC has a trackball, no keyboard): every value is set
    with big stepper chips + scroll-nudge + one-click presets; no typing/Tab/Enter required.
  * disciplined layout — controls keep their natural height, the chart absorbs slack.
  * dense single-view instrument: hero V/I/P, dual CV/CC gauges, status lamps, event log.
UI/UX takes cues from ISDT's WebLink + the hardware's single-screen density.
"""
from __future__ import annotations

import time
from collections import deque

import pyqtgraph as pg
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QPropertyAnimation, pyqtProperty, QRectF)
from PyQt6.QtGui import QColor, QPainter, QPen, QFont
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QPlainTextEdit, QSizePolicy, QDialog, QGridLayout,
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


def _card():
    f = QFrame(); f.setProperty("class", "card")
    f.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    return f


# ---------------------------------------------------------------- widgets
class ToggleSwitch(QPushButton):
    def __init__(self):
        super().__init__()
        self.setCheckable(True); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(66, 36)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self); self._anim.setDuration(150)
        self.toggled.connect(lambda on: (self._anim.stop(), self._anim.setStartValue(self._pos),
                                         self._anim.setEndValue(1.0 if on else 0.0), self._anim.start()))

    def getKnob(self): return self._pos
    def setKnob(self, v): self._pos = v; self.update()
    knob = pyqtProperty(float, fget=getKnob, fset=setKnob)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(C["on"]) if self.isChecked() else QColor(C["stroke"]))
        p.setPen(Qt.PenStyle.NoPen); p.drawRoundedRect(self.rect(), 18, 18)
        d = 28; x = 4 + self._pos * (self.width() - d - 8)
        p.setBrush(QColor(C["text"])); p.drawEllipse(QRectF(x, 4, d, d))


class ArcGauge(QWidget):
    """270° arc gauge with center numeral, quantity color, and a CV/CC tag."""
    def __init__(self, label, color):
        super().__init__()
        self.setMinimumSize(150, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._frac = 0.0; self._center = "0.00"; self._label = label
        self._color = color; self._tag = ""; self._active = False

    def set(self, frac, center, tag="", active=False):
        self._frac = max(0.0, min(1.0, frac)); self._center = center
        self._tag = tag; self._active = active; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height() - 8)
        m = 12; rect = QRectF((self.width() - side) / 2 + m, m, side - 2 * m, side - 2 * m)
        start, span = 225 * 16, -270 * 16
        p.setPen(QPen(QColor(C["stroke"]), 12, cap=Qt.PenCapStyle.RoundCap)); p.drawArc(rect, start, span)
        pen = QPen(QColor(self._color), 12, cap=Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.drawArc(rect, start, int(span * self._frac))
        p.setPen(QColor(C["text"])); f = mono(22); p.setFont(f)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._center)
        p.setPen(QColor(C["muted"])); f2 = QFont(); f2.setPointSize(9); f2.setBold(True); p.setFont(f2)
        p.drawText(rect.adjusted(0, rect.height() * 0.62, 0, 0),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self._label)
        if self._tag:
            p.setPen(QColor(self._color if self._active else C["muted"]))
            ft = QFont(); ft.setPointSize(10); ft.setBold(True); p.setFont(ft)
            p.drawText(self.rect().adjusted(0, 6, -8, 0),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self._tag)


class Lamp(QWidget):
    def __init__(self, label):
        super().__init__()
        self.setFixedHeight(20); self._on = False; self._color = C["off"]; self._label = label

    def set(self, on, color):
        self._on = on; self._color = color; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(self._color) if self._on else QColor(C["stroke"])
        p.setBrush(col); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QRectF(0, 4, 12, 12))
        p.setPen(QColor(C["text"] if self._on else C["muted"]))
        f = QFont(); f.setPointSize(9); f.setBold(True); p.setFont(f)
        p.drawText(self.rect().adjusted(20, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self._label)


class Chip(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self.setFixedHeight(40); self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        f = QFont(); f.setPointSize(10); f.setBold(True); self.setFont(f)
        self.setStyleSheet("padding: 0 2px;")   # override the global 16px button padding
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class Keypad(QDialog):
    """Big pointer-operable numeric keypad for exact entry without a keyboard."""
    def __init__(self, title, value, unit, vmax, dec, units, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title); self.setModal(True)
        self._max = vmax; self._dec = dec
        self._units = units; self._mult = units[0][1]
        self._s = f"{value:.{dec}f}" if dec else f"{int(value)}"
        v = QVBoxLayout(self); v.setContentsMargins(16, 16, 16, 16); v.setSpacing(10)
        v.addWidget(_lab(f"{title}  ({unit}, max {vmax:g})", "cardTitle"))
        self._disp = QLabel(self._s); self._disp.setFont(mono(30))
        self._disp.setStyleSheet(f"color:{C['accent']};background:{C['bg']};"
                                 f"border:1px solid {C['stroke']};border-radius:10px;padding:8px 12px;")
        self._disp.setAlignment(Qt.AlignmentFlag.AlignRight)
        v.addWidget(self._disp)
        grid = QGridLayout(); grid.setSpacing(8)
        keys = ["7", "8", "9", "4", "5", "6", "1", "2", "3", ".", "0", "⌫"]
        for i, k in enumerate(keys):
            b = QPushButton(k); b.setFixedSize(78, 56); b.setFont(mono(16))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, key=k: self._key(key))
            grid.addWidget(b, i // 3, i % 3)
        v.addLayout(grid)
        # unit buttons commit the entry (e.g. "9" then "V", or "1500" then "mA")
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


class Setpoint(QFrame):
    """Pointer-only setpoint editor: tap the value for an on-screen keypad (exact entry),
    stepper chips for fine nudges, scroll-wheel to bump, presets for instant rails. No keyboard."""
    changed = pyqtSignal(float)

    def __init__(self, title, unit, vmax, color, steps):
        super().__init__()
        self.setProperty("class", "card")
        self._val = 0.0; self._max = vmax; self._steps = steps
        self._title, self._unit = title, unit
        _decs = [len(str(s).split(".")[-1]) for s in steps if "." in str(s)]
        self._dec = max(_decs) if _decs else 0
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(self); v.setContentsMargins(16, 12, 16, 12); v.setSpacing(8)
        v.addWidget(_lab(title, "cardTitle"))
        row = QHBoxLayout(); row.setSpacing(6); row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._val_btn = QPushButton("0.00"); self._val_btn.setFont(mono(28))
        self._val_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._val_btn.setToolTip("Tap for keypad (exact entry)")
        self._val_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{color};text-align:left;padding:0;}}"
            f"QPushButton:hover{{color:{C['accent_hi']};}}")
        self._val_btn.clicked.connect(self._open_keypad)
        row.addWidget(self._val_btn)
        u = QLabel(unit); u.setProperty("class", "unit"); row.addWidget(u, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        v.addLayout(row)
        chips = QHBoxLayout(); chips.setSpacing(6)
        for s in steps:
            b = Chip(f"−{s:g}"); b.clicked.connect(lambda _, d=-s: self._nudge(d)); chips.addWidget(b)
        for s in reversed(steps):
            b = Chip(f"+{s:g}"); b.clicked.connect(lambda _, d=s: self._nudge(d)); chips.addWidget(b)
        v.addLayout(chips)

    _UNITS = {"V": [("V", 1.0), ("mV", 0.001)], "A": [("A", 1.0), ("mA", 0.001)]}

    def _open_keypad(self):
        units = self._UNITS.get(self._unit, [(self._unit, 1.0)])
        dlg = Keypad(self._title, self._val, self._unit, self._max, self._dec, units, self)
        if dlg.exec():
            self.set_value(dlg.value(), emit=True)

    def value(self): return self._val

    def set_value(self, v, emit=False):
        self._val = max(0.0, min(self._max, round(v, self._dec)))
        self._val_btn.setText(f"{self._val:.{self._dec}f}")
        if emit:
            self.changed.emit(self._val)

    def _nudge(self, d): self.set_value(self._val + d, emit=True)

    def wheelEvent(self, e):
        self._nudge(self._steps[1] * (1 if e.angleDelta().y() > 0 else -1))


# ---------------------------------------------------------------- main window
class MainWindow(QWidget):
    reqConnect = pyqtSignal(); reqDisconnect = pyqtSignal()
    reqV = pyqtSignal(float); reqA = pyqtSignal(float); reqOut = pyqtSignal(bool)

    def __init__(self, prefer_real=True):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("MP305 — ISDT bench supply")
        self.resize(1280, 900)
        self._sync = False; self._init_sp = False
        self._t0 = time.monotonic()
        self._t = deque(maxlen=900); self._v = deque(maxlen=900); self._i = deque(maxlen=900)
        self.backend, self.is_real = make_backend(prefer_real)
        self._build_ui(); self._start_worker()
        self.reqConnect.emit()

    # ---- layout
    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._topbar())
        body = QWidget(); bl = QHBoxLayout(body)
        bl.setContentsMargins(14, 12, 14, 14); bl.setSpacing(14)
        bl.addWidget(self._left_column(), 0)
        bl.addLayout(self._dashboard(), 1)
        root.addWidget(body, 1)

    def _topbar(self):
        bar = QFrame(); bar.setProperty("class", "topbar"); bar.setFixedHeight(62)
        h = QHBoxLayout(bar); h.setContentsMargins(18, 0, 16, 0); h.setSpacing(10)
        t = QVBoxLayout(); t.setSpacing(0)
        t.addWidget(_lab("⚡ MP305", "h1")); t.addWidget(_lab("smart bench power supply", "sub"))
        h.addLayout(t); h.addStretch(1)
        self.badge = QLabel("SIM" if not self.is_real else "USB"); self.badge.setObjectName("pill")
        self.badge.setStyleSheet(f"color:{C['warn'] if not self.is_real else C['on']};")
        self.devlabel = _lab("—", "sub")
        self.status = QLabel("Disconnected"); self.status.setObjectName("pill")
        self.btn_remote = QPushButton("Remote"); self.btn_remote.setCheckable(True); self.btn_remote.setChecked(True)
        self.btn_remote.setToolTip("Take / release remote control")
        self.btn_conn = QPushButton("Connect"); self.btn_conn.setObjectName("primary")
        self.btn_conn.clicked.connect(self._toggle_conn)
        for w in (self.badge, self.devlabel, self.status, self.btn_remote, self.btn_conn):
            h.addWidget(w)
        return bar

    def _left_column(self):
        col = QFrame(); col.setFixedWidth(340)
        v = QVBoxLayout(col); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)

        # output card
        oc = _card(); oc.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        ov = QVBoxLayout(oc); ov.setContentsMargins(16, 12, 16, 12); ov.setSpacing(8)
        r = QHBoxLayout(); r.addWidget(_lab("OUTPUT", "cardTitle")); r.addStretch(1)
        self.toggle = ToggleSwitch()
        self.toggle.toggled.connect(lambda on: None if self._sync else self.reqOut.emit(on))
        r.addWidget(self.toggle); ov.addLayout(r)
        self.out_state = QLabel("OFF"); self.out_state.setFont(mono(15)); self.out_state.setStyleSheet(f"color:{C['off']};")
        ov.addWidget(self.out_state)
        v.addWidget(oc)

        # setpoints (pointer-only)
        self.sp_v = Setpoint("SET VOLTAGE", "V", 30.0, C["volt"], [1, 0.1, 0.01])
        self.sp_a = Setpoint("SET CURRENT", "A", 5.0, C["curr"], [1, 0.1, 0.01])
        self.sp_v.changed.connect(lambda x: None if self._sync else self.reqV.emit(x))
        self.sp_a.changed.connect(lambda x: None if self._sync else self.reqA.emit(x))
        v.addWidget(self.sp_v); v.addWidget(self.sp_a)

        # presets (one-click recall — the most trackball-friendly way to set V)
        pc = _card(); pc.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        pv = QVBoxLayout(pc); pv.setContentsMargins(16, 10, 16, 12); pv.setSpacing(8)
        pv.addWidget(_lab("PRESETS", "cardTitle"))
        prow = QHBoxLayout(); prow.setSpacing(6)
        for volts in (3.3, 5, 9, 12, 20):
            b = Chip(f"{volts:g}V"); b.clicked.connect(lambda _, x=volts: self.sp_v.set_value(x, emit=True))
            prow.addWidget(b)
        pv.addLayout(prow)
        v.addWidget(pc)

        if isinstance(self.backend, SimBackend):
            self.sp_load = Setpoint("SIM LOAD", "Ω", 100.0, C["pink"], [10, 1])
            self.sp_load.set_value(self.backend.load)
            self.sp_load.changed.connect(self.backend.set_load)
            v.addWidget(self.sp_load)

        # event log absorbs the leftover vertical space (not the controls)
        lc = _card(); lv = QVBoxLayout(lc); lv.setContentsMargins(14, 10, 14, 12); lv.setSpacing(6)
        lv.addWidget(_lab("EVENT LOG", "cardTitle"))
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setFont(mono(9, bold=False))
        self.log.setStyleSheet(f"background:{C['bg']};border:1px solid {C['stroke']};border-radius:8px;")
        lv.addWidget(self.log)
        v.addWidget(lc, 1)

        self.btn_off = QPushButton("◼  ALL OFF"); self.btn_off.setObjectName("danger")
        self.btn_off.setFixedHeight(42)
        self.btn_off.clicked.connect(lambda: self.toggle.setChecked(False))
        v.addWidget(self.btn_off)
        self._set_enabled(False)
        return col

    def _dashboard(self):
        col = QVBoxLayout(); col.setSpacing(14)
        hero = QHBoxLayout(); hero.setSpacing(14)
        self.card_v = self._readout("VOLTAGE", "V", C["volt"])
        self.card_a = self._readout("CURRENT", "A", C["curr"])
        self.card_w = self._readout("POWER", "W", C["pow"])
        for c in (self.card_v[0], self.card_a[0], self.card_w[0]):
            c.setFixedHeight(104); hero.addWidget(c, 1)
        col.addLayout(hero)

        mid = QHBoxLayout(); mid.setSpacing(14)
        # gauges + lamps card
        gc = _card(); gc.setFixedWidth(300)
        gv = QVBoxLayout(gc); gv.setContentsMargins(14, 14, 14, 12); gv.setSpacing(8)
        grow = QHBoxLayout(); grow.setSpacing(8)
        self.gauge_v = ArcGauge("VOLTS", C["volt"]); self.gauge_a = ArcGauge("AMPS", C["curr"])
        grow.addWidget(self.gauge_v); grow.addWidget(self.gauge_a)
        gv.addLayout(grow, 1)
        lamps = QHBoxLayout(); lamps.setSpacing(10)
        self.lamp_out = Lamp("OUT"); self.lamp_cv = Lamp("CV"); self.lamp_cc = Lamp("CC")
        self.lamp_ovp = Lamp("OVP"); self.lamp_ocp = Lamp("OCP")
        for L in (self.lamp_out, self.lamp_cv, self.lamp_cc, self.lamp_ovp, self.lamp_ocp):
            lamps.addWidget(L)
        gv.addLayout(lamps)
        mid.addWidget(gc)
        mid.addWidget(self._charts(), 1)
        col.addLayout(mid, 1)

        stats = QHBoxLayout(); stats.setSpacing(14)
        self.s_energy = self._readout("ENERGY", "", C["text"], small=True)
        self.s_temp = self._readout("TEMP", "", C["text"], small=True)
        self.s_time = self._readout("RUNTIME", "", C["text"], small=True)
        self.s_set = self._readout("SETPOINT", "", C["muted"], small=True)
        for s in (self.s_energy, self.s_temp, self.s_time, self.s_set):
            s[0].setFixedHeight(72); stats.addWidget(s[0], 1)
        col.addLayout(stats)
        return col

    def _readout(self, title, unit, color, small=False):
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(16, 10, 16, 10); v.setSpacing(2)
        v.addWidget(_lab(title, "cardTitle"))
        row = QHBoxLayout(); row.setSpacing(6); row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        val = QLabel("—"); val.setFont(mono(16 if small else 38)); val.setStyleSheet(f"color:{color};")
        row.addWidget(val)
        if unit:
            u = QLabel(unit); u.setProperty("class", "unit"); row.addWidget(u, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1); v.addLayout(row)
        return card, val

    def _charts(self):
        pg.setConfigOptions(antialias=True)
        wrap = _card(); lay = QVBoxLayout(wrap); lay.setContentsMargins(8, 8, 8, 8)
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
        lay.addWidget(glw)
        return wrap

    # ---- worker
    def _start_worker(self):
        self.thread = QThread(); self.worker = DeviceWorker(self.backend, poll_ms=100)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.state.connect(self._on_state)
        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected)
        self.worker.error.connect(self._on_error)
        self.reqConnect.connect(self.worker.connect_device)
        self.reqDisconnect.connect(self.worker.disconnect_device)
        self.reqV.connect(self.worker.set_voltage)
        self.reqA.connect(self.worker.set_current)
        self.reqOut.connect(self.worker.set_output)
        self.thread.start()

    def _toggle_conn(self):
        (self.reqConnect if self.btn_conn.text() == "Connect" else self.reqDisconnect).emit()

    def _set_enabled(self, on):
        for w in (self.toggle, self.sp_v, self.sp_a, self.btn_off):
            w.setEnabled(on)

    def _logline(self, msg, color):
        ts = time.strftime("%H:%M:%S")
        self.log.appendHtml(f'<span style="color:{C["muted"]}">{ts}</span> '
                            f'<span style="color:{color}">{msg}</span>')

    # ---- callbacks
    def _on_connected(self, info):
        self.status.setText("● Connected"); self.status.setStyleSheet(f"color:{C['on']};")
        self.devlabel.setText(f"{info.get('model','MP305')}  ·  {info.get('fw','')}")
        self.badge.setText(info.get("transport", "USB").upper().replace("SIMULATOR", "SIM"))
        self.btn_conn.setText("Disconnect"); self._set_enabled(True); self._init_sp = False
        self._logline(f"connected — {info.get('model','MP305')} {info.get('fw','')}", C["on"])

    def _on_disconnected(self, _):
        self.status.setText("Disconnected"); self.status.setStyleSheet(f"color:{C['muted']};")
        self.btn_conn.setText("Connect"); self._set_enabled(False)
        self._logline("disconnected", C["warn"])

    def _on_error(self, msg):
        self._logline(f"error: {msg}", C["danger"])

    def _on_state(self, st):
        on = bool(st["output"]); cc = st.get("mode") == "CC"
        self._sync = True
        if not self._init_sp:
            self.sp_v.set_value(st["set_voltage"]); self.sp_a.set_value(st["set_current"])
            self._init_sp = True
        if self.toggle.isChecked() != on:
            self.toggle.setChecked(on)
            self._logline(f"output {'ON' if on else 'OFF'}", C["on"] if on else C["muted"])
        self._sync = False

        self.card_v[1].setText(f"{st['voltage']:.2f}")
        self.card_a[1].setText(f"{st['current']:.3f}")
        self.card_w[1].setText(f"{st['power']:.2f}")
        self.out_state.setText("ON" if on else "OFF")
        self.out_state.setStyleSheet(f"color:{C['on'] if on else C['off']};")

        self.gauge_v.set(st["voltage"] / 30.0, f"{st['voltage']:.2f}",
                         "CV" if on else "", active=(on and not cc))
        seta = max(1e-6, st["set_current"])
        self.gauge_a.set(st["current"] / seta, f"{st['current']:.3f}",
                         "CC" if on else "", active=(on and cc))

        self.lamp_out.set(on, C["on"])
        self.lamp_cv.set(on and not cc, C["on"]); self.lamp_cc.set(on and cc, C["warn"])
        errs = st.get("errors", [])
        self.lamp_ovp.set("errorDcOutOVP" in errs, C["danger"])
        self.lamp_ocp.set("errorDcOutOCP" in errs, C["danger"])

        self.s_energy[1].setText(f"{st['energy']:.3f} Wh")
        self.s_temp[1].setText(f"{st['temperature']} °C")
        h, rem = divmod(int(st["working_time"]), 3600); m, s = divmod(rem, 60)
        self.s_time[1].setText(f"{h:02d}:{m:02d}:{s:02d}")
        self.s_set[1].setText(f"{st['set_voltage']:.2f}V / {st['set_current']:.3f}A")

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
