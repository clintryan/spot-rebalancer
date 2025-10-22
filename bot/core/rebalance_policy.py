from dataclasses import dataclass
from typing import Optional


@dataclass
class Thresholds:
    units: str  # 'base' or 'percent'
    soft: float
    hard: float


@dataclass
class EmaRebalanceConfig:
    """Configuration for EMA-based opportunistic rebalancing"""
    enabled: bool = True
    # Percentage above fast EMA to trigger rebalancing in uptrend
    uptrend_breakout_pct: float = 1.0
    # Percentage distance from fast EMA to trigger rebalancing in downtrend
    downtrend_ema_touch_pct: float = 0.2
    # Minimum position size (in USDT) to consider for EMA-based rebalancing
    min_position_usdt: float = 100.0
    # Partial exit ratio when EMA trigger hits (0.0 to 1.0)
    ema_partial_ratio: float = 0.3


class RebalancePolicy:
    def __init__(
        self,
        thresholds: Thresholds,
        partial_ratio: float,
        hysteresis_fraction: float,
        ema_config: Optional[EmaRebalanceConfig] = None,
    ):
        self.th = thresholds
        self.partial_ratio = max(0.0, min(1.0, partial_ratio))
        self.hysteresis_fraction = max(0.0, min(1.0, hysteresis_fraction))
        self._last_action_threshold_used_abs: float | None = None
        self._last_action_side: int | None = None  # +1 sell, -1 buy
        
        # EMA-based opportunistic rebalancing
        self.ema_config = ema_config or EmaRebalanceConfig(enabled=False)
        self._last_ema_rebalance_time: float = 0.0

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

    def check_ema_rebalance_opportunity(
        self,
        current_price: float,
        ema_fast: float,
        trend: str,
        position_usdt: float,
        current_time: float,
    ) -> dict:
        """
        Check if EMA-based opportunistic rebalancing should trigger.
        
        Logic:
        - If long in uptrend: rebalance when price breaks X% above fast EMA (take profits defensively)
        - If long in downtrend: rebalance when price comes back to fast EMA (defensive exit opportunity)
        
        Returns dict with:
          - should_rebalance: bool
          - reason: str
          - suggested_ratio: float (0.0 to 1.0, portion of position to reduce)
        """
        if not self.ema_config.enabled:
            return {"should_rebalance": False, "reason": "", "suggested_ratio": 0.0}
        
        # Only trigger if we have a meaningful position
        if abs(position_usdt) < self.ema_config.min_position_usdt:
            return {"should_rebalance": False, "reason": "Position too small", "suggested_ratio": 0.0}
        
        # Cooldown check (prevent too frequent EMA-based rebalances)
        min_cooldown = 60.0  # Minimum 60 seconds between EMA rebalances
        if current_time - self._last_ema_rebalance_time < min_cooldown:
            return {"should_rebalance": False, "reason": "Cooldown active", "suggested_ratio": 0.0}
        
        is_long = position_usdt > 0
        price_vs_ema_pct = ((current_price - ema_fast) / ema_fast) * 100.0 if ema_fast > 0 else 0.0
        
        # Case 1: Long position in uptrend - rebalance when price breaks X% above fast EMA
        if is_long and trend == "UPTREND":
            if price_vs_ema_pct >= self.ema_config.uptrend_breakout_pct:
                return {
                    "should_rebalance": True,
                    "reason": f"Long in uptrend: price {price_vs_ema_pct:.2f}% above EMA (defensive profit-taking)",
                    "suggested_ratio": self.ema_config.ema_partial_ratio
                }
        
        # Case 2: Long position in downtrend - rebalance when price comes back to fast EMA
        elif is_long and trend == "DOWNTREND":
            # In downtrend, we want to exit when price rallies back near the EMA
            # Check if price is within X% of the EMA (either side)
            if abs(price_vs_ema_pct) <= self.ema_config.downtrend_ema_touch_pct:
                return {
                    "should_rebalance": True,
                    "reason": f"Long in downtrend: price near EMA ({price_vs_ema_pct:.2f}% - defensive exit)",
                    "suggested_ratio": self.ema_config.ema_partial_ratio
                }
        
        # Case 3: Short position logic (mirror of long logic)
        elif not is_long and position_usdt < 0:
            # Short in downtrend: rebalance when price breaks X% below fast EMA
            if trend == "DOWNTREND" and price_vs_ema_pct <= -self.ema_config.uptrend_breakout_pct:
                return {
                    "should_rebalance": True,
                    "reason": f"Short in downtrend: price {abs(price_vs_ema_pct):.2f}% below EMA (defensive profit-taking)",
                    "suggested_ratio": self.ema_config.ema_partial_ratio
                }
            # Short in uptrend: rebalance when price comes back to fast EMA
            elif trend == "UPTREND" and abs(price_vs_ema_pct) <= self.ema_config.downtrend_ema_touch_pct:
                return {
                    "should_rebalance": True,
                    "reason": f"Short in uptrend: price near EMA ({price_vs_ema_pct:.2f}% - defensive exit)",
                    "suggested_ratio": self.ema_config.ema_partial_ratio
                }
        
        return {"should_rebalance": False, "reason": "No EMA trigger", "suggested_ratio": 0.0}

    def mark_ema_rebalance(self, current_time: float):
        """Mark that an EMA-based rebalance occurred"""
        self._last_ema_rebalance_time = current_time

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


