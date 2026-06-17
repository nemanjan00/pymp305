"""MP305 desktop dashboard — PyQt6 + pyqtgraph, Dracula-themed.

UI/UX takes cues from ISDT's WebLink (hero current/voltage readout, mode tabs, the circular
gauge, live chart) and modernizes it for desktop. Runs against real hardware (pymp305) or a
built-in simulator.
"""
from __future__ import annotations

import math
import time
from collections import deque

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, pyqtProperty, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QFont
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QGridLayout, QDoubleSpinBox, QSlider, QButtonGroup, QSizePolicy,
)

from .theme import C, STYLESHEET
from .worker import DeviceWorker
from .backend import make_backend, SimBackend

WINDOW = 60.0  # seconds of history shown


# ---------------------------------------------------------------- widgets
class ToggleSwitch(QPushButton):
    """A modern pill toggle (checkable)."""
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(64, 34)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def getKnob(self):
        return self._pos

    def setKnob(self, v):
        self._pos = v
        self.update()

    knob = pyqtProperty(float, fget=getKnob, fset=setKnob)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor(C["on"]) if self.isChecked() else QColor(C["stroke"])
        p.setBrush(track)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 17, 17)
        d = 26
        x = 4 + self._pos * (self.width() - d - 8)
        p.setBrush(QColor(C["text"]))
        p.drawEllipse(QRectF(x, 4, d, d))


class ArcGauge(QWidget):
    """A 270° arc gauge with a big center value."""
    def __init__(self, label="", color=C["curr"]):
        super().__init__()
        self.setMinimumSize(230, 210)
        self._frac = 0.0
        self._center = "0.00"
        self._sub = ""
        self._label = label
        self._color = color

    def set(self, frac, center, sub, color=None):
        self._frac = max(0.0, min(1.0, frac))
        self._center, self._sub = center, sub
        if color:
            self._color = color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(18, 18, self.width() - 36, self.width() - 36)
        start, span = 225 * 16, -270 * 16
        p.setPen(QPen(QColor(C["stroke"]), 14, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, start, span)
        p.setPen(QPen(QColor(self._color), 14, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, start, int(span * self._frac))
        p.setPen(QColor(C["text"]))
        f = QFont(self.font()); f.setPointSize(30); f.setBold(True); p.setFont(f)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._center)
        p.setPen(QColor(C["muted"]))
        f2 = QFont(self.font()); f2.setPointSize(10); f2.setBold(True); p.setFont(f2)
        p.drawText(self.rect().adjusted(0, self.height() - 34, 0, 0),
                   Qt.AlignmentFlag.AlignHCenter, f"{self._label}\n{self._sub}")


def _label(text, cls):
    lab = QLabel(text)
    lab.setProperty("class", cls)
    return lab


def _card():
    f = QFrame()
    f.setProperty("class", "card")
    return f


class StatCard(QFrame):
    def __init__(self, title, unit, color=None):
        super().__init__()
        self.setProperty("class", "card")
        v = QVBoxLayout(self); v.setContentsMargins(18, 14, 18, 14); v.setSpacing(2)
        v.addWidget(_label(title, "cardTitle"))
        row = QHBoxLayout(); row.setSpacing(6); row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.value = _label("0.00", "bigValue")
        if color:
            self.value.setStyleSheet(f"color: {color};")
        row.addWidget(self.value)
        u = _label(unit, "unit"); row.addWidget(u, alignment=Qt.AlignmentFlag.AlignBottom)
        v.addLayout(row)

    def set(self, text):
        self.value.setText(text)


class MiniStat(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setProperty("class", "card")
        v = QVBoxLayout(self); v.setContentsMargins(16, 12, 16, 12); v.setSpacing(3)
        v.addWidget(_label(title, "cardTitle"))
        self.value = _label("—", "statValue")
        v.addWidget(self.value)

    def set(self, text):
        self.value.setText(text)


# ---------------------------------------------------------------- main window
class MainWindow(QWidget):
    reqConnect = pyqtSignal()
    reqDisconnect = pyqtSignal()
    reqV = pyqtSignal(float)
    reqA = pyqtSignal(float)
    reqOut = pyqtSignal(bool)

    def __init__(self, prefer_real=True):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("MP305 — ISDT bench supply")
        self.resize(1180, 760)
        self._syncing = False
        self._t0 = time.monotonic()
        self._t = deque(maxlen=600)
        self._v = deque(maxlen=600)
        self._i = deque(maxlen=600)

        self.backend, self.is_real = make_backend(prefer_real)
        self._build_ui()
        self._start_worker()
        # auto-connect for an instantly-live dashboard
        self.reqConnect.emit()

    # ---- UI
    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._topbar())
        root.addWidget(self._tabs())

        body = QWidget(); bl = QHBoxLayout(body)
        bl.setContentsMargins(18, 12, 18, 18); bl.setSpacing(16)
        bl.addWidget(self._controls(), 0)
        bl.addLayout(self._dashboard(), 1)
        root.addWidget(body, 1)

    def _topbar(self):
        bar = QFrame(); bar.setProperty("class", "topbar"); bar.setFixedHeight(64)
        h = QHBoxLayout(bar); h.setContentsMargins(20, 0, 18, 0); h.setSpacing(12)
        title = QVBoxLayout(); title.setSpacing(0)
        title.addWidget(_label("⚡ MP305", "h1"))
        title.addWidget(_label("smart bench power supply", "sub"))
        h.addLayout(title)
        h.addStretch(1)
        self.badge = QLabel("SIM" if not self.is_real else "USB"); self.badge.setObjectName("pill")
        self.badge.setStyleSheet(f"color:{C['warn'] if not self.is_real else C['on']};")
        self.devlabel = _label("—", "sub")
        self.status = QLabel("Disconnected"); self.status.setObjectName("pill")
        self.btn_conn = QPushButton("Connect"); self.btn_conn.setObjectName("primary")
        self.btn_conn.clicked.connect(self._toggle_conn)
        for w in (self.badge, self.devlabel, self.status, self.btn_conn):
            h.addWidget(w)
        return bar

    def _tabs(self):
        bar = QFrame(); bar.setProperty("class", "topbar"); bar.setFixedHeight(48)
        h = QHBoxLayout(bar); h.setContentsMargins(18, 6, 18, 6); h.setSpacing(6)
        self.tabgroup = QButtonGroup(self)
        for i, name in enumerate(["DC Power", "USB-PD", "Charge", "Programmable"]):
            b = QPushButton(name); b.setProperty("class", "tab"); b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if i == 0:
                b.setChecked(True)
            else:
                b.setToolTip("Available on hardware — this build wires up DC Power + telemetry")
            self.tabgroup.addButton(b, i)
            h.addWidget(b)
        h.addStretch(1)
        return bar

    def _controls(self):
        card = _card(); card.setFixedWidth(320)
        v = QVBoxLayout(card); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(14)

        out_row = QHBoxLayout()
        out_row.addWidget(_label("OUTPUT", "cardTitle"))
        out_row.addStretch(1)
        self.toggle = ToggleSwitch()
        self.toggle.toggled.connect(lambda on: None if self._syncing else self.reqOut.emit(on))
        out_row.addWidget(self.toggle)
        v.addLayout(out_row)
        self.out_state = _label("OFF", "statValue"); self.out_state.setStyleSheet(f"color:{C['off']};")
        v.addWidget(self.out_state)

        v.addSpacing(6)
        v.addWidget(_label("SET VOLTAGE", "cardTitle"))
        self.sp_v = QDoubleSpinBox(); self.sp_v.setRange(0, 30); self.sp_v.setDecimals(2)
        self.sp_v.setSingleStep(0.1); self.sp_v.setSuffix(" V")
        self.sl_v = QSlider(Qt.Orientation.Horizontal); self.sl_v.setRange(0, 3000)
        self.sp_v.valueChanged.connect(self._on_spv)
        self.sl_v.valueChanged.connect(self._on_slv)
        v.addWidget(self.sp_v); v.addWidget(self.sl_v)

        v.addSpacing(6)
        v.addWidget(_label("SET CURRENT", "cardTitle"))
        self.sp_a = QDoubleSpinBox(); self.sp_a.setRange(0, 5); self.sp_a.setDecimals(3)
        self.sp_a.setSingleStep(0.05); self.sp_a.setSuffix(" A")
        self.sl_a = QSlider(Qt.Orientation.Horizontal); self.sl_a.setRange(0, 5000)
        self.sp_a.valueChanged.connect(self._on_spa)
        self.sl_a.valueChanged.connect(self._on_sla)
        v.addWidget(self.sp_a); v.addWidget(self.sl_a)

        if isinstance(self.backend, SimBackend):
            v.addSpacing(10)
            v.addWidget(_label("SIM LOAD (Ω)", "cardTitle"))
            self.sl_load = QSlider(Qt.Orientation.Horizontal); self.sl_load.setRange(1, 100)
            self.sl_load.setValue(int(self.backend.load))
            self.sl_load.valueChanged.connect(lambda x: self.backend.set_load(float(x)))
            v.addWidget(self.sl_load)

        v.addStretch(1)
        self.btn_off = QPushButton("All Off"); self.btn_off.setObjectName("danger")
        self.btn_off.clicked.connect(lambda: self.toggle.setChecked(False))
        v.addWidget(self.btn_off)
        self._set_controls_enabled(False)
        return card

    def _dashboard(self):
        col = QVBoxLayout(); col.setSpacing(16)
        hero = QHBoxLayout(); hero.setSpacing(16)
        self.card_v = StatCard("VOLTAGE", "V", C["volt"])
        self.card_a = StatCard("CURRENT", "A", C["curr"])
        self.card_w = StatCard("POWER", "W", C["pow"])
        for c in (self.card_v, self.card_a, self.card_w):
            hero.addWidget(c, 1)
        col.addLayout(hero)

        mid = QHBoxLayout(); mid.setSpacing(16)
        gwrap = _card(); gl = QVBoxLayout(gwrap); gl.setContentsMargins(14, 14, 14, 8)
        self.gauge = ArcGauge("CURRENT", C["curr"])
        gl.addWidget(self.gauge)
        self.mode_pill = QLabel("CV"); self.mode_pill.setObjectName("pill")
        self.mode_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gl.addWidget(self.mode_pill, alignment=Qt.AlignmentFlag.AlignHCenter)
        gwrap.setFixedWidth(280)
        mid.addWidget(gwrap)
        mid.addWidget(self._charts(), 1)
        col.addLayout(mid, 1)

        stats = QHBoxLayout(); stats.setSpacing(16)
        self.s_energy = MiniStat("ENERGY")
        self.s_temp = MiniStat("TEMPERATURE")
        self.s_time = MiniStat("RUNTIME")
        self.s_err = MiniStat("STATUS")
        for s in (self.s_energy, self.s_temp, self.s_time, self.s_err):
            stats.addWidget(s, 1)
        col.addLayout(stats)
        return col

    def _charts(self):
        pg.setConfigOptions(antialias=True)
        wrap = _card(); lay = QVBoxLayout(wrap); lay.setContentsMargins(8, 8, 8, 8)
        glw = pg.GraphicsLayoutWidget()
        glw.setBackground(C["card"])
        self.p_v = glw.addPlot(row=0, col=0)
        self.p_i = glw.addPlot(row=1, col=0)
        for p, color, unit in ((self.p_v, C["volt"], "V"), (self.p_i, C["curr"], "A")):
            p.showGrid(x=True, y=True, alpha=0.12)
            p.getAxis("left").setPen(C["muted"]); p.getAxis("bottom").setPen(C["muted"])
            p.getAxis("left").setTextPen(C["muted"]); p.getAxis("bottom").setTextPen(C["muted"])
            p.setLabel("left", unit, color=C["muted"])
            p.setMouseEnabled(x=False, y=False)
        self.p_i.setXLink(self.p_v)
        self.p_v.setLabel("bottom", "")
        self.p_i.setLabel("bottom", "seconds", color=C["muted"])
        self.curve_v = self.p_v.plot(pen=pg.mkPen(C["volt"], width=2))
        self.curve_i = self.p_i.plot(pen=pg.mkPen(C["curr"], width=2))
        lay.addWidget(glw)
        return wrap

    # ---- worker plumbing
    def _start_worker(self):
        self.thread = QThread()
        self.worker = DeviceWorker(self.backend)
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

    # ---- control sync (guard against state->widget->command feedback)
    def _on_spv(self, val):
        if self._syncing: return
        self._syncing = True; self.sl_v.setValue(int(val * 100)); self._syncing = False
        self.reqV.emit(val)

    def _on_slv(self, val):
        if self._syncing: return
        self._syncing = True; self.sp_v.setValue(val / 100); self._syncing = False
        self.reqV.emit(val / 100)

    def _on_spa(self, val):
        if self._syncing: return
        self._syncing = True; self.sl_a.setValue(int(val * 1000)); self._syncing = False
        self.reqA.emit(val)

    def _on_sla(self, val):
        if self._syncing: return
        self._syncing = True; self.sp_a.setValue(val / 1000); self._syncing = False
        self.reqA.emit(val / 1000)

    def _toggle_conn(self):
        if self.btn_conn.text() == "Connect":
            self.reqConnect.emit()
        else:
            self.reqDisconnect.emit()

    def _set_controls_enabled(self, on):
        for w in (self.toggle, self.sp_v, self.sl_v, self.sp_a, self.sl_a, self.btn_off):
            w.setEnabled(on)

    # ---- worker callbacks
    def _on_connected(self, info):
        self.status.setText("● Connected"); self.status.setStyleSheet(f"color:{C['on']};")
        self.devlabel.setText(f"{info.get('model','MP305')}  ·  {info.get('fw','')}")
        self.badge.setText(info.get("transport", "USB").upper().replace("SIMULATOR", "SIM"))
        self.btn_conn.setText("Disconnect")
        self._set_controls_enabled(True)
        self._init_setpoints = False

    def _on_disconnected(self, _):
        self.status.setText("Disconnected"); self.status.setStyleSheet(f"color:{C['muted']};")
        self.btn_conn.setText("Connect")
        self._set_controls_enabled(False)

    def _on_error(self, msg):
        self.s_err.set(f"⚠ {msg[:40]}")
        self.s_err.value.setStyleSheet(f"color:{C['danger']};")

    def _on_state(self, st):
        self._syncing = True
        # initialise setpoints from the device once
        if not getattr(self, "_init_setpoints", False):
            self.sp_v.setValue(st["set_voltage"]); self.sp_a.setValue(st["set_current"])
            self._init_setpoints = True
        self.toggle.setChecked(bool(st["output"]))
        self._syncing = False

        self.card_v.set(f"{st['voltage']:.2f}")
        self.card_a.set(f"{st['current']:.3f}")
        self.card_w.set(f"{st['power']:.2f}")
        on = bool(st["output"])
        self.out_state.setText("ON" if on else "OFF")
        self.out_state.setStyleSheet(f"color:{C['on'] if on else C['off']};")

        seta = max(1e-6, st["set_current"])
        self.gauge.set(st["current"] / seta, f"{st['current']:.3f}", "AMPS", C["curr"])
        cc = st.get("mode") == "CC"
        self.mode_pill.setText("CONSTANT CURRENT" if cc else "CONSTANT VOLTAGE")
        self.mode_pill.setStyleSheet(f"color:{C['warn'] if cc else C['on']};")

        self.s_energy.set(f"{st['energy']:.3f} Wh")
        self.s_temp.set(f"{st['temperature']} °C")
        h, rem = divmod(int(st["working_time"]), 3600); m, s = divmod(rem, 60)
        self.s_time.set(f"{h:02d}:{m:02d}:{s:02d}")
        if st.get("errors"):
            self.s_err.set("⚠ " + ", ".join(st["errors"])[:36]); self.s_err.value.setStyleSheet(f"color:{C['danger']};")
        else:
            self.s_err.set("OK"); self.s_err.value.setStyleSheet(f"color:{C['on']};")

        t = time.monotonic() - self._t0
        self._t.append(t); self._v.append(st["voltage"]); self._i.append(st["current"])
        while self._t and self._t[0] < t - WINDOW:
            self._t.popleft(); self._v.popleft(); self._i.popleft()
        self.curve_v.setData(self._t, self._v)
        self.curve_i.setData(self._t, self._i)

    def closeEvent(self, e):
        try:
            self.reqDisconnect.emit()
            self.thread.quit(); self.thread.wait(800)
        except Exception:
            pass
        e.accept()


def run(prefer_real=True):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    win = MainWindow(prefer_real=prefer_real)
    win.show()
    return app.exec()
