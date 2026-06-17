"""Parsers for ISDT MP305 (MP305A / MP305B) response frames. Offsets/units transcribed
from the WebLink source (DP3005Resp.js, dpstateResp.js, hardwareInfoResp.js, constant.js)."""
from __future__ import annotations

from dataclasses import dataclass, field

# bit-index -> error name, from constant.js `errorLists`
ERROR_LIST = [
    "errorOutRev", "errorBattVolt", "errorBattTemp_L", "errorBattTemp_H",
    "errorBoardTemp_H", "errorDcOutOCP", "errorDcOutOVP", "errorDICInitFail",
    "errorDcOutVol", "errorTimeOut", "errorConnectionBroken", "errorBatteryOver",
    "errorBatteryLow", "errorCellsNode", "errorNoBattery", "errorCapacity",
    "errorUnknown",
]

# control/model enums
MODEL_DC = 0          # plain DC power supply (CV/CC)
MODEL_PROGRAMMABLE = 1
MODEL_USB_PD = 2
MODEL_CHARGE = 3

BATTERY_TYPES = ["LiHv", "LiPo", "Lilon", "LiFe", "Pb", "NiMH/Cd"]


def _u8(b, i):  return b[i]
def _u16(b, i): return b[i] | (b[i + 1] << 8)
def _u32(b, i): return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)


# Non-MP305B devices (e.g. MP305A) remap a few low error bits — from constant.js getByteType.
SPECIAL_ERRORS = {1: "errorUnknown", 2: "errorUnknown", 3: "errorBattTemp_H_A"}


def decode_errors(mask: int, device_name: str | None = None,
                  charge_mode: bool = False) -> list[str]:
    """Decode a charge-error bitmask to error names, MP305A/MP305B-aware.

    Mirrors WebLink's `getByteType`: the meaningful bit-width is 17 in charge mode
    (model 3) and 9 otherwise; MP305B maps bits straight to `ERROR_LIST` while other
    models remap bits 1-3 via `SPECIAL_ERRORS`.
    """
    if not mask:
        return []
    width = 17 if charge_mode else 9
    names: list[str] = []
    for i in range(min(width, len(ERROR_LIST))):
        if not (mask & (1 << i)):
            continue
        name = ERROR_LIST[i] if device_name == "MP305B" else (SPECIAL_ERRORS.get(i) or ERROR_LIST[i])
        if name:
            names.append(name)
    return names


@dataclass
class State:
    """Decoded 0xC3 state frame (DP3005Resp), with physical units applied."""
    out_state: int = 0
    battery_state: int = 0
    percentage: int = 0
    voltage: float = 0.0        # V  (measured output)
    set_voltage: float = 0.0    # V
    current: float = 0.0        # A  (measured output)
    set_current: float = 0.0    # A
    working_time: int = 0       # s
    energy: float = 0.0         # Wh
    power: float = 0.0          # W
    current_over: int = 0
    real_change: int = 0
    voltage_slow: int = 0
    output: int = 0             # 1 = output on
    model: int = 0
    voltage_board: int = 0
    current_board: int = 0
    temperature: int = 0        # °C
    charge_error: int = 0
    errors: list[str] = field(default_factory=list)
    wave_pause: int = 0
    wave_time: int = 0
    raw: bytes = b""

    @classmethod
    def parse(cls, frame_values: bytes, index: int = 5,
              device_name: str | None = None) -> "State":
        v = frame_values
        i = index
        s = cls(raw=bytes(v))
        s.out_state = _u8(v, i); i += 1
        s.battery_state = _u8(v, i); i += 1
        s.percentage = _u8(v, i); i += 1
        s.voltage = _u16(v, i) / 100.0; i += 2
        s.set_voltage = _u16(v, i) / 100.0; i += 2
        s.current = _u16(v, i) / 1000.0; i += 2
        s.set_current = _u16(v, i) / 1000.0; i += 2
        s.working_time = _u32(v, i); i += 4
        s.energy = _u32(v, i) / 1000.0; i += 4
        s.power = _u16(v, i) / 100.0; i += 2
        s.current_over = _u8(v, i); i += 1
        s.real_change = _u8(v, i); i += 1
        s.voltage_slow = _u8(v, i); i += 1
        s.output = _u8(v, i); i += 1
        s.model = _u8(v, i); i += 1
        s.voltage_board = _u8(v, i); i += 1
        s.current_board = _u8(v, i); i += 1
        s.temperature = _u8(v, i); i += 1
        # optional trailing fields, gated on the length byte (values[0]) like the JS
        if v[0] > 34 and i + 1 < len(v):
            s.charge_error = _u16(v, i); i += 2
            s.errors = decode_errors(s.charge_error, device_name,
                                     charge_mode=(s.model == MODEL_CHARGE))
        if v[0] > 36 and i + 4 < len(v):
            s.wave_pause = _u8(v, i); i += 1
            s.wave_time = _u32(v, i); i += 4
        return s


@dataclass
class SystemSettings:
    """Decoded 0xC5 frame (DPStateResp)."""
    per_limit: int = 0
    volume: int = 0
    screen_off: int = 0
    shutdown: int = 0
    screen_direction: int = 0
    slope_steps: int = 0
    current_over: int = 0
    usb_line: int | None = None
    raw: bytes = b""

    @classmethod
    def parse(cls, frame_values: bytes, index: int = 5) -> "SystemSettings":
        v = frame_values
        i = index
        s = cls(raw=bytes(v))
        s.per_limit = _u8(v, i); i += 1
        s.volume = _u8(v, i); i += 1
        s.screen_off = _u8(v, i); i += 1
        s.shutdown = _u8(v, i); i += 1
        s.screen_direction = _u8(v, i); i += 1
        s.slope_steps = _u16(v, i); i += 2
        s.current_over = _u16(v, i); i += 2
        if v[0] > 14 and i + 1 < len(v):
            s.usb_line = _u16(v, i); i += 2
        return s


@dataclass
class HardwareInfo:
    """Decoded 0xE1 frame (HardwareInfoResp, HID layout: index=5)."""
    device_id: list[int] = field(default_factory=list)
    hardware_version: str = ""
    boot_version: str = ""
    app_version: str = ""
    device_name: str = ""
    raw: bytes = b""

    @classmethod
    def parse(cls, frame_values: bytes, index: int = 5) -> "HardwareInfo":
        v = frame_values
        i = index
        info = cls(raw=bytes(v))
        info.device_id = [v[i + k] for k in range(8)]; i += 8
        hw = [v[i + k] for k in range(4)]; i += 4
        bt = [v[i + k] for k in range(4)]; i += 4
        ap = [v[i + k] for k in range(4)]; i += 4
        name = bytes(v[i:i + 10]); i += 10
        info.hardware_version = "V{}.{}.{}.{}".format(*hw)
        info.boot_version = "V{}.{}.{}.{}".format(*bt)
        info.app_version = "V{}.{}.{}.{}".format(*ap)
        info.device_name = name.split(b"\x00")[0].decode("ascii", "replace").strip()
        return info
