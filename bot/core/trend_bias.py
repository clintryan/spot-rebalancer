from dataclasses import dataclass
from typing import Optional


@dataclass
class EmaConfig:
    fast_period: int
    slow_period: int
    slope_lookback_s: int
    trend_threshold_pct: float = 0.0


class EmaTrendBias:
    """
    Maintains EMA state on closed candles and produces a normalized bias ∈ [-1, +1]
    using price vs EMA alignment and EMA slope sign/magnitude.
    """

    def __init__(self, config: EmaConfig):
        self.cfg = config
        self._ema_fast: Optional[float] = None
        self._ema_slow: Optional[float] = None
        self._last_close: Optional[float] = None
        self._last_ts: Optional[int] = None
        self._prev_ema_fast: Optional[float] = None
        self._prev_ema_slow: Optional[float] = None

    def initialize(self, closes: list[float]):
        if not closes or len(closes) < max(self.cfg.fast_period, self.cfg.slow_period):
            return
        # seed with SMA
        def seed(period: int):
            return sum(closes[:period]) / period
        self._ema_fast = seed(self.cfg.fast_period)
        self._ema_slow = seed(self.cfg.slow_period)
        a_fast = 2 / (self.cfg.fast_period + 1)
        a_slow = 2 / (self.cfg.slow_period + 1)
        for p in closes[max(self.cfg.fast_period, self.cfg.slow_period):]:
            self._ema_fast = p * a_fast + self._ema_fast * (1 - a_fast)
            self._ema_slow = p * a_slow + self._ema_slow * (1 - a_slow)

    def on_closed_candle(self, close_price: float, close_ts_ms: int):
        if self._ema_fast is None or self._ema_slow is None:
            return
        a_fast = 2 / (self.cfg.fast_period + 1)
        a_slow = 2 / (self.cfg.slow_period + 1)
        self._prev_ema_fast = self._ema_fast
        self._prev_ema_slow = self._ema_slow
        self._ema_fast = close_price * a_fast + self._ema_fast * (1 - a_fast)
        self._ema_slow = close_price * a_slow + self._ema_slow * (1 - a_slow)
        self._last_close = close_price
        self._last_ts = close_ts_ms

    def get_bias(self, current_price: float, delta_sign_needed: int) -> float:
        """
        delta_sign_needed: +1 if we need to sell (long delta) or buy? We interpret as:
          +1 means we need to SELL spot (we are long vs desired),
          -1 means we need to BUY spot (we are short vs desired).
        Outputs bias in [-1, +1]: positive means conditions favor acting now, negative suggests waiting.
        """
        if self._ema_fast is None or self._ema_slow is None:
            return 0.0

        # price vs EMA alignment (fast vs slow trend direction)
        trend = 0.0
        if self._ema_slow > 0:
            diff_pct = (self._ema_fast - self._ema_slow) / self._ema_slow * 100.0
            if abs(diff_pct) >= self.cfg.trend_threshold_pct:
                trend = 1.0 if diff_pct > 0 else -1.0

        # slope sign from fast EMA
        slope = 0.0
        if self._prev_ema_fast is not None and self._prev_ema_fast > 0:
            slope_raw = (self._ema_fast - self._prev_ema_fast) / self._prev_ema_fast
            # normalize slope into [-1, 1] using a soft clip
            slope = max(-1.0, min(1.0, slope_raw * 1000))  # scale factor

        # alignment with action side; if we need to SELL (delta_sign_needed=+1),
        # a downtrend (trend=-1) should increase urgency; uptrend reduces it.
        ema_bias = 0.6 * (-trend * delta_sign_needed) + 0.4 * (-slope * delta_sign_needed)
        # clamp
        if ema_bias > 1.0:
            ema_bias = 1.0
        elif ema_bias < -1.0:
            ema_bias = -1.0
        return ema_bias


