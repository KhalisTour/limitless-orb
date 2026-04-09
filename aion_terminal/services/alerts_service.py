from __future__ import annotations

from typing import Any


def check_alerts(current_levels: dict[str, Any], previous_levels: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Compare current vs previous levels and return alert payloads."""
    if not current_levels or previous_levels is None:
        return []

    alerts: list[dict[str, Any]] = []
    symbol = current_levels.get("symbol") or previous_levels.get("symbol")
    spot = current_levels.get("spot")

    if current_levels.get("regime") != previous_levels.get("regime"):
        alerts.append(
            {
                "type": "REGIME_CHANGE",
                "symbol": symbol,
                "from_regime": previous_levels.get("regime"),
                "to_regime": current_levels.get("regime"),
                "spot": spot,
            }
        )

    if current_levels.get("king_node") != previous_levels.get("king_node"):
        alerts.append(
            {
                "type": "KING_NODE_SHIFT",
                "symbol": symbol,
                "from_king": previous_levels.get("king_node"),
                "to_king": current_levels.get("king_node"),
                "spot": spot,
            }
        )

    call_wall = current_levels.get("call_wall")
    if call_wall is not None and spot is not None and abs(spot - call_wall) < 2.0:
        alerts.append({"type": "CALL_WALL_HIT", "symbol": symbol, "call_wall": call_wall, "spot": spot})

    put_wall = current_levels.get("put_wall")
    if put_wall is not None and spot is not None and abs(spot - put_wall) < 2.0:
        alerts.append({"type": "PUT_WALL_HIT", "symbol": symbol, "put_wall": put_wall, "spot": spot})

    if current_levels.get("regime") == "acceleration" and previous_levels.get("regime") != "acceleration":
        alerts.append(
            {
                "type": "ACCELERATION_ZONE_ENTRY",
                "symbol": symbol,
                "spot": spot,
                "acceleration_zones": current_levels.get("acceleration_zones") or [],
            }
        )

    return alerts


def dispatch_alerts(alerts: list[dict[str, Any]]) -> None:
    if not alerts:
        return

    for alert in alerts:
        print(f"[ALERT] {alert.get('type')} | {alert.get('symbol')} | payload={alert}")
