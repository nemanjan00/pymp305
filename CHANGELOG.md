# Changelog

All notable changes to `pymp305`. Versions follow semver (pre-1.0: minor = features).

> ⚠️ Nothing here has been validated against physical hardware yet — see the README banner.

## 0.4.1

### Changed
- **GUI v2** (`gui/`): redesigned for **trackball-only / no-keyboard** use — stepper-chip
  setpoints, scroll-to-nudge, one-click presets, and an on-screen **keypad with digit + unit
  buttons** (V/mV, A/mA). Dual CV/CC arc gauges with a fused CV/CC tag (replaces the empty
  card), status lamps (OUT/CV/CC/OVP/OCP), an event log, and disciplined layout (controls keep
  natural height; the chart absorbs slack). (Library unchanged.)

## 0.4.0

### Added
- **Desktop GUI** (`gui/`): a Dracula-themed PyQt6 + pyqtgraph dashboard around the library —
  live V/I/W readouts, a CV/CC arc gauge, output toggle, set-point controls, rolling charts,
  and a built-in **simulator** so it runs with no hardware. Kept as a separate package so the
  core library stays Qt-free.

(The `pymp305` library itself is unchanged from 0.3.0; this release adds the GUI + docs.)

## 0.3.0

First release covering the whole MP305 line, a second transport, and the full advanced
command surface. (0.2.0 was a local-only tag, never released — superseded by this.)

### Added
- **Typed package**: ships `py.typed` so downstream type-checkers see the annotations.
- **Safety gates**: `open()` emits a one-time `UserWarning` until `HARDWARE_VALIDATED`;
  OTA `flash()` / `flash_ble()` require an explicit `allow_untested_ota=True`.
- **BLE transport** `MP305BLE` (async, `bleak`): scan/connect, AF02 binding handshake,
  request/response over AF01, and the full high-level API mirroring the USB driver.
- **Charge mode**: `read_charge_state()`, `read_charge_settings()`, `charge(ChargeCommand)`.
- **USB-PD (PDO)**: `read_pdo()`, `pdo_connect()`, `write_pdo()` + `parse_pdo_item`
  (fixed/augmented/SPR-AVS/EPR-AVS decode).
- **Programmable sequences**: `read_program_list()`, `read_program_steps()`,
  `read_program_state()`, `program_connect()`, `write_program()`, `program_change()`.
- **E-marker**: `read_emarker()` (USB-C cable info with speed/format labels).
- **OTA + firmware**: `ota.Firmware` (decrypt + verify the encrypted `.bin`; validated against
  ISDT's released MP305A/MP305B images), `ota.IntelHexFirmware`, `ota.BootInfo`,
  experimental `MP305.flash()` (HID) and `MP305BLE.flash_ble()` (BLE FEE1), gated behind `allow_untested_ota=True`.
- **Experimental/undisclosed commands** (reverse-engineered): `get_language()` (`0xA0`),
  `soft_reset()` (`0xFE`, magic-gated, confirmed non-destructive).
- **MP305A + MP305B** in one driver (`MP305`, with `MP305A`/`MP305B` aliases); model
  auto-detected for error decoding.
- Tools: `tools/fetch_firmware.py` (data-driven via `tools/ota_endpoints.json`, mirror +
  staging support); `tools/fetch_weblink_sources.py`.

### Fixed
- `parse_report` now truncates to the frame length byte `N`, so HID report zero-padding
  no longer leaks into the decoded payload (matters for real 64-byte device reports).

### Changed
- Package renamed `pymp305b` → `pymp305`; covers the whole MP305 line.
- Python 3.10–3.14 in CI.

## 0.1.0

Initial release: USB-HID driver — connect, read state, set V/I/output, system settings,
reboot/bootloader; golden-vector framing tests.
