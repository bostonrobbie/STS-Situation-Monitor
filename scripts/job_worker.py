#!/usr/bin/env python3
from __future__ import annotations

import json
import time

from sts_monitor.database import SessionLocal
from sts_monitor.main import llm_client, pipeline
from sts_monitor.jobs import process_next_job


def run_worker(poll_interval_s: float = 1.0, max_loops: int | None = None) -> None:
    loops = 0
    while True:
        with SessionLocal() as session:
            result = process_next_job(session=session, pipeline=pipeline, llm_client=llm_client)
        if result:
            print(json.dumps(result, default=str))
        else:
            time.sleep(poll_interval_s)

        loops += 1
        if max_loops is not None and loops >= max_loops:
            break


if __name__ == "__main__":
    run_worker()
