"""pymp305 — Python driver for the ISDT MP305 line (MP305A / MP305B) over USB-HID.

Both models share one controller and protocol; the model is auto-detected for error
decoding. Reverse-engineered from the official ISDT WebLink web app; see ../PROTOCOL.md.
"""
from .device import (
    MP305,
    MP305A,
    MP305B,
    MP305Error,
    MP305BError,
    ControlCommand,
    ChargeCommand,
    SystemSetCommand,
)
from .responses import (
    State,
    SystemSettings,
    HardwareInfo,
    ChargeState,
    ChargeInfo,
    PDO,
    ProgramState,
    ProgramList,
    ProgramEntry,
    ProgramSteps,
    parse_pdo_item,
    decode_errors,
    BATTERY_TYPES,
    ERROR_LIST,
    MODEL_DC,
    MODEL_PROGRAMMABLE,
    MODEL_USB_PD,
    MODEL_CHARGE,
)
from . import protocol
from . import commands
from . import ota
from .ota import Firmware, IntelHexFirmware
from .ble import MP305BLE   # safe to import even without bleak installed

__all__ = [
    "MP305", "MP305A", "MP305B", "MP305Error", "MP305BError", "MP305BLE",
    "ControlCommand", "ChargeCommand", "SystemSetCommand",
    "State", "SystemSettings", "HardwareInfo",
    "ChargeState", "ChargeInfo", "PDO", "ProgramState", "ProgramList",
    "ProgramEntry", "ProgramSteps", "parse_pdo_item", "decode_errors",
    "BATTERY_TYPES", "ERROR_LIST",
    "MODEL_DC", "MODEL_PROGRAMMABLE", "MODEL_USB_PD", "MODEL_CHARGE",
    "protocol", "commands", "ota", "Firmware", "IntelHexFirmware",
]
__version__ = "0.2.0"
