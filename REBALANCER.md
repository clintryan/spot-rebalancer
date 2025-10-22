## Spot Rebalancer (Bybit) - EMA/Anchor Aware Delta Control

This bot does NOT run a grid. You run Bybit's grid (or trade manually). The rebalancer only trades spot to correct net delta drift versus your futures, aiming to keep spread neutral/positive and maximize volume safely.

### Overview
- Computes net delta in base units: net = spot_base − futures_base.
- Keeps you near a desired net delta (often 0). Uses two thresholds:
  - soft: partial rebalance (default 50% of drift).
  - hard: full restore to target.
- Modulates thresholds with bias:
  - EMA/trend bias: more tolerant in favorable trend, less tolerant into resistance/trend conflict.
  - Recent-fills anchor: compares price to your 5–10 min buy/sell VWAP to prefer rebalancing when spread is favorable versus recent fills.
- Executes SPOT orders only: maker on soft (with short chase), taker on hard or if conditions worsen/timeout.

### Inputs
- desired_net_delta_base: target net base exposure (e.g., 0 for flat).
- thresholds: soft/hard in base or % of spot notional; partial_rebalance_ratio (default 0.5).
- bias:
  - mode: ema | manual | off
  - strength: [0..1], how strongly bias expands/shrinks thresholds
  - w_ema, w_anchor: weights for EMA vs anchor contributions
  - manual_override: [-1..+1] bias without EMA
- ema: fast/slow periods, slope_lookback_s, trend_threshold_pct.
- anchor: window_s, edge_bps_soft, edge_bps_hard, max_wait_s_on_soft, degrade_edge_with_time.
- execution: maker/taker behavior, min/max trade sizes.
- risk: slippage cap, cooldowns, hysteresis, notional caps, kill switch (drawdown).

### Decision Flow
1) Compute current price (WS), update EMA on candle close, update recent fills.
2) Snapshot delta: spot_base, futures_base, net_base_delta; delta_gap = net − desired.
3) Determine action side: SELL if gap>0 (long drift), BUY if gap<0 (short drift).
4) Compute combined bias = w_ema*ema_bias + w_anchor*anchor_bias.
5) Effective thresholds: soft/hard scaled by (1 + strength*combined_bias).
6) If |gap| ≥ hard → trade full: target_trade = −gap (taker allowed).
7) Else if |gap| ≥ soft → trade partial: target_trade = −0.5*gap (prefer maker). Apply anchor gate:
   - SELL requires price ≥ buy_vwap × (1 + edge_bps_soft).
   - BUY requires price ≤ sell_vwap × (1 − edge_bps_soft).
   - If gate fails, wait up to max_wait then execute (maker→optional taker per config).
8) Hysteresis prevents immediate flip-flopping after a trade.

### Files
- `bot/core/delta_engine.py`: Calculates spot/futures base exposure and net delta.
- `bot/core/recent_fills.py`: Rolling buy/sell VWAP and net fill imbalance.
- `bot/core/trend_bias.py`: EMA-based bias signal in [-1, +1].
- `bot/core/rebalance_policy.py`: Soft/hard thresholds, partial/full decisions, hysteresis.
- `bot/core/execution_spot.py`: Spot-only maker/taker with simple escalation.
- `bot/core/rebalancer.py`: Orchestrates all components per tick.
- `rebalancer_main.py`: Runnable entrypoint.

### Config (snippet)
See `config.yaml` under `rebalancer:`.

### Running
1) Set API keys in your `.env` file:
   ```
   BYBIT_API_KEY_Wood=your_api_key_here
   BYBIT_API_SECRET_Wood=your_api_secret_here
   ```
2) Configure `api.account_name` and `rebalancer` block in `config.yaml`.
3) Run:
```bash
python rebalancer_main.py --config config.yaml --symbol BTCUSDT
```

### Notes
- The rebalancer trades spot only; your futures hedge is assumed to be adjusted manually as needed.
- In strong trends, hard threshold ensures you recentre quickly to avoid dangerous drift.
- Recent-fills anchor helps avoid rebalancing at worse prices than your recent activity.


