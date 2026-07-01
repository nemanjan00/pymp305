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

# operating modes (the device's `model` field): mutually exclusive
MODE_DC, MODE_PD, MODE_CHARGE = 0, 2, 3
CHEMS = ["LiHv", "LiPo", "LiFe", "Li-ion", "NiMH", "NiCd", "Pb"]
SIM_PDOS = [(5.0, 3.0), (9.0, 3.0), (12.0, 3.0), (15.0, 3.0), (20.0, 5.0)]
SIM_EMARKER = "USB-C · 100 W (20 V / 5 A) · USB 3.2 Gen2"


def _state_to_dict(st, info_name=None) -> dict:
    return {
        "voltage": st.voltage, "current": st.current, "power": st.power,
        "set_voltage": st.set_voltage, "set_current": st.set_current,
        "output": int(st.output), "model": st.model, "temperature": st.temperature,
        "energy": st.energy, "working_time": st.working_time,
        "battery": st.percentage, "battery_state": st.battery_state,
        "charging": st.battery_state == 1,
        "out_state": st.out_state,          # device regulation status: 1=CV, 2=CC (verified on hardware)
        "current_over": st.current_over,     # over-current behaviour setting: 0=CC, 1=OCP
        "errors": list(getattr(st, "errors", []) or []),
        "mode": "CC" if st.out_state == 2 else "CV",
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
        st = self._psu.read_state()
        d = _state_to_dict(st, self._name)
        em, pdos = self._caps()
        d["emarker"] = em; d["pdos"] = pdos; d["pdo_sel"] = getattr(self, "_pdo_sel", 0)
        d["chem"] = getattr(self, "_chem", 0); d["cells"] = getattr(self, "_cells", 1)
        d["charge_current"] = getattr(self, "_charge_a", 0.0)
        if st.model == MODE_CHARGE:                  # enrich with live charge telemetry
            try:
                cs = self._psu.read_charge_state()
                d["charge_pct"] = cs.percentage; d["charging_ext"] = bool(cs.output)
            except Exception:
                d["charge_pct"] = 0; d["charging_ext"] = False
        else:
            d["charge_pct"] = 0; d["charging_ext"] = False
        return d

    def _caps(self):
        # cable + PDO list don't change live — read once, cache.
        # The MP305 is a USB-PD *source* with several power profiles; the active one
        # (0xE4) lists the voltage points it offers (5/9/12/15/20 V, each with a
        # current). Show those. (Verified against a real 60 W source on hardware.)
        if not hasattr(self, "_em"):
            try:
                em = self._psu.read_emarker()
                self._em = "USB-C cable" if em.get("present") else "no e-marked cable"
            except Exception:
                self._em = "—"
            self._pl = []
            try:
                idx = self._psu.read_pdo_index()
                self._pdo_sel = idx
                p = self._psu.read_pdo(idx)
                for it in (p.items if p else []):
                    v, a = it.get("voltage_v", 0.0), it.get("current_a", 0.0)
                    if v > 0:
                        self._pl.append((v, a))
            except Exception:
                pass
        return self._em, self._pl

    def apply(self, v=None, a=None, on=None):
        self._psu.set_output(voltage=v, current=a, on=on)

    def set_mode(self, model):
        # switch via the driver, which routes through the current mode's connect
        # command and holds remote (the device reverts to DC if remote is released)
        self._psu.set_mode(int(model))

    def set_charge(self, chem=None, cells=None, current=None):
        if chem is not None: self._chem = int(chem)
        if cells is not None: self._cells = max(1, int(cells))
        if current is not None: self._charge_a = max(0.0, float(current))

    def set_charging(self, on):
        from pymp305 import ChargeCommand
        self._psu.charge(ChargeCommand(
            remote_con=1, battery_type=getattr(self, "_chem", 0), cells=getattr(self, "_cells", 1),
            current=getattr(self, "_charge_a", 0.0), output=1 if on else 0))

    def select_pdo(self, i):
        from pymp305 import commands as C
        self._pdo_sel = int(i)
        self._psu.pdo_connect(C.PDOConnect(remote_con=1, pdo_index=int(i), update=1, output=1))

    def set_current_over(self, mode):
        # CC (0) / OCP (1); set_output does the remote handshake and preserves V/I/output
        self._psu.set_output(current_over=int(mode))

    def set_remote(self, held):
        # take remote control (remote_con=1, preserving V/I/output) or hand it back to the panel
        if held:
            self._psu.set_output()        # set_output() with no args just takes remote + preserves
        else:
            self._psu.release_remote()    # remote_con=0 → front panel regains control

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
        self.remote = True         # whether the app holds remote control (vs front panel)
        self._ocp_trip = False
        self.mode = 0              # operating mode: 0 = DC PSU, 2 = USB-PD, 3 = charge
        self.chem = 1; self.cells = 3; self.charge_a = 1.0; self.charging_ext = False
        self.cbatt = 30.0          # % of the external battery being charged
        self.pdo_sel = 4           # selected USB-PD profile index
        self._t0 = time.monotonic()
        self._last = self._t0
        self._n = 0

    def toggle_charging(self):
        self.charging = not self.charging
        return self.charging

    def set_current_over(self, mode):
        self.current_over = int(mode)

    def set_remote(self, held):
        self.remote = bool(held)

    def set_mode(self, model):
        self.mode = int(model); self.on = False     # switching mode drops the live output

    def set_charge(self, chem=None, cells=None, current=None):
        if chem is not None: self.chem = int(chem)
        if cells is not None: self.cells = max(1, int(cells))
        if current is not None: self.charge_a = max(0.0, min(10.0, float(current)))

    def set_charging(self, on):
        self.charging_ext = bool(on)
        if on: self.mode = 3

    def select_pdo(self, i):
        self.pdo_sel = max(0, min(len(SIM_PDOS) - 1, int(i)))

    def _charge_step(self, dt):
        if not self.charging_ext or self.cbatt >= 100.0:
            return 0.0, 0.0, 0
        taper = 1.0 if self.cbatt < 90 else max(0.05, (100 - self.cbatt) / 10.0)  # CC→CV
        current = self.charge_a * taper
        pack_v = self.cells * (3.2 + 0.9 * self.cbatt / 100.0)        # ~3.2→4.1 V/cell
        self.cbatt = min(100.0, self.cbatt + current * dt * 2.0)
        return pack_v, current, (2 if taper >= 0.999 else 1)   # out_state: 2=CC (bulk), 1=CV (taper)

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
        out_state = 0           # 0 = off, 1 = CV, 2 = CC (matches MP305B hardware)
        if self.mode == MODE_CHARGE:                 # charging an external pack
            voltage, current, out_state = self._charge_step(dt)
        elif self.mode == MODE_PD:                   # USB-PD: output fixed to the selected PDO
            pv, pa = SIM_PDOS[self.pdo_sel]
            if self.on:
                i = min(pa, pv / self.load); voltage = pv; current = i
                out_state = 2 if i >= pa - 1e-9 else 1
            else:
                voltage = current = 0.0
        else:                                        # DC PSU
            if self.on:
                i_cv = self.set_v / self.load        # current the load would draw at set V
                over = i_cv > self.set_a + 1e-9
                if over and self.current_over == 1:  # OCP selected → trip the output off
                    self.on = False; self._ocp_trip = True
                    voltage = current = 0.0
                elif over:                           # CC: limit current
                    current = self.set_a; voltage = self.set_a * self.load; out_state = 2
                else:                                # CV: hold voltage
                    voltage = self.set_v; current = i_cv; out_state = 1
            else:
                voltage = current = 0.0
        live = self.on or (self.mode == MODE_CHARGE and self.charging_ext)
        if live:
            voltage = max(0.0, voltage * (1 + ripple)); current = max(0.0, current * (1 + ripple))
        power = voltage * current
        self.energy_wh += power * dt / 3600.0
        target_temp = 25.0 + power * 1.4
        self.temp += (target_temp - self.temp) * min(1.0, dt * 0.5)
        self.batt = max(0.0, min(100.0, self.batt + (1.5 if self.charging else -1.5) * dt))
        return {
            "voltage": round(voltage, 2), "current": round(current, 3), "power": round(power, 2),
            "set_voltage": self.set_v, "set_current": self.set_a,
            "output": int(self.on), "model": self.mode, "temperature": round(self.temp),
            "energy": round(self.energy_wh, 3), "working_time": int(now - self._t0),
            "battery": int(round(self.batt)), "battery_state": 1 if self.charging else 0,
            "charging": self.charging, "out_state": out_state, "current_over": self.current_over,  # 1=CV 2=CC
            "errors": ["errorDcOutOCP"] if self._ocp_trip else [],
            "mode": "CC" if out_state == 2 else "CV",
            "emarker": SIM_EMARKER, "pdos": SIM_PDOS, "pdo_sel": self.pdo_sel,
            "chem": self.chem, "cells": self.cells, "charge_current": self.charge_a,
            "charging_ext": self.charging_ext, "charge_pct": int(round(self.cbatt)),
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
