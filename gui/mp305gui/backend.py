"""Backends the GUI can drive: the real pymp305 device, or a built-in simulator so the
app runs and demos with no hardware (and honours the 'not yet hardware-validated' reality).

Both expose the same tiny surface the worker needs:
    connect() -> dict(info)   read() -> dict(state)   apply(v=None, a=None, on=None)   close()
"""
from __future__ import annotations

import math
import os
import sys
import time

# make the sibling library importable when run from a checkout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "python"))


def _state_to_dict(st, info_name=None) -> dict:
    cc = bool(st.output) and st.current >= max(0.0, st.set_current) - 1e-3
    return {
        "voltage": st.voltage, "current": st.current, "power": st.power,
        "set_voltage": st.set_voltage, "set_current": st.set_current,
        "output": int(st.output), "model": st.model, "temperature": st.temperature,
        "energy": st.energy, "working_time": st.working_time,
        "errors": list(getattr(st, "errors", []) or []),
        "mode": "CC" if cc else "CV",
    }


class RealBackend:
    """Wraps pymp305.MP305 over USB-HID."""
    name = "USB"

    def __init__(self):
        self._psu = None
        self._name = "MP305"

    def connect(self) -> dict:
        from pymp305 import MP305
        self._psu = MP305.open()
        info = self._psu.hardware_info()
        self._name = info.device_name or "MP305"
        return {"model": self._name, "fw": info.app_version, "transport": "USB-HID"}

    def read(self) -> dict:
        return _state_to_dict(self._psu.read_state(), self._name)

    def apply(self, v=None, a=None, on=None):
        self._psu.set_output(voltage=v, current=a, on=on)

    def close(self):
        try:
            if self._psu:
                self._psu.output_off(); self._psu.release_remote(); self._psu.close()
        except Exception:
            pass


class SimBackend:
    """A plausible PSU simulator: CV/CC against a switchable load, noise, thermal drift,
    energy integration — enough to make the dashboard and charts feel real."""
    name = "SIM"

    def __init__(self, load_ohms: float = 8.0):
        self.set_v = 5.0
        self.set_a = 1.0
        self.on = False
        self.load = load_ohms
        self.temp = 25.0
        self.energy_wh = 0.0
        self._t0 = time.monotonic()
        self._last = self._t0
        self._n = 0

    def connect(self) -> dict:
        self._t0 = self._last = time.monotonic()
        return {"model": "MP305B", "fw": "1.6.0.48 (sim)", "transport": "Simulator"}

    def read(self) -> dict:
        now = time.monotonic()
        dt = max(1e-3, now - self._last)
        self._last = now
        self._n += 1
        ripple = 0.01 * math.sin(self._n / 6.0)
        if self.on:
            i_cv = self.set_v / self.load            # current the load would draw at set V
            cc = i_cv > self.set_a
            current = self.set_a if cc else i_cv
            voltage = (self.set_a * self.load) if cc else self.set_v
        else:
            voltage = current = 0.0
            cc = False
        voltage = max(0.0, voltage * (1 + ripple)) if self.on else 0.0
        current = max(0.0, current * (1 + ripple)) if self.on else 0.0
        power = voltage * current
        self.energy_wh += power * dt / 3600.0
        target_temp = 25.0 + power * 1.4
        self.temp += (target_temp - self.temp) * min(1.0, dt * 0.5)
        return {
            "voltage": round(voltage, 2), "current": round(current, 3), "power": round(power, 2),
            "set_voltage": self.set_v, "set_current": self.set_a,
            "output": int(self.on), "model": 0, "temperature": round(self.temp),
            "energy": round(self.energy_wh, 3), "working_time": int(now - self._t0),
            "errors": [], "mode": "CC" if (self.on and cc) else "CV",
        }

    def apply(self, v=None, a=None, on=None):
        if v is not None:
            self.set_v = max(0.0, min(30.0, float(v)))
        if a is not None:
            self.set_a = max(0.0, min(5.0, float(a)))
        if on is not None:
            self.on = bool(on)

    def set_load(self, ohms: float):
        self.load = max(0.1, ohms)

    def close(self):
        self.on = False


def make_backend(prefer_real: bool):
    """Return (backend, is_real). Falls back to the simulator if no device / not requested."""
    if prefer_real:
        try:
            from pymp305 import MP305
            if MP305.list_devices():
                return RealBackend(), True
        except Exception:
            pass
    return SimBackend(), False
