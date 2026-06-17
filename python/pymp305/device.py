"""High-level driver for the ISDT MP305 (MP305A / MP305B) over USB-HID.

Both models share the same controller and protocol; the only model-specific behaviour is
error-bit decoding, handled automatically once the device name is read.

Requires the `hid` package (cython-hidapi):  pip install hidapi

Example
-------
    from pymp305 import MP305
    with MP305.open() as psu:
        print(psu.hardware_info())
        psu.set_output(voltage=5.0, current=1.0, on=True)
        print(psu.read_state())
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass

from . import protocol as P
from . import commands as C
from .responses import (
    HardwareInfo, State, SystemSettings,
    ChargeState, ChargeInfo, PDO, ProgramState, ProgramList, ProgramSteps,
)

try:
    import hid  # cython-hidapi
except ImportError:  # pragma: no cover
    hid = None


class MP305Error(Exception):
    pass


@dataclass
class ControlCommand:
    """Fields for the 0xC8 control command (DPConnectModel)."""
    remote_con: int = 1          # 1 = take remote control (required to change anything)
    set_voltage: float = 0.0     # volts
    set_current: float = 0.0     # amps
    real_change: int = 3         # live-apply: 1=V, 2=I, 3=both
    voltage_slow: int = 0
    current_over: int = 0
    output: int = 0              # 1 = output ON
    model: int = 0               # 0 = DC PSU
    refresh: int = 0

    def payload(self) -> bytes:
        return struct.pack(
            "<BHHBBBBBB",
            self.remote_con & 0xFF,
            int(round(self.set_voltage * 100)) & 0xFFFF,   # 10 mV units
            int(round(self.set_current * 1000)) & 0xFFFF,  # 1 mA units
            self.real_change & 0xFF,
            self.voltage_slow & 0xFF,
            self.current_over & 0xFF,
            self.output & 0xFF,
            self.model & 0xFF,
            self.refresh & 0xFF,
        )


@dataclass
class ChargeCommand:
    """Fields for the 0xEE charge command (chargeConnectCmd)."""
    remote_con: int = 1
    battery_type: int = 0        # index into responses.BATTERY_TYPES
    capacity_voltage: float = 0  # per-cell V (×1000); raw cell count for NiMH/Cd
    cells: int = 1
    current: float = 0.0         # amps
    output: int = 0
    model: int = 3               # charge

    def payload(self) -> bytes:
        cap = (int(round(self.capacity_voltage * 1000))
               if self.battery_type != 5 else int(self.capacity_voltage))
        return struct.pack(
            "<BBHBHBB",
            self.remote_con & 0xFF,
            self.battery_type & 0xFF,
            cap & 0xFFFF,
            self.cells & 0xFF,
            int(round(self.current * 1000)) & 0xFFFF,
            self.output & 0xFF,
            self.model & 0xFF,
        )


@dataclass
class SystemSetCommand:
    """Fields for the 0xC6 system-settings command (systemSetCmd)."""
    per_limit: int = 90
    volume: int = 3
    screen_off: int = 0
    shutdown: int = 0
    screen_direction: int = 0
    slope_steps: int = 0
    current_over: int = 0
    system_check: int = 0
    recover: int = 0
    usb_line: int | None = None

    def payload(self) -> bytes:
        buf = struct.pack(
            "<BBBBBHHBB",
            self.per_limit & 0xFF, self.volume & 0xFF, self.screen_off & 0xFF,
            self.shutdown & 0xFF, self.screen_direction & 0xFF,
            self.slope_steps & 0xFFFF, self.current_over & 0xFFFF,
            self.system_check & 0xFF, self.recover & 0xFF,
        )
        if self.usb_line is not None:
            buf += struct.pack("<H", self.usb_line & 0xFFFF)
        return buf


class MP305:
    """Driver for an ISDT MP305A or MP305B. The concrete model is auto-detected from the
    device name on the first `hardware_info()` call and used for error decoding."""

    def __init__(self, device, report_size: int = P.REPORT_SIZE):
        self._dev = device
        self._report_size = report_size
        self.device_name: str | None = None   # "MP305A" / "MP305B", set by hardware_info()

    # ---- connection ------------------------------------------------------
    @staticmethod
    def list_devices() -> list[dict]:
        if hid is None:
            raise MP305Error("the 'hidapi' package is not installed (pip install hidapi)")
        return [d for d in hid.enumerate() if d["vendor_id"] == P.VENDOR_ID]

    @classmethod
    def open(cls, path: bytes | None = None, serial: str | None = None) -> "MP305":
        """Open the MP305. Picks the HID interface with usage_page 0x01 / usage 0x04
        when several interfaces from VID 0x28E9 are present."""
        if hid is None:
            raise MP305Error("the 'hidapi' package is not installed (pip install hidapi)")
        dev = hid.device()
        if path is not None:
            dev.open_path(path)
        else:
            candidates = cls.list_devices()
            if not candidates:
                raise MP305Error(f"no device with VID 0x{P.VENDOR_ID:04X} found")
            preferred = [c for c in candidates
                         if c.get("usage_page") == P.HID_USAGE_PAGE
                         and c.get("usage") == P.HID_USAGE]
            chosen = (preferred or candidates)[0]
            dev.open_path(chosen["path"])
        try:
            dev.set_nonblocking(0)
        except Exception:
            pass
        return cls(dev)

    def close(self):
        self._dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- raw transport ---------------------------------------------------
    def send(self, cmd: int, payload: bytes = b"") -> None:
        report = P.build_report(cmd, payload, self._report_size)
        n = self._dev.write(report)
        if n < 0:
            raise MP305Error("HID write failed")

    def send_raw_payload(self, payload: bytes) -> None:
        """Send a payload whose first byte is the command byte (e.g. BOOT/REBOOT)."""
        self.send(payload[0], payload[1:])

    def read_frame(self, timeout_ms: int = 1000) -> P.Frame | None:
        raw = self._dev.read(self._report_size + 1, timeout_ms)
        if not raw:
            return None
        return P.parse_report(bytes(raw))

    def request(self, cmd: int, expect: int, payload: bytes = b"",
                timeout_ms: int = 1500) -> P.Frame:
        """Send `cmd` and wait until a frame with command byte `expect` arrives."""
        self.send(cmd, payload)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            frame = self.read_frame(timeout_ms=max(1, int((deadline - time.monotonic()) * 1000)))
            if frame is None:
                continue
            if frame.cmd == expect:
                return frame
        raise MP305Error(f"timed out waiting for response 0x{expect:02X} to 0x{cmd:02X}")

    # ---- high-level reads ------------------------------------------------
    def hardware_info(self, timeout_ms: int = 1500) -> HardwareInfo:
        f = self.request(P.CMD_HW_INFO, P.RESP_HW_INFO, timeout_ms=timeout_ms)
        info = HardwareInfo.parse(f.values)
        if info.device_name:
            self.device_name = info.device_name   # cache for model-aware error decoding
        return info

    def read_state(self, realtime: bool = True, timeout_ms: int = 1500) -> State:
        """Read the live measurement/state frame (0xC3)."""
        cmd = P.CMD_REALTIME if realtime else P.CMD_STATE_INFO
        f = self.request(cmd, P.RESP_STATE, timeout_ms=timeout_ms)
        return State.parse(f.values, device_name=self.device_name)

    def read_system_settings(self, timeout_ms: int = 1500) -> SystemSettings:
        f = self.request(P.CMD_SYS_GET, P.RESP_SYS, timeout_ms=timeout_ms)
        return SystemSettings.parse(f.values)

    # ---- high-level control ---------------------------------------------
    def control(self, cmd: ControlCommand, timeout_ms: int = 1500) -> P.Frame:
        return self.request(P.CMD_CONTROL, P.RESP_CONTROL, cmd.payload(), timeout_ms)

    def set_output(self, voltage: float | None = None, current: float | None = None,
                   on: bool | None = None, *, model: int = 0,
                   real_change: int = 3, timeout_ms: int = 1500) -> State:
        """Convenience: take remote control and set V / I / output in one call.

        Unspecified values are read from the current state so they are preserved.
        Returns the fresh state after the change.
        """
        st = self.read_state(timeout_ms=timeout_ms)
        cmd = ControlCommand(
            remote_con=1,
            set_voltage=st.set_voltage if voltage is None else voltage,
            set_current=st.set_current if current is None else current,
            real_change=real_change,
            voltage_slow=st.voltage_slow,
            current_over=st.current_over,
            output=st.output if on is None else (1 if on else 0),
            model=model,
            refresh=0,
        )
        self.control(cmd, timeout_ms=timeout_ms)
        return self.read_state(timeout_ms=timeout_ms)

    def output_on(self, **kw) -> State:
        return self.set_output(on=True, **kw)

    def output_off(self, **kw) -> State:
        return self.set_output(on=False, **kw)

    def release_remote(self, timeout_ms: int = 1500) -> P.Frame:
        """Hand control back to the device's front panel (remoteCon = 0)."""
        return self.control(ControlCommand(remote_con=0), timeout_ms=timeout_ms)

    def set_system_settings(self, cmd: SystemSetCommand, timeout_ms: int = 1500) -> P.Frame:
        return self.request(P.CMD_SYS_SET, P.RESP_SYS_SET, cmd.payload(), timeout_ms)

    def charge(self, cmd: ChargeCommand, timeout_ms: int = 1500) -> P.Frame:
        return self.request(P.CMD_CHARGE_CONTROL, P.RESP_CHARGE, cmd.payload(), timeout_ms)

    def set_language(self, index: int, timeout_ms: int = 1500) -> P.Frame:
        return self.request(P.CMD_SET_LANGUAGE, 0xA3, bytes([index & 0xFF]), timeout_ms)

    # ---- charge mode -----------------------------------------------------
    def read_charge_state(self, timeout_ms: int = 1500) -> ChargeState:
        f = self.request(P.CMD_CHARGE_INFO, P.RESP_CHARGE, timeout_ms=timeout_ms)
        return ChargeState.parse(f.values, index=5, device_name=self.device_name)

    def read_charge_settings(self, timeout_ms: int = 1500) -> ChargeInfo:
        f = self.request(P.CMD_CHARGE_SEARCH, P.RESP_CHARGE_INFO, timeout_ms=timeout_ms)
        return ChargeInfo.parse(f.values, index=5)

    # ---- USB-PD ----------------------------------------------------------
    def read_pdo(self, pdo_id: int, timeout_ms: int = 1500) -> PDO:
        cmd, payload = C.pdo_search(pdo_id)
        f = self.request(cmd, P.RESP_PDO, payload, timeout_ms)
        return PDO.parse(f.values, index=5)

    def pdo_connect(self, conn: C.PDOConnect, timeout_ms: int = 1500) -> P.Frame:
        cmd, payload = conn.build()
        return self.request(cmd, P.RESP_PDO_CONNECT, payload, timeout_ms)

    # ---- programmable sequences -----------------------------------------
    def read_program_state(self, timeout_ms: int = 1500) -> ProgramState:
        cmd, payload = C.programmable_info()
        f = self.request(cmd, P.RESP_PROGRAM_STATE, payload, timeout_ms)
        return ProgramState.parse(f.values, index=5)

    def read_program_list(self, timeout_ms: int = 1500) -> ProgramList:
        cmd, payload = C.programmable_list()
        f = self.request(cmd, P.RESP_PROGRAM_LIST, payload, timeout_ms)
        return ProgramList.parse(f.values, index=5)

    def read_program_steps(self, seq_id: int, number: int, timeout_ms: int = 1500) -> ProgramSteps:
        cmd, payload = C.programmable_read(seq_id)
        f = self.request(cmd, P.RESP_PROGRAM_STEPS, payload, timeout_ms)
        return ProgramSteps.parse(f.values, number, index=5)

    def program_connect(self, conn: C.ProgramConnect, timeout_ms: int = 1500) -> P.Frame:
        cmd, payload = conn.build()
        return self.request(cmd, P.RESP_PROGRAM_CONNECT, payload, timeout_ms)

    def send_command(self, cmd_payload: tuple[int, bytes]) -> None:
        """Send a (cmd, payload) tuple from the ``commands`` module without waiting."""
        self.send(cmd_payload[0], cmd_payload[1])

    # ---- danger zone -----------------------------------------------------
    def reboot(self) -> None:
        self.send_raw_payload(P.REBOOT_PAYLOAD)

    def enter_bootloader(self) -> None:
        self.send_raw_payload(P.BOOT_PAYLOAD)


# Both models share this driver; aliases for discoverability / explicit intent.
MP305A = MP305
MP305B = MP305
# Backwards-compatible error alias.
MP305BError = MP305Error
