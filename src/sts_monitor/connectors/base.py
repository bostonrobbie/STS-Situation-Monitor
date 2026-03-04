from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sts_monitor.pipeline import Observation


@dataclass(slots=True)
class ConnectorResult:
    connector: str
    observations: list[Observation]


class Connector(Protocol):
    name: str

    def collect(self, query: str | None = None) -> ConnectorResult:
        ...
