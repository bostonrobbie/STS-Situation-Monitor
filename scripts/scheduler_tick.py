#!/usr/bin/env python3
from __future__ import annotations

import json
import time

from sts_monitor.database import SessionLocal
from sts_monitor.jobs import tick_schedules


def run_scheduler(poll_interval_s: float = 5.0, max_loops: int | None = None) -> None:
    loops = 0
    while True:
        with SessionLocal() as session:
            enqueued = tick_schedules(session)
        print(json.dumps({"enqueued": enqueued}))

        loops += 1
        if max_loops is not None and loops >= max_loops:
            break
        time.sleep(poll_interval_s)


if __name__ == "__main__":
    run_scheduler()
