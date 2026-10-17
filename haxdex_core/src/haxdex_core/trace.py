from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from time import perf_counter
from typing import Any


class TraceWriter:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def begin(self, name: str, **args: Any) -> None:
        self._event(name, "B", args)

    def end(self, name: str, **args: Any) -> None:
        self._event(name, "E", args)

    def instant(self, name: str, **args: Any) -> None:
        self._event(name, "i", args)

    def _event(
        self,
        name: str,
        phase: str,
        args: dict[str, Any],
    ) -> None:
        event: dict[str, Any] = {
            "name": name,
            "ph": phase,
            "ts": perf_counter() * 1_000_000,
            "pid": os.getpid(),
            "tid": threading.get_ident(),
        }

        if args:
            event["args"] = args

        with self.lock:
            self.events.append(event)

    def save(self, path: str | Path) -> None:
        with self.lock:
            events = list(self.events)

        with open(path, "w") as file:
            json.dump({"traceEvents": events}, file)
