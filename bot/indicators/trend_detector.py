# bot/indicators.py
from collections import deque

class TrendDetector:
    """
    EMA on close seeded by SMA(period), with optional smoothing applied to the EMA series
    to match platform behavior (e.g., Bybit/TradingView variations):
      - smoothing_type = 'none' | 'sma' | 'ema'
      - smoothing_window defaults to the EMA period when not provided
    Updates only on CLOSED candles.
    """
    def __init__(
        self,
        ema_fast_period: int,
        ema_slow_period: int,
        smoothing_type: str = 'none',
        smoothing_window_fast: int | None = None,
        smoothing_window_slow: int | None = None,
        trend_threshold_pct: float = 0.0,
    ):
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period

        # Smoothing configuration
        self.smoothing_type = (smoothing_type or 'none').lower()
        self.smoothing_window_fast = smoothing_window_fast or ema_fast_period
        self.smoothing_window_slow = smoothing_window_slow or ema_slow_period
        
        # Trend threshold configuration
        self.trend_threshold_pct = trend_threshold_pct

        # Raw EMA values (unsmoothed)
        self.ema_fast_raw = None
        self.ema_slow_raw = None

        # Final EMA values in use (smoothed or raw based on config)
        self.ema_fast = None
        self.ema_slow = None

        # For SMA smoothing we need rolling windows of raw EMA values
        self._fast_raw_window = deque(maxlen=self.smoothing_window_fast)
        self._slow_raw_window = deque(maxlen=self.smoothing_window_slow)
        self._fast_raw_sum = 0.0
        self._slow_raw_sum = 0.0

        # For EMA smoothing we maintain a second-level EMA on the raw EMA series
        self._ema_smooth_fast = None
        self._ema_smooth_slow = None

        self.trend = "RANGING"
        self.previous_trend = "RANGING"
        self.cross_detected = False

    def _seed_ema(self, closes: list, period: int) -> tuple[float, int]:
        """Return (seed_value, next_index) using SMA(period) as seed.
        next_index is the index in closes from which to start EMA recursion.
        """
        sma = sum(closes[:period]) / period
        return sma, period

    def _apply_smoothing_after_append(self, is_fast: bool):
        """Update smoothed EMA after appending latest raw EMA into the window/EMA."""
        if self.smoothing_type == 'none':
            self.ema_fast = self.ema_fast_raw
            self.ema_slow = self.ema_slow_raw
            return

        if self.smoothing_type == 'sma':
            # Compute rolling average for each stream
            if is_fast:
                if len(self._fast_raw_window) == self.smoothing_window_fast:
                    self.ema_fast = self._fast_raw_sum / self.smoothing_window_fast
                else:
                    # Not enough points yet; fall back to raw
                    self.ema_fast = self.ema_fast_raw
            else:
                if len(self._slow_raw_window) == self.smoothing_window_slow:
                    self.ema_slow = self._slow_raw_sum / self.smoothing_window_slow
                else:
                    self.ema_slow = self.ema_slow_raw
            return

        if self.smoothing_type == 'ema':
            if is_fast:
                alpha2 = 2 / (self.smoothing_window_fast + 1)
                if self._ema_smooth_fast is None:
                    self._ema_smooth_fast = self.ema_fast_raw
                else:
                    self._ema_smooth_fast = (
                        self.ema_fast_raw * alpha2 + self._ema_smooth_fast * (1 - alpha2)
                    )
                self.ema_fast = self._ema_smooth_fast
            else:
                alpha2 = 2 / (self.smoothing_window_slow + 1)
                if self._ema_smooth_slow is None:
                    self._ema_smooth_slow = self.ema_slow_raw
                else:
                    self._ema_smooth_slow = (
                        self.ema_slow_raw * alpha2 + self._ema_smooth_slow * (1 - alpha2)
                    )
                self.ema_slow = self._ema_smooth_slow

    def initialize(self, historical_closes: list):
        """Initialize EMAs from historical closes using SMA-seeded EMA, with optional smoothing."""
        min_required = max(self.ema_fast_period, self.ema_slow_period)
        if not historical_closes or len(historical_closes) < min_required:
            print("⚠️ Not enough historical data to initialize trend detector.")
            return

        # Seed fast EMA with SMA(fast)
        self.ema_fast_raw, idx_fast = self._seed_ema(historical_closes, self.ema_fast_period)
        alpha_fast = 2 / (self.ema_fast_period + 1)
        # Walk forward computing raw EMA and building smoothing state
        # Include the seed point into smoothing windows as platforms usually start plotting from there
        self._fast_raw_window.clear()
        self._fast_raw_sum = 0.0
        self._fast_raw_window.append(self.ema_fast_raw)
        self._fast_raw_sum += self.ema_fast_raw
        self._apply_smoothing_after_append(is_fast=True)
        for i in range(idx_fast, len(historical_closes)):
            price = historical_closes[i]
            self.ema_fast_raw = price * alpha_fast + self.ema_fast_raw * (1 - alpha_fast)
            # update SMA smoothing window
            if len(self._fast_raw_window) == self._fast_raw_window.maxlen:
                self._fast_raw_sum -= self._fast_raw_window[0]
            self._fast_raw_window.append(self.ema_fast_raw)
            self._fast_raw_sum += self.ema_fast_raw
            self._apply_smoothing_after_append(is_fast=True)

        # Seed slow EMA with SMA(slow)
        self.ema_slow_raw, idx_slow = self._seed_ema(historical_closes, self.ema_slow_period)
        alpha_slow = 2 / (self.ema_slow_period + 1)
        self._slow_raw_window.clear()
        self._slow_raw_sum = 0.0
        self._slow_raw_window.append(self.ema_slow_raw)
        self._slow_raw_sum += self.ema_slow_raw
        self._apply_smoothing_after_append(is_fast=False)
        for i in range(idx_slow, len(historical_closes)):
            price = historical_closes[i]
            self.ema_slow_raw = price * alpha_slow + self.ema_slow_raw * (1 - alpha_slow)
            if len(self._slow_raw_window) == self._slow_raw_window.maxlen:
                self._slow_raw_sum -= self._slow_raw_window[0]
            self._slow_raw_window.append(self.ema_slow_raw)
            self._slow_raw_sum += self.ema_slow_raw
            self._apply_smoothing_after_append(is_fast=False)

        print(
            f"✅ Trend detector initialized (EMA seeded by SMA, smoothing={self.smoothing_type}): "
            f"EMA Fast={self.ema_fast:.4f}, EMA Slow={self.ema_slow:.4f}"
        )
        self._update_trend()

    def update_with_close(self, close_price: float):
        """Update EMAs using a new closed candle price, maintaining smoothing state."""
        if self.ema_fast_raw is None or self.ema_slow_raw is None:
            return

        alpha_fast = 2 / (self.ema_fast_period + 1)
        alpha_slow = 2 / (self.ema_slow_period + 1)

        # Update raw EMAs
        self.ema_fast_raw = (close_price * alpha_fast) + (self.ema_fast_raw * (1 - alpha_fast))
        self.ema_slow_raw = (close_price * alpha_slow) + (self.ema_slow_raw * (1 - alpha_slow))

        # Update SMA smoothing windows and smoothed values
        if self.smoothing_type == 'sma':
            if len(self._fast_raw_window) == self._fast_raw_window.maxlen:
                self._fast_raw_sum -= self._fast_raw_window[0]
            self._fast_raw_window.append(self.ema_fast_raw)
            self._fast_raw_sum += self.ema_fast_raw
            self._apply_smoothing_after_append(is_fast=True)

            if len(self._slow_raw_window) == self._slow_raw_window.maxlen:
                self._slow_raw_sum -= self._slow_raw_window[0]
            self._slow_raw_window.append(self.ema_slow_raw)
            self._slow_raw_sum += self.ema_slow_raw
            self._apply_smoothing_after_append(is_fast=False)
        elif self.smoothing_type == 'ema':
            self._apply_smoothing_after_append(is_fast=True)
            self._apply_smoothing_after_append(is_fast=False)
        else:
            # No smoothing
            self.ema_fast = self.ema_fast_raw
            self.ema_slow = self.ema_slow_raw

        self._update_trend()

    def _update_trend(self):
        """Internal method to set the trend string and detect crosses (uses smoothed EMAs)."""
        self.previous_trend = self.trend

        if self.ema_fast is None or self.ema_slow is None:
            self.trend = "RANGING"
            self.cross_detected = False
            return

        # Calculate percentage difference between EMAs
        if self.ema_slow > 0:
            diff_pct = abs((self.ema_fast - self.ema_slow) / self.ema_slow) * 100
        else:
            diff_pct = 0

        # Only consider it a trend if the difference exceeds the threshold
        if diff_pct >= self.trend_threshold_pct:
            if self.ema_fast > self.ema_slow:
                self.trend = "UPTREND"
            elif self.ema_fast < self.ema_slow:
                self.trend = "DOWNTREND"
            else:
                self.trend = "RANGING"
        else:
            self.trend = "RANGING"

        cross_occurred = (
            (self.previous_trend == "UPTREND" and self.trend == "DOWNTREND") or
            (self.previous_trend == "DOWNTREND" and self.trend == "UPTREND")
        )
        self.cross_detected = cross_occurred

    def get_and_reset_cross(self) -> bool:
        cross_status = self.cross_detected
        self.cross_detected = False
        return cross_status

# ----------------------
# EMA Diagnostic Helpers
# ----------------------
def _compute_ema_series_seed_sma(closes: list, period: int) -> list:
    """Standard EMA series seeded by SMA(period). Returns list with None until seed ready."""
    if not closes or period <= 0 or len(closes) < period:
        return [None] * len(closes)
    alpha = 2 / (period + 1)
    series = [None] * len(closes)
    ema = sum(closes[:period]) / period
    series[period - 1] = ema
    for i in range(period, len(closes)):
        ema = closes[i] * alpha + ema * (1 - alpha)
        series[i] = ema
    return series

def _sma_of_series(series: list, window: int) -> list:
    if window <= 1:
        return series[:]
    out = [None] * len(series)
    run_sum = 0.0
    count = 0
    for i, v in enumerate(series):
        if v is not None:
            run_sum += v
            count += 1
        if i >= window and series[i - window] is not None:
            run_sum -= series[i - window]
            count -= 1
        if count == window:
            out[i] = run_sum / window
    return out

def _ema_of_series(series: list, period: int) -> list:
    if period <= 1:
        return series[:]
    alpha = 2 / (period + 1)
    out = [None] * len(series)
    ema = None
    for i, v in enumerate(series):
        if v is None:
            continue
        if ema is None:
            ema = v
        else:
            ema = v * alpha + ema * (1 - alpha)
        out[i] = ema
    return out

def compute_ema_variants(closes: list, fast: int = 9, slow: int = 21) -> dict:
    """Compute several EMA variants for diagnostics and return last values."""
    ema_fast_std = _compute_ema_series_seed_sma(closes, fast)
    ema_slow_std = _compute_ema_series_seed_sma(closes, slow)

    # Our current: SMA smoothing of EMA series with same window length
    ema_fast_sma_smooth = _sma_of_series(ema_fast_std, fast)
    ema_slow_sma_smooth = _sma_of_series(ema_slow_std, slow)

    # Alternative: EMA smoothing of EMA series (double EMA-like)
    ema_fast_ema_smooth = _ema_of_series(ema_fast_std, fast)
    ema_slow_ema_smooth = _ema_of_series(ema_slow_std, slow)

    # Extract last non-None values
    def last_valid(series):
        for v in reversed(series):
            if v is not None:
                return v
        return None

    return {
        'standard': {
            'fast': last_valid(ema_fast_std),
            'slow': last_valid(ema_slow_std)
        },
        'sma_smoothed': {
            'fast': last_valid(ema_fast_sma_smooth),
            'slow': last_valid(ema_slow_sma_smooth)
        },
        'ema_smoothed': {
            'fast': last_valid(ema_fast_ema_smooth),
            'slow': last_valid(ema_slow_ema_smooth)
        }
    }