"""Run blocking service calls away from the Qt event loop."""

from __future__ import annotations

import itertools
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Signals(QObject):
    completed = Signal(int, object, object)


class _Work(QRunnable):
    def __init__(self, number: int, fn: Callable):
        super().__init__()
        self.number, self.fn = number, fn
        self.signals = _Signals()

    @Slot()
    def run(self):
        try:
            value = self.fn()
        except Exception as error:
            self.signals.completed.emit(self.number, None, error)
        else:
            self.signals.completed.emit(self.number, value, None)


class Tasks(QObject):
    changed = Signal(bool)
    progress = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        # Serial calls protect service mutation and avoid status races during provisioning.
        self.pool.setMaxThreadCount(1)
        self._counter = itertools.count()
        self._pending: dict = {}

    @property
    def busy(self):
        return bool(self._pending)

    def submit(self, fn: Callable, success: Callable, failure: Callable):
        number = next(self._counter)
        work = _Work(number, fn)
        self._pending[number] = (work, success, failure)
        work.signals.completed.connect(self._complete)
        self.changed.emit(True)
        self.pool.start(work)

    @Slot(int, object, object)
    def _complete(self, number, result, error):
        _, success, failure = self._pending.pop(number)
        try:
            if error is None:
                success(result)
            else:
                failure(error)
        except Exception as callback_error:
            failure(callback_error)
        finally:
            self.changed.emit(self.busy)
