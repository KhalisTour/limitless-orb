from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeState:
    latest_levels: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_connections: list[Any] = field(default_factory=list)


runtime_state = RuntimeState()
