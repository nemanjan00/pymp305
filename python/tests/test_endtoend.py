"""End-to-end tests: drive real MP305 methods through a fake HID device, asserting both
the request bytes sent AND the decoded response object. Exercises the method wiring
(command selection, payload build, response decode) — the layer above pure parsing.
No hardware."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymp305 import protocol as P
from pymp305 import commands as C
from pymp305.device import MP305


class FakeHID:
    def __init__(self, responses=None):
        self.written = []
        self.responses = list(responses or [])

    def write(self, data):
        self.written.append(bytes(data)); return len(data)

    def read(self, n, timeout=0):
        return list(self.responses.pop(0)) if self.responses else []

    def set_nonblocking(self, v): pass
    def close(self): pass


def _resp(cmd, payload=b""):
    return bytes([P.REPORT_ID]) + P.build_frame(cmd, payload)


def _state_payload(*, volt=1234, setv=2000, cur=1500, setc=3000, out=1, model=0, temp=27):
    return (bytes([1, 0, 73]) + struct.pack("<HHHH", volt, setv, cur, setc)
            + struct.pack("<II", 3661, 12345) + struct.pack("<H", 1850)
            + bytes([1, 0, 0, out, model, 0, 0, temp]))


def test_e2e_read_state():
    psu = MP305(FakeHID([_resp(P.RESP_STATE, _state_payload())]))
    st = psu.read_state()
    assert psu._dev.written[0] == P.build_report(P.CMD_REALTIME)   # polled with 0xBD
    assert abs(st.voltage - 12.34) < 1e-9 and st.output == 1 and st.temperature == 27


def test_e2e_read_state_nonrealtime_uses_c2():
    psu = MP305(FakeHID([_resp(P.RESP_STATE, _state_payload())]))
    psu.read_state(realtime=False)
    assert psu._dev.written[0] == P.build_report(P.CMD_STATE_INFO)  # 0xC2


def test_e2e_hardware_info_sets_model():
    payload = bytes(range(8)) + bytes([1, 2, 3, 4]) + bytes([5, 6, 7, 8]) + bytes([9, 10, 11, 12]) \
        + b"MP305B\x00\x00\x00\x00"
    psu = MP305(FakeHID([_resp(P.RESP_HW_INFO, payload)]))
    info = psu.hardware_info()
    assert info.device_name == "MP305B" and psu.device_name == "MP305B"
    assert info.hardware_version == "V1.2.3.4"


def test_e2e_read_charge_state():
    pl = (bytes([2, 80]) + struct.pack("<H", 2000) + struct.pack("<I", 1500) + bytes([1, 3])
          + struct.pack("<H", 1260) + struct.pack("<I", 5000) + struct.pack("<I", 600)
          + struct.pack("<H", 2520) + bytes([0, 1, 3, 30]))
    psu = MP305(FakeHID([_resp(P.RESP_CHARGE, pl)]))
    cs = psu.read_charge_state()
    assert psu._dev.written[0] == P.build_report(P.CMD_CHARGE_INFO)   # 0xEC
    assert cs.percentage == 80 and abs(cs.voltage - 12.6) < 1e-9 and cs.cells == 3


def test_e2e_read_pdo_request_and_decode():
    item = struct.pack("<I", C._pack_pdo_item(0, {"type": 1, "voltage_v": 9.0, "current_a": 2.0}))
    pl = bytes([7]) + b"PD-9V".ljust(16, b"\x00") + bytes([60, 1]) + item
    psu = MP305(FakeHID([_resp(P.RESP_PDO, pl)]))
    pdo = psu.read_pdo(7)
    assert psu._dev.written[0] == P.build_report(*C.pdo_search(7))    # 0xD0 + id
    assert pdo.pdo_id == 7 and pdo.name == "PD-9V" and abs(pdo.items[0]["voltage_v"] - 9.0) < 1e-6


def test_e2e_read_program_list():
    pl = bytes([2]) + b"A".ljust(16, b"\x00") + bytes([3]) + b"B".ljust(16, b"\x00") + bytes([5])
    psu = MP305(FakeHID([_resp(P.RESP_PROGRAM_LIST, pl)]))
    pgl = psu.read_program_list()
    assert [(e.name, e.num) for e in pgl.entries] == [("A", 3), ("B", 5)]


def test_e2e_set_output_builds_control():
    # set_output: read_state -> control(0xC8) -> read_state
    psu = MP305(FakeHID([
        _resp(P.RESP_STATE, _state_payload(setv=500, setc=1000, out=0)),
        _resp(P.RESP_CONTROL, b"\x00"),
        _resp(P.RESP_STATE, _state_payload(setv=900, setc=2000, out=1)),
    ]))
    st = psu.set_output(voltage=9.0, current=2.0, on=True)
    # 2nd write is the 0xC8 control frame
    ctrl = P.parse_report(psu._dev.written[1])
    assert ctrl.cmd == P.CMD_CONTROL
    rc, sv, sc, rch, vs, co, out, model, refresh = struct.unpack("<BHHBBBBBB", ctrl.payload)
    assert (rc, sv, sc, out) == (1, 900, 2000, 1)     # 9.00V*100, 2.000A*1000, output on
    assert st.output == 1 and abs(st.set_voltage - 9.0) < 1e-9


def test_e2e_write_program_request():
    psu = MP305(FakeHID([_resp(P.RESP_PROGRAM_WRITE, b"\x00")]))
    psu.write_program(1, [{"V": 5.0, "A": 1.0, "S": 10}])
    assert P.parse_report(psu._dev.written[0]).cmd == 0xDA


def test_e2e_pdo_connect_request():
    psu = MP305(FakeHID([_resp(P.RESP_PDO_CONNECT, b"\x00")]))
    psu.pdo_connect(C.PDOConnect(pdo_index=2, output=1))
    f = P.parse_report(psu._dev.written[0])
    assert f.cmd == 0xE8 and f.payload[1] == 2     # pdo_index low byte


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
