from dataclasses import dataclass
from typing import Optional


@dataclass
class DeltaSnapshot:
    spot_base: float
    futures_base: float
    net_base_delta: float
    desired_net_delta_base: float


class DeltaEngine:
    """
    Computes spot/futures base exposure and net delta (base units).
    futures_base ≈ futures_notional_quote / mark_price for USDT-linear.
    """

    def __init__(self, client, spot_symbol: str, futures_symbol: Optional[str] = None, desired_net_delta_base: float = 0.0):
        self.client = client
        self.spot_symbol = spot_symbol
        self.futures_symbol = futures_symbol or spot_symbol
        self.base_symbol = spot_symbol.replace("USDT", "") if spot_symbol.endswith("USDT") else spot_symbol
        self.desired_net_delta_base = float(desired_net_delta_base)

    def get_spot_base(self) -> float:
        bal = 0.0
        try:
            resp = self.client.get_coin_balance(self.base_symbol)
            if resp and resp.get('retCode') == 0:
                for acct in resp.get('result', {}).get('list', []) or []:
                    for coin in acct.get('coin', []) or []:
                        if coin.get('coin') == self.base_symbol:
                            v = coin.get('walletBalance') or '0'
                            bal += float(v or 0)
        except Exception:
            pass
        return bal

    def get_futures_base(self, mark_price: Optional[float]) -> float:
        try:
            resp = self.client.get_positions(category='linear', symbol=self.futures_symbol)
            if resp and resp.get('retCode') == 0:
                lst = resp.get('result', {}).get('list', []) or []
                if not lst:
                    return 0.0
                pos = lst[0]
                size = float(pos.get('size') or 0)
                side = pos.get('side')
                signed = size if side == 'Buy' else -size if side == 'Sell' else 0.0
                if signed == 0.0:
                    return 0.0
                px = mark_price
                if px is None:
                    try:
                        px = float(pos.get('markPrice') or 0)
                    except Exception:
                        px = 0.0
                if px <= 0:
                    return 0.0
                # futures notional quote = signed * px; convert to base by dividing by px ⇒ equals signed
                return signed
        except Exception:
            pass
        return 0.0

    def snapshot(self, mark_price: Optional[float]) -> DeltaSnapshot:
        spot_base = self.get_spot_base()
        futures_base = self.get_futures_base(mark_price)
        net = spot_base - futures_base
        return DeltaSnapshot(
            spot_base=spot_base,
            futures_base=futures_base,
            net_base_delta=net,
            desired_net_delta_base=self.desired_net_delta_base,
        )


