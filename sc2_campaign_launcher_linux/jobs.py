"""Keep background work alive until QThread has finished."""

import logging
import time
import weakref
from threading import Event

from PyQt6 import sip
from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from .files import Cancelled, check_cancel

log = logging.getLogger(__name__)


class Job(QThread):
    progress = pyqtSignal(object)

    def __init__(self, function, callback, owner=None, progress=None):
        super().__init__()
        self.function = function
        self.callback = callback
        self.progress_callback = progress
        self.owner = weakref.ref(owner) if owner is not None else None
        self.cancel = Event()
        self.result = None
        self.error = None
        self._last_progress = 0.0
        self._last_file = None

    def notify(self, value):
        now = time.monotonic()
        if isinstance(value, dict):
            name = value.get('name')
            complete = value.get('received') == value.get('total')
            if name == self._last_file and not complete and now - self._last_progress < 0.1:
                return
            self._last_file = name
        self._last_progress = now
        self.progress.emit(value)

    def run(self):
        try:
            check_cancel(self.cancel)
            self.result = self.function(self.cancel, self.notify)
        except Cancelled as error:
            self.error = error
        except Exception as error:
            self.error = error
            log.exception('Background operation failed')

    def owner_exists(self):
        owner = self.owner() if self.owner else None
        return self.owner is None or (owner is not None and not sip.isdeleted(owner))


class JobPool(QObject):
    idle = pyqtSignal()

    def __init__(self, parent=None, limit=4):
        super().__init__(parent)
        self.limit = limit
        self.active = set()
        self.queued = []

    def submit(self, function, callback, owner=None, progress=None, priority=False) -> Job:
        job = Job(function, callback, owner, progress)
        job.finished.connect(self._finished)
        job.progress.connect(self._progress)
        self.queued.insert(0 if priority else len(self.queued), job)
        self._pump()
        return job

    def _pump(self):
        while self.queued and len(self.active) < self.limit:
            job = self.queued.pop(0)
            self.active.add(job)
            job.start()

    @pyqtSlot(object)
    def _progress(self, value):
        job = self.sender()
        if job.owner_exists() and job.progress_callback and not job.cancel.is_set():
            job.progress_callback(value)

    @pyqtSlot()
    def _finished(self):
        job = self.sender()
        self.active.discard(job)
        callback = job.callback
        try:
            if job.owner_exists() and callback:
                callback(job.result, job.error)
        finally:
            job.callback = job.progress_callback = job.function = None
            job.deleteLater()
            self._pump()
            if not self.busy():
                self.idle.emit()

    def busy(self) -> bool:
        return bool(self.active or self.queued)

    def cancel_all(self):
        for job in [*self.active, *self.queued]:
            job.cancel.set()
