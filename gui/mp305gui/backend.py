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
    return {
        "voltage": st.voltage, "current": st.current, "power": st.power,
        "set_voltage": st.set_voltage, "set_current": st.set_current,
        "output": int(st.output), "model": st.model, "temperature": st.temperature,
        "energy": st.energy, "working_time": st.working_time,
        "battery": st.percentage, "battery_state": st.battery_state,
        "charging": st.battery_state == 1,
        "out_state": st.out_state,          # device regulation status: 1=CC, 2=CV
        "current_over": st.current_over,     # over-current behaviour setting: 0=CC, 1=OCP
        "errors": list(getattr(st, "errors", []) or []),
        "mode": "CC" if st.out_state == 1 else "CV",
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

    def set_current_over(self, mode):
        from pymp305 import ControlCommand
        st = self._psu.read_state()
        self._psu.control(ControlCommand(
            remote_con=1, set_voltage=st.set_voltage, set_current=st.set_current,
            real_change=3, voltage_slow=st.voltage_slow, current_over=int(mode),
            output=st.output, model=st.model))

    def reset_energy(self):
        pass   # no device command mapped for the energy/time reset yet

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
        self.batt = 86.0
        self.charging = True
        self.current_over = 0      # 0 = CC (current-limit), 1 = OCP (trip)
        self._ocp_trip = False
        self._t0 = time.monotonic()
        self._last = self._t0
        self._n = 0

    def toggle_charging(self):
        self.charging = not self.charging
        return self.charging

    def set_current_over(self, mode):
        self.current_over = int(mode)

    def reset_energy(self):
        self.energy_wh = 0.0

    def connect(self) -> dict:
        self._t0 = self._last = time.monotonic()
        return {"model": "MP305B", "fw": "1.6.0.48 (sim)", "transport": "Simulator"}

    def read(self) -> dict:
        now = time.monotonic()
        dt = max(1e-3, now - self._last)
        self._last = now
        self._n += 1
        ripple = 0.01 * math.sin(self._n / 6.0)
        out_state = 0           # 0 = off, 1 = CC, 2 = CV
        if self.on:
            i_cv = self.set_v / self.load            # current the load would draw at set V
            over = i_cv > self.set_a + 1e-9
            if over and self.current_over == 1:      # OCP selected → trip the output off
                self.on = False; self._ocp_trip = True
                voltage = current = 0.0
            elif over:                               # CC: limit current
                current = self.set_a; voltage = self.set_a * self.load; out_state = 1
            else:                                    # CV: hold voltage
                voltage = self.set_v; current = i_cv; out_state = 2
        else:
            voltage = current = 0.0
        if self.on:
            voltage = max(0.0, voltage * (1 + ripple)); current = max(0.0, current * (1 + ripple))
        power = voltage * current
        self.energy_wh += power * dt / 3600.0
        target_temp = 25.0 + power * 1.4
        self.temp += (target_temp - self.temp) * min(1.0, dt * 0.5)
        self.batt = max(0.0, min(100.0, self.batt + (1.5 if self.charging else -1.5) * dt))
        return {
            "voltage": round(voltage, 2), "current": round(current, 3), "power": round(power, 2),
            "set_voltage": self.set_v, "set_current": self.set_a,
            "output": int(self.on), "model": 0, "temperature": round(self.temp),
            "energy": round(self.energy_wh, 3), "working_time": int(now - self._t0),
            "battery": int(round(self.batt)), "battery_state": 1 if self.charging else 0,
            "charging": self.charging, "out_state": out_state, "current_over": self.current_over,
            "errors": ["errorDcOutOCP"] if self._ocp_trip else [],
            "mode": "CC" if out_state == 1 else "CV",
        }

    def apply(self, v=None, a=None, on=None):
        if v is not None:
            self.set_v = max(0.0, min(30.0, float(v)))
        if a is not None:
            self.set_a = max(0.0, min(5.0, float(a)))
        if on is not None:
            self.on = bool(on)
            if self.on:
                self._ocp_trip = False               # re-enabling clears the OCP latch

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
