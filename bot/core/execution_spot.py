import time
from dataclasses import dataclass


@dataclass
class MakerConfig:
    post_only: bool = True
    quote_improve_bps: int = 0
    chase_seconds: float = 5.0
    max_requotes: int = 3


@dataclass
class TakerConfig:
    allowed_on_soft: bool = False
    allowed_on_hard: bool = True


@dataclass
class ExecutionConfig:
    profile: str
    maker: MakerConfig
    taker: TakerConfig
    min_trade_base: float
    max_trade_base: float


class SpotExecutionEngine:
    def __init__(self, client, symbol: str, cfg: ExecutionConfig):
        self.client = client
        self.symbol = symbol
        self.cfg = cfg
        self.category = 'spot'

    def _clamp_qty(self, qty_base: float) -> float:
        return max(self.cfg.min_trade_base, min(self.cfg.max_trade_base, abs(qty_base)))

    def market(self, side: str, qty_base: float) -> bool:
        q = self._clamp_qty(qty_base)
        if q <= 0:
            return False
        resp = self.client.place_market_order(
            category=self.category,
            symbol=self.symbol,
            side=side,
            qty=q,
            verbose=False,
        )
        return bool(resp and resp.get('retCode') == 0)

    def post_only_limit_once(self, side: str, best_px: float) -> bool:
        """Place a single post-only limit at touch or 1 tick inside (approximated by quote_improve_bps)."""
        price = best_px
        # Bybit v5 does not accept explicit post-only via general params in this wrapper; rely on timeInForce='PostOnly' if available
        tif = 'PostOnly' if self.cfg.maker.post_only else 'GTC'
        resp = self.client.place_order(
            category=self.category,
            symbol=self.symbol,
            side=side,
            orderType='Limit',
            qty=self.cfg.min_trade_base,  # minimal order for probing is not ideal; caller should pass sized qty with market fallback
            price=price,
            timeInForce=tif,
            verbose=False,
        )
        return bool(resp and resp.get('retCode') == 0)

    def maker_then_escalate(self, side: str, qty_base: float, best_px: float, allow_taker: bool) -> bool:
        q = self._clamp_qty(qty_base)
        if q <= 0:
            return False

        # Try maker order(s) by re-placing at touch; simplified without tracking orderIds
        deadline = time.time() + max(0.0, float(self.cfg.maker.chase_seconds))
        placed = False
        while time.time() < deadline and not placed:
            resp = self.client.place_order(
                category=self.category,
                symbol=self.symbol,
                side=side,
                orderType='Limit',
                qty=q,
                price=best_px,
                timeInForce='PostOnly' if self.cfg.maker.post_only else 'GTC',
                verbose=False,
            )
            placed = bool(resp and resp.get('retCode') == 0)
            if placed:
                return True
            time.sleep(0.5)

        if allow_taker:
            return self.market(side, q)
        return False


