"""Device worker — runs all (blocking) device I/O on a QThread so the UI stays smooth.

The MainWindow moves a DeviceWorker onto a QThread, connects request-signals to its slots
(auto-queued across threads), and renders the `state` signal.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot


class DeviceWorker(QObject):
    state = pyqtSignal(dict)
    connected = pyqtSignal(dict)
    disconnected = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, backend, poll_ms: int = 200):
        super().__init__()
        self.backend = backend
        self._poll_ms = poll_ms
        self._timer: QTimer | None = None
        self._live = False

    @pyqtSlot()
    def start(self):
        self._timer = QTimer(self)   # parent to worker so its affinity follows the thread
        self._timer.setInterval(self._poll_ms)
        self._timer.timeout.connect(self._poll)

    @pyqtSlot()
    def connect_device(self):
        try:
            info = self.backend.connect()
            self._live = True
            self.connected.emit(info)
            self._timer.start()
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))

    @pyqtSlot()
    def disconnect_device(self):
        if self._timer:
            self._timer.stop()
        try:
            self.backend.close()
        except Exception:  # noqa: BLE001
            pass
        self._live = False
        self.disconnected.emit("disconnected")

    def _poll(self):
        if not self._live:
            return
        try:
            self.state.emit(self.backend.read())
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))

    @pyqtSlot(float)
    def set_voltage(self, v: float):
        self._apply(v=v)

    @pyqtSlot(float)
    def set_current(self, a: float):
        self._apply(a=a)

    @pyqtSlot(bool)
    def set_output(self, on: bool):
        self._apply(on=on)

    @pyqtSlot(int)
    def set_current_over(self, mode: int):
        self._call("set_current_over", mode)

    @pyqtSlot(bool)
    def set_remote(self, held: bool):
        self._call("set_remote", held)

    @pyqtSlot(int)
    def set_mode(self, model: int):
        self._call("set_mode", model)

    @pyqtSlot(dict)
    def set_charge(self, params: dict):
        if self._live:
            fn = getattr(self.backend, "set_charge", None)
            if fn is not None:
                try:
                    fn(**params)
                except Exception as e:  # noqa: BLE001
                    self.error.emit(str(e))

    @pyqtSlot(bool)
    def set_charging(self, on: bool):
        self._call("set_charging", on)

    @pyqtSlot(int)
    def select_pdo(self, i: int):
        self._call("select_pdo", i)

    @pyqtSlot(bool)
    def set_pd_output(self, on: bool):
        self._call("set_pd_output", on)

    def _call(self, method: str, *args):
        if not self._live:
            return
        fn = getattr(self.backend, method, None)
        if fn is not None:
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001
                self.error.emit(str(e))

    def _apply(self, **kw):
        if not self._live:
            return
        try:
            self.backend.apply(**kw)
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))
