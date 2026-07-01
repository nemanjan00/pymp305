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
    annotate_emark,
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
        self._no_realtime = False              # set if the unit never answers the 0xBD realtime poll
        self._remote_held = False              # True once the device has granted remote control
        self._model = 0                        # last-seen active mode (0 DC, 1 program, 2 USB-PD, 3 charge)

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
        P.warn_untested()
        return cls(dev)

    def close(self):
        self._dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- raw transport ---------------------------------------------------
    def send(self, cmd: int, payload: bytes = b"") -> None:
        reports = P.build_reports(cmd, payload, self._report_size)
        for i, report in enumerate(reports):
            if self._dev.write(report) < 0:
                raise MP305Error("HID write failed")
            if len(reports) > 1 and i < len(reports) - 1:
                time.sleep(0.005)   # inter-fragment gap, as WebLink does

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
        """Read the live measurement/state frame (0xC3).

        Some units (observed: MP305B app V1.6) never answer the realtime poll
        (0xBD); once that is seen we fall back to the stored-state query (0xC2),
        which returns the same 0xC3 frame, and remember it for later calls."""
        if realtime and not self._no_realtime:
            try:
                f = self.request(P.CMD_REALTIME, P.RESP_STATE, timeout_ms=timeout_ms)
                st = State.parse(f.values, device_name=self.device_name)
                self._model = st.model
                return st
            except MP305Error:
                self._no_realtime = True   # this unit doesn't answer 0xBD; use 0xC2 from now on
        f = self.request(P.CMD_STATE_INFO, P.RESP_STATE, timeout_ms=timeout_ms)
        st = State.parse(f.values, device_name=self.device_name)
        self._model = st.model
        return st

    def read_system_settings(self, timeout_ms: int = 1500) -> SystemSettings:
        f = self.request(P.CMD_SYS_GET, P.RESP_SYS, timeout_ms=timeout_ms)
        return SystemSettings.parse(f.values)

    # ---- high-level control ---------------------------------------------
    # The 0xC8 control command is answered by 0xC9 whose first payload byte is a
    # status: 0 = accepted, 1 = rejected (this session does not hold remote
    # control), 2 = pending. Taking control is a two-step handshake, exactly as
    # ISDT's WebLink does it: first *request* control with remoteCon=2 (the
    # device grants it and answers status 0), then every subsequent change is
    # sent with remoteCon=1. A bare remoteCon=1 with no prior request is
    # rejected (status 1) and silently ignored -- which is why setpoints never
    # applied before this handshake was added.
    def control(self, cmd: ControlCommand, timeout_ms: int = 1500) -> P.Frame:
        return self.request(P.CMD_CONTROL, P.RESP_CONTROL, cmd.payload(), timeout_ms)

    @staticmethod
    def _control_status(frame: P.Frame) -> int:
        return frame.payload[0] if frame.payload else -1

    # Each mode has its own connect command (cmd, response). Remote control and
    # mode switching are done through the CURRENT mode's command -- e.g. while in
    # USB-PD mode you request remote and switch away with 0xE8, not 0xC8.
    _MODE_CONNECT = {
        0: (P.CMD_CONTROL,        P.RESP_CONTROL),          # DC PSU
        1: (0xE2,                 P.RESP_PROGRAM_CONNECT),  # programmable
        2: (0xE8,                 P.RESP_PDO_CONNECT),      # USB-PD
        3: (P.CMD_CHARGE_CONTROL, P.RESP_CHARGE_CONTROL),   # charge
    }

    def _mode_payload(self, mode: int, remote_con: int, new_model: int,
                      st: "State | None" = None) -> bytes:
        """Build the payload for `mode`'s connect command carrying `remote_con`
        and selecting `new_model`, output off. When leaving DC with remote_con=1
        the DC setpoint is preserved from `st` (a remote_con=2 request ignores
        these fields, so it is harmless there)."""
        if mode == 0:
            cc = ControlCommand(remote_con=remote_con, model=new_model, output=0)
            if st is not None:
                cc.set_voltage, cc.set_current = st.set_voltage, st.set_current
                cc.voltage_slow, cc.current_over = st.voltage_slow, st.current_over
            return cc.payload()
        if mode == 1:
            return C.ProgramConnect(remote_con=remote_con, output=0, model=new_model).build()[1]
        if mode == 2:
            return C.PDOConnect(remote_con=remote_con, output=0, model=new_model).build()[1]
        if mode == 3:
            return ChargeCommand(remote_con=remote_con, output=0, model=new_model).payload()
        raise MP305Error(f"unknown model {mode}")

    def request_remote(self, timeout_ms: int = 1500) -> bool:
        """Request remote control from the device (remoteCon=2), using the current
        mode's connect command. Returns True if granted (status 0). Does not
        change any setpoint."""
        cmd, expect = self._MODE_CONNECT.get(self._model, self._MODE_CONNECT[0])
        f = self.request(cmd, expect, self._mode_payload(self._model, 2, self._model), timeout_ms)
        self._remote_held = self._control_status(f) == 0
        return self._remote_held

    def _ensure_remote(self, timeout_ms: int) -> None:
        if not self._remote_held and not self.request_remote(timeout_ms=timeout_ms):
            raise MP305Error("device refused remote control (0xC9 status 1) -- "
                             "is another controller connected, or the panel locked?")

    def set_mode(self, model: int, timeout_ms: int = 1500) -> int:
        """Switch the device's active mode: 0=DC PSU, 1=programmable, 2=USB-PD,
        3=charge. Acquires and KEEPS remote control (the mode only persists while
        remote is held -- release_remote() reverts the unit to DC). The switch is
        sent through the current mode's connect command, as the device requires.
        Returns the confirmed model."""
        if model not in self._MODE_CONNECT:
            raise MP305Error(f"unknown model {model} (expected 0/1/2/3)")
        st = self.read_state(timeout_ms=timeout_ms)   # also refreshes self._model
        cur = st.model
        self._ensure_remote(timeout_ms)               # request remote in the current mode
        if cur == model:
            return model
        cmd, expect = self._MODE_CONNECT[cur]
        self.request(cmd, expect,
                     self._mode_payload(cur, 1, model, st=st if cur == 0 else None), timeout_ms)
        last = cur
        for _ in range(6):                            # the state frame lags the switch by ~1 read
            last = self.read_state(timeout_ms=timeout_ms).model
            if last == model:
                return model
            time.sleep(0.1)
        raise MP305Error(f"mode switch to {model} not confirmed (still {last})")

    def set_output(self, voltage: float | None = None, current: float | None = None,
                   on: bool | None = None, *, model: int = 0, current_over: int | None = None,
                   real_change: int = 3, reapply: bool = False, timeout_ms: int = 1500) -> State:
        """Convenience: take remote control and set V / I / output in one call.

        Unspecified values are read from the current state so they are preserved.
        `current_over` sets the over-current behaviour (0 = CC current-limit,
        1 = OCP trip); left as None it is preserved. Returns the fresh state.

        `reapply`: when True and the output ends up on, the output is briefly
        cycled off->on after applying. This works around a device quirk: *lowering*
        the current limit while the output is already on does not engage
        constant-current mode (the CC threshold only re-arms on output-enable);
        raising it live works fine. Use `reapply=True` when you lower the current
        limit mid-run and need CC to take effect immediately -- note it momentarily
        interrupts the output.
        """
        self._ensure_remote(timeout_ms)
        st = self.read_state(timeout_ms=timeout_ms)
        target_on = st.output if on is None else (1 if on else 0)
        cmd = ControlCommand(
            remote_con=1,
            set_voltage=st.set_voltage if voltage is None else voltage,
            set_current=st.set_current if current is None else current,
            real_change=real_change,
            voltage_slow=st.voltage_slow,
            current_over=st.current_over if current_over is None else current_over,
            output=target_on,
            model=model,
            refresh=0,
        )
        f = self.control(cmd, timeout_ms=timeout_ms)
        if self._control_status(f) == 1:        # remote was dropped -- re-acquire once and retry
            self._remote_held = False
            self._ensure_remote(timeout_ms)
            f = self.control(cmd, timeout_ms=timeout_ms)
            if self._control_status(f) == 1:
                raise MP305Error("control command rejected: device refused remote control")
        if reapply and target_on:               # cycle output so a lowered current limit re-arms as CC
            cmd.output = 0
            self.control(cmd, timeout_ms=timeout_ms)
            cmd.output = 1
            self.control(cmd, timeout_ms=timeout_ms)
        return self.read_state(timeout_ms=timeout_ms)

    def output_on(self, **kw) -> State:
        return self.set_output(on=True, **kw)

    def output_off(self, **kw) -> State:
        return self.set_output(on=False, **kw)

    def release_remote(self, timeout_ms: int = 1500) -> P.Frame:
        """Hand control back to the device's front panel (remoteCon = 0)."""
        f = self.control(ControlCommand(remote_con=0), timeout_ms=timeout_ms)
        self._remote_held = False
        return f

    def _connect(self, cmd: int, expect: int, payload: bytes, timeout_ms: int) -> P.Frame:
        """Send a mode connect/control command that requires remote control.

        Does the two-step handshake and honours the status byte, which is shared
        by every connect response (0xC9/0xE3/0xE9/0xEF). Following WebLink, only
        status 1 means "rejected -- this session does not hold remote control";
        0 is success and any other value is mode-specific and non-fatal. If we
        see status 1 we re-acquire remote once and retry, then give up."""
        self._ensure_remote(timeout_ms)
        f = self.request(cmd, expect, payload, timeout_ms)
        if self._control_status(f) == 1:
            self._remote_held = False
            self._ensure_remote(timeout_ms)
            f = self.request(cmd, expect, payload, timeout_ms)
            if self._control_status(f) == 1:
                raise MP305Error(f"command 0x{cmd:02X} rejected: device refused remote control")
        return f

    def set_system_settings(self, cmd: SystemSetCommand, timeout_ms: int = 1500) -> P.Frame:
        return self.request(P.CMD_SYS_SET, P.RESP_SYS_SET, cmd.payload(), timeout_ms)

    def charge(self, cmd: ChargeCommand, timeout_ms: int = 1500) -> P.Frame:
        return self._connect(P.CMD_CHARGE_CONTROL, P.RESP_CHARGE_CONTROL, cmd.payload(), timeout_ms)

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

    def read_pdo_index(self, timeout_ms: int = 1500) -> int:
        """The device's active USB-PD source-profile index (0xE4 -> 0xE5).
        Use it with read_pdo() to get the voltage points the source currently offers."""
        f = self.request(0xE4, 0xE5, timeout_ms=timeout_ms)
        return f.payload[0] if f.payload else 0

    def pdo_connect(self, conn: C.PDOConnect, timeout_ms: int = 1500) -> P.Frame:
        cmd, payload = conn.build()
        return self._connect(cmd, P.RESP_PDO_CONNECT, payload, timeout_ms)

    def write_pdo(self, pdo_id: int, name: str, power: int, items: list[dict],
                  is_last: int = 1, timeout_ms: int = 1500) -> P.Frame:
        """Write/define a USB-PD profile (advanced — overwrites a stored profile).
        `0xD2`→`0xD3`. See commands.pdo_write / responses.parse_pdo_item for item shape."""
        cmd, payload = C.pdo_write(pdo_id, name, power, items, is_last)
        return self.request(cmd, P.RESP_PDO_WRITE, payload, timeout_ms)

    # ---- programmable sequences -----------------------------------------
    def read_program_state(self, timeout_ms: int = 1500) -> ProgramState:
        cmd, payload = C.programmable_info()
        f = self.request(cmd, P.RESP_PROGRAM_STATE, payload, timeout_ms)
        return ProgramState.parse(f.values, index=5)

    def read_emarker(self, timeout_ms: int = 1500) -> dict:
        """Read the attached USB-C cable's e-marker info (from the programmable-run state
        `0xDF`), with speed/format labels applied. `present=False` if no e-marked cable."""
        return annotate_emark(self.read_program_state(timeout_ms).emark)

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
        return self._connect(cmd, P.RESP_PROGRAM_CONNECT, payload, timeout_ms)

    def write_program(self, seq_id: int, steps: list[dict], timeout_ms: int = 1500) -> P.Frame:
        """Write a programmable sequence's steps (`0xDA`→`0xDB`). Each step:
        {"V": volts, "A": amps, "S": seconds}."""
        cmd, payload = C.programmable_write(seq_id, steps)
        return self.request(cmd, P.RESP_PROGRAM_WRITE, payload, timeout_ms)

    def program_change(self, seq_id: int, name: str, num: int, is_last: int = 1,
                       remove: int = 0, timeout_ms: int = 1500) -> P.Frame:
        """Create / rename / delete a programmable-sequence slot (`0xD6`→`0xD7`)."""
        cmd, payload = C.programmable_change(seq_id, name, num, is_last, remove)
        return self.request(cmd, P.RESP_PROGRAM_CHANGE, payload, timeout_ms)

    def send_command(self, cmd_payload: tuple[int, bytes]) -> None:
        """Send a (cmd, payload) tuple from the ``commands`` module without waiting."""
        self.send(cmd_payload[0], cmd_payload[1])

    # ---- experimental / undisclosed (reverse-engineered, UNTESTED) -------
    # Handled by the firmware but never sent by the official app; behaviour inferred
    # from disassembly — see reversing/FINDINGS-commands.md.
    def get_language(self, timeout_ms: int = 1500) -> int:
        """[experimental] Read the UI language index — the read counterpart of
        ``set_language()`` (undisclosed cmd 0xA0 → 0xA1). Returns the language byte."""
        f = self.request(P.CMD_GET_LANGUAGE, P.RESP_GET_LANGUAGE, timeout_ms=timeout_ms)
        return f.payload[0] if f.payload else -1

    def soft_reset(self, *, confirm: bool = False, timeout_ms: int = 1500) -> bool:
        """[experimental] Magic-gated soft re-init: resets the regulator/USB-PD control
        state to factory defaults and restarts the control task (undisclosed cmd 0xFE,
        payload ``AA 55``, resp 0xFF).

        Static analysis of the whole call tree shows **no flash/NVM access** — it cannot
        brick the unit — but it interrupts and resets the **live output**, so it is gated
        behind ``confirm=True``. Returns True if the device echoed the magic (accepted).
        """
        if not confirm:
            raise MP305Error("soft_reset() is experimental — pass confirm=True to proceed")
        f = self.request(P.CMD_SOFT_RESET, P.RESP_SOFT_RESET, P.SOFT_RESET_MAGIC, timeout_ms)
        return f.payload[:2] == P.SOFT_RESET_MAGIC

    # ---- danger zone -----------------------------------------------------
    def reboot(self) -> None:
        self.send_raw_payload(P.REBOOT_PAYLOAD)

    def enter_bootloader(self) -> None:
        self.send_raw_payload(P.BOOT_PAYLOAD)

    # ---- OTA (experimental, see ota.py warnings) -------------------------
    def _write_report(self, report_payload: bytes) -> None:
        buf = bytes([P.REPORT_ID]) + bytes(report_payload)
        if self._report_size and len(buf) < self._report_size + 1:
            buf += b"\x00" * (self._report_size + 1 - len(buf))
        self._dev.write(buf)

    def _ota_wait(self, expect: int, timeout_ms: int = 8000) -> P.Frame:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            f = self.read_frame(timeout_ms=max(1, int((deadline - time.monotonic()) * 1000)))
            if f and f.cmd == expect:
                return f
        raise MP305Error(f"OTA: timed out waiting for 0x{expect:02X}")

    def flash(self, firmware, *, allow_untested_ota: bool = False, progress=None,
              boot_delay_s: float = 4.0) -> None:
        """EXPERIMENTAL, UNTESTED firmware flash over USB-HID.

        `firmware` is an ``ota.Firmware``. This enters the bootloader, erases, writes the
        app (and data) region driven by device acks, verifies, and reboots. A failed app
        write is normally recoverable by re-flashing (the bootloader is not touched), but
        this path has **never run against real hardware** — you must explicitly pass
        ``allow_untested_ota=True`` to accept the brick risk. `progress(done, total)` is
        called as bytes are written.
        """
        from . import ota
        if not allow_untested_ota:
            raise MP305Error("flash() is EXPERIMENTAL and untested on hardware — pass "
                             "allow_untested_ota=True to proceed (you accept the brick risk)")
        idx = 4  # HID addressId

        def report(done):
            if progress:
                progress(done, firmware.app_size + firmware.data_size)

        # 1) enter bootloader, wait for 0xF1 ack
        self.enter_bootloader()
        f = self._ota_wait(0xF1)
        if f.values[idx + 1] != 0:
            raise MP305Error("OTA: bootloader did not accept (0xF1)")
        time.sleep(boot_delay_s)

        # 2) erase app
        self.send_frame_raw(firmware.erase_app_frame())
        f = self._ota_wait(0xF3)
        if f.values[idx + 1] != 0:
            raise MP305Error("OTA: erase failed (0xF3)")

        # 3) write app, ack-driven fragmentation
        write_bit = 0
        chunks = ota.fragment_frame(firmware.write_app_frame(write_bit))
        ci = 1
        self._write_report(chunks[0])
        while True:
            f = self._ota_wait(0xF5)
            sub = f.values[idx + 6]
            if sub == 0x01:                        # device wants the next fragment
                self._write_report(chunks[ci]); ci += 1
            elif sub == 0x00:                      # block accepted
                stat = ota.parse_stat_address(f.values, idx)
                if stat != firmware.app_storage_offset + write_bit:
                    continue
                write_bit += 128
                report(min(write_bit, firmware.app_size))
                if write_bit >= firmware.app_size:
                    break
                chunks = ota.fragment_frame(firmware.write_app_frame(write_bit)); ci = 1
                self._write_report(chunks[0])
            else:
                raise MP305Error(f"OTA: app write error (0xF5 sub=0x{sub:02X})")

        # 4) app checksum
        self.send_frame_raw(firmware.checksum_app_frame())
        f = self._ota_wait(0xF7)
        if f.values[idx + 1] != 0:
            raise MP305Error("OTA: app checksum failed (0xF7)")

        # 5) optional data region
        if firmware.data_size:
            write_bit = 0
            chunks = ota.fragment_frame(firmware.write_data_frame(write_bit)); ci = 1
            self._write_report(chunks[0])
            while True:
                f = self._ota_wait(0x20)
                sub = f.values[idx + 1]
                if sub == 0x04:                    # next fragment
                    self._write_report(chunks[ci]); ci += 1
                elif sub == 0x05:                  # block accepted
                    write_bit += 128
                    report(firmware.app_size + min(write_bit, firmware.data_size))
                    if write_bit >= firmware.data_size:
                        self.send_frame_raw(firmware.checksum_data_frame())
                    else:
                        chunks = ota.fragment_frame(firmware.write_data_frame(write_bit)); ci = 1
                        self._write_report(chunks[0])
                elif sub == 0x06:                  # verified -> reboot
                    break
                else:
                    raise MP305Error(f"OTA: data write error (0x20 sub=0x{sub:02X})")

        # 6) reboot into the new app
        self.reboot()

    def send_frame_raw(self, frame: bytes) -> None:
        """Write a pre-built frame (from ota builders) as one HID report."""
        self._write_report(frame)


# Both models share this driver; aliases for discoverability / explicit intent.
MP305A = MP305
MP305B = MP305
# Backwards-compatible error alias.
MP305BError = MP305Error
