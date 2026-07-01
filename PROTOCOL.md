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
  2   group id — 0x12 on commands (host→device). NOTE: real MP305B hardware
      answers with group id 0x21 on responses, not 0x12 (see Response framing).
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
Identical stuffing/checksum, with `CMD` at index 4 of the de-stuffed buffer and payload at
index 5. Responses generally use `request_cmd + 1`.

> **Verified on hardware (MP305B, app V1.6.0.46):** responses carry group id **`0x21`** at
> index 2, not the `0x12` used on commands. The WebLink source only ever builds `0x12`
> frames, so this was missed until first hardware contact; `parse_report` accepts both.
> The realtime poll `0xBD` is **not answered** by this unit — use `0xC2` (same `0xC3`
> response) instead; `read_state()` falls back automatically.

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

> **Remote handshake (verified on hardware).** Taking control is **two steps**: first send
> `remoteCon=2` (a *request* — the device grants it, `0xC9` status 0, and it does not touch
> setpoints); only then are `remoteCon=1` changes applied. A bare `remoteCon=1` without a
> prior request is rejected (`0xC9` status 1) and silently ignored. The `0xC9` status byte
> (payload[0], shared by `0xE3`/`0xE9`/`0xEF` too): **0 = accepted, 1 = rejected/no remote,
> 2 = pending**. Releasing (`remoteCon=0`) reverts the unit to DC mode.
>
> **Mode switching** goes through the *current* mode's connect command (`0xC8`/`0xE2`/`0xE8`/
> `0xEE`) carrying the new `model`; the change only persists while remote is held.
>
> **CC re-arm quirk:** *lowering* the current limit while the output is on does not engage CC
> until the output is cycled (raising it works live).

```
remoteCon  : u8   2 = request remote control, 1 = apply (holding control), 0 = release
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
outState     u8    regulation status: 0=off, 1=CV, 2=CC  (verified on MP305B hardware)
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

## Typical session (as the app does it, corrected against MP305B hardware)
1. `requestDevice` → open HID. 2. send `0xE0` → parse `0xE1` (info / firmware ver).
3. send `0xC4` (system) → `0xC5`; send `0xC2` (state) → `0xC3`.
   (`0xBD` realtime is unanswered on this unit — poll `0xC2` instead.)
4. Take control: send `0xC8` with `remoteCon=2` → expect `0xC9` status 0 (granted).
5. To control: send `0xC8` with `remoteCon=1, model=0, setVoltage, setCurrent, output`
   → `0xC9` status 0. (A bare `remoteCon=1` without step 4 is rejected, status 1.)
6. Poll `0xC2` → `0xC3` (~3 s) and update the UI. Release with `remoteCon=0` when done.

## BLE transport
GATT service `0000af00-…`. Commands are written to characteristic **AF01** as raw
`[0x12, cmd, …LE-payload]` (no length/0xAA/checksum); responses arrive as notifications and
are parsed at **index 2** (cmd at index 1). Binding/hardware-info uses characteristic **AF02**:

1. Write binding `[0x18, …16 random bytes, fastBinding=0, status=0]` to AF02.
2. Device replies on AF02 with `[0x19, status, …]`.
3. Write `[0xE0]` to AF02 (after ~0.5 s); device replies `[0xE1, …]`.

AF02 handshake frames start with the command byte itself (cmd at index 0). The BLE
hardware-info layout differs from HID: `[bleHwMaj, bleHwMin, bleSwMaj, bleSwMin, deviceId[8],
hwVer[4]?]` — there is **no device-name field over BLE** (take it from the advertised name,
which is `0000MP305A`/`0000MP305B`). `0000fee0`/`fee1` carry the BLE OTA.

## Charge / USB-PD / programmable responses
- `0xED` ChargeResp (index 5/2): batteryState, percentage, current u16/1000, capacity u32 (mAh),
  batteryType, cells, voltage u16/100, energy u32/1000, workingTime u32, power u16/100,
  chargeFull, output, model, temperature; optional 32-bit error word on long frames.
- `0xEB` ChargeInfoResp: current + previous-run settings.
- `0xD1` PDOResp: id, name[16], power, number, then `number` × u32 entries — decoded by slot:
  fixed-PDO (j<5), augmented-PDO (j=5), SPR-AVS (j=6), EPR-AVS (j=8) with the bit layouts in
  `responses.parse_pdo_item`.
- `0xDF` ProgramResp: live programmable-run state incl. a large e-marker block.
- `0xD5` ProgrammableSearchResp: list of stored sequences `[name[16], num]`.
- `0xD9` ProgrammableOutputResp: steps `[V mV, A mA, S ×0.1s]` (last step with S=0 is raw V/A).

## Firmware & OTA
Two formats, both implemented in `pymp305.ota`:

**Encrypted `.bin` (USB-HID / UART OTA).** 32-byte little-endian header:
`encryptionKey, fileChecksum, appStorageOffset, dataStorageOffset, appSize, dataSize,
originalBaudRate, rapidBaudRate`. The body is a reversible XOR keystream seeded from the
header — **the key is in the file**, so decryption needs nothing external:
```
ks = fileChecksum
for each 4-byte LE word: plain = word XOR ks ; ks = ((ks + key) mod 2^32) XOR key
```
`sum(plain words) & 0xFFFFFFFF == fileChecksum` verifies integrity. A device-info table
(marker `0xAA55CC33`) inside the app holds the 8-byte device id (ASCII, e.g. `MP305B`) and
hw version. *Validated*: ISDT's released MP305A/MP305B images decrypt with passing checksums,
a valid ARM Cortex-M vector table, and the embedded id matching the model.

HID OTA flow (acks at addressId 4): bootloader `0xF0AC` → `0xF1`; erase `0xF2` → `0xF3`;
write app `0xF4` (128-byte blocks, fragmented into 61-byte reports; `0xF5` sub `0x01`=send
next fragment, `0x00`=block accepted) → checksum `0xF6` → `0xF7`; optional data region via
`0x20`; `0xFCCA` reboot.

**Intel HEX (BLE FEE1 OTA).** Parsed by `IntelHexFirmware`; flashed over characteristic FEE1
with `0x81` erase / `0x80` programme / `0x85` checksum / `0x83` end, driven by FEE1 reads.

> The HID app-OTA writes only the application region (`appStorageOffset`); the bootloader
> that runs the OTA is untouched, so a failed app write is normally recoverable by
> re-flashing. There is **no flash-read command** — you cannot dump the device's current
> firmware, only re-flash an official image.
