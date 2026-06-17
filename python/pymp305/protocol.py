"""Wire-protocol framing for the ISDT MP305 (MP305A / MP305B) over USB-HID.

Transcribed from the official WebLink web app (see ../../PROTOCOL.md). All framing
quirks (length byte, 0xAA stuffing, checksum) are reproduced exactly so the
checksums match what the firmware expects.
"""
from __future__ import annotations

from dataclasses import dataclass

# USB-HID identity (from HIDDeviceManager.connect)
VENDOR_ID = 0x28E9          # GigaDevice
HID_USAGE_PAGE = 0x01
HID_USAGE = 0x04
REPORT_ID = 0x01
REPORT_SIZE = 64            # output report payload size (zero padded)

FRAME_START = 0xAA
GROUP_ID = 0x12

# ---- command bytes -------------------------------------------------------
CMD_HW_INFO        = 0xE0   # -> 0xE1
CMD_STATE_INFO     = 0xC2   # -> 0xC3
CMD_REALTIME       = 0xBD   # -> 0xC3
CMD_SYS_GET        = 0xC4   # -> 0xC5
CMD_SYS_SET        = 0xC6   # -> 0xC7
CMD_CONTROL        = 0xC8   # -> 0xC9
CMD_CHARGE_INFO    = 0xEC   # -> 0xED
CMD_CHARGE_SEARCH  = 0xEA   # -> 0xEB
CMD_CHARGE_CONTROL = 0xEE   # -> 0xEF
CMD_SET_LANGUAGE   = 0xA2   # -> 0xA3

# response command bytes
RESP_HW_INFO   = 0xE1
RESP_STATE     = 0xC3
RESP_SYS       = 0xC5
RESP_SYS_SET   = 0xC7
RESP_CONTROL   = 0xC9
RESP_CHARGE    = 0xED

# raw multi-byte payloads for boot/reboot (cmd byte + extra data bytes)
BOOT_PAYLOAD   = bytes([0xF0, 0xAC])   # jump to bootloader
REBOOT_PAYLOAD = bytes([0xFC, 0xCA])   # reboot device


def _add_0xAA(data: list[int]) -> list[int]:
    """Double every 0xAA in the DATA region (index > 5), matching Cmd.add0xAA."""
    out: list[int] = []
    for i, b in enumerate(data):
        out.append(b)
        if b == FRAME_START and i > 5:
            out.append(FRAME_START)
    return out


def _process_hex_array(arr: list[int]) -> list[int]:
    """Set the length byte [0] and checksum byte [last]; matches Cmd.processHexArray."""
    a = list(arr)
    a[0] = (len(a) - 1) & 0xFF

    total = 0
    prev_is_aa = False
    for i in range(2, len(a) - 1):          # index 2 .. second-to-last
        v = a[i]
        cur_is_aa = v == FRAME_START
        if cur_is_aa and prev_is_aa:        # consecutive 0xAA counted once
            prev_is_aa = False
            continue
        total += v
        prev_is_aa = cur_is_aa
    a[-1] = total & 0xFF

    if a[-1] == FRAME_START:                # avoid a checksum that looks like a marker
        a.append(FRAME_START)
        a[0] = (a[0] + 1) & 0xFF
    return a


def build_frame(cmd: int, payload: bytes = b"") -> bytes:
    """Build a complete HID frame (without the report-ID byte) for `cmd`+`payload`."""
    body = [0x00, FRAME_START, GROUP_ID, (1 + len(payload)) & 0xFF, cmd, *payload, 0x00]
    body = _add_0xAA(body)
    return bytes(_process_hex_array(body))


def build_report(cmd: int, payload: bytes = b"", report_size: int = REPORT_SIZE) -> bytes:
    """Build the full bytes to hand to a hidapi `write()`:  report-id + frame + padding."""
    frame = build_frame(cmd, payload)
    buf = bytes([REPORT_ID]) + frame
    if report_size and len(buf) < report_size + 1:
        buf += b"\x00" * (report_size + 1 - len(buf))
    return buf


@dataclass
class Frame:
    cmd: int
    payload: bytes      # bytes after the cmd byte, de-stuffed
    values: bytes       # full de-stuffed frame: [N, 0xAA, 0x12, L, cmd, ...]


def parse_report(raw: bytes) -> Frame | None:
    """De-stuff and parse a raw HID input report (may or may not include the report-ID).

    Returns None if no valid [0xAA, 0x12] header is found.
    """
    buf = bytes(raw)
    # Locate the 0xAA,0x12 header; the byte before it is the length byte N.
    start = -1
    for i in range(len(buf) - 1):
        if buf[i] == FRAME_START and buf[i + 1] == GROUP_ID:
            start = i - 1
            break
    if start < 0:
        return None
    frame = list(buf[start:])

    # De-stuff consecutive 0xAA (keep first), decrement N for each dropped byte.
    values: list[int] = []
    prev_is_aa = False
    for b in frame:
        cur_is_aa = b == FRAME_START
        if cur_is_aa and prev_is_aa:
            if values:
                values[0] = (values[0] - 1) & 0xFF
        else:
            values.append(b)
        prev_is_aa = cur_is_aa

    if len(values) < 6:
        return None
    cmd = values[4]
    payload = bytes(values[5:-1])   # exclude the trailing checksum byte
    return Frame(cmd=cmd, payload=payload, values=bytes(values))
