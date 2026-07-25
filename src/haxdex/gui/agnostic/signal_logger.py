#!/usr/bin/env python

from contextlib import ContextDecorator
from functools import wraps
from PyQt6.QtCore import QObject, QEvent, QCoreApplication, pyqtBoundSignal

import logging

log = logging.getLogger()


def describe_qobject(obj):
    if isinstance(obj, QObject):
        name = obj.objectName()
        cls = obj.metaObject().className()
        return f"{cls}(name={name!r})"
    return repr(obj)


class _EventSpy(QObject):

    def eventFilter(self, obj, event):
        log.debug(f"EVENT {describe_qobject(obj)} type={event.type().name}")
        return False


class QtTrace(ContextDecorator):

    def __init__(self):
        self._event_spy = _EventSpy()
        self._old_emit = None
        self._old_connect = None
        self._wrapped_slots = {}

    def _wrap_slot(self, slot):
        if slot in self._wrapped_slots:
            return self._wrapped_slots[slot]

        @wraps(slot)
        def wrapper(*args, **kwargs):
            target = getattr(slot, "__self__", None)
            log.debug(f"SLOT {slot!r} target={describe_qobject(target)} "
                      f"args={args!r} kwargs={kwargs!r}")
            return slot(*args, **kwargs)

        self._wrapped_slots[slot] = wrapper
        return wrapper

    def __enter__(self):
        app = QCoreApplication.instance()
        app.installEventFilter(self._event_spy)

        self._old_emit = pyqtBoundSignal.emit
        self._old_connect = pyqtBoundSignal.connect

        def traced_emit(bound_signal, *args):
            sender = getattr(bound_signal, "instance", None)
            log.debug(f"SIGNAL sender={describe_qobject(sender)} "
                      f"signal={bound_signal.signal} args={args!r}")
            return self._old_emit(bound_signal, *args)

        def traced_connect(bound_signal, slot, *args, **kwargs):
            wrapped = self._wrap_slot(slot)
            sender = getattr(bound_signal, "instance", None)
            log.debug(f"CONNECT sender={describe_qobject(sender)} "
                      f"signal={bound_signal.signal} slot={slot!r}")
            return self._old_connect(bound_signal, wrapped, *args, **kwargs)

        pyqtBoundSignal.emit = traced_emit
        pyqtBoundSignal.connect = traced_connect
        return self

    def __exit__(self, exc_type, exc, tb):
        app = QCoreApplication.instance()
        app.removeEventFilter(self._event_spy)
        pyqtBoundSignal.emit = self._old_emit
        pyqtBoundSignal.connect = self._old_connect
        return False
