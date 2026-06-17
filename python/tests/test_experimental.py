"""Experimental/undisclosed command helpers (get_language 0xA0, soft_reset 0xFE).
Driven through a fake HID device — no hardware. See reversing/FINDINGS-commands.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from pymp305 import protocol as P
from pymp305.device import MP305, MP305Error


class FakeHID:
    """Minimal hidapi-device stand-in: records writes, replays queued read frames."""
    def __init__(self, responses=None):
        self.written = []
        self.responses = list(responses or [])
        self.opened = True

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, n, timeout=0):
        return list(self.responses.pop(0)) if self.responses else []

    def set_nonblocking(self, v):
        pass

    def close(self):
        self.opened = False


def _resp(cmd, payload=b""):
    """A device input report (report-id + frame) for cmd/payload."""
    return bytes([P.REPORT_ID]) + P.build_frame(cmd, payload)


def test_get_language_request_and_decode():
    dev = FakeHID([_resp(P.RESP_GET_LANGUAGE, b"\x01")])     # 0xA1, language index 1
    psu = MP305(dev)
    assert psu.get_language() == 1
    # it must have sent exactly the 0xA0 request report
    assert dev.written[0] == P.build_report(P.CMD_GET_LANGUAGE)


def test_soft_reset_gated():
    psu = MP305(FakeHID())
    with pytest.raises(MP305Error):
        psu.soft_reset()                       # confirm defaults to False -> refuse
    assert psu._dev.written == []              # nothing sent


def test_soft_reset_sends_magic_and_reads_ack():
    dev = FakeHID([_resp(P.RESP_SOFT_RESET, P.SOFT_RESET_MAGIC)])   # 0xFF echoes AA 55
    psu = MP305(dev)
    assert psu.soft_reset(confirm=True) is True
    # sent 0xFE with the AA 55 magic payload
    assert dev.written[0] == P.build_report(P.CMD_SOFT_RESET, P.SOFT_RESET_MAGIC)
    frame = P.parse_report(dev.written[0])
    assert frame.cmd == 0xFE and frame.payload == b"\xAA\x55"


def test_soft_reset_reports_not_accepted():
    dev = FakeHID([_resp(P.RESP_SOFT_RESET, b"\x00\x00")])   # magic not echoed
    assert MP305(dev).soft_reset(confirm=True) is False


def test_flash_gated_without_allow_untested_ota():
    psu = MP305(FakeHID())
    with pytest.raises(MP305Error):
        psu.flash(None)                        # missing allow_untested_ota -> refuse
    assert psu._dev.written == []              # nothing sent (refused before any I/O)


def test_warn_untested_fires_once():
    import warnings
    P._warned_untested = False                 # reset the one-shot guard
    assert P.HARDWARE_VALIDATED is False
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        P.warn_untested()
        P.warn_untested()                      # second call must be silent
    assert sum(issubclass(x.category, UserWarning) for x in w) == 1


if __name__ == "__main__":
    import pytest as _pt
    raise SystemExit(_pt.main([__file__, "-q"]))
