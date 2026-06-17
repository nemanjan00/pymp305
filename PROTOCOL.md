# ISDT MP305 (MP305A / MP305B) — wire protocol (reverse-engineered)

Source of truth: the official **WebLink** web app (`https://www.isdt.co/weblink/`) ships
with public source-maps. The original (un-minified) source was recovered and the protocol
below is transcribed from it. That recovered material is ISDT's copyright, so it is kept
**locally only** under `reversing/` (git-ignored, not published). This document describes
the wire protocol as factual interoperability information.

Both **MP305A** and **MP305B** use the same **"DP3005"** PSU controller (30 V / 5 A /
305 W) and speak an identical command set. The only model-specific difference observed is
how a few low error bits are named (see *State response* below). It speaks the same
protocol over two transports:

| Transport | How the official app uses it | Framing |
|-----------|------------------------------|---------|
| **USB-HID** | WebLink web app (WebHID) — **this is what the Python lib targets** | length + `0xAA` + checksum, see below |
| **BLE GATT** | PolyLink phone app | raw `[0x12, cmd, …payload]` written to char `AF01`, binding via `AF02` |

## USB-HID transport

- **VID `0x28E9`** (GigaDevice). PID is not filtered by the app; it additionally matches
  HID `usagePage 0x01 / usage 0x04`. Enumerate by VID and pick that usage if multiple.
- **Report ID `1`** for both output (host→device) and input (device→host) reports.
- Reports are 64 bytes (zero-padded). Frames longer than 63 bytes are fragmented into
  61-byte chunks (used only by OTA / PDO-table writes); all normal commands fit one report.

### Frame layout (the bytes after the HID report-ID)

```
 idx  field
  0   N        = (frame_length_without_this_byte) & 0xFF   ── set automatically
  1   0xAA     frame-start marker
  2   0x12     protocol/group id (constant)
  3   L        payload length = 1 (cmd byte) + len(data)
  4   CMD      command byte
  5.. DATA     little-endian fields (see commands)
 last CHK      = sum(bytes[2 .. last-1]) & 0xFF
```

Two stuffing rules tie TX and RX together:

1. **Checksum** sums bytes from index 2 up to (but not including) the checksum byte.
   Consecutive `0xAA` bytes are counted **once**.
2. **`0xAA` byte-stuffing**: in the DATA region (**index > 5 only**), every `0xAA` is
   doubled on send. On receive, any run of repeated `0xAA` collapses to a single `0xAA`
   and `N` is decremented per dropped byte.
3. If the computed checksum equals `0xAA`, an extra `0xAA` is appended and `N` incremented.

(Implemented in `Cmd.processHexArray` / `Cmd.add0xAA` in the JS; mirrored in `protocol.py`.)

### Response framing
Identical, with `CMD` at index 4 of the de-stuffed buffer and payload at index 5.
Responses generally use `request_cmd + 1`.

## Commands (CMD byte) and responses

| Direction | CMD | Name | Payload (LE) | Response CMD |
|-----------|-----|------|--------------|--------------|
| → | `0xE0` | Hardware info request | — | `0xE1` HardwareInfoResp |
| → | `0xC2` | Device/state info request | — | `0xC3` State (DP3005Resp) |
| → | `0xBD` | Realtime poll (app polls every 3 s) | — | `0xC3` State |
| → | `0xC4` | System-settings request | — | `0xC5` SystemState (DPStateResp) |
| → | `0xC6` | **Set system settings** | see SystemSet | `0xC7` (ok if next byte==0) |
| → | `0xC8` | **Set output / V / I (control)** | see Control | `0xC9` (ok if next byte==0) |
| → | `0xC4`/`0xEA` | charge search | — | `0xC5`/`0xEB` |
| → | `0xEC` | charge info request | — | `0xED` ChargeResp |
| → | `0xEE` | **Charge control** | see Charge | `0xEF` |
| → | `0xE4`/`0xE6`/`0xE8`/`0xD0`/`0xD2` | USB-PD (PDO) read/connect/write | — | `0xE5`/`0xE7`/`0xE9`/`0xD1`/`0xD3` |
| → | `0xD4`/`0xD6`/`0xD8`/`0xDA`/`0xDC`/`0xDE` | programmable sequences | — | `0xD5`… |
| → | `0xA2` | set UI language | `index:u8` | `0xA3` |
| → | `0xF0AC` | jump to bootloader (`BOOT_DATA`) | — | — |
| → | `0xFCCA` | reboot (`REBOOT_DATA`) | — | — |
| → | `0xF2/0xF4/0xF6/0x2005/0x2006` | OTA erase/write/checksum | — | — |

### `0xC8` Control payload (`DPConnectModel`)
```
remoteCon  : u8   1 = take remote control (must be 1 to change anything), 0 = release
setVoltage : u16  volts * 100      (10 mV units)
setCurrent : u16  amps  * 1000     (1 mA units)
realChange : u8   live-apply flags: 1=V,2=I,3=both
voltageSlow: u8   slow/ramp flag
currentOver: u8   OCP enable
output     : u8   1 = output ON, 0 = OFF
model      : u8   0=DC PSU, 1=programmable, 2=USB-PD, 3=charge
refresh    : u8
```

### `0xC6` System-settings payload (`systemSetCmd`)
```
perLimit u8, volume u8, screenOff u8, shutdown u8, screenDirection u8,
slopeSteps u16, currentOver u16, systemCheck u8, recover u8, [usbLine u16 optional]
```

### `0xEE` Charge payload (`chargeConnectCmd`)
```
remoteCon u8, batteryType u8, capacityVoltage u16 (V*1000, or raw cells for NiMH),
cells u8, current u16 (A*1000), output u8, model u8
```

## State response `0xC3` (`DP3005Resp`) — payload from index 5
```
outState     u8
batteryState u8
percentage   u8                 battery %
voltage      u16   / 100  -> V   (measured output)
setVoltage   u16   / 100  -> V
current      u16   / 1000 -> A   (measured output)
setCurrent   u16   / 1000 -> A
workingTime  u32           -> s
energy       u32   / 1000  -> Wh
power        u16   / 100   -> W
currentOver  u8                  (OCP setting echo)
realChange   u8
voltageSlow  u8
output       u8                  1 = output on
model        u8
voltageBoard u8
currentBoard u8
temperature  u8            -> °C
chargeError  u16   (present only if N(byte0) > 34)   bitmask -> errorLists[]
wavePause    u8    (present only if N > 36)
waveTime     u32
```
`errorLists` (bit index → meaning): `errorOutRev, errorBattVolt, errorBattTemp_L,
errorBattTemp_H, errorBoardTemp_H, errorDcOutOCP, errorDcOutOVP, errorDICInitFail,
errorDcOutVol, errorTimeOut, errorConnectionBroken, errorBatteryOver, errorBatteryLow,
errorCellsNode, errorNoBattery, errorCapacity, errorUnknown`.

**Model-specific decode** (`getByteType` in `constant.js`): the meaningful bit-width is
**17** in charge mode (`model == 3`) and **9** otherwise. **MP305B** maps each set bit
straight to `errorLists`. Other models (e.g. **MP305A**) remap the low bits:
`bit1 → errorUnknown, bit2 → errorUnknown, bit3 → errorBattTemp_H_A`, falling back to
`errorLists` for the rest. `pymp305` does this automatically once the device name is read.

## System-settings response `0xC5` (`DPStateResp`)
```
perLimit u8, volume u8, screenOff u8, shutdown u8, screenDirection u8,
slopeSteps u16, currentOver u16, [usbLine u16 if N>14]
```

## Hardware-info response `0xE1` (`HardwareInfoResp`, HID index=5)
```
deviceId[8], hwVer[4] (main.sub.mend.layout), bootVer[4], appVer[4], deviceName[10] (ascii)
```

## Typical session (as the app does it)
1. `requestDevice` → open HID. 2. send `0xE0` → parse `0xE1` (info / firmware ver).
3. send `0xC4` (system) → `0xC5`; send `0xC2` (state) → `0xC3`.
4. To control: send `0xC8` with `remoteCon=1, model=0, setVoltage, setCurrent, output`.
5. Poll `0xBD` (realtime) every ~3 s → `0xC3` and update the UI.
</content>
</invoke>
