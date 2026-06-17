"""Tests for charge/PDO/programmable parsers, BLE framing, and the command builders.
No hardware required."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymp305 import protocol as P
from pymp305 import commands as C
from pymp305.responses import (
    ChargeState, PDO, ProgramList, ProgramSteps, parse_pdo_item, HardwareInfo,
)


def _hid_frame(cmd, payload):
    """Build a HID frame and return the de-stuffed values array a parser would see."""
    return P.parse_report(bytes([P.REPORT_ID]) + P.build_frame(cmd, payload)).values


# ---- BLE framing ---------------------------------------------------------
def test_ble_frame_build_and_parse():
    f = P.build_ble_frame(P.CMD_CONTROL, b"\x01\x02")
    assert f == bytes([0x12, P.CMD_CONTROL, 0x01, 0x02])
    fr = P.parse_ble_notification(f)
    assert fr.cmd == P.CMD_CONTROL and fr.payload == b"\x01\x02"
    # AF02 direct frame (cmd at index 0)
    fr2 = P.parse_ble_notification(bytes([0xE1, 0xAA, 0xBB]))
    assert fr2.cmd == 0xE1 and fr2.payload == b"\xAA\xBB"


def test_ble_binding_packet():
    pkt = P.build_ble_binding(bytes(range(16)), fast_binding=0, status=0)
    assert pkt[0] == 0x18 and len(pkt) == 19
    assert list(pkt[1:17]) == list(range(16)) and pkt[17] == 0 and pkt[18] == 0


def test_ble_state_index2():
    # A BLE AF01 state frame [0x12, 0xC3, ...payload] parses at index 2.
    payload = (bytes([1, 0, 50]) + struct.pack("<H", 500) + struct.pack("<H", 500)
               + struct.pack("<H", 1000) + struct.pack("<H", 1000)
               + struct.pack("<I", 10) + struct.pack("<I", 5000) + struct.pack("<H", 500)
               + bytes([0, 0, 0, 1, 0, 0, 0, 25]))
    from pymp305.responses import State
    fr = P.parse_ble_notification(bytes([0x12, P.RESP_STATE]) + payload)
    st = State.parse(fr.values, index=P.BLE_PARSE_INDEX)
    assert abs(st.voltage - 5.0) < 1e-9 and st.output == 1 and st.temperature == 25


# ---- charge --------------------------------------------------------------
def test_charge_state_decode():
    payload = bytearray()
    payload += bytes([2, 80])                  # battery_state, percentage
    payload += struct.pack("<H", 2000)         # current 2.0 A
    payload += struct.pack("<I", 1500)         # capacity 1500 mAh
    payload += bytes([1, 3])                   # battery_type LiPo, cells 3
    payload += struct.pack("<H", 1260)         # voltage 12.60 V
    payload += struct.pack("<I", 5000)         # energy 5.0 Wh
    payload += struct.pack("<I", 600)          # working_time 600 s
    payload += struct.pack("<H", 2520)         # power 25.20 W
    payload += bytes([0, 1, 3, 30])            # chargeFull, output, model, temp 30C
    st = ChargeState.parse(_hid_frame(P.RESP_CHARGE, bytes(payload)), index=5)
    assert st.percentage == 80
    assert abs(st.current - 2.0) < 1e-9
    assert abs(st.voltage - 12.6) < 1e-9
    assert abs(st.power - 25.2) < 1e-9
    assert st.cells == 3 and st.output == 1 and st.temperature == 30
    assert st.errors == []          # short frame -> no error word


# ---- USB-PD --------------------------------------------------------------
def test_pdo_item_roundtrip():
    # fixed PDO 5V/3A
    val = C._pack_pdo_item(0, {"type": 1, "voltage_v": 5.0, "current_a": 3.0})
    item = parse_pdo_item(0, val)
    assert item["kind"] == "FPDO"
    assert abs(item["voltage_v"] - 5.0) < 1e-6
    assert abs(item["current_a"] - 3.0) < 1e-6
    # augmented PDO (j=5): 3.3-11V @ 5A
    val5 = C._pack_pdo_item(5, {"type": 1, "max_current_a": 5.0,
                                "max_voltage_v": 11.0, "min_voltage_v": 3.3})
    a = parse_pdo_item(5, val5)
    assert a["kind"] == "APDO"
    assert abs(a["max_current_a"] - 5.0) < 1e-6
    assert abs(a["max_voltage_v"] - 11.0) < 1e-6


def test_pdo_frame_decode():
    item = struct.pack("<I", C._pack_pdo_item(0, {"type": 1, "voltage_v": 9.0, "current_a": 2.0}))
    name = b"PD-9V".ljust(16, b"\x00")
    payload = bytes([7]) + name + bytes([60, 1]) + item     # id=7, power=60W, number=1
    pdo = PDO.parse(_hid_frame(P.RESP_PDO, payload), index=5)
    assert pdo.pdo_id == 7 and pdo.name == "PD-9V" and pdo.power == 60
    assert pdo.number == 1 and abs(pdo.items[0]["voltage_v"] - 9.0) < 1e-6


# ---- programmable --------------------------------------------------------
def test_program_list_decode():
    e1 = b"seqA".ljust(16, b"\x00") + bytes([3])
    e2 = b"seqB".ljust(16, b"\x00") + bytes([5])
    payload = bytes([2]) + e1 + e2
    pl = ProgramList.parse(_hid_frame(P.RESP_PROGRAM_LIST, payload), index=5)
    assert [(e.name, e.num) for e in pl.entries] == [("seqA", 3), ("seqB", 5)]


def test_program_steps_decode():
    # two steps: 5V/1A/10s, then a terminating 0s step
    payload = bytearray([0])  # id
    payload += struct.pack("<iii", 5000, 1000, 100)     # 5V,1A,10.0s
    payload += struct.pack("<iii", 12, 2, 0)            # last step, S=0 -> raw V/A
    ps = ProgramSteps.parse(_hid_frame(P.RESP_PROGRAM_STEPS, bytes(payload)), number=2, index=5)
    assert abs(ps.steps[0]["V"] - 5.0) < 1e-9 and ps.steps[0]["S"] == 10.0
    assert ps.steps[1]["V"] == 12 and ps.steps[1]["S"] == 0.0   # sentinel


def test_program_write_builder():
    cmd, payload = C.programmable_write(0, [{"V": 5.0, "A": 1.0, "S": 10},
                                            {"V": 12.0, "A": 0.0, "S": 0}])
    assert cmd == 0xDA and payload[0] == 0
    # first step encoded as mV/mA/0.1s
    v, a, s = struct.unpack("<iii", payload[1:13])
    assert (v, a, s) == (5000, 1000, 100)


def test_write_builders_roundtrip():
    # programmable write/change + pdo write produce frames that parse back with right cmds
    cmd, pl = C.programmable_write(2, [{"V": 5.0, "A": 1.0, "S": 10}])
    assert cmd == 0xDA
    assert P.parse_report(bytes([P.REPORT_ID]) + P.build_frame(cmd, pl)).cmd == 0xDA
    cmd, pl = C.programmable_change(2, "seqX", 1, is_last=1, remove=0)
    assert cmd == 0xD6 and pl[1:17].rstrip(b"\x00") == b"seqX"
    cmd, pl = C.pdo_write(3, "PD", 60, [{"type": 1, "voltage_v": 5.0, "current_a": 3.0}])
    assert cmd == 0xD2
    assert P.parse_report(bytes([P.REPORT_ID]) + P.build_frame(cmd, pl)).cmd == 0xD2


def test_emark_annotate():
    from pymp305.responses import annotate_emark
    em = {"emark": 1, "speed": 2, "format": 1}
    a = annotate_emark(em)
    assert a["present"] is True
    assert a["speed_label"] == "USB3.2/USB4 Gen2 (10Gbps/20Gbps)"
    assert a["format_label"] == "V:2"
    assert annotate_emark({"emark": 0, "speed": 99})["present"] is False


def test_bootinfo_parse():
    from pymp305 import ota
    # [type=1('B'), offset(4 LE)=0x1000, blockSize lo,hi=0x00,0x08 ->2048, support, appid]
    data = bytes([1, 0x00, 0x10, 0x00, 0x00, 0x00, 0x08, 0x01, 0x05])
    bi = ota.BootInfo.parse(data)
    assert bi.type == "B" and bi.offset == 0x1000 and bi.block_size == 2048
    assert bi.support_0x85 == 1 and bi.app_id == 5


def test_hardware_info_ble():
    # [0xE1, bleHwMaj, bleHwMin, bleSwMaj, bleSwMin, id(8), hw(4)]
    v = bytes([0xE1, 1, 2, 3, 4]) + bytes(range(8)) + bytes([5, 6, 7, 8])
    info = HardwareInfo.parse_ble(v, index=1)
    assert info.app_version == "V1.2.3.4"
    assert info.hardware_version == "V5.6.7.8"
    assert info.device_id == list(range(8))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nAll {len(fns)} advanced tests passed.")
