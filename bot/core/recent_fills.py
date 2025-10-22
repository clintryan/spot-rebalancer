import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple


@dataclass
class Fill:
    ts: float
    side: str  # 'Buy' or 'Sell'
    price: float
    qty: float  # base units


class RecentFillsAnchor:
    """
    Maintains a rolling window of the user's spot executions and computes
    side-specific VWAPs (buy_vwap, sell_vwap) and net base imbalance.

    Data source: Bybit private executions API (category='spot').
    """

    def __init__(
        self,
        client,
        symbol: str,
        window_seconds: int = 600,
        poll_interval_s: float = 3.0,
        max_cached: int = 500,
    ):
        self.client = client
        self.symbol = symbol
        self.window_seconds = max(60, int(window_seconds))
        self.poll_interval_s = max(1.0, float(poll_interval_s))
        self.fills: Deque[Fill] = deque(maxlen=max_cached)
        self._last_poll_time: float = 0.0
        self._last_exec_id: Optional[str] = None

    def _fetch_new_fills(self) -> None:
        now = time.time()
        if now - self._last_poll_time < self.poll_interval_s:
            return
        self._last_poll_time = now

        try:
            resp = self.client.get_executions(category="spot", symbol=self.symbol, limit=100)
            if not resp or resp.get("retCode") != 0:
                return
            records = resp.get("result", {}).get("list", []) or []
            # API returns most recent first typically; process oldest→newest
            records = list(reversed(records))

            for r in records:
                # Use execId as unique; skip ones we've already processed
                exec_id = r.get("execId") or r.get("execID")
                if self._last_exec_id is not None and exec_id is not None:
                    # fast path: if seen id matches, likely older ones too
                    # but to be safe we won't break; we skip duplicates
                    pass

                try:
                    side = r.get("side")
                    price = float(r.get("execPrice") or r.get("price") or 0)
                    qty = float(r.get("execQty") or r.get("qty") or 0)
                    # execTime may be in ms
                    ts_raw = r.get("execTime") or r.get("time")
                    ts_sec = float(ts_raw) / 1000.0 if ts_raw and int(ts_raw) > 1e12 else float(ts_raw or 0)
                except (TypeError, ValueError):
                    continue

                if price <= 0 or qty <= 0 or side not in ("Buy", "Sell"):
                    continue

                # Avoid duplicate append by comparing last element
                if self.fills and exec_id is not None:
                    # crude duplicate check: same ts, side, qty, price
                    last = self.fills[-1]
                    if (
                        abs(last.ts - ts_sec) < 1e-6
                        and last.side == side
                        and abs(last.price - price) < 1e-12
                        and abs(last.qty - qty) < 1e-12
                    ):
                        continue

                self.fills.append(Fill(ts=ts_sec or now, side=side, price=price, qty=qty))
                self._last_exec_id = exec_id or self._last_exec_id

            self._prune_old()
        except Exception:
            # Silent fail; upstream handles logging
            pass

    def _prune_old(self) -> None:
        cutoff = time.time() - self.window_seconds
        while self.fills and self.fills[0].ts < cutoff:
            self.fills.popleft()

    def update(self) -> None:
        self._fetch_new_fills()

    def _compute_vwaps(self) -> Tuple[Optional[float], Optional[float], float]:
        cutoff = time.time() - self.window_seconds
        buy_notional = 0.0
        buy_qty = 0.0
        sell_notional = 0.0
        sell_qty = 0.0
        net_imbalance_base = 0.0

        for f in self.fills:
            if f.ts < cutoff:
                continue
            if f.side == "Buy":
                buy_notional += f.price * f.qty
                buy_qty += f.qty
                net_imbalance_base += f.qty
            else:
                sell_notional += f.price * f.qty
                sell_qty += f.qty
                net_imbalance_base -= f.qty

        buy_vwap = (buy_notional / buy_qty) if buy_qty > 0 else None
        sell_vwap = (sell_notional / sell_qty) if sell_qty > 0 else None
        return buy_vwap, sell_vwap, net_imbalance_base

    def get_anchor(self) -> dict:
        """
        Returns:
          {
            'buy_vwap': Optional[float],
            'sell_vwap': Optional[float],
            'net_fill_imbalance_base': float,
            'window_s': int,
            'sample_count': int
          }
        """
        self._prune_old()
        buy_vwap, sell_vwap, net_imbalance = self._compute_vwaps()
        return {
            "buy_vwap": buy_vwap,
            "sell_vwap": sell_vwap,
            "net_fill_imbalance_base": net_imbalance,
            "window_s": self.window_seconds,
            "sample_count": sum(1 for f in self.fills if f.ts >= time.time() - self.window_seconds),
        }


