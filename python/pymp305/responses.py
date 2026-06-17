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

    @classmethod
    def parse_ble(cls, frame_values: bytes, index: int = 1) -> "HardwareInfo":
        """BLE hardware-info layout (HardwareInfoResp index=1): the BLE chip's own
        hw/sw versions + the device id, with the PSU hardware version appended if present.
        There is no device-name field over BLE — take that from the advertised name."""
        v = frame_values
        i = index
        info = cls(raw=bytes(v))
        ble_hw_major = v[i]; i += 1
        ble_hw_minor = v[i]; i += 1
        ble_sw_major = v[i]; i += 1
        ble_sw_minor = v[i]; i += 1
        info.device_id = [v[i + k] for k in range(8)]; i += 8
        info.app_version = f"V{ble_hw_major}.{ble_hw_minor}.{ble_sw_major}.{ble_sw_minor}"
        if len(v) > 16:
            hw = [v[i + k] for k in range(4)]; i += 4
            info.hardware_version = "V{}.{}.{}.{}".format(*hw)
        return info


# ---- charge mode (0xED ChargeResp / 0xEB ChargeInfoResp) ----------------
@dataclass
class ChargeState:
    """Decoded 0xED charge-state frame (ChargeResp)."""
    battery_state: int = 0
    percentage: int = 0
    current: float = 0.0          # A
    capacity_current: float = 0.0 # mAh accumulated (raw, mAh)
    battery_type: int = 0
    cells: int = 0
    voltage: float = 0.0          # V
    energy: float = 0.0           # Wh
    working_time: int = 0         # s
    power: float = 0.0            # W
    charge_full: int = 0
    output: int = 0
    model: int = 0
    temperature: int = 0          # °C
    charge_error: int = 0
    errors: list[str] = field(default_factory=list)
    raw: bytes = b""

    @classmethod
    def parse(cls, v: bytes, index: int = 5, device_name: str | None = None) -> "ChargeState":
        i = index
        s = cls(raw=bytes(v))
        s.battery_state = _u8(v, i); i += 1
        s.percentage = _u8(v, i); i += 1
        s.current = _u16(v, i) / 1000.0; i += 2
        s.capacity_current = _u32(v, i); i += 4         # mAh
        s.battery_type = _u8(v, i); i += 1
        s.cells = _u8(v, i); i += 1
        s.voltage = _u16(v, i) / 100.0; i += 2
        s.energy = _u32(v, i) / 1000.0; i += 4
        s.working_time = _u32(v, i); i += 4
        s.power = _u16(v, i) / 100.0; i += 2
        s.charge_full = _u8(v, i); i += 1
        s.output = _u8(v, i); i += 1
        s.model = _u8(v, i); i += 1
        s.temperature = _u8(v, i); i += 1
        # 32-bit error word present on longer frames (HID values[0]>31 / BLE len>28)
        long = (index == 5 and v[0] > 31) or (index == 2 and len(v) > 28)
        if long and i + 3 < len(v):
            s.charge_error = _u32(v, i); i += 4
            s.errors = decode_errors(s.charge_error, device_name, charge_mode=True)
        return s


@dataclass
class ChargeInfo:
    """Decoded 0xEB charge-settings frame (ChargeInfoResp): current + previous run."""
    battery_type: int = 0
    capacity_voltage: int = 0
    cells: int = 0
    current: float = 0.0
    prev_battery_type: int = 0
    prev_cells: int = 0
    prev_capacity_current: int = 0
    prev_energy: int = 0
    prev_working_time: int = 0
    raw: bytes = b""

    @classmethod
    def parse(cls, v: bytes, index: int = 5) -> "ChargeInfo":
        i = index
        s = cls(raw=bytes(v))
        s.battery_type = _u8(v, i); i += 1
        s.capacity_voltage = _u16(v, i); i += 2
        s.cells = _u8(v, i); i += 1
        s.current = _u16(v, i) / 1000.0; i += 2
        s.prev_battery_type = _u8(v, i); i += 1
        s.prev_cells = _u8(v, i); i += 1
        s.prev_capacity_current = _u32(v, i); i += 4
        s.prev_energy = _u32(v, i); i += 4
        s.prev_working_time = _u32(v, i); i += 4
        return s


# ---- USB-PD (0xD1 PDOResp) ----------------------------------------------
def parse_pdo_item(j: int, value: int) -> dict:
    """Decode one 32-bit PDO entry. The interpretation depends on the slot index `j`
    (transcribed from pdoResp.js): j<5 fixed PDO, j==5 augmented PDO, j==6 SPR-AVS,
    j==8 EPR-AVS, else fixed PDO."""
    fw = value & 0xFFFF
    sw = (value >> 16) & 0xFFFF
    type_on = (fw & 0x07) != 0
    if j == 5:        # APDO
        return {"kind": "APDO", "type": type_on, "reserved": (fw >> 3) & 0x3F,
                "max_current_a": ((fw >> 9) & 0x1FF) * 0.05,
                "max_voltage_v": (sw & 0xFF) * 0.1,
                "min_voltage_v": ((sw >> 8) & 0xFF) * 0.1}
    if j == 6:        # SPR-AVS
        return {"kind": "SPRAVS", "type": type_on, "reserved": (fw >> 3) & 0x07,
                "max_current_15v_a": ((fw >> 6) & 0x3FF) * 0.01,
                "max_current_20v_a": ((sw >> 6) & 0x3FF) * 0.01}
    if j == 8:        # EPR-AVS
        return {"kind": "EPRAVS", "type": type_on, "reserved": (fw >> 3) & 0x0F,
                "max_voltage_v": ((fw >> 7) & 0x1FF) * 0.1,
                "min_voltage_v": (sw & 0xFF) * 0.1,
                "power_w": (sw >> 8) & 0xFF}
    # fixed PDO (j 0-4, 7, >=9)
    return {"kind": "FPDO", "type": type_on, "reserved": (fw >> 3) & 0x07,
            "voltage_v": ((fw >> 6) & 0x3FF) * 0.05,
            "current_a": ((sw >> 6) & 0x3FF) * 0.01}


@dataclass
class PDO:
    """Decoded 0xD1 USB-PD profile (PDOResp)."""
    pdo_id: int = -1
    name: str = ""
    power: int = 0
    number: int = 0
    items: list[dict] = field(default_factory=list)
    raw: bytes = b""

    @classmethod
    def parse(cls, v: bytes, index: int = 5) -> "PDO":
        i = index
        p = cls(raw=bytes(v))
        p.pdo_id = _u8(v, i); i += 1
        name = bytes(v[i:i + 16]); i += 16
        p.name = name.split(b"\x00")[0].decode("ascii", "replace").strip()
        p.power = _u8(v, i); i += 1
        p.number = _u8(v, i); i += 1
        for j in range(p.number):
            val = _u32(v, i); i += 4
            p.items.append(parse_pdo_item(j, val))
        return p


# ---- programmable sequences ---------------------------------------------
@dataclass
class ProgramState:
    """Decoded 0xDF programmable-run state (ProgramResp), including e-marker info."""
    out_state: int = 0
    battery_state: int = 0
    percentage: int = 0
    voltage: float = 0.0
    current: float = 0.0
    working_time: int = 0
    energy: float = 0.0
    power: float = 0.0
    working_index: int = 0
    output: int = 0
    model: int = 0
    temperature: int = 0
    is_stop: int = 0
    frequency: int = 0
    emark: dict = field(default_factory=dict)
    charge_error: int = 0
    wave_time: int = 0
    raw: bytes = b""

    @classmethod
    def parse(cls, v: bytes, index: int = 5) -> "ProgramState":
        i = index
        s = cls(raw=bytes(v))
        s.out_state = _u8(v, i); i += 1
        s.battery_state = _u8(v, i); i += 1
        s.percentage = _u8(v, i); i += 1
        s.voltage = _u16(v, i) / 100.0; i += 2
        s.current = _u16(v, i) / 1000.0; i += 2
        s.working_time = _u32(v, i); i += 4
        s.energy = _u32(v, i) / 1000.0; i += 4
        s.power = _u16(v, i) / 100.0; i += 2
        s.working_index = _u8(v, i); i += 1
        s.output = _u8(v, i); i += 1
        s.model = _u8(v, i); i += 1
        s.temperature = _u8(v, i); i += 1
        s.is_stop = _u8(v, i); i += 1
        s.frequency = _u32(v, i); i += 4
        em = {}
        em["emark"] = _u8(v, i); i += 1
        em["gen"] = _u8(v, i); i += 1
        em["type"] = _u8(v, i); i += 1
        em["profile"] = _u16(v, i); i += 2
        em["voltage"] = _u8(v, i); i += 1
        em["current"] = _u8(v, i); i += 1
        em["power"] = _u8(v, i); i += 1
        em["epr"] = _u8(v, i); i += 1
        em["delay"] = _u8(v, i); i += 1
        em["speed"] = _u8(v, i); i += 1
        em["format"] = _u8(v, i); i += 1
        em["id"] = _u32(v, i); i += 4
        em["cert_stat"] = _u32(v, i); i += 4
        em["product"] = _u32(v, i); i += 4
        em["cable"] = _u32(v, i); i += 4
        em["svid"] = _u32(v, i); i += 4
        em["tbt"] = _u32(v, i); i += 4
        s.emark = em
        long = (index == 5 and v[0] > 67) or (index == 2 and len(v) > 64)
        if long and i + 1 < len(v):
            s.charge_error = _u16(v, i); i += 2
        wave = (index == 5 and v[0] > 69) or (index == 2 and len(v) > 66)
        if wave and i + 3 < len(v):
            s.wave_time = _u32(v, i); i += 4
        return s


@dataclass
class ProgramEntry:
    name: str = ""
    num: int = 0


@dataclass
class ProgramList:
    """Decoded 0xD5 list of stored programmable sequences (ProgrammableSearchResp)."""
    entries: list[ProgramEntry] = field(default_factory=list)
    raw: bytes = b""

    @classmethod
    def parse(cls, v: bytes, index: int = 5) -> "ProgramList":
        i = index
        p = cls(raw=bytes(v))
        count = _u8(v, i); i += 1
        for _ in range(count):
            name = bytes(v[i:i + 16]); i += 16
            num = _u8(v, i); i += 1
            p.entries.append(ProgramEntry(
                name=name.split(b"\x00")[0].decode("ascii", "replace").strip(), num=num))
        return p


@dataclass
class ProgramSteps:
    """Decoded 0xD9 steps of one programmable sequence (ProgrammableOutputResp).
    Each step: V (volts), A (amps), S (seconds)."""
    id: int = -1
    steps: list[dict] = field(default_factory=list)
    raw: bytes = b""

    @classmethod
    def parse(cls, v: bytes, number: int, index: int = 5) -> "ProgramSteps":
        i = index
        p = cls(raw=bytes(v))
        p.id = _u8(v, i); i += 1
        for k in range(number):
            volt = _u32(v, i); i += 4
            cur = _u32(v, i); i += 4
            t = _u32(v, i); i += 4
            last_zero = (k == number - 1 and t // 10 == 0)
            p.steps.append({
                "V": volt if last_zero else volt / 1000.0,
                "A": cur if last_zero else cur / 1000.0,
                "S": t / 10.0,
            })
        return p
