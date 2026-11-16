"""MP305BLE control-path tests through a fake bleak client: the client answers each AF01
write with the next queued notification, so the request bytes AND the driver's handling of
the replies are checked. No hardware, no bleak needed."""
import asyncio
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymp305 import protocol as P
from pymp305 import commands as C
from pymp305.ble import MP305BLE
from pymp305.device import MP305Error


class FakeBleak:
    """Each write_gatt_char is answered by the next queued (cmd, payload) as an AF01
    notification ([0x31, cmd, ...payload]); None queues no answer (timeout)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.written = []           # (cmd, payload) of each AF01 write
        self.notify = None
        self.is_connected = True

    async def write_gatt_char(self, char, data, response=False):
        fr = P.parse_ble_notification(bytes(data))
        self.written.append((fr.cmd, bytes(fr.payload)))
        if self.replies:
            r = self.replies.pop(0)
            if r is not None:
                self.notify(None, bytearray([P.BLE_RESP_GROUP, r[0]]) + r[1])

    async def disconnect(self):
        pass


def _psu(replies):
    client = FakeBleak(replies)
    psu = MP305BLE(client, "0000MP305B")
    client.notify = psu._on_notify
    return psu, client


def _state(*, setv=500, setc=1000, out=0, model=0):
    return (P.RESP_STATE, bytes([1, 0, 73]) + struct.pack("<HHHH", 0, setv, 0, setc)
            + struct.pack("<II", 0, 0) + struct.pack("<H", 0)
            + bytes([0, 0, 0, out, model, 0, 0, 25]))


def _ok(cmd, status=0):
    return (cmd, bytes([status]))


def _run(coro):
    return asyncio.run(coro)


def test_ble_read_state_tracks_mode():
    psu, _ = _psu([_state(model=2)])
    _run(psu.read_state())
    assert psu._model == 2


def test_ble_request_remote_uses_current_mode_command():
    psu, cl = _psu([_state(model=2), _ok(P.RESP_PDO_CONNECT)])
    async def go():
        await psu.read_state()
        return await psu.request_remote(timeout=1.0)
    assert _run(go()) is True
    cmd, pl = cl.written[1]
    assert cmd == 0xE8 and pl[0] == 2 and pl[-1] == 2    # remoteCon=2, model stays USB-PD


def test_ble_set_output_builds_control():
    psu, cl = _psu([_state(), _ok(P.RESP_CONTROL), _ok(P.RESP_CONTROL), _state(setv=900, out=1)])
    st = _run(psu.set_output(voltage=9.0, current=2.0, on=True))
    assert [c for c, _ in cl.written] == [0xC2, 0xC8, 0xC8, 0xC2]
    assert cl.written[1][1][0] == 2                                   # remote request
    rc, sv, sc, rch, vs, co, out, model, refresh = struct.unpack("<BHHBBBBBB", cl.written[2][1])
    assert (rc, sv, sc, out, model) == (1, 900, 2000, 1, 0)
    assert st.output == 1


def test_ble_set_output_reapply_cycles_output():
    psu, cl = _psu([_state(out=1), _ok(P.RESP_CONTROL), _ok(P.RESP_CONTROL),
                    _ok(P.RESP_CONTROL), _ok(P.RESP_CONTROL), _state(out=1)])
    _run(psu.set_output(current=0.003, reapply=True))
    off = struct.unpack("<BHHBBBBBB", cl.written[3][1])
    on = struct.unpack("<BHHBBBBBB", cl.written[4][1])
    assert off[6] == 0 and on[6] == 1


def test_ble_set_output_no_reapply_when_output_off():
    psu, cl = _psu([_state(out=0), _ok(P.RESP_CONTROL), _ok(P.RESP_CONTROL), _state(out=0)])
    _run(psu.set_output(current=0.003, reapply=True))
    assert len(cl.written) == 4                                       # no off/on cycle


def test_ble_set_output_retry_raises_on_second_rejection():
    psu, cl = _psu([_state(), _ok(P.RESP_CONTROL), _ok(P.RESP_CONTROL, 1),
                    _ok(P.RESP_CONTROL), _ok(P.RESP_CONTROL, 1)])
    try:
        _run(psu.set_output(voltage=5.0))
        assert False, "expected MP305Error"
    except MP305Error:
        pass
    assert len(cl.written) == 5


def test_ble_set_output_refuses_outside_dc_mode():
    psu, cl = _psu([_state(model=1)])
    try:
        _run(psu.set_output(voltage=5.0))
        assert False, "expected MP305Error"
    except MP305Error:
        pass
    assert [c for c, _ in cl.written] == [0xC2]                       # no 0xC8 went out


def test_ble_write_program_batches():
    steps = [{"V": 5.0, "A": 1.0, "S": 1.0}] * 12
    psu, cl = _psu([_ok(P.RESP_PROGRAM_WRITE), _ok(P.RESP_PROGRAM_WRITE)])
    _run(psu.write_program(2, steps))
    assert cl.written == [C.programmable_write(2, steps[:10]), C.programmable_write(2, steps[10:])]
