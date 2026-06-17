"""Framing tests — golden vectors derived by hand from the WebLink JS (Cmd.js),
plus encode/decode round-trips. No hardware required:  python -m pytest (or run directly)."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymp305b import protocol as P
from pymp305b.device import ControlCommand
from pymp305b.responses import State


def test_info_frame_golden():
    # Cmd.INFO_DATA: [0x00,0xAA,0x12,0x01,0xE0,0x00] -> [0x05,0xAA,0x12,0x01,0xE0,0xF3]
    assert P.build_frame(0xE0) == bytes([0x05, 0xAA, 0x12, 0x01, 0xE0, 0xF3])


def test_state_request_golden():
    # DP3005Req.INFO_DATA cmd 0xC2 ; checksum = 0x12+0x01+0xC2 = 0xD5
    assert P.build_frame(0xC2) == bytes([0x05, 0xAA, 0x12, 0x01, 0xC2, 0xD5])
    # SEARCH cmd 0xC4 ; checksum = 0x12+0x01+0xC4 = 0xD7
    assert P.build_frame(0xC4) == bytes([0x05, 0xAA, 0x12, 0x01, 0xC4, 0xD7])


def test_report_has_report_id_and_padding():
    rep = P.build_report(0xE0)
    assert rep[0] == P.REPORT_ID
    assert rep[1:7] == bytes([0x05, 0xAA, 0x12, 0x01, 0xE0, 0xF3])
    assert len(rep) == P.REPORT_SIZE + 1


def test_control_payload_units():
    # 5.00 V -> 500 (0x01F4), 1.000 A -> 1000 (0x03E8)
    cmd = ControlCommand(remote_con=1, set_voltage=5.0, set_current=1.0,
                         real_change=3, output=1, model=0)
    pl = cmd.payload()
    rc, sv, sc, rch, vs, co, out, model, refresh = struct.unpack("<BHHBBBBBB", pl)
    assert (rc, sv, sc, out, model) == (1, 500, 1000, 1, 0)
    # full frame for 0xC8 should checksum cleanly and round-trip through the parser
    frame = P.build_frame(P.CMD_CONTROL, pl)
    parsed = P.parse_report(bytes([P.REPORT_ID]) + frame)
    assert parsed.cmd == P.CMD_CONTROL
    assert parsed.payload == pl


def test_parse_strips_report_id_and_finds_header():
    frame = P.build_frame(0xE0)
    # with report id (hidraw style)
    assert P.parse_report(bytes([0x01]) + frame).cmd == 0xE0
    # without report id
    assert P.parse_report(frame).cmd == 0xE0
    # with trailing zero padding
    assert P.parse_report(bytes([0x01]) + frame + b"\x00" * 50).cmd == 0xE0


def _build_state_payload():
    # Construct a realistic 0xC3 state payload and verify decoded units.
    p = b""
    p += bytes([1, 0, 73])                       # outState, batteryState, percentage=73%
    p += struct.pack("<H", 1234)                 # voltage 12.34 V
    p += struct.pack("<H", 2000)                 # setVoltage 20.00 V
    p += struct.pack("<H", 1500)                 # current 1.5 A
    p += struct.pack("<H", 3000)                 # setCurrent 3.0 A
    p += struct.pack("<I", 3661)                 # workingTime 3661 s
    p += struct.pack("<I", 12345)                # energy 12.345 Wh
    p += struct.pack("<H", 1850)                 # power 18.50 W
    p += bytes([1, 0, 0, 1, 0, 0, 0, 27])        # currentOver..temperature(27C)
    return p


def test_state_decode():
    payload = _build_state_payload()
    frame = P.build_frame(P.RESP_STATE, payload)
    parsed = P.parse_report(bytes([P.REPORT_ID]) + frame)
    st = State.parse(parsed.values)
    assert st.percentage == 73
    assert abs(st.voltage - 12.34) < 1e-9
    assert abs(st.set_voltage - 20.0) < 1e-9
    assert abs(st.current - 1.5) < 1e-9
    assert abs(st.set_current - 3.0) < 1e-9
    assert st.working_time == 3661
    assert abs(st.energy - 12.345) < 1e-9
    assert abs(st.power - 18.5) < 1e-9
    assert st.output == 1
    assert st.temperature == 27


def test_0xAA_stuffing_roundtrip():
    # Isolated 0xAA bytes in the DATA region survive the stuff/de-stuff round-trip.
    # NOTE: two *adjacent* 0xAA payload bytes are ambiguous in this protocol (the
    # firmware doubles each on TX but collapses any run to one on RX), so they are
    # unsupported by design — matching the WebLink app's own behaviour.
    payload = bytes([0x10, 0xAA, 0x20, 0xAA, 0x30])
    frame = P.build_frame(0x55, payload)
    parsed = P.parse_report(bytes([P.REPORT_ID]) + frame)
    assert parsed.cmd == 0x55
    assert parsed.payload == payload


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nAll {len(fns)} framing tests passed.")
