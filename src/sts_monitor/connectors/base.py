from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sts_monitor.pipeline import Observation


@dataclass(slots=True)
class ConnectorResult:
    connector: str
    observations: list[Observation]
    metadata: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    name: str

    def collect(self, query: str | None = None) -> ConnectorResult:
        ...
