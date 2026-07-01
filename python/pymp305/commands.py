"""Transport-agnostic request builders for the advanced subsystems (USB-PD, programmable
sequences, charge read/search). Each returns ``(cmd_byte, payload_bytes)``; the HID and BLE
layers add their own framing. Field order/units transcribed from DP3005Req.js."""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import protocol as P

# ---- simple no-payload requests -----------------------------------------
def pdo_main():            return (0xE4, b"")        # -> 0xE5
def pdo_main_info():       return (0xE6, b"")        # -> 0xE7
def programmable_list():   return (0xD4, b"")        # -> 0xD5 (ProgramList)
def programmable_main():   return (0xDC, b"")        # -> 0xDD
def programmable_info():   return (0xDE, b"")        # -> 0xDF (ProgramState)
def charge_info():         return (0xEC, b"")        # -> 0xED (ChargeState)
def charge_search():       return (0xEA, b"")        # -> 0xEB (ChargeInfo)


def pdo_search(pdo_id: int):
    """Read one stored USB-PD profile by id. -> 0xD1 (PDO)."""
    return (0xD0, bytes([pdo_id & 0xFF]))


def programmable_read(seq_id: int):
    """Read the steps of one stored programmable sequence. -> 0xD9 (ProgramSteps)."""
    return (0xD8, bytes([seq_id & 0xFF]))


@dataclass
class PDOConnect:
    """0xE8 — USB-PD source connect/control (NOT a profile selector). -> 0xE9.

    `src_enable_mask` is a **bitmask of which PDO items in the active profile are
    advertised/live** (bit i = item i), matching WebLink's `pdoIndex` (built from
    each item's `type`) and the Android app. It does NOT select a stored profile —
    there is no remote "set active profile" command (read via 0xE4/0xD0; rewrite a
    profile with 0xD2; pick the active one on the device). `update=1` changes the
    advertised set; `update=0` is a plain output toggle.
    """
    remote_con: int = 1
    src_enable_mask: int = 0
    update: int = 0
    output: int = 0
    model: int = 2          # USB-PD
    def build(self):
        return (0xE8, struct.pack("<BHBBB", self.remote_con & 0xFF,
                                  self.src_enable_mask & 0xFFFF, self.update & 0xFF,
                                  self.output & 0xFF, self.model & 0xFF))


@dataclass
class ProgramConnect:
    """0xE2 — run/stop a programmable sequence. -> 0xE3."""
    remote_con: int = 1
    program_control: int = 0
    output: int = 0
    model: int = 1          # programmable
    def build(self):
        return (0xE2, bytes([self.remote_con & 0xFF, self.program_control & 0xFF,
                             self.output & 0xFF, self.model & 0xFF]))


def programmable_change(seq_id: int, name: str, num: int, is_last: int = 1,
                        remove: int = 0):
    """0xD6 — create/rename/delete a sequence slot. -> 0xD7."""
    nm = name.encode("ascii", "replace")[:16].ljust(16, b"\x00")
    return (0xD6, bytes([seq_id & 0xFF]) + nm + bytes([num & 0xFF, is_last & 0xFF, remove & 0xFF]))


def programmable_write(seq_id: int, steps: list[dict]):
    """0xDA — write the steps of a sequence. -> 0xDB.

    Each step is a dict {"V": volts, "A": amps, "S": seconds}. Matches
    DP3005Req.ProgrammableWriteCmd: V/A in mV/mA, S in 0.1 s units; a final step with
    S==0 is sent with raw V/A (sentinel) per the firmware's convention.
    """
    body = bytearray([seq_id & 0xFF])
    n = len(steps)
    for j, st in enumerate(steps):
        s = st.get("S", 0)
        last_zero = (j == n - 1 and s == 0)
        v = int(round(st.get("V", 0))) if last_zero else int(round(st.get("V", 0) * 1000))
        a = int(round(st.get("A", 0))) if last_zero else int(round(st.get("A", 0) * 1000))
        body += struct.pack("<iii", v, a, int(round(s * 10)))
    return (0xDA, bytes(body))


# ---- USB-PD profile write (advanced; bit-packed) ------------------------
def _pack_pdo_item(j: int, item: dict) -> int:
    """Inverse of responses.parse_pdo_item — pack one PDO slot into a u32.
    Transcribed from DP3005Req.pdoWriteCmd; advanced/rarely needed."""
    lo = hi = 0
    t = item.get("type", 0) & 0x07
    res = item.get("reserved", 0)
    if j == 5:
        lo |= t; lo |= (res << 3)
        lo |= int(round(item["max_current_a"] / 0.05)) << 9
        hi |= int(round(item["max_voltage_v"] / 0.1))
        hi |= int(round(item["min_voltage_v"] / 0.1)) << 8
    elif j == 6:
        lo |= t; lo |= (res << 3)
        lo |= int(round(item["max_current_15v_a"] / 0.01)) << 6
        hi |= item.get("reserved1", 0)
        hi |= int(round(item["max_current_20v_a"] / 0.01)) << 6
    elif j == 8:
        lo |= t; lo |= (res << 3)
        lo |= int(round(item["max_voltage_v"] / 0.1)) << 7
        hi |= int(round(item["min_voltage_v"] / 0.1))
        hi |= (item.get("power_w", 0)) << 8
    else:
        lo |= t; lo |= (res << 3)
        lo |= int(round(item["voltage_v"] / 0.05)) << 6
        hi |= item.get("reserved1", 0)
        hi |= int(round(item["current_a"] / 0.01)) << 6
    return ((hi << 16) | lo) & 0xFFFFFFFF


def pdo_write(pdo_id: int, name: str, power: int, items: list[dict], is_last: int = 1):
    """0xD2 — write a USB-PD profile (advanced). -> 0xD3."""
    nm = name.encode("ascii", "replace")[:16].ljust(16, b"\x00")
    body = bytearray([pdo_id & 0xFF]) + nm + bytes([power & 0xFF, len(items) & 0xFF, is_last & 0xFF])
    for j, it in enumerate(items):
        body += struct.pack("<I", _pack_pdo_item(j, it))
    return (0xD2, bytes(body))
