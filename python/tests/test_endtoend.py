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
    # default polls with 0xC2 (INFO_DATA) -- WebLink's primary state poll. 0xBD carries the
    # same 0xC3 frame but is a fire-and-forget background command some firmware never answers.
    assert psu._dev.written[0] == P.build_report(P.CMD_STATE_INFO)   # 0xC2
    assert abs(st.voltage - 12.34) < 1e-9 and st.output == 1 and st.temperature == 27


def test_e2e_read_state_realtime_uses_bd():
    psu = MP305(FakeHID([_resp(P.RESP_STATE, _state_payload())]))
    psu.read_state(realtime=True)
    assert psu._dev.written[0] == P.build_report(P.CMD_REALTIME)     # 0xBD (opt-in)


def test_e2e_read_state_realtime_falls_back_to_c2():
    # realtime=True, but 0xBD is never answered on this firmware (the handler emits no reply):
    # request() times out -> read_state() falls back to 0xC2 and remembers it (_no_realtime).
    psu = MP305(FakeHID([_resp(P.RESP_STATE, _state_payload())]))
    psu._no_realtime = True                                          # simulate "already learned"
    psu.read_state(realtime=True)
    assert psu._dev.written[0] == P.build_report(P.CMD_STATE_INFO)   # went straight to 0xC2


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
    # set_output reads the state (mode check), then does the remote handshake:
    #   read_state -> request_remote (0xC8 remoteCon=2) -> control(0xC8 remoteCon=1) -> read_state
    psu = MP305(FakeHID([
        _resp(P.RESP_STATE, _state_payload(setv=500, setc=1000, out=0)),
        _resp(P.RESP_CONTROL, b"\x00"),   # remote request granted
        _resp(P.RESP_CONTROL, b"\x00"),   # setpoint applied
        _resp(P.RESP_STATE, _state_payload(setv=900, setc=2000, out=1)),
    ]))
    st = psu.set_output(voltage=9.0, current=2.0, on=True)
    # 2nd write requests remote control (remoteCon=2), does not touch setpoints
    req = P.parse_report(psu._dev.written[1])
    assert req.cmd == P.CMD_CONTROL and req.payload[0] == 2
    # 3rd write is the actual 0xC8 setpoint frame (remoteCon=1)
    ctrl = P.parse_report(psu._dev.written[2])
    assert ctrl.cmd == P.CMD_CONTROL
    rc, sv, sc, rch, vs, co, out, model, refresh = struct.unpack("<BHHBBBBBB", ctrl.payload)
    assert (rc, sv, sc, out) == (1, 900, 2000, 1)     # 9.00V*100, 2.000A*1000, output on
    assert st.output == 1 and abs(st.set_voltage - 9.0) < 1e-9
    assert psu._remote_held is True


def test_e2e_set_output_rejected_without_remote():
    # if the device refuses remote control (0xC9 status 1), set_output raises
    from pymp305.device import MP305Error
    psu = MP305(FakeHID([_resp(P.RESP_STATE, _state_payload()),
                         _resp(P.RESP_CONTROL, b"\x01")]))   # remote request denied
    try:
        psu.set_output(voltage=5.0, current=1.0, on=True)
        assert False, "expected MP305Error"
    except MP305Error:
        pass


def test_e2e_set_output_reapply_cycles_output():
    # reapply=True should cycle the output off->on after applying so a lowered
    # current limit re-arms; last two control frames must be output=0 then output=1
    psu = MP305(FakeHID([
        _resp(P.RESP_STATE, _state_payload(out=1)),           # read_state (output on)
        _resp(P.RESP_CONTROL, b"\x00"),                       # request_remote
        _resp(P.RESP_CONTROL, b"\x00"),                       # apply setpoint
        _resp(P.RESP_CONTROL, b"\x00"),                       # cycle: output off
        _resp(P.RESP_CONTROL, b"\x00"),                       # cycle: output on
        _resp(P.RESP_STATE, _state_payload(out=1)),           # final read_state
    ]))
    psu.set_output(current=0.003, reapply=True)
    off = struct.unpack("<BHHBBBBBB", P.parse_report(psu._dev.written[3]).payload)
    on = struct.unpack("<BHHBBBBBB", P.parse_report(psu._dev.written[4]).payload)
    assert off[6] == 0 and on[6] == 1     # the output field toggles 0 then 1


def test_e2e_set_output_current_over():
    # set_output(current_over=1) sets the OCP mode byte in the 0xC8 frame (via handshake)
    psu = MP305(FakeHID([
        _resp(P.RESP_STATE, _state_payload()),        # read_state
        _resp(P.RESP_CONTROL, b"\x00"),               # request_remote
        _resp(P.RESP_CONTROL, b"\x00"),               # apply
        _resp(P.RESP_STATE, _state_payload()),        # final read_state
    ]))
    psu.set_output(current_over=1)
    ctrl = struct.unpack("<BHHBBBBBB", P.parse_report(psu._dev.written[2]).payload)
    assert ctrl[5] == 1     # current_over field = OCP


def test_e2e_set_mode_switch():
    # switch DC(0) -> USB-PD(2): read_state, request remote (via current mode 0xC8),
    # switch (0xC8 with new model), then confirm via read_state
    psu = MP305(FakeHID([
        _resp(P.RESP_STATE, _state_payload(model=0)),   # initial read_state (current = DC)
        _resp(P.RESP_CONTROL, b"\x00"),                 # request_remote (0xC8 remoteCon=2)
        _resp(P.RESP_CONTROL, b"\x00"),                 # switch (0xC8 remoteCon=1 model=2)
        _resp(P.RESP_STATE, _state_payload(model=2)),   # confirm -> now in USB-PD
    ]))
    assert psu.set_mode(2) == 2
    req = P.parse_report(psu._dev.written[1])
    assert req.cmd == P.CMD_CONTROL and req.payload[0] == 2      # remote request in current (DC) mode
    sw = P.parse_report(psu._dev.written[2])
    rc, sv, sc, rch, vs, co, out, model, refresh = struct.unpack("<BHHBBBBBB", sw.payload)
    assert (rc, out, model) == (1, 0, 2)                          # apply, output off, new model=USB-PD
    assert psu._model == 2


def test_build_reports_fragments_large_frames():
    # small frame -> one padded report
    small = P.build_reports(P.CMD_STATE_INFO)
    assert len(small) == 1 and small[0][0] == P.REPORT_ID
    # large frame (write_program, 6 steps) -> multiple UNPADDED fragments, each prefixed
    # with its own payload-length byte
    cmd, payload = C.programmable_write(1, [{"V": 5, "A": 3, "S": 10}] * 5 + [{"V": 0, "A": 0, "S": 0}])
    reps = P.build_reports(cmd, payload)
    assert len(reps) >= 2
    assert any(len(r) < P.REPORT_SIZE + 1 for r in reps)   # fragments not padded to report size
    for r in reps:
        assert r[0] == P.REPORT_ID and r[1] == len(r) - 2   # [report-id, len, ...len bytes]


def test_e2e_write_program_request():
    psu = MP305(FakeHID([_resp(P.RESP_PROGRAM_WRITE, b"\x00")]))
    psu.write_program(1, [{"V": 5.0, "A": 1.0, "S": 10}])
    assert P.parse_report(psu._dev.written[0]).cmd == 0xDA


def test_e2e_set_output_retry_raises_on_second_rejection():
    # remote dropped (status 1) -> re-acquire once and retry; a second status 1 raises
    from pymp305.device import MP305Error
    psu = MP305(FakeHID([
        _resp(P.RESP_STATE, _state_payload()),
        _resp(P.RESP_CONTROL, b"\x00"),   # remote granted
        _resp(P.RESP_CONTROL, b"\x01"),   # apply rejected
        _resp(P.RESP_CONTROL, b"\x00"),   # remote re-granted
        _resp(P.RESP_CONTROL, b"\x01"),   # retry rejected again
    ]))
    try:
        psu.set_output(voltage=5.0)
        assert False, "expected MP305Error"
    except MP305Error:
        pass


def test_e2e_set_output_refuses_outside_dc_mode():
    # in USB-PD mode (model 2) a plain set_output must not send 0xC8 (it would switch to DC)
    from pymp305.device import MP305Error
    psu = MP305(FakeHID([_resp(P.RESP_STATE, _state_payload(model=2))]))
    try:
        psu.set_output(voltage=5.0)
        assert False, "expected MP305Error"
    except MP305Error as e:
        assert "mode 2" in str(e)
    assert len(psu._dev.written) == 1                 # only the state read went out
    assert psu._model == 2


def test_e2e_set_output_rejects_non_dc_model():
    from pymp305.device import MP305Error
    psu = MP305(FakeHID([]))
    try:
        psu.set_output(voltage=5.0, model=2)
        assert False, "expected MP305Error"
    except MP305Error:
        pass
    assert psu._dev.written == []


def test_e2e_set_output_model0_switches_to_dc_first():
    # explicit model=0 from USB-PD: set_mode(0) through the PD connect command (0xE8), then 0xC8
    psu = MP305(FakeHID([
        _resp(P.RESP_STATE, _state_payload(model=2)),        # set_output's read_state
        _resp(P.RESP_STATE, _state_payload(model=2)),        # set_mode's read_state
        _resp(P.RESP_PDO_CONNECT, b"\x00"),                  # request_remote via 0xE8
        _resp(P.RESP_PDO_CONNECT, b"\x00"),                  # switch 0xE8 model=0
        _resp(P.RESP_STATE, _state_payload(model=0)),        # set_mode confirm
        _resp(P.RESP_STATE, _state_payload(model=0, setv=500, setc=1000)),  # re-read DC setpoints
        _resp(P.RESP_CONTROL, b"\x00"),                      # apply
        _resp(P.RESP_STATE, _state_payload(model=0)),        # final read_state
    ]))
    psu.set_output(voltage=9.0, model=0)
    cmds = [P.parse_report(w).cmd for w in psu._dev.written]
    assert cmds == [0xC2, 0xC2, 0xE8, 0xE8, 0xC2, 0xC2, 0xC8, 0xC2]
    sw = P.parse_report(psu._dev.written[3])
    assert sw.payload[0] == 1 and sw.payload[-1] == 0               # remoteCon=1, model=0
    rc, sv, sc, *_rest, model, refresh = struct.unpack("<BHHBBBBBB", P.parse_report(psu._dev.written[6]).payload)
    assert (rc, sv, sc, model) == (1, 900, 1000, 0)


def test_e2e_request_remote_uses_current_mode_command():
    # after a read_state in program mode, request_remote goes through 0xE2 (not 0xC8)
    psu = MP305(FakeHID([
        _resp(P.RESP_STATE, _state_payload(model=1)),
        _resp(P.RESP_PROGRAM_CONNECT, b"\x00"),
    ]))
    psu.read_state()
    assert psu.request_remote() is True
    f = P.parse_report(psu._dev.written[1])
    assert f.cmd == 0xE2 and f.payload[0] == 2 and f.payload[3] == 1   # remoteCon=2, stays model 1


def test_e2e_write_program_batches_of_10():
    # 23 steps -> three 0xDA frames (10 + 10 + 3), each waiting for its 0xDB
    steps = [{"V": 1.0 + k, "A": 0.5, "S": 2.0} for k in range(22)] + [{"V": 0, "A": 0, "S": 0}]
    psu = MP305(FakeHID([_resp(P.RESP_PROGRAM_WRITE, b"\x00")] * 3))
    psu.write_program(4, steps)
    expected = []
    for batch in (steps[:10], steps[10:20], steps[20:]):
        expected += P.build_reports(*C.programmable_write(4, batch))
    assert psu._dev.written == expected


def test_e2e_write_program_stops_on_refused_batch():
    from pymp305.device import MP305Error
    steps = [{"V": 5.0, "A": 1.0, "S": 1.0}] * 15
    psu = MP305(FakeHID([_resp(P.RESP_PROGRAM_WRITE, b"\x02")]))
    try:
        psu.write_program(1, steps)
        assert False, "expected MP305Error"
    except MP305Error:
        pass


def test_e2e_read_pdo_index():
    psu = MP305(FakeHID([_resp(0xE5, bytes([7]))]))
    assert psu.read_pdo_index() == 7
    assert P.parse_report(psu._dev.written[0]).cmd == 0xE4


def test_e2e_pdo_connect_request():
    # pdo_connect goes through the shared remote handshake first (0xC8 remoteCon=2)
    psu = MP305(FakeHID([
        _resp(P.RESP_CONTROL, b"\x00"),       # remote request granted
        _resp(P.RESP_PDO_CONNECT, b"\x00"),   # pdo connect accepted
    ]))
    psu.pdo_connect(C.PDOConnect(src_enable_mask=2, output=1))
    req = P.parse_report(psu._dev.written[0])
    assert req.cmd == P.CMD_CONTROL and req.payload[0] == 2    # remote request
    f = P.parse_report(psu._dev.written[1])
    assert f.cmd == 0xE8 and f.payload[1] == 2     # src_enable_mask low byte


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
