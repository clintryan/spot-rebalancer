# EMA-Based Opportunistic Rebalancing

## Overview

The bot now includes **EMA-based opportunistic rebalancing** - a defensive strategy that triggers rebalancing based on price movement relative to the fast EMA (EMA9 by default), independent of your normal delta thresholds.

This is particularly useful for:
- Taking profits defensively in strong trends
- Exiting positions defensively when trends reverse
- Managing risk more dynamically based on price action

## How It Works

The system monitors your position direction, current trend, and price distance from the fast EMA. It triggers rebalancing in two key scenarios:

### 1. Long in Uptrend - Defensive Profit Taking

When you have a **long position in an uptrend**, the system takes profits when price breaks **X% above the fast EMA**.

**Logic:**
- Position: Long (positive spot exposure)
- Trend: UPTREND (EMA9 > EMA21)
- Trigger: Price > EMA9 + X%

**Example:**
```
Position: +$1000 in spot
Trend: UPTREND
EMA9: $100.00
Current Price: $101.05 (1.05% above EMA9)
Config: uptrend_breakout_pct = 1.0

✅ TRIGGERS: Price is 1.05% above EMA9 (threshold: 1.0%)
Action: Sell 30% of position ($300) defensively
```

**Why?** In an uptrend, when price extends significantly above the EMA, it's a good opportunity to take some profits before potential pullbacks.

### 2. Long in Downtrend - Defensive Exit

When you have a **long position in a downtrend**, the system exits when price rallies back **near the fast EMA**.

**Logic:**
- Position: Long (positive spot exposure)
- Trend: DOWNTREND (EMA9 < EMA21)
- Trigger: Price within ±X% of EMA9

**Example:**
```
Position: +$1000 in spot
Trend: DOWNTREND
EMA9: $100.00
Current Price: $100.15 (0.15% above EMA9)
Config: downtrend_ema_touch_pct = 0.2

✅ TRIGGERS: Price is 0.15% from EMA9 (threshold: 0.2%)
Action: Sell 30% of position ($300) defensively
```

**Why?** In a downtrend, the EMA acts as resistance. When price rallies back to the EMA, it's often rejected, so it's a good opportunity to reduce exposure.

## Configuration

All settings are in `config.yaml` under `rebalancer.ema_rebalance`:

```yaml
rebalancer:
  # ... other rebalancer settings ...
  
  # EMA-based opportunistic rebalancing (defensive strategy)
  ema_rebalance:
    enabled: true                       # Enable/disable the feature
    uptrend_breakout_pct: 1.0          # Long in uptrend: trigger when X% above EMA9
    downtrend_ema_touch_pct: 0.2       # Long in downtrend: trigger when within X% of EMA9
    min_position_usdt: 100.0           # Minimum position size to consider ($100)
    ema_partial_ratio: 0.3             # Reduce 30% of position when triggered
    cooldown_seconds: 60               # Minimum 60s between EMA rebalances
```

### Parameter Details

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enabled` | `true` | Enable or disable EMA-based rebalancing |
| `uptrend_breakout_pct` | `1.0` | Percentage above EMA9 to trigger profit-taking in uptrend |
| `downtrend_ema_touch_pct` | `0.2` | Distance from EMA9 to trigger exit in downtrend |
| `min_position_usdt` | `100.0` | Only trigger if position size >= this value |
| `ema_partial_ratio` | `0.3` | Fraction of position to reduce (0.3 = 30%) |
| `cooldown_seconds` | `60` | Minimum time between EMA-triggered rebalances |

## Tuning Recommendations

### Conservative (Lower Risk)

For a more conservative approach that takes profits earlier and exits faster:

```yaml
ema_rebalance:
  enabled: true
  uptrend_breakout_pct: 0.5           # Take profits at 0.5% above EMA
  downtrend_ema_touch_pct: 0.3        # Exit more aggressively in downtrend
  ema_partial_ratio: 0.5              # Reduce 50% of position
  cooldown_seconds: 45                # Allow more frequent rebalances
```

### Aggressive (Higher Risk)

For a more aggressive approach that holds positions longer:

```yaml
ema_rebalance:
  enabled: true
  uptrend_breakout_pct: 1.5           # Wait for larger breakout
  downtrend_ema_touch_pct: 0.1        # Only exit right at EMA
  ema_partial_ratio: 0.2              # Only reduce 20% of position
  cooldown_seconds: 120               # Less frequent rebalances
```

### Fast EMA Only Strategy (Your Preference)

As you mentioned preferring to focus on the fast EMA:

```yaml
ema_rebalance:
  enabled: true
  uptrend_breakout_pct: 1.0           # 1% above EMA9
  downtrend_ema_touch_pct: 0.2        # Within 0.2% of EMA9
  ema_partial_ratio: 0.3              # 30% reduction
  cooldown_seconds: 60
  
rebalancer:
  ema_fast_period: 9                  # Fast EMA (the one we're watching)
  ema_slow_period: 21                 # Slow EMA (for trend determination only)
  trend_threshold_pct: 0.1            # 0.1% separation defines trend
```

## Example Scenarios

### Scenario 1: Profit Taking in Strong Uptrend

```
Initial State:
- Position: +$1,000 in spot
- Trend: UPTREND
- EMA9: $1.0000
- Price: $0.9950 (below EMA)

Price moves up...
- Price: $1.0050 (0.5% above EMA) → No trigger
- Price: $1.0105 (1.05% above EMA) → ✅ TRIGGER!

Action:
🎯 EMA OPPORTUNISTIC REBALANCE TRIGGERED
   Reason: Long in uptrend: price 1.05% above EMA9 (defensive profit-taking)
   Suggested reduction: 30% of position
   
✅ Sell $300 worth of spot at market

Result:
- Position reduced: $1,000 → $700
- Profits locked: ~$30 (at 1.05% above entry)
- Still have exposure for further upside
```

### Scenario 2: Defensive Exit in Downtrend

```
Initial State:
- Position: +$1,000 in spot
- Trend: DOWNTREND (EMA9 crossed below EMA21)
- EMA9: $1.0000
- Price: $0.9800 (2% below EMA)

Price rallies back to EMA...
- Price: $0.9850 (-1.5% from EMA) → No trigger
- Price: $0.9985 (-0.15% from EMA) → ✅ TRIGGER!

Action:
🎯 EMA OPPORTUNISTIC REBALANCE TRIGGERED
   Reason: Long in downtrend: price near EMA9 (-0.15% - defensive exit)
   Suggested reduction: 30% of position
   
✅ Sell $300 worth of spot at market

Result:
- Position reduced: $1,000 → $700
- Reduced exposure before potential rejection at EMA
- Smaller loss or breakeven exit
```

## Integration with Normal Rebalancing

The EMA-based rebalancing works **in addition to** your normal threshold-based rebalancing:

1. **EMA rebalancing is checked FIRST** in every cycle
   - If triggered, it executes and skips normal rebalancing for that cycle
   - Has its own cooldown independent of normal rebalancing

2. **Normal threshold rebalancing still works**
   - If EMA rebalancing doesn't trigger, normal logic proceeds
   - Delta thresholds, trend multipliers, etc. all still apply

3. **Both systems share the cooldown timer**
   - After EMA rebalance, both timers reset
   - Prevents excessive trading

## Monitoring

When EMA rebalancing triggers, you'll see clear output:

```
🎯 EMA OPPORTUNISTIC REBALANCE TRIGGERED
   Reason: Long in uptrend: price 1.05% above EMA9 (defensive profit-taking)
   Suggested reduction: 30% of position

🎯 EXECUTING EMA REBALANCE
   Side: Sell
   Quantity: 15.234 ZBT ($300)
   Price: $19.6950
   Reason: Long in uptrend: price 1.05% above EMA9 (defensive profit-taking)

✅ EMA rebalance order executed successfully
```

## Benefits

1. **Defensive Profit Taking**: Automatically lock in profits when price extends too far
2. **Risk Management**: Reduce exposure when price returns to resistance in downtrends
3. **Independent of Delta**: Can trigger even when overall delta is within thresholds
4. **Configurable**: Fine-tune all parameters to match your risk tolerance
5. **Fast EMA Focus**: Uses the more responsive EMA9 for quicker signals

## Testing Tips

1. **Start Conservative**: Begin with larger `uptrend_breakout_pct` (1.5%) to see how it behaves
2. **Monitor Frequency**: Check if `cooldown_seconds` is appropriate for your trading frequency
3. **Watch Position Size**: Adjust `min_position_usdt` based on your typical position sizes
4. **Review Ratio**: Start with smaller `ema_partial_ratio` (0.2-0.3) and adjust based on results
5. **Backtest**: If possible, review historical data to see when triggers would have occurred

## Disabling

To disable EMA-based rebalancing:

```yaml
ema_rebalance:
  enabled: false
```

All other settings will be ignored when disabled, and only normal threshold-based rebalancing will operate.

---

## Technical Details

### Code Structure

The feature is implemented in:
- `bot/core/rebalance_policy.py`: Core logic for EMA rebalancing checks
- `main.py`: Integration into the main rebalancing loop
- `config.yaml`: Configuration parameters

### Flow

1. Every cycle, after updating EMAs:
   - Check if EMA rebalancing is enabled
   - Check position size vs minimum threshold
   - Check cooldown
   - Calculate price distance from fast EMA
   - Evaluate trigger conditions based on trend
   - If triggered, execute market order
   - Update cooldown timer

2. Execution is immediate (market orders) for defensive purposes

3. Independent cooldown prevents spam but allows both systems to work together

