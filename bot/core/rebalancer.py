import time
from dataclasses import dataclass
from typing import Optional

from .recent_fills import RecentFillsAnchor
from .trend_bias import EmaConfig, EmaTrendBias
from .rebalance_policy import Thresholds, RebalancePolicy
from .execution_spot import ExecutionConfig, MakerConfig, TakerConfig, SpotExecutionEngine
from .delta_engine import DeltaEngine


@dataclass
class AnchorConfig:
    window_s: int
    edge_bps_soft: int
    edge_bps_hard: int
    max_wait_s_on_soft: int
    degrade_edge_with_time: bool


class SpotRebalancer:
    def __init__(self, client, ws_manager, cfg: dict):
        self.client = client
        self.ws = ws_manager
        self.cfg = cfg

        r = cfg["rebalancer"]
        # Use the main symbol from strategy section
        self.symbol = cfg["strategy"]["symbol"]
        self.desired_net_delta_base = float(r.get("desired_net_delta_base", 0.0))

        # Engines
        self.delta_engine = DeltaEngine(
            client, self.symbol, self.symbol, self.desired_net_delta_base
        )

        ema_cfg = EmaConfig(
            fast_period=r["ema"].get("fast_period", 20),
            slow_period=r["ema"].get("slow_period", 50),
            slope_lookback_s=r["ema"].get("slope_lookback_s", 60),
            trend_threshold_pct=r["ema"].get("trend_threshold_pct", 0.0),
        )
        self.ema_bias = EmaTrendBias(ema_cfg)

        anc = r["anchor"]
        self.anchor_cfg = AnchorConfig(
            window_s=anc.get("window_s", 600),
            edge_bps_soft=anc.get("edge_bps_soft", 10),
            edge_bps_hard=anc.get("edge_bps_hard", 2),
            max_wait_s_on_soft=anc.get("max_wait_s_on_soft", 30),
            degrade_edge_with_time=bool(anc.get("degrade_edge_with_time", True)),
        )
        self.fills_anchor = RecentFillsAnchor(client, self.symbol, self.anchor_cfg.window_s)

        th = r["thresholds"]
        self.policy = RebalancePolicy(
            thresholds=Thresholds(units=th.get("units", "percent"), soft=float(th["soft"]), hard=float(th["hard"])),
            partial_ratio=float(r.get("partial_rebalance_ratio", 0.5)),
            hysteresis_fraction=float(r["risk"].get("hysteresis_fraction", 0.7)),
        )

        ex = r["execution"]
        self.exec = SpotExecutionEngine(
            client,
            self.symbol,
            ExecutionConfig(
                profile=ex.get("profile", "maker_on_soft_taker_on_hard"),
                maker=MakerConfig(
                    post_only=bool(ex.get("maker", {}).get("post_only", True)),
                    quote_improve_bps=int(ex.get("maker", {}).get("quote_improve_bps", 0)),
                    chase_seconds=float(ex.get("maker", {}).get("chase_seconds", 5)),
                    max_requotes=int(ex.get("maker", {}).get("max_requotes", 3)),
                ),
                taker=TakerConfig(
                    allowed_on_soft=bool(ex.get("taker", {}).get("allowed_on_soft", False)),
                    allowed_on_hard=bool(ex.get("taker", {}).get("allowed_on_hard", True)),
                ),
                min_trade_base=float(ex.get("min_trade_base", 0.000001)),
                max_trade_base=float(ex.get("max_trade_base", 1_000_000)),
            ),
        )

        self.bias_cfg = r["bias"]
        self._last_soft_wait_start: Optional[float] = None

    def _combined_bias(self, current_price: float, action_side_needed: int) -> float:
        # ema component
        ema_b = 0.0
        if self.bias_cfg.get("mode", "ema") == "ema":
            ema_b = self.ema_bias.get_bias(current_price, action_side_needed)
        elif self.bias_cfg.get("mode") == "manual":
            ema_b = float(self.bias_cfg.get("manual_override", 0.0))
        # anchor component
        anchor = self.fills_anchor.get_anchor()
        buy_vwap = anchor.get("buy_vwap")
        sell_vwap = anchor.get("sell_vwap")
        anchor_bias = 0.0
        if action_side_needed == 1:  # need to SELL
            if buy_vwap is not None:
                edge = self.anchor_cfg.edge_bps_soft / 1e4 * buy_vwap
                anchor_bias = 1.0 if current_price >= buy_vwap + edge else -1.0
        elif action_side_needed == -1:  # need to BUY
            if sell_vwap is not None:
                edge = self.anchor_cfg.edge_bps_soft / 1e4 * sell_vwap
                anchor_bias = 1.0 if current_price <= sell_vwap - edge else -1.0

        w_ema = float(self.bias_cfg.get("w_ema", 0.5))
        w_anchor = float(self.bias_cfg.get("w_anchor", 0.5))
        combined = w_ema * ema_b + w_anchor * anchor_bias
        # clamp
        if combined > 1.0:
            combined = 1.0
        if combined < -1.0:
            combined = -1.0
        return combined

    def _spot_notional_quote(self, price: float) -> float:
        try:
            resp = self.client.get_coin_balance(self.delta_engine.base_symbol)
            total = 0.0
            if resp and resp.get('retCode') == 0:
                for acct in resp.get('result', {}).get('list', []) or []:
                    for coin in acct.get('coin', []) or []:
                        if coin.get('coin') == self.delta_engine.base_symbol:
                            v = float(coin.get('walletBalance') or 0)
                            total += v
            return total * price
        except Exception:
            return 0.0

    def step(self):
        # Refresh inputs
        price = self.ws.get_latest_price()
        if price is None:
            return
        closed = self.ws.get_latest_closed_kline()
        if closed:
            self.ema_bias.on_closed_candle(float(closed['close']), int(closed['ts']))
        self.fills_anchor.update()

        snap = self.delta_engine.snapshot(price)
        delta_gap = snap.net_base_delta - snap.desired_net_delta_base
        side_needed = 1 if delta_gap > 0 else -1 if delta_gap < 0 else 0
        if side_needed == 0:
            self._last_soft_wait_start = None
            return

        combined_bias = self._combined_bias(price, side_needed)
        eff_soft, eff_hard = self.policy.compute_effective_thresholds(
            spot_notional_quote=self._spot_notional_quote(price),
            mark_price=price,
            combined_bias=combined_bias,
            bias_strength=float(self.cfg["rebalancer"]["bias"].get("strength", 0.6)),
        )

        decision = self.policy.decide(delta_gap, eff_soft, eff_hard)
        action = decision["action"]
        target = decision["target_trade_base"]

        if action == "none":
            self._last_soft_wait_start = None
            return

        qty = abs(target)
        side = "Sell" if target < 0 else "Buy"

        if action == "full":
            # hard breach: allow taker
            self.exec.market(side, qty)
            self._last_soft_wait_start = None
            return

        # partial on soft breach: respect anchor gate with wait
        max_wait = self.anchor_cfg.max_wait_s_on_soft
        now = time.time()
        if self._last_soft_wait_start is None:
            self._last_soft_wait_start = now

        waited = now - self._last_soft_wait_start
        anchor_ok = True
        anchor = self.fills_anchor.get_anchor()
        if side == "Sell" and anchor.get("buy_vwap") is not None:
            edge = self.anchor_cfg.edge_bps_soft / 1e4 * anchor["buy_vwap"]
            anchor_ok = price >= anchor["buy_vwap"] + edge
        elif side == "Buy" and anchor.get("sell_vwap") is not None:
            edge = self.anchor_cfg.edge_bps_soft / 1e4 * anchor["sell_vwap"]
            anchor_ok = price <= anchor["sell_vwap"] - edge

        if anchor_ok or waited >= max_wait:
            # prefer maker then escalate to taker only if allowed_on_soft
            allow_taker = bool(self.cfg["rebalancer"]["execution"].get("taker", {}).get("allowed_on_soft", False))
            self.exec.maker_then_escalate(side, qty, price, allow_taker)
            self._last_soft_wait_start = None
        # else: keep waiting until next step


