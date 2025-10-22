from dataclasses import dataclass


@dataclass
class Thresholds:
    units: str  # 'base' or 'percent'
    soft: float
    hard: float


class RebalancePolicy:
    def __init__(
        self,
        thresholds: Thresholds,
        partial_ratio: float,
        hysteresis_fraction: float,
    ):
        self.th = thresholds
        self.partial_ratio = max(0.0, min(1.0, partial_ratio))
        self.hysteresis_fraction = max(0.0, min(1.0, hysteresis_fraction))
        self._last_action_threshold_used_abs: float | None = None
        self._last_action_side: int | None = None  # +1 sell, -1 buy

    def _to_base_units(self, value: float, spot_notional_quote: float, mark_price: float) -> float:
        if self.th.units == 'base':
            return value
        # percent of spot notional — convert to base: (value * notional_quote) / mark_price
        return (value * spot_notional_quote) / max(mark_price, 1e-12)

    def compute_effective_thresholds(
        self,
        spot_notional_quote: float,
        mark_price: float,
        combined_bias: float,
        bias_strength: float,
    ) -> tuple[float, float]:
        """Return (soft_base, hard_base) with bias expansion/shrink."""
        base_soft = self._to_base_units(self.th.soft, spot_notional_quote, mark_price)
        base_hard = self._to_base_units(self.th.hard, spot_notional_quote, mark_price)
        bs = max(0.0, min(1.0, bias_strength))
        cb = max(-1.0, min(1.0, combined_bias))
        eff_soft = base_soft * (1.0 + bs * cb)
        eff_hard = base_hard * (1.0 + 0.5 * bs * cb)
        # enforce ordering and floor
        eff_soft = max(0.0, eff_soft)
        eff_hard = max(eff_soft, eff_hard)
        return eff_soft, eff_hard

    def decide(
        self,
        delta_gap_base: float,
        eff_soft: float,
        eff_hard: float,
    ) -> dict:
        """
        Returns a decision dict with keys:
          - action: 'none' | 'partial' | 'full'
          - target_trade_base: float (signed, + buy base, - sell base)
        Hysteresis: require a fraction of the last threshold in the opposite direction before acting again.
        """
        gap = delta_gap_base  # positive => long vs desired; we need to SELL spot
        side_needed = 1 if gap > 0 else -1 if gap < 0 else 0

        # Hysteresis check
        if self._last_action_threshold_used_abs is not None and self._last_action_side is not None:
            if side_needed != 0 and side_needed == -self._last_action_side:
                required = self.hysteresis_fraction * self._last_action_threshold_used_abs
                if abs(gap) < required:
                    return {"action": "none", "target_trade_base": 0.0}

        if abs(gap) >= eff_hard and eff_hard > 0:
            target = -gap  # full restore
            self._last_action_threshold_used_abs = eff_hard
            self._last_action_side = side_needed
            return {"action": "full", "target_trade_base": target}
        if abs(gap) >= eff_soft and eff_soft > 0:
            target = -self.partial_ratio * gap
            self._last_action_threshold_used_abs = eff_soft
            self._last_action_side = side_needed
            return {"action": "partial", "target_trade_base": target}
        return {"action": "none", "target_trade_base": 0.0}


