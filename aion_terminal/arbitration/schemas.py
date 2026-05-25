from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgreementMatrix:
    technical: float = 0.5
    dealer: float = 0.5
    contracts: float = 0.5
    macro: float = 0.5
    memory: float = 0.5
    expectancy: float = 0.5

    def average(self) -> float:
        return (
            self.technical
            + self.dealer
            + self.contracts
            + self.macro
            + self.memory
            + self.expectancy
        ) / 6.0


@dataclass
class TriggerSpec:
    type: str
    level: float | None = None
    condition: str = ""


@dataclass
class HoldPolicy:
    partial_take_profit_allowed: bool = True
    avoid_full_exit_before_acceptance: bool = True
    trail_runner_after_target_hit: bool = True
    max_contracts_to_runner: int = 1


@dataclass
class ArbResult:
    symbol: str
    generated_at: str
    arb_decision: str
    final_bias: str
    confidence: float
    confidence_bucket: str
    setup_class: str
    agreement_matrix: AgreementMatrix
    conflicts: list[str]
    required_trigger: TriggerSpec | None
    kill_switch: TriggerSpec | None
    approved_contract_role: str
    sizing_modifier: float
    hold_policy: HoldPolicy
    warnings: list[str]
    supporting_factors: list[str]
    rejection_factors: list[str]
    inputs_summary: dict[str, Any] = field(default_factory=dict)
