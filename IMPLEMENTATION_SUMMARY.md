# EMA Rebalancing Implementation Summary

## What Was Implemented

I've enhanced your bot's rebalancing logic with **EMA-based opportunistic rebalancing** - a defensive strategy that rebalances based on price movement relative to your fast EMA (EMA9), independent of normal delta thresholds.

## Key Features

### 1. **Long in Uptrend → Defensive Profit Taking**
When you have a long position in an uptrend and price breaks X% above the fast EMA, the bot automatically takes partial profits.

**Why?** In uptrends, when price extends too far above the EMA, it often pulls back. This locks in profits before that happens.

### 2. **Long in Downtrend → Defensive Exit**
When you have a long position in a downtrend and price rallies back to the fast EMA, the bot reduces exposure.

**Why?** In downtrends, the EMA acts as resistance. When price rallies back to it, it's often rejected. This gives you a better exit opportunity.

## Files Modified

### 1. `bot/core/rebalance_policy.py`
**Added:**
- `EmaRebalanceConfig` dataclass for configuration
- `check_ema_rebalance_opportunity()` method for EMA-based trigger logic
- `mark_ema_rebalance()` method for cooldown tracking

**Purpose:** Core logic for EMA-based rebalancing decisions

### 2. `main.py` (SpotRebalancer class)
**Added:**
- EMA rebalancing config loading in `__init__()`
- `check_ema_rebalance_opportunity()` method to evaluate triggers
- `execute_ema_rebalance()` method to execute EMA-triggered rebalances
- Integration in `step()` method to check EMA triggers before normal rebalancing

**Purpose:** Main rebalancer integration

### 3. `config.yaml`
**Added:**
```yaml
ema_rebalance:
  enabled: true
  uptrend_breakout_pct: 1.0
  downtrend_ema_touch_pct: 0.2
  min_position_usdt: 100.0
  ema_partial_ratio: 0.3
  cooldown_seconds: 60
```

**Purpose:** Configuration parameters for the feature

### 4. `EMA_REBALANCING.md` (New)
Comprehensive documentation covering:
- How it works
- Configuration guide
- Tuning recommendations
- Example scenarios
- Integration details
- Testing tips

## How to Use

### Quick Start (Default Settings)

The feature is **enabled by default** with conservative settings:
- Takes profits at 1% above EMA9 in uptrends
- Exits near EMA9 (±0.2%) in downtrends
- Reduces 30% of position when triggered
- 60-second cooldown between EMA rebalances

Just run your rebalancer as normal - it will automatically use EMA-based rebalancing!

### Customization

Edit `config.yaml` to tune the behavior:

```yaml
rebalancer:
  ema_rebalance:
    enabled: true                      # Toggle feature on/off
    uptrend_breakout_pct: 1.0         # Adjust profit-taking trigger
    downtrend_ema_touch_pct: 0.2      # Adjust exit trigger
    ema_partial_ratio: 0.3            # Adjust position reduction %
    cooldown_seconds: 60              # Adjust rebalance frequency
```

### Disable Feature

To disable and use only traditional rebalancing:

```yaml
ema_rebalance:
  enabled: false
```

## Example Output

When EMA rebalancing triggers, you'll see:

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

## Design Philosophy

### Fast EMA Focus
As you requested, this focuses on the **fast EMA (EMA9)** only. The slow EMA is only used for trend determination, but all rebalancing triggers are based on distance from the fast EMA.

### Defensive Strategy
This is designed as a **defensive** strategy:
- Takes profits before pullbacks
- Exits on bounces in downtrends
- Uses market orders for immediate execution
- Independent cooldowns prevent over-trading

### Non-Intrusive
The feature:
- Works alongside existing rebalancing logic
- Can be disabled without affecting other features
- Has its own cooldown mechanism
- Clearly logs when it triggers

## Technical Highlights

1. **Priority Check**: EMA rebalancing is checked BEFORE normal threshold-based rebalancing
2. **Independent**: Has its own cooldown and doesn't interfere with normal rebalancing
3. **Market Orders**: Uses market orders (not limit) for defensive execution
4. **Balance Checks**: Validates available balance before placing orders
5. **Configurable**: All parameters exposed in config file

## Testing Recommendations

1. **Monitor First**: Watch the logs to see when it would trigger
2. **Start Conservative**: Use larger `uptrend_breakout_pct` (1.5-2.0%) initially
3. **Adjust Gradually**: Fine-tune based on your trading style
4. **Review Results**: Check if the 30% reduction ratio works for you
5. **Consider Market**: Adjust triggers based on volatility of your market

## Next Steps

1. ✅ Review `EMA_REBALANCING.md` for detailed documentation
2. ✅ Check current settings in `config.yaml`
3. ✅ Test with paper trading or small positions first
4. ✅ Monitor logs to see when triggers occur
5. ✅ Adjust parameters based on results

## Questions & Tuning

**Q: What if I want to be more aggressive?**
A: Increase `uptrend_breakout_pct` to 1.5-2.0% and decrease `ema_partial_ratio` to 0.2 (20%)

**Q: What if I want to exit faster in downtrends?**
A: Increase `downtrend_ema_touch_pct` to 0.3-0.5%

**Q: Can I use this with different EMA periods?**
A: Yes! Adjust `ema_fast_period` and `ema_slow_period` in the main rebalancer config

**Q: How do I know if it's working?**
A: Watch for the "🎯 EMA OPPORTUNISTIC REBALANCE TRIGGERED" messages in your logs

---

**Note**: All changes are backward compatible. If you disable the feature, your bot will work exactly as before.

