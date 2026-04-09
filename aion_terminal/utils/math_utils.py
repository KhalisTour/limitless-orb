from __future__ import annotations

from typing import Any


def as_float(value: Any, default: float = 0.0) -> float:
    """Safely coerce arbitrary values into float."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    """Safely coerce arbitrary values into int."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
