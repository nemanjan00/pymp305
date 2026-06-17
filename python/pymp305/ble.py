"""Async BLE transport for the ISDT MP305, mirroring the WebLink PolyLink path.

Requires bleak:  pip install bleak  (or:  pip install pymp305[ble])

The same command set as USB-HID is used, but BLE frames drop the length/0xAA/checksum
wrapper: commands are written to characteristic AF01 as ``[0x12, cmd, ...payload]`` and
responses arrive as notifications parsed at index 2. A binding handshake on AF02 kicks off
communication (see ``_bind``).

Example
-------
    import asyncio
    from pymp305.ble import MP305BLE

    async def main():
        psu = await MP305BLE.open()
        print(await psu.hardware_info())
        await psu.set_output(voltage=5.0, current=1.0, on=True)
        print(await psu.read_state())
        await psu.close()

    asyncio.run(main())

Note: each response is assumed to fit in a single notification, which needs an ATT MTU
large enough for the ~40-byte state frame. bleak negotiates a larger MTU on most backends;
if state reads come back truncated, that's the thing to check.
"""
from __future__ import annotations

import asyncio
import os

from . import protocol as P
from . import commands as C
from .device import ControlCommand, ChargeCommand, SystemSetCommand, MP305Error
from .responses import (
    HardwareInfo, State, SystemSettings,
    ChargeState, ChargeInfo, PDO, ProgramState, ProgramList, ProgramSteps,
)

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # pragma: no cover
    BleakClient = BleakScanner = None

_IDX = P.BLE_PARSE_INDEX   # 2


def _model_from_name(name: str | None) -> str | None:
    if not name:
        return None
    # advertised name is e.g. "0000MP305B"; the app uses name.slice(4)
    return name[4:] if name.startswith("0000") and len(name) > 4 else name


class MP305BLE:
    def __init__(self, client, device_name: str | None = None):
        self._client = client
        self.device_name = _model_from_name(device_name)
        self.hardware: HardwareInfo | None = None
        self._queue: asyncio.Queue = asyncio.Queue()

    # ---- discovery / connection -----------------------------------------
    @staticmethod
    async def discover(timeout: float = 8.0, name_prefix: str = P.BLE_NAME_PREFIX) -> list:
        if BleakScanner is None:
            raise MP305Error("the 'bleak' package is not installed (pip install bleak)")
        found = []
        devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
        for dev, adv in devices.values():
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if (dev.name or "").startswith(name_prefix) or P.BLE_SERVICE_AF00 in uuids:
                found.append(dev)
        return found

    @classmethod
    async def open(cls, address: str | None = None, name_prefix: str = P.BLE_NAME_PREFIX,
                   timeout: float = 10.0, bind: bool = True) -> "MP305BLE":
        if BleakClient is None:
            raise MP305Error("the 'bleak' package is not installed (pip install bleak)")
        name = None
        if address is None:
            dev = await BleakScanner.find_device_by_filter(
                lambda d, adv: (d.name or "").startswith(name_prefix)
                or P.BLE_SERVICE_AF00 in [u.lower() for u in (adv.service_uuids or [])],
                timeout=timeout,
            )
            if dev is None:
                raise MP305Error("no MP305 BLE device found")
            address, name = dev.address, dev.name
        client = BleakClient(address, timeout=timeout)
        await client.connect()
        self = cls(client, name)
        await client.start_notify(P.BLE_CHAR_AF01, self._on_notify)
        await client.start_notify(P.BLE_CHAR_AF02, self._on_notify)
        if bind:
            await self._bind()
        return self

    async def close(self):
        try:
            await self._client.disconnect()
        except Exception:
            pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    # ---- transport -------------------------------------------------------
    def _on_notify(self, _sender, data: bytearray):
        frame = P.parse_ble_notification(bytes(data))
        if frame is not None:
            self._queue.put_nowait(frame)

    async def _await_cmd(self, expect: int, timeout: float, allow_none: bool = False):
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                if allow_none:
                    return None
                raise MP305Error(f"timed out waiting for BLE response 0x{expect:02X}")
            try:
                frame = await asyncio.wait_for(self._queue.get(), remaining)
            except asyncio.TimeoutError:
                if allow_none:
                    return None
                raise MP305Error(f"timed out waiting for BLE response 0x{expect:02X}")
            if frame.cmd == expect:
                return frame

    async def _bind(self):
        """Binding handshake on AF02: write the binding packet, and when the device
        replies (0x19) request hardware info (0xE0 -> 0xE1)."""
        uuid = os.urandom(16)
        await self._client.write_gatt_char(P.BLE_CHAR_AF02, P.build_ble_binding(uuid), response=True)
        await self._await_cmd(P.BLE_CMD_BIND_RESP, timeout=3.0, allow_none=True)
        await asyncio.sleep(0.5)   # the firmware needs a gap before the next AF02 write
        await self._client.write_gatt_char(P.BLE_CHAR_AF02, bytes([P.CMD_HW_INFO]), response=True)
        f = await self._await_cmd(P.RESP_HW_INFO, timeout=3.0, allow_none=True)
        if f is not None:
            self.hardware = HardwareInfo.parse_ble(f.values)

    async def send(self, cmd: int, payload: bytes = b"") -> None:
        await self._client.write_gatt_char(P.BLE_CHAR_AF01, P.build_ble_frame(cmd, payload),
                                            response=False)

    async def request(self, cmd: int, expect: int, payload: bytes = b"",
                      timeout: float = 2.0) -> P.Frame:
        await self.send(cmd, payload)
        return await self._await_cmd(expect, timeout)

    # ---- high-level reads ------------------------------------------------
    async def hardware_info(self, timeout: float = 3.0) -> HardwareInfo:
        await self._client.write_gatt_char(P.BLE_CHAR_AF02, bytes([P.CMD_HW_INFO]), response=True)
        f = await self._await_cmd(P.RESP_HW_INFO, timeout)
        self.hardware = HardwareInfo.parse_ble(f.values)
        return self.hardware

    async def read_state(self, realtime: bool = True, timeout: float = 2.0) -> State:
        cmd = P.CMD_REALTIME if realtime else P.CMD_STATE_INFO
        f = await self.request(cmd, P.RESP_STATE, timeout=timeout)
        return State.parse(f.values, index=_IDX, device_name=self.device_name)

    async def read_system_settings(self, timeout: float = 2.0) -> SystemSettings:
        f = await self.request(P.CMD_SYS_GET, P.RESP_SYS, timeout=timeout)
        return SystemSettings.parse(f.values, index=_IDX)

    async def read_charge_state(self, timeout: float = 2.0) -> ChargeState:
        f = await self.request(P.CMD_CHARGE_INFO, P.RESP_CHARGE, timeout=timeout)
        return ChargeState.parse(f.values, index=_IDX, device_name=self.device_name)

    async def read_charge_settings(self, timeout: float = 2.0) -> ChargeInfo:
        f = await self.request(P.CMD_CHARGE_SEARCH, P.RESP_CHARGE_INFO, timeout=timeout)
        return ChargeInfo.parse(f.values, index=_IDX)

    async def read_pdo(self, pdo_id: int, timeout: float = 2.0) -> PDO:
        cmd, payload = C.pdo_search(pdo_id)
        f = await self.request(cmd, P.RESP_PDO, payload, timeout)
        return PDO.parse(f.values, index=_IDX)

    async def read_program_state(self, timeout: float = 2.0) -> ProgramState:
        cmd, payload = C.programmable_info()
        f = await self.request(cmd, P.RESP_PROGRAM_STATE, payload, timeout)
        return ProgramState.parse(f.values, index=_IDX)

    async def read_program_list(self, timeout: float = 2.0) -> ProgramList:
        cmd, payload = C.programmable_list()
        f = await self.request(cmd, P.RESP_PROGRAM_LIST, payload, timeout)
        return ProgramList.parse(f.values, index=_IDX)

    async def read_program_steps(self, seq_id: int, number: int, timeout: float = 2.0) -> ProgramSteps:
        cmd, payload = C.programmable_read(seq_id)
        f = await self.request(cmd, P.RESP_PROGRAM_STEPS, payload, timeout)
        return ProgramSteps.parse(f.values, number, index=_IDX)

    # ---- high-level control ---------------------------------------------
    async def control(self, cmd: ControlCommand, timeout: float = 2.0) -> P.Frame:
        return await self.request(P.CMD_CONTROL, P.RESP_CONTROL, cmd.payload(), timeout)

    async def set_output(self, voltage: float | None = None, current: float | None = None,
                         on: bool | None = None, *, model: int = 0,
                         real_change: int = 3, timeout: float = 2.0) -> State:
        st = await self.read_state(timeout=timeout)
        cmd = ControlCommand(
            remote_con=1,
            set_voltage=st.set_voltage if voltage is None else voltage,
            set_current=st.set_current if current is None else current,
            real_change=real_change,
            voltage_slow=st.voltage_slow,
            current_over=st.current_over,
            output=st.output if on is None else (1 if on else 0),
            model=model, refresh=0,
        )
        await self.control(cmd, timeout=timeout)
        return await self.read_state(timeout=timeout)

    async def output_on(self, **kw) -> State:
        return await self.set_output(on=True, **kw)

    async def output_off(self, **kw) -> State:
        return await self.set_output(on=False, **kw)

    async def release_remote(self, timeout: float = 2.0) -> P.Frame:
        return await self.control(ControlCommand(remote_con=0), timeout=timeout)

    async def set_system_settings(self, cmd: SystemSetCommand, timeout: float = 2.0) -> P.Frame:
        return await self.request(P.CMD_SYS_SET, P.RESP_SYS_SET, cmd.payload(), timeout)

    async def charge(self, cmd: ChargeCommand, timeout: float = 2.0) -> P.Frame:
        return await self.request(P.CMD_CHARGE_CONTROL, P.RESP_CHARGE, cmd.payload(), timeout)

    async def pdo_connect(self, conn: C.PDOConnect, timeout: float = 2.0) -> P.Frame:
        cmd, payload = conn.build()
        return await self.request(cmd, P.RESP_PDO_CONNECT, payload, timeout)

    async def program_connect(self, conn: C.ProgramConnect, timeout: float = 2.0) -> P.Frame:
        cmd, payload = conn.build()
        return await self.request(cmd, P.RESP_PROGRAM_CONNECT, payload, timeout)

    async def set_language(self, index: int, timeout: float = 2.0) -> P.Frame:
        return await self.request(P.CMD_SET_LANGUAGE, 0xA3, bytes([index & 0xFF]), timeout)

    # ---- experimental / undisclosed (reverse-engineered, UNTESTED) -------
    async def get_language(self, timeout: float = 2.0) -> int:
        """[experimental] Read the UI language index (undisclosed cmd 0xA0 → 0xA1)."""
        f = await self.request(P.CMD_GET_LANGUAGE, P.RESP_GET_LANGUAGE, timeout=timeout)
        return f.payload[0] if f.payload else -1

    async def soft_reset(self, *, confirm: bool = False, timeout: float = 2.0) -> bool:
        """[experimental] Magic-gated soft re-init of the regulator/USB-PD state (cmd 0xFE,
        payload AA 55). No flash access (can't brick) but resets the live output; gated
        behind confirm=True. See reversing/FINDINGS-commands.md."""
        if not confirm:
            raise MP305Error("soft_reset() is experimental — pass confirm=True to proceed")
        f = await self.request(P.CMD_SOFT_RESET, P.RESP_SOFT_RESET, P.SOFT_RESET_MAGIC, timeout)
        return f.payload[:2] == P.SOFT_RESET_MAGIC

    # ---- danger zone -----------------------------------------------------
    async def reboot(self) -> None:
        await self.send(P.REBOOT_PAYLOAD[0], P.REBOOT_PAYLOAD[1:])

    async def enter_bootloader(self) -> None:
        await self.send(P.BOOT_PAYLOAD[0], P.BOOT_PAYLOAD[1:])
