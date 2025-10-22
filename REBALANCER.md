## Spot Rebalancer (Bybit) - Simple Trend-Aware Delta Control

**Purpose**: Automatically rebalance spot positions to maintain target delta exposure between spot and futures positions.

### How It Works

The bot continuously monitors your spot and futures positions and rebalances when they diverge from your target:

1. **Calculate Total Delta**: `spot_usdt + futures_usdt`
2. **Calculate Divergence**: `total_delta - target_delta`
3. **Check Trend** (optional): Updates EMAs to determine market trend
4. **Adjust Threshold**: If trend supports current exposure, be more tolerant (1.5x threshold)
5. **Rebalance If Needed**: If divergence exceeds threshold, place spot orders to rebalance

### Key Features

- ✅ **Simple & Clear**: No complex bias calculations or anchor logic
- 📈 **Trend-Aware**: More tolerant of long exposure in uptrends, short exposure in downtrends
- 🎯 **Smart Execution**: Starts with limit orders, escalates to market if needed
- ⚡ **Responsive**: Monitors positions in real-time via websocket

### Configuration

Simple configuration in `config.yaml`:

```yaml
rebalancer:
  target_delta_usdt: 0              # Target exposure (usually 0 for neutral)
  rebalance_threshold_usdt: 100     # Rebalance when divergence exceeds this
  
  # Trend awareness
  use_trend: true                   # Enable trend-based threshold adjustment
  ema_fast_period: 9                # Fast EMA period
  ema_slow_period: 21               # Slow EMA period
  trend_threshold_pct: 0.1          # % difference to declare trend
  trend_multiplier: 1.5             # How much more tolerant in favorable trends
  
  # Execution
  use_limit_orders: true            # Try limit orders first
  max_wait_seconds: 30              # Max wait before using market order
  cooldown_seconds: 10              # Time between rebalance attempts
```

### Example Scenarios

**Scenario 1: Uptrend with Long Exposure**
- Spot: +$500, Futures: $0, Target: $0
- Divergence: +$500 (too much long exposure)
- Trend: UPTREND (EMA9 > EMA21)
- Adjusted threshold: $150 (base $100 × 1.5)
- Action: ⏸️ **Wait** - divergence within adjusted threshold

**Scenario 2: Downtrend with Long Exposure**
- Spot: +$500, Futures: $0, Target: $0
- Divergence: +$500 (too much long exposure)
- Trend: DOWNTREND (EMA9 < EMA21)
- Adjusted threshold: $100 (standard, trend opposes)
- Action: 🔄 **Rebalance** - sell spot to reduce exposure

### Running

1) Set API keys in your `.env` file:
   ```
   BYBIT_API_KEY_Wood=your_api_key_here
   BYBIT_API_SECRET_Wood=your_api_secret_here
   ```

2) Configure settings in `config.yaml`

3) Run:
   ```bash
   python rebalancer_main.py --config config.yaml
   ```

### Files

- `bot/core/rebalancer.py`: Main rebalancer logic (simplified!)
- `rebalancer_main.py`: Entry point

### Notes

- The rebalancer **only trades spot** - your futures positions are managed separately
- Trend awareness is optional but recommended to avoid fighting the market
- Start with conservative thresholds ($100-$500) and adjust based on your risk tolerance


