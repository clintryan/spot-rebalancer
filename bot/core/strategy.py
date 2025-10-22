# strategy_simple.py
"""
Simplified EMA Strategy - Single source of truth: exchange position
No base position tracking, no inventory management, just clean trading logic
"""
import time
from datetime import datetime
from typing import Optional, Dict, List
from dataclasses import dataclass
import json

@dataclass
class TradeLog:
    """Simple trade logging"""
    timestamp: float
    action: str  # 'BUY', 'SELL', 'STOP_LOSS', 'TAKE_PROFIT'
    price: float
    quantity: float
    position_before: float
    position_after: float
    reason: str
    pnl: Optional[float] = None

class SimplifiedEMAStrategy:
    """
    Clean EMA strategy without base position complexity
    Assumes dedicated subaccount - ALL positions are bot positions
    """
    
    def __init__(self, client, symbol: str, config: dict):
        self.client = client
        self.symbol = symbol
        self.config = config
        
        # Position tracking - SINGLE SOURCE OF TRUTH
        self.position = 0.0  # Current position from exchange
        self.avg_entry_price = 0.0  # Weighted average entry
        self.original_position_size = 0.0  # Track original position for TP calculations
        
        # Delta management (will be set by runner if provided)
        self.delta_tracker = None
        
        # Per-EMA allocation tracking
        # Track how much of each EMA's allocation is currently "locked" in positions
        self.ema9_position_value = 0.0  # USDT value of position from EMA9 entries
        self.ema21_position_value = 0.0  # USDT value of position from EMA21 entries
        
        # Limit orders
        self.limit_orders = {}  # order_id -> {'ema': '9' or '21', 'price': x, 'qty': x}
        
        # Order placement throttling - prevent placing multiple orders at same EMA too quickly
        self.last_ema9_order_time = 0
        self.last_ema21_order_time = 0
        self.order_placement_cooldown = 10  # Minimum 10 seconds between orders at same EMA
        
        # TP/SL tracking
        self.tp_levels_hit = set()  # Track which TP levels have been hit
        self.tp_orders = {}  # Track active TP orders: tp_level -> order_id
        self.last_position_size = 0.0  # Track position size changes
        
        # Indicators
        self.ema_fast = None
        self.ema_slow = None
        self.trend = "NEUTRAL"  # UPTREND, DOWNTREND, NEUTRAL
        
        # Stop tracking - Two-tier system
        self.stop_loss_price = None
        self.stop_loss_order_id = None
        self.trailing_stop_price = None          # Conditional stop (0.25%) - only on candle close
        self.hard_stop_price = None              # Hard stop (1%) - immediate trigger
        self.last_candle_close = None
        self.conditional_stop_triggered = False  # Track if conditional stop was breached during candle
        
        # Timing
        self.last_entry_time = 0
        self.last_update_time = 0
        self.last_order_update_time = 0
        
        # P&L and logging
        self.realized_pnl = 0
        self.trades = []  # List of TradeLog entries
        
        # Instrument info for proper formatting
        self.qty_step = 0.001  # Default, will be updated
        self.price_step = 0.0001  # Default, will be updated
        self.min_order_qty = 1.0  # Default, will be updated
        
        # Extract config
        self.setup_config()
        
        
    def setup_config(self):
        """Extract configuration parameters"""
        c = self.config
        
        # Core parameters
        self.category = c.get('category', 'linear')
        self.ema_fast_period = c.get('ema_fast_period', 9)
        self.ema_slow_period = c.get('ema_slow_period', 21)
        self.trend_threshold_pct = c.get('trend_threshold_pct', 0.1)
        
        # Position sizing with inventory management
        self.max_allocation_usdt = c.get('max_allocation_usdt', 1000)
        ema_allocations = c.get('ema_allocations', {'ema9_pct': 25, 'ema21_pct': 75})
        self.ema9_allocation_usdt = self.max_allocation_usdt * (ema_allocations['ema9_pct'] / 100)
        self.ema21_allocation_usdt = self.max_allocation_usdt * (ema_allocations['ema21_pct'] / 100)
        
        # Multi-level TP/SL configuration
        self.take_profit_levels = c.get('take_profit_levels', {
            'tp1': {'pct': 0.3, 'exit_pct': 30},
            'tp2': {'pct': 0.6, 'exit_pct': 40},
            'tp3': {'pct': 1.0, 'exit_pct': 50}
        })
        self.tp_execution_method = c.get('tp_execution_method', 'limit')  # 'limit' or 'market'
        # Two-tier stop loss configuration
        self.stop_loss_pct = c.get('stop_loss_pct', 0.25)           # Conditional stop (candle close)
        self.hard_stop_loss_pct = c.get('hard_stop_loss_pct', 1.0)  # Hard stop (immediate)
        
        # Entry parameters
        self.entry_cooldown = c.get('entry_cooldown_seconds', 120)
        self.order_update_threshold_pct = c.get('order_update_threshold_pct', 0.1)
        
        print(f"""
        ========== SIMPLIFIED EMA STRATEGY ==========
        Symbol: {self.symbol}
        EMA: {self.ema_fast_period} / {self.ema_slow_period}
        Trend Threshold: {self.trend_threshold_pct}%
        Max Allocation: ${self.max_allocation_usdt}
        EMA9 Allocation: ${self.ema9_allocation_usdt}
        EMA21 Allocation: ${self.ema21_allocation_usdt}
        Take Profit Levels: {len(self.take_profit_levels)}
        Stop Loss: {self.stop_loss_pct}% from slow EMA
        ============================================
        """)
    
    def set_delta_tracker(self, delta_tracker):
        """Set the delta tracker for position management"""
        self.delta_tracker = delta_tracker
        
    def initialize(self) -> bool:
        """Initialize strategy with historical data"""
        print("Initializing strategy...")
        
        # Get historical data using the configured timeframe
        timeframe = self.config.get('timeframe', '5')
        print(f"📊 Using timeframe: {timeframe} minutes from config")
        klines = self.client.get_kline(
            category=self.category,
            symbol=self.symbol,
            interval=timeframe,
            limit=200
        )
        
        if not klines or klines.get('retCode') != 0:
            print("Failed to get historical data")
            return False
            
        # Calculate initial EMAs
        closes = [float(k[4]) for k in reversed(klines['result']['list'])]
        print(f"📊 Using {len(closes)} {timeframe}-minute candles for EMA calculation")
        print(f"📊 Recent closes: {closes[-5:]}")  # Show last 5 closes
        print(f"📊 Current close: {closes[-1]:.5f}")
        self.calculate_initial_emas(closes)
        print(f"📊 Initial EMAs: EMA9={self.ema_fast:.5f}, EMA21={self.ema_slow:.5f}")
        
        # Get instrument info for proper order formatting
        self.get_instrument_info()
        
        # Sync with exchange position
        self.sync_position()
        
        # Initialize per-EMA allocations if we're starting with an existing position
        # This is critical to prevent re-allocating the same capital
        if self.position != 0 and self.avg_entry_price > 0:
            if self.ema9_position_value == 0 and self.ema21_position_value == 0:
                position_value_usdt = abs(self.position) * self.avg_entry_price
                ema9_pct = self.ema9_allocation_usdt / self.max_allocation_usdt
                ema21_pct = self.ema21_allocation_usdt / self.max_allocation_usdt
                
                self.ema9_position_value = position_value_usdt * ema9_pct
                self.ema21_position_value = position_value_usdt * ema21_pct
                
                print(f"\n🔒 IMPORTANT: Starting with existing position - locking allocations:")
                print(f"   Total position: {abs(self.position):.3f} @ ${self.avg_entry_price:.4f} = ${position_value_usdt:.0f}")
                print(f"   EMA9 locked: ${self.ema9_position_value:.0f} (of ${self.ema9_allocation_usdt:.0f})")
                print(f"   EMA21 locked: ${self.ema21_position_value:.0f} (of ${self.ema21_allocation_usdt:.0f})")
                print(f"   EMA9 available: ${self.ema9_allocation_usdt - self.ema9_position_value:.0f}")
                print(f"   EMA21 available: ${self.ema21_allocation_usdt - self.ema21_position_value:.0f}\n")
        
        # Calculate initial trend
        self.calculate_trend()
        
        print(f"✅ Initialized: EMA9=${self.ema_fast:.5f}, EMA21=${self.ema_slow:.5f}")
        print(f"📈 Initial trend: {self.trend}")
        print(f"Position: {self.position} @ ${self.avg_entry_price:.4f}")
        print(f"Instrument info: qtyStep={self.qty_step}, minOrderQty={self.min_order_qty}")
        
        return True
        
    def get_instrument_info(self):
        """Get instrument specifications for proper order formatting"""
        try:
            response = self.client.get_instruments_info(
                category=self.category,
                symbol=self.symbol
            )
            
            if response and response.get('retCode') == 0:
                instruments = response['result']['list']
                if instruments:
                    info = instruments[0]
                    
                    # Parse lot size filter for quantity step
                    lot_size = info.get('lotSizeFilter', {})
                    self.qty_step = float(lot_size.get('qtyStep', '0.001'))
                    self.min_order_qty = float(lot_size.get('minOrderQty', '1'))
                    
                    # Parse price filter for price step
                    price_filter = info.get('priceFilter', {})
                    self.price_step = float(price_filter.get('tickSize', '0.0001'))
                    
                    print(f"📏 Instrument specs: qtyStep={self.qty_step}, minOrderQty={self.min_order_qty}, priceStep={self.price_step}")
                    
        except Exception as e:
            print(f"⚠️ Could not get instrument info: {e}")
            # Use safe defaults for SOMIUSDT based on typical Bybit specs
            self.qty_step = 0.001
            self.min_order_qty = 1.0
            self.price_step = 0.0001
            
    def format_quantity(self, qty: float) -> float:
        """Format quantity according to instrument specifications"""
        # Round to the correct step size
        steps = round(qty / self.qty_step)
        formatted_qty = steps * self.qty_step
        
        # Ensure minimum order quantity
        if formatted_qty < self.min_order_qty:
            formatted_qty = self.min_order_qty
            
        # Round to avoid floating point precision issues
        decimal_places = len(str(self.qty_step).split('.')[-1])
        return round(formatted_qty, decimal_places)
        
    def format_price(self, price: float) -> float:
        """Format price according to instrument specifications"""
        steps = round(price / self.price_step)
        formatted_price = steps * self.price_step
        
        # Round to avoid floating point precision issues
        decimal_places = len(str(self.price_step).split('.')[-1])
        return round(formatted_price, decimal_places)
        
    def calculate_initial_emas(self, closes: List[float]):
        """Calculate initial EMA values"""
        if len(closes) < self.ema_slow_period:
            return
            
        # Simple EMA calculation
        self.ema_fast = sum(closes[-self.ema_fast_period:]) / self.ema_fast_period
        self.ema_slow = sum(closes[-self.ema_slow_period:]) / self.ema_slow_period
        
        # Then apply EMA formula for recent closes
        alpha_fast = 2 / (self.ema_fast_period + 1)
        alpha_slow = 2 / (self.ema_slow_period + 1)
        
        for close in closes[-50:]:
            self.ema_fast = close * alpha_fast + self.ema_fast * (1 - alpha_fast)
            self.ema_slow = close * alpha_slow + self.ema_slow * (1 - alpha_slow)
            
    def update_emas(self, price: float):
        """Update EMA values with new price"""
        if self.ema_fast is None or self.ema_slow is None:
            return
            
        alpha_fast = 2 / (self.ema_fast_period + 1)
        alpha_slow = 2 / (self.ema_slow_period + 1)
        
        self.ema_fast = price * alpha_fast + self.ema_fast * (1 - alpha_fast)
        self.ema_slow = price * alpha_slow + self.ema_slow * (1 - alpha_slow)
        
        # Update trend after EMA calculation
        self.calculate_trend()
        
    def calculate_trend(self):
        """Calculate trend based on current EMA values"""
        if self.ema_fast is None or self.ema_slow is None:
            return
            
        # Update trend using configurable threshold
        threshold_multiplier = self.trend_threshold_pct / 100
        upper_threshold = 1 + threshold_multiplier
        lower_threshold = 1 - threshold_multiplier
        
        # Debug trend calculation
        ema_ratio = self.ema_fast / self.ema_slow
        print(f"🔍 Trend Debug: EMA9={self.ema_fast:.5f}, EMA21={self.ema_slow:.5f}")
        print(f"🔍 Ratio: {ema_ratio:.6f}, Upper: {upper_threshold:.6f}, Lower: {lower_threshold:.6f}")
        
        if self.ema_fast > self.ema_slow * upper_threshold:
            self.trend = "UPTREND"
        elif self.ema_fast < self.ema_slow * lower_threshold:
            self.trend = "DOWNTREND"
        else:
            self.trend = "NEUTRAL"
            
        print(f"🔍 Result: {self.trend}")
    
    def _has_sufficient_trend_strength(self) -> bool:
        """
        Check if EMAs have sufficient separation to justify placing orders.
        This prevents trading when EMAs are too close together (weak trend).
        """
        if self.ema_fast is None or self.ema_slow is None:
            return False
            
        # Calculate EMA separation as percentage
        ema_ratio = self.ema_fast / self.ema_slow
        separation_pct = abs(1 - ema_ratio) * 100
        
        # Require minimum separation defined by trend_threshold_pct
        min_separation = self.trend_threshold_pct
        
        if separation_pct < min_separation:
            if not hasattr(self, '_trend_strength_debug_count'):
                self._trend_strength_debug_count = 0
            self._trend_strength_debug_count += 1
            
            # Only log occasionally to avoid spam
            if self._trend_strength_debug_count <= 3 or self._trend_strength_debug_count % 20 == 0:
                print(f"🚫 Insufficient trend strength: {separation_pct:.3f}% < {min_separation:.3f}% required")
                print(f"   EMA9: ${self.ema_fast:.5f}, EMA21: ${self.ema_slow:.5f}, Ratio: {ema_ratio:.6f}")
            
            return False
        
        return True
            
    def sync_position(self):
        """Sync position with exchange - SINGLE SOURCE OF TRUTH"""
        try:
            response = self.client.get_positions(
                category=self.category,
                symbol=self.symbol
            )
            
            if response and response.get('retCode') == 0:
                positions = response['result']['list']
                if positions:
                    pos = positions[0]
                    size = float(pos.get('size', 0))
                    side = pos.get('side', 'None')
                    
                    # Update position
                    old_position = self.position
                    self.position = size if side == 'Buy' else -size if side == 'Sell' else 0
                    
                    # Handle empty avgPrice strings
                    avg_price_str = pos.get('avgPrice', '0')
                    if avg_price_str == '' or avg_price_str is None:
                        self.avg_entry_price = 0.0
                    else:
                        try:
                            self.avg_entry_price = float(avg_price_str)
                        except (ValueError, TypeError):
                            self.avg_entry_price = 0.0
                    
                    if old_position != self.position:
                        print(f"📊 Position synced: {old_position:.3f} → {self.position:.3f}")
                        
                        # If we went from zero to a position, track original size and reset TP levels
                        if old_position == 0 and self.position != 0:
                            self.original_position_size = abs(self.position)
                            self.last_position_size = abs(self.position)
                            self.tp_levels_hit = set()
                            print(f"🎯 New position opened: {self.original_position_size:.3f} - TP levels reset")
                            
                            # Place TP orders for the new position based on execution method
                            if self.tp_execution_method == 'limit':
                                self.place_tp_limit_orders(self.avg_entry_price)
                            
                            # Initialize per-EMA position tracking if not already set
                            # This handles the case when bot starts with an existing position
                            if self.ema9_position_value == 0 and self.ema21_position_value == 0 and self.avg_entry_price > 0:
                                # Assume position came proportionally from both EMAs based on config
                                position_value_usdt = abs(self.position) * self.avg_entry_price
                                ema9_pct = self.ema9_allocation_usdt / self.max_allocation_usdt
                                ema21_pct = self.ema21_allocation_usdt / self.max_allocation_usdt
                                
                                self.ema9_position_value = position_value_usdt * ema9_pct
                                self.ema21_position_value = position_value_usdt * ema21_pct
                                
                                print(f"🔄 Initialized per-EMA tracking from existing position:")
                                print(f"   Total position: ${position_value_usdt:.0f}")
                                print(f"   EMA9: ${self.ema9_position_value:.0f} ({ema9_pct*100:.0f}%)")
                                print(f"   EMA21: ${self.ema21_position_value:.0f} ({ema21_pct*100:.0f}%)")
                                
                                print(f"🔒 Initialized EMA allocations from existing position:")
                                print(f"   EMA9 locked: ${self.ema9_position_value:.0f}")
                                print(f"   EMA21 locked: ${self.ema21_position_value:.0f}")
                        
                        # Reset trailing stop if position is now zero
                        elif self.position == 0:
                            self.trailing_stop_price = None
                            self.original_position_size = 0.0
                            self.tp_levels_hit = set()
                            # Reset per-EMA allocations when position closes
                            self.ema9_position_value = 0.0
                            self.ema21_position_value = 0.0
                            print(f"🔄 Position closed - EMA allocations reset")
                        
                        # CRITICAL FIX: If position changed but didn't close, recalculate EMA allocations
                        # This handles partial exits (TPs) and ensures allocations match actual position
                        elif self.position != 0 and self.avg_entry_price > 0:
                            actual_position_value = abs(self.position) * self.avg_entry_price
                            total_tracked = self.ema9_position_value + self.ema21_position_value
                            
                            # Check if tracking is out of sync (more than 5% difference)
                            if total_tracked > 0 and abs(actual_position_value - total_tracked) / total_tracked > 0.05:
                                print(f"🔧 EMA allocation sync needed: tracked ${total_tracked:.0f} vs actual ${actual_position_value:.0f}")
                                
                                # Recalculate proportionally based on current tracking ratio
                                if total_tracked > 0:
                                    ema9_ratio = self.ema9_position_value / total_tracked
                                    ema21_ratio = self.ema21_position_value / total_tracked
                                else:
                                    # Fallback to config ratios
                                    ema9_ratio = self.ema9_allocation_usdt / self.max_allocation_usdt
                                    ema21_ratio = self.ema21_allocation_usdt / self.max_allocation_usdt
                                
                                self.ema9_position_value = actual_position_value * ema9_ratio
                                self.ema21_position_value = actual_position_value * ema21_ratio
                                
                                print(f"   ✅ Recalculated: EMA9=${self.ema9_position_value:.0f}, EMA21=${self.ema21_position_value:.0f}")
                else:
                    self.position = 0
                    self.avg_entry_price = 0
                    self.trailing_stop_price = None
                    self.original_position_size = 0.0
                    self.tp_levels_hit = set()
                    
        except Exception as e:
            print(f"Error syncing position: {e}")
            
    def sync_orders(self):
        """Sync internal order tracking with exchange reality"""
        try:
            response = self.client.get_open_orders(
                category=self.category,
                symbol=self.symbol
            )
            
            if response and response.get('retCode') == 0:
                exchange_orders = response['result']['list']
                exchange_order_ids = {order['orderId'] for order in exchange_orders}
                
                # Find orders we think exist but don't exist on exchange
                stale_orders = []
                for order_id in list(self.limit_orders.keys()):
                    if order_id not in exchange_order_ids:
                        stale_orders.append(order_id)
                        
                # Clean up stale orders AND update per-EMA position tracking
                # These are likely filled orders, so we need to lock their allocation
                for order_id in stale_orders:
                    order_info = self.limit_orders.get(order_id)
                    if order_info:
                        # Assume order was filled and lock the allocation
                        usdt_amount = order_info.get('usdt_amount', 0)
                        if order_info['ema'] == '9':
                            self.ema9_position_value += usdt_amount
                            print(f"🧹 Order filled: EMA9 +${usdt_amount:.0f} → ${self.ema9_position_value:.0f} locked")
                        elif order_info['ema'] == '21':
                            self.ema21_position_value += usdt_amount
                            print(f"🧹 Order filled: EMA21 +${usdt_amount:.0f} → ${self.ema21_position_value:.0f} locked")
                    
                    del self.limit_orders[order_id]
                    
                # Also check for orders on exchange we don't know about
                unknown_orders = exchange_order_ids - set(self.limit_orders.keys())
                if unknown_orders:
                    print(f"⚠️ Found {len(unknown_orders)} unknown orders on exchange")
                    
        except Exception as e:
            print(f"Error syncing orders: {e}")
            
    def update(self, price: float, is_new_candle: bool = False, candle_close_price: float = None):
        """Main update method"""
        current_time = time.time()
        
        # Update EMAs on new candle only (like most charts)
        if is_new_candle:
            self.last_candle_close = price
            self.update_emas(price)
            print(f"\n🕐 {datetime.now().strftime('%H:%M:%S')} | ${price:.5f}")
            print(f"   EMA9: ${self.ema_fast:.5f} | EMA21: ${self.ema_slow:.5f} | {self.trend}")
            
        # Sync position and orders every 30 seconds
        if current_time - self.last_update_time > 30:
            self.sync_position()
            self.sync_orders()
            self.last_update_time = current_time
            
        # Check exit conditions and manage stops
        if self.position != 0:
            self.check_exits(price)
            # Use candle close price for stop loss checks if available, otherwise use current price
            stop_check_price = candle_close_price if (is_new_candle and candle_close_price is not None) else price
            self.manage_stops(stop_check_price, is_new_candle)
            
        # Always manage entry orders (based on available inventory, not position)
        self.manage_entry_orders(price, current_time - self.last_entry_time > self.entry_cooldown)
        
        # Manage TP orders dynamically when position size changes
        self.manage_tp_orders(price)
        
        # Place TP orders if we have a position but no TP orders
        if self.position != 0 and not self.tp_orders:
            if self.tp_execution_method == 'limit':
                self.place_tp_limit_orders(price)
            # For 'market' method, TP levels are checked in check_exits()
        
    def update_emas_only(self, price: float, candle_close_price: float = None):
        """Update EMAs only (for pause mode) - no trading decisions"""
        if candle_close_price is not None:
            self.last_candle_close = candle_close_price
            self.update_emas(candle_close_price)
            print(f"\n🕐 {datetime.now().strftime('%H:%M:%S')} | ${candle_close_price:.5f} (PAUSED)")
            print(f"   EMA9: ${self.ema_fast:.5f} | EMA21: ${self.ema_slow:.5f} | {self.trend}")
            
    def check_exits(self, price: float):
        """Check exit conditions for current position with multi-level TP/SL"""
        if self.position == 0 or self.avg_entry_price == 0:
            return
            
        # Calculate current P&L
        if self.position > 0:  # Long
            pnl_pct = (price - self.avg_entry_price) / self.avg_entry_price * 100
        else:  # Short
            pnl_pct = (self.avg_entry_price - price) / self.avg_entry_price * 100
            
        # Check Take Profit levels based on execution method
        if self.tp_execution_method == 'market':
            # Market execution - check levels and execute immediately
            for tp_name in ['tp1', 'tp2', 'tp3']:
                if tp_name in self.tp_levels_hit:
                    continue  # Already hit this level
                    
                tp_config = self.take_profit_levels.get(tp_name)
                if not tp_config:
                    continue
                    
                if pnl_pct >= tp_config['pct']:
                    self.execute_tp_level(price, tp_name, tp_config)
                    self.tp_levels_hit.add(tp_name)
                    break  # Only hit one TP level per update
        # For 'limit' method, TP levels are managed via limit orders on the exchange
            
        # Trend Strength Exit - exit when trend strength falls below threshold (NEUTRAL or opposing trend)
        if self.position > 0 and self.trend in ["NEUTRAL", "DOWNTREND"]:
            ema_ratio = self.ema_fast / self.ema_slow
            print(f"🔴 TREND WEAKENING: Long position but trend is {self.trend} (EMA ratio: {ema_ratio:.6f}) - Exiting")
            self.execute_full_exit(price, "TREND_WEAKENING")
            return
        elif self.position < 0 and self.trend in ["NEUTRAL", "UPTREND"]:
            ema_ratio = self.ema_fast / self.ema_slow
            print(f"🟢 TREND WEAKENING: Short position but trend is {self.trend} (EMA ratio: {ema_ratio:.6f}) - Exiting")
            self.execute_full_exit(price, "TREND_WEAKENING")
            return
            
    def manage_stops(self, price: float, is_new_candle: bool = False):
        """
        Manage two-tier stop loss system:
        1. Conditional stop (0.25%) - only triggers on candle close
        2. Hard stop (1%) - triggers immediately
        """
        if self.position == 0 or self.avg_entry_price == 0:
            return
            
        if self.position > 0:  # Long position
            # Calculate stop levels based on slow EMA
            conditional_stop = self.ema_slow * (1 - self.stop_loss_pct / 100)
            hard_stop = self.ema_slow * (1 - self.hard_stop_loss_pct / 100)
            
            # Initialize or update stops (only move up, never down)
            if self.trailing_stop_price is None:
                self.trailing_stop_price = conditional_stop
                self.hard_stop_price = hard_stop
            else:
                if conditional_stop > self.trailing_stop_price:
                    self.trailing_stop_price = conditional_stop
                if hard_stop > self.hard_stop_price:
                    self.hard_stop_price = hard_stop
            
            # Check hard stop first (1% - immediate trigger)
            if price <= self.hard_stop_price:
                print(f"🚨 HARD STOP triggered: ${price:.4f} <= ${self.hard_stop_price:.4f} (EMA{self.ema_slow_period}: ${self.ema_slow:.4f}, -{self.hard_stop_loss_pct}%)")
                self.execute_full_exit(price, "HARD_STOP")
                return
                
            # Track if conditional stop level was breached during candle
            if price <= self.trailing_stop_price:
                self.conditional_stop_triggered = True
                
            # Check conditional stop on candle close (0.25% - only if candle CLOSES below stop)
            if is_new_candle and price <= self.trailing_stop_price:
                print(f"🛑 CONDITIONAL STOP triggered on candle close: ${price:.4f} <= ${self.trailing_stop_price:.4f} (EMA{self.ema_slow_period}: ${self.ema_slow:.4f}, -{self.stop_loss_pct}%)")
                self.execute_full_exit(price, "CONDITIONAL_STOP")
                return
                
            # Reset conditional stop flag on new candle regardless of where it closes
            if is_new_candle:
                self.conditional_stop_triggered = False
                
        else:  # Short position
            # Calculate stop levels based on slow EMA
            conditional_stop = self.ema_slow * (1 + self.stop_loss_pct / 100)
            hard_stop = self.ema_slow * (1 + self.hard_stop_loss_pct / 100)
            
            # Initialize or update stops (only move down, never up)
            if self.trailing_stop_price is None:
                self.trailing_stop_price = conditional_stop
                self.hard_stop_price = hard_stop
            else:
                if conditional_stop < self.trailing_stop_price:
                    self.trailing_stop_price = conditional_stop
                if hard_stop < self.hard_stop_price:
                    self.hard_stop_price = hard_stop
            
            # Check hard stop first (1% - immediate trigger)
            if price >= self.hard_stop_price:
                print(f"🚨 HARD STOP triggered: ${price:.4f} >= ${self.hard_stop_price:.4f} (EMA{self.ema_slow_period}: ${self.ema_slow:.4f}, +{self.hard_stop_loss_pct}%)")
                self.execute_full_exit(price, "HARD_STOP")
                return
                
            # Track if conditional stop level was breached during candle
            if price >= self.trailing_stop_price:
                self.conditional_stop_triggered = True
                
            # Check conditional stop on candle close (0.25% - only if candle CLOSES above stop)
            if is_new_candle and price >= self.trailing_stop_price:
                print(f"🛑 CONDITIONAL STOP triggered on candle close: ${price:.4f} >= ${self.trailing_stop_price:.4f} (EMA{self.ema_slow_period}: ${self.ema_slow:.4f}, +{self.stop_loss_pct}%)")
                self.execute_full_exit(price, "CONDITIONAL_STOP")
                return
                
            # Reset conditional stop flag on new candle regardless of where it closes
            if is_new_candle:
                self.conditional_stop_triggered = False
            
    def execute_tp_level(self, price: float, tp_name: str, tp_config: dict):
        """Execute a specific take profit level"""
        # Check if we have a position to reduce
        if abs(self.position) < 0.001 or self.original_position_size == 0:
            return
            
        # Calculate exit quantity based on ORIGINAL position size, not current
        exit_qty = self.original_position_size * (tp_config['exit_pct'] / 100)
        
        # But don't exit more than current position
        max_exit = abs(self.position)
        if exit_qty > max_exit:
            exit_qty = max_exit
            
        # Format quantity properly
        exit_qty = self.format_quantity(exit_qty)
        
        # Check if formatted quantity is meaningful
        if exit_qty < self.min_order_qty:
            return
            
        side = "Sell" if self.position > 0 else "Buy"
        
        # Double-check position before placing order
        if abs(self.position) < 0.001:
            print(f"⚠️ Position became zero before {tp_name.upper()}, skipping")
            return
        
        self.log_trade(
            action=f"{tp_name.upper()}_{side.upper()}",
            price=price,
            quantity=exit_qty,
            reason=f"{tp_name.upper()}_PROFIT"
        )
        
        # Execute market order
        response = self.client.place_market_order(
            category=self.category,
            symbol=self.symbol,
            side=side,
            qty=exit_qty,
            reduce_only=True,
            verbose=False
        )
        
        if response and response.get('retCode') == 0:
            # Calculate and record P&L
            pnl = self.calculate_pnl(self.avg_entry_price, price, exit_qty)
            self.realized_pnl += pnl
            
            # Calculate USDT value being freed up
            exit_value_usdt = exit_qty * self.avg_entry_price
            
            # Free up allocation proportionally from each EMA based on their contribution
            ema9_freed = 0.0
            ema21_freed = 0.0
            total_ema_value = self.ema9_position_value + self.ema21_position_value
            if total_ema_value > 0:
                ema9_pct = self.ema9_position_value / total_ema_value
                ema21_pct = self.ema21_position_value / total_ema_value
                
                ema9_freed = exit_value_usdt * ema9_pct
                ema21_freed = exit_value_usdt * ema21_pct
                
                self.ema9_position_value -= ema9_freed
                self.ema21_position_value -= ema21_freed
                
                # Ensure non-negative
                self.ema9_position_value = max(0, self.ema9_position_value)
                self.ema21_position_value = max(0, self.ema21_position_value)
            
            print(f"""
            🎯 {tp_name.upper()} HIT - {tp_config['exit_pct']}% EXIT
            Price: ${price:.4f} (Target: {tp_config['pct']}%)
            Quantity: {exit_qty:.3f}
            P&L: ${pnl:.2f}
            Total P&L: ${self.realized_pnl:.2f}
            EMA9 freed: ${ema9_freed:.0f}, EMA21 freed: ${ema21_freed:.0f}
            """)
            
            # Sync position from exchange immediately after TP exit
            # This is critical to ensure position tracking stays accurate
            self.sync_position()
            
            
    def execute_full_exit(self, price: float, reason: str):
        """Execute full exit"""
        if self.position == 0:
            return
            
        exit_qty = abs(self.position)
        
        # Format quantity properly
        exit_qty = self.format_quantity(exit_qty)
        
        # Check if formatted quantity is meaningful
        if exit_qty < self.min_order_qty:
            return
            
        side = "Sell" if self.position > 0 else "Buy"
        
        self.log_trade(
            action=f"EXIT_{side.upper()}",
            price=price,
            quantity=exit_qty,
            reason=reason
        )
        
        # Cancel all limit orders, TP orders, and stop orders first
        self.cancel_all_orders()
        self.cancel_tp_orders()
        self.cancel_stop_order()
        
        # Execute market order
        response = self.client.place_market_order(
            category=self.category,
            symbol=self.symbol,
            side=side,
            qty=exit_qty,
            reduce_only=True,
            verbose=False
        )
        
        if response and response.get('retCode') == 0:
            # Calculate and record P&L
            pnl = self.calculate_pnl(self.avg_entry_price, price, exit_qty)
            self.realized_pnl += pnl
            
            print(f"""
            ⛔ FULL EXIT ({reason})
            Price: ${price:.4f}
            Quantity: {exit_qty:.3f}
            P&L: ${pnl:.2f}
            Total P&L: ${self.realized_pnl:.2f}
            """)
            
            # Sync position from exchange immediately after full exit
            # This ensures position tracking stays accurate
            self.sync_position()
            
            # Reset stops and tracking
            self.trailing_stop_price = None
            self.hard_stop_price = None
            self.conditional_stop_triggered = False
            self.stop_loss_order_id = None
            self.original_position_size = 0.0
            self.tp_levels_hit = set()
            self.last_entry_time = time.time()  # Cooldown before new entry
            
    def manage_entry_orders(self, price: float, can_place_new: bool = True):
        """Place and update limit orders at EMAs with per-EMA allocation tracking"""
        current_time = time.time()
        
        # Always update existing orders every 5 seconds
        if current_time - self.last_order_update_time > 5:
            self.update_limit_orders()
            self.last_order_update_time = current_time
        
        # Check minimum trend strength before placing any orders
        if not self._has_sufficient_trend_strength():
            ema_ratio = self.ema_fast / self.ema_slow
            separation_pct = abs(1 - ema_ratio) * 100
            print(f"🚫 Insufficient trend strength: {separation_pct:.3f}% < {self.trend_threshold_pct:.3f}% required")
            return
            
        # Don't place new orders when trend is neutral (unless forced)
        if self.trend == "NEUTRAL" and not can_place_new:
            print(f"🚫 Trend is NEUTRAL and can_place_new={can_place_new}")
            return
        
        # For existing positions, only place orders in the same direction
        if self.position != 0:
            # Don't add to position if we're in opposing trend
            if (self.position > 0 and self.trend == "DOWNTREND") or \
               (self.position < 0 and self.trend == "UPTREND"):
                print(f"🚫 Position {self.position:.3f} opposes trend {self.trend}")
                return
        
        # Calculate available allocation per EMA level
        # Each EMA has its own budget, and we track what's locked in positions
        # Also account for spot positions in total exposure
        spot_position_usdt = 0.0
        if self.delta_tracker:
            delta_status = self.delta_tracker.get_status(price)
            spot_position_usdt = abs(delta_status.get('spot_position_usdt', 0.0))
        
        # Calculate total exposure and adjust available capital accordingly
        total_futures_locked = self.ema9_position_value + self.ema21_position_value
        total_exposure = total_futures_locked + spot_position_usdt
        
        # If total exposure exceeds max allocation, reduce available capital proportionally
        exposure_factor = 1.0
        if total_exposure > 0:
            remaining_capacity = max(0, self.max_allocation_usdt - spot_position_usdt)
            exposure_factor = remaining_capacity / self.max_allocation_usdt if self.max_allocation_usdt > 0 else 0
        
        ema9_available = (self.ema9_allocation_usdt - self.ema9_position_value) * exposure_factor
        ema21_available = (self.ema21_allocation_usdt - self.ema21_position_value) * exposure_factor
        
        # Also check for pending orders and subtract their allocation
        for order_info in self.limit_orders.values():
            if order_info['ema'] == '9':
                ema9_available -= order_info.get('usdt_amount', 0)
            elif order_info['ema'] == '21':
                ema21_available -= order_info.get('usdt_amount', 0)
        
        # Ensure non-negative and cap at very small minimum to avoid tiny orders
        ema9_available = max(0, ema9_available)
        ema21_available = max(0, ema21_available)
        
        # Additional safety: If position value exceeds allocation, set available to 0
        if self.ema9_position_value >= self.ema9_allocation_usdt:
            ema9_available = 0
        if self.ema21_position_value >= self.ema21_allocation_usdt:
            ema21_available = 0
        
        # Only show debug info occasionally to reduce noise
        current_time = time.time()
        if not hasattr(self, '_last_debug_time') or current_time - self._last_debug_time > 30:
            print(f"💰 EMA Allocations:")
            print(f"   EMA9: ${self.ema9_position_value:.0f} locked, ${ema9_available:.0f} available (of ${self.ema9_allocation_usdt:.0f})")
            print(f"   EMA21: ${self.ema21_position_value:.0f} locked, ${ema21_available:.0f} available (of ${self.ema21_allocation_usdt:.0f})")
            if spot_position_usdt > 0:
                print(f"   Spot: ${spot_position_usdt:.0f} | Total Exposure: ${total_exposure:.0f} / ${self.max_allocation_usdt:.0f}")
            print(f"🔍 Current orders: {len(self.limit_orders)}")
            for order_id, info in self.limit_orders.items():
                print(f"   Order {order_id[:8]}...: EMA{info['ema']} {info['side']} @ ${info['price']:.4f} (${info.get('usdt_amount', 0):.0f})")
            self._last_debug_time = current_time
        
        # Safety check: Don't place ANY orders if we're at or over the total allocation limit
        # Include spot positions in total exposure calculation
        if total_exposure >= self.max_allocation_usdt:
            if not hasattr(self, '_max_alloc_warning_count') or time.time() - getattr(self, '_max_alloc_warning_time', 0) > 60:
                print(f"🚫 Maximum allocation reached: ${total_exposure:.0f} / ${self.max_allocation_usdt:.0f} - no new orders")
                print(f"   Futures: ${total_futures_locked:.0f} | Spot: ${spot_position_usdt:.0f}")
                self._max_alloc_warning_count = 0
                self._max_alloc_warning_time = time.time()
            return
        
        # Only place new orders if allowed (cooldown respected)
        if can_place_new:
            # Check delta constraints before placing orders
            if self.delta_tracker:
                delta_status = self.delta_tracker.get_status(price)
                if not self._should_place_orders_given_delta(delta_status, price):
                    return
            
            # Check which EMA levels need orders
            existing_emas = {info['ema'] for info in self.limit_orders.values()}
            needs_ema9 = '9' not in existing_emas
            needs_ema21 = '21' not in existing_emas
            
            # Only show order needs info occasionally
            if not hasattr(self, '_last_order_debug_time') or current_time - self._last_order_debug_time > 30:
                print(f"🔍 Order needs: EMA9={needs_ema9} (${ema9_available:.0f} avail), EMA21={needs_ema21} (${ema21_available:.0f} avail)")
                self._last_order_debug_time = current_time
            
            # Minimum order size to prevent dust orders
            min_order_value = 50  # Minimum $50 per order
            
        # Determine order parameters - more aggressive approach
        if self.trend == "UPTREND":
            # Place buy orders in uptrend regardless of price position relative to EMAs
            if not hasattr(self, '_last_trend_msg_time') or current_time - self._last_trend_msg_time > 60:
                print(f"🟢 UPTREND: placing buy orders (price: ${price:.4f}, EMA9: ${self.ema_fast:.4f}, EMA21: ${self.ema_slow:.4f})")
                self._last_trend_msg_time = current_time
            if needs_ema9 and ema9_available >= min_order_value:
                self.place_limit_order("Buy", self.ema_fast, "9", ema9_available)
            if needs_ema21 and ema21_available >= min_order_value:
                self.place_limit_order("Buy", self.ema_slow, "21", ema21_available)
        elif self.trend == "DOWNTREND":
            # Place sell orders in downtrend regardless of price position relative to EMAs
            if not hasattr(self, '_last_trend_msg_time') or current_time - self._last_trend_msg_time > 60:
                print(f"🔴 DOWNTREND: placing sell orders (price: ${price:.4f}, EMA9: ${self.ema_fast:.4f}, EMA21: ${self.ema_slow:.4f})")
                self._last_trend_msg_time = current_time
            if needs_ema9 and ema9_available >= min_order_value:
                self.place_limit_order("Sell", self.ema_fast, "9", ema9_available)
            if needs_ema21 and ema21_available >= min_order_value:
                self.place_limit_order("Sell", self.ema_slow, "21", ema21_available)
        # No logging for neutral conditions to reduce noise
    
    def manage_tp_orders(self, price: float):
        """Manage take profit orders dynamically based on current position size"""
        if self.position == 0 or self.avg_entry_price == 0:
            return
            
        # Check if position size changed significantly (more than 5%)
        position_size_change = abs(self.position - self.last_position_size)
        if position_size_change / max(abs(self.position), 0.001) > 0.05:
            print(f"🔄 Position size changed: {self.last_position_size:.3f} → {self.position:.3f} - Managing TP orders")
            self.update_tp_orders(price)
            self.last_position_size = self.position
    
    def place_tp_limit_orders(self, price: float):
        """Place take profit limit orders for all levels"""
        if self.position == 0 or self.avg_entry_price == 0:
            return
            
        print(f"\n🎯 PLACING TAKE PROFIT LIMIT ORDERS")
        print(f"   Position: {self.position:.3f} @ ${self.avg_entry_price:.4f}")
        
        # Place TP orders for each level
        for tp_name, tp_config in self.take_profit_levels.items():
            if tp_name in self.tp_levels_hit:
                continue  # Skip levels already hit
                
            # Calculate TP price
            if self.position > 0:  # Long position
                tp_price = self.avg_entry_price * (1 + tp_config['pct'] / 100)
            else:  # Short position
                tp_price = self.avg_entry_price * (1 - tp_config['pct'] / 100)
            
            # Calculate quantity to exit (based on ORIGINAL position size)
            exit_qty = self.original_position_size * (tp_config['exit_pct'] / 100)
            exit_qty = self.format_quantity(exit_qty)
            
            if exit_qty < self.min_order_qty:
                print(f"   ⚠️ {tp_name.upper()}: Quantity {exit_qty:.3f} below minimum, skipping")
                continue
                
            # Format price
            tp_price = self.format_price(tp_price)
            
            # Determine side
            side = "Sell" if self.position > 0 else "Buy"
            
            # Place TP limit order
            response = self.client.place_order(
                category=self.category,
                symbol=self.symbol,
                side=side,
                orderType="Limit",
                qty=exit_qty,
                price=tp_price,
                timeInForce="GTC",
                reduce_only=True,
                verbose=False
            )
            
            if response and response.get('retCode') == 0:
                order_id = response['result']['orderId']
                self.tp_orders[tp_name] = order_id
                print(f"   ✅ {tp_name.upper()}: {side} {exit_qty:.3f} @ ${tp_price:.4f} ({tp_config['exit_pct']}% of {self.original_position_size:.3f})")
            else:
                print(f"   ❌ {tp_name.upper()}: Failed to place order - {response.get('retMsg', 'Unknown error')}")
    
    def update_tp_orders(self, price: float):
        """Update take profit orders based on current position"""
        if self.position == 0 or self.avg_entry_price == 0:
            return
            
        # Cancel existing TP orders
        self.cancel_tp_orders()
        
        # Place new TP orders for each level
        for tp_name, tp_config in self.take_profit_levels.items():
            if tp_name in self.tp_levels_hit:
                continue  # Skip levels already hit
                
            # Calculate TP price
            if self.position > 0:  # Long position
                tp_price = self.avg_entry_price * (1 + tp_config['pct'] / 100)
            else:  # Short position
                tp_price = self.avg_entry_price * (1 - tp_config['pct'] / 100)
            
            # Calculate quantity to exit (based on CURRENT position, not original)
            exit_qty = abs(self.position) * (tp_config['exit_pct'] / 100)
            exit_qty = self.format_quantity(exit_qty)
            
            if exit_qty < self.min_order_qty:
                continue
                
            # Format price
            tp_price = self.format_price(tp_price)
            
            # Determine side
            side = "Sell" if self.position > 0 else "Buy"
            
            # Place TP order
            response = self.client.place_order(
                category=self.category,
                symbol=self.symbol,
                side=side,
                orderType="Limit",
                qty=exit_qty,
                price=tp_price,
                timeInForce="GTC",
                reduce_only=True,
                verbose=False
            )
            
            if response and response.get('retCode') == 0:
                order_id = response['result']['orderId']
                self.tp_orders[tp_name] = order_id
                print(f"🎯 TP{tp_name.upper()} order placed: {side} {exit_qty:.3f} @ ${tp_price:.4f} ({tp_config['exit_pct']}% of {abs(self.position):.3f})")
    
    def cancel_tp_orders(self):
        """Cancel all active TP orders"""
        for tp_name, order_id in list(self.tp_orders.items()):
            try:
                response = self.client.cancel_order(
                    category=self.category,
                    symbol=self.symbol,
                    orderId=order_id
                )
                if response and response.get('retCode') == 0:
                    print(f"🗑️ Cancelled TP{tp_name.upper()} order")
                del self.tp_orders[tp_name]
            except Exception as e:
                print(f"⚠️ Error cancelling TP{tp_name.upper()} order: {e}")
                del self.tp_orders[tp_name]
    
    def _should_place_orders_given_delta(self, delta_status: dict, price: float) -> bool:
        """
        Check if we should place orders given current delta status.
        Returns True if orders should be placed, False otherwise.
        """
        if not delta_status:
            return True  # No delta constraints if no tracker
        
        # If we need rebalancing, be more restrictive about new entries
        if delta_status['needs_rebalance']:
            print("🚫 Skipping new orders: Delta rebalancing needed")
            return False
        
        # Check if new entries would push us further from desired delta
        # Calculate available capital for delta check, accounting for spot positions
        current_position_value = abs(self.position) * self.avg_entry_price if self.avg_entry_price > 0 else 0
        spot_position_usdt = abs(delta_status.get('spot_position_usdt', 0.0))
        total_exposure = current_position_value + spot_position_usdt
        total_entry_usdt = self.max_allocation_usdt - total_exposure
        current_delta = delta_status['total_delta']
        desired_delta = delta_status['desired_delta']
        
        # Simulate the impact of new orders
        if self.trend == "UPTREND":
            # Buy orders would increase long exposure (positive delta)
            projected_delta = current_delta + total_entry_usdt
        elif self.trend == "DOWNTREND":
            # Sell orders would increase short exposure (negative delta)
            projected_delta = current_delta - total_entry_usdt
        else:
            return True  # Neutral trend
        
        # Calculate how much further from desired delta this would take us
        current_divergence = abs(current_delta - desired_delta)
        projected_divergence = abs(projected_delta - desired_delta)
        
        # Debug output (only occasionally to avoid spam)
        if not hasattr(self, '_delta_debug_count'):
            self._delta_debug_count = 0
        self._delta_debug_count += 1
        
        # Delta check debug removed to reduce noise
        
        # Allow new trades if they don't exceed the divergence threshold
        threshold = self.delta_tracker.divergence_threshold_usdt
        
        # Only block trades if projected divergence exceeds the threshold AND we're already close to it
        if projected_divergence > threshold and current_divergence > (threshold * 0.7):
            trend_direction = "LONG" if self.trend == "UPTREND" else "SHORT"
            print(f"🚫 Skipping {trend_direction} orders: Would exceed delta threshold (${projected_divergence:+,.0f} > ${threshold:,.0f})")
            return False
        
        return True
            
    def place_limit_order(self, side: str, ema_price: float, ema_type: str, allocation_usdt: float):
        """Place a single limit order based on available allocation"""
        current_time = time.time()
        
        # Check if we already have an order at this EMA
        for order_info in self.limit_orders.values():
            if order_info['ema'] == ema_type:
                # Only log this occasionally to reduce noise
                if not hasattr(self, '_skip_count'):
                    self._skip_count = {}
                if ema_type not in self._skip_count:
                    self._skip_count[ema_type] = 0
                self._skip_count[ema_type] += 1
                if self._skip_count[ema_type] <= 2 or self._skip_count[ema_type] % 10 == 0:
                    print(f"📍 EMA{ema_type} order already exists, skipping")
                return
        
        # Check if we recently placed an order at this EMA (throttling)
        if ema_type == '9':
            if current_time - self.last_ema9_order_time < self.order_placement_cooldown:
                time_remaining = self.order_placement_cooldown - (current_time - self.last_ema9_order_time)
                print(f"⏳ EMA9 order cooldown: {time_remaining:.0f}s remaining")
                return
        elif ema_type == '21':
            if current_time - self.last_ema21_order_time < self.order_placement_cooldown:
                time_remaining = self.order_placement_cooldown - (current_time - self.last_ema21_order_time)
                print(f"⏳ EMA21 order cooldown: {time_remaining:.0f}s remaining")
                return
        
        # Apply entry offset to improve fill probability
        entry_offset_pct = self.config.get('entry_offset_pct', 0.0)
        if side == "Buy":
            # For buy orders: bid below EMA for better entry prices
            adjusted_price = ema_price * (1 - entry_offset_pct / 100)
        else:  # Sell
            # For sell orders: offer above EMA for better entry prices
            adjusted_price = ema_price * (1 + entry_offset_pct / 100)
        
        # Debug allocation check removed to reduce noise
        
        # Skip if no allocation available
        if allocation_usdt <= 0:
            # Only show this message occasionally to reduce noise
            if not hasattr(self, '_no_allocation_count'):
                self._no_allocation_count = 0
            self._no_allocation_count += 1
            if self._no_allocation_count <= 2 or self._no_allocation_count % 20 == 0:
                print(f"❌ Skipping EMA{ema_type} order: No allocation available (${allocation_usdt:.2f})")
            return
                
        # Calculate quantity based on available allocation (use adjusted price for accurate allocation)
        qty = allocation_usdt / adjusted_price
        
        # Format quantity properly
        qty = self.format_quantity(qty)
        
        # Check if formatted quantity is meaningful
        if qty < self.min_order_qty:
            print(f"⚠️ Calculated quantity {qty} too small for {ema_type} EMA order")
            return
        
        # Format price properly
        formatted_price = self.format_price(adjusted_price)
        
        # Place order
        response = self.client.place_order(
            category=self.category,
            symbol=self.symbol,
            side=side,
            orderType="Limit",
            qty=qty,
            price=formatted_price,
            timeInForce="GTC",
            verbose=False
        )
        
        if response and response.get('retCode') == 0:
            order_id = response['result']['orderId']
            self.limit_orders[order_id] = {
                'ema': ema_type,
                'price': formatted_price,
                'side': side,
                'qty': qty,
                'usdt_amount': allocation_usdt  # Track USDT amount for this order
            }
            
            # Update last order time for this EMA
            if ema_type == '9':
                self.last_ema9_order_time = time.time()
            elif ema_type == '21':
                self.last_ema21_order_time = time.time()
            
            print(f"📍 {side} order placed at EMA{ema_type}: ${formatted_price:.4f} (EMA: ${ema_price:.4f}, offset: {entry_offset_pct:.3f}%, qty: {qty:.3f}, ${allocation_usdt:.0f})")
            
    def update_limit_orders(self):
        """Update or cancel stale limit orders and replace with current EMA prices"""
        # First, check if any orders would violate capital limits
        current_position_value = abs(self.position) * self.avg_entry_price if self.avg_entry_price > 0 else 0
        
        # Account for spot positions in available capital calculation
        spot_position_usdt = 0.0
        if self.delta_tracker:
            delta_status = self.delta_tracker.get_status()
            spot_position_usdt = abs(delta_status.get('spot_position_usdt', 0.0))
        
        total_exposure = current_position_value + spot_position_usdt
        available_capital = self.max_allocation_usdt - total_exposure
        
        for order_id, info in list(self.limit_orders.items()):
            # Check if this order would exceed capital limits if filled
            order_usdt_value = info.get('usdt_amount', 0)
            
            if order_usdt_value > available_capital * 1.1:  # 10% buffer for price movement
                print(f"🚫 Cancelling {info['ema']} EMA order: Would exceed capital limits")
                print(f"   Order value: ${order_usdt_value:.0f}, Available capital: ${available_capital:.0f}")
                self.cancel_order(order_id)
                continue
            
            current_ema = self.ema_fast if info['ema'] == '9' else self.ema_slow
            
            # Calculate what the order price SHOULD be (EMA + offset)
            entry_offset_pct = self.config.get('entry_offset_pct', 0.0)
            if info['side'] == "Buy":
                target_price = current_ema * (1 + entry_offset_pct / 100)
            else:  # Sell
                target_price = current_ema * (1 - entry_offset_pct / 100)
            
            # Check if order price needs updating based on configurable threshold
            price_diff_pct = abs(info['price'] - target_price) / target_price
            threshold = self.order_update_threshold_pct / 100  # Convert percentage to decimal
            
            if price_diff_pct > threshold:
                print(f"🔄 Updating {info['ema']} EMA order: ${info['price']:.4f} → ${target_price:.4f} (diff: {price_diff_pct*100:.2f}% > {self.order_update_threshold_pct}%)")
                
                # Cancel old order - if it was already filled, don't place a new one
                was_filled = self.cancel_order(order_id)
                
                if not was_filled:
                    # Only place new order if the old one wasn't already filled
                    # Recalculate allocation based on CURRENT available capital (including spot positions)
                    current_position_value = abs(self.position) * self.avg_entry_price if self.avg_entry_price > 0 else 0
                    current_available = self.max_allocation_usdt - (current_position_value + spot_position_usdt)
                    ema_pct = self.ema9_allocation_usdt if info['ema'] == '9' else self.ema21_allocation_usdt
                    new_allocation = current_available * (ema_pct / self.max_allocation_usdt)
                    
                    if new_allocation > 0:
                        self.place_limit_order(info['side'], current_ema, info['ema'], new_allocation)
                    else:
                        print(f"  ⚠️ No capital available for new {info['ema']} EMA order")
                else:
                    print(f"  ✅ Not placing new order since {info['ema']} EMA order was already filled")
            else:
                # Only show this debug info occasionally to avoid spam
                if not hasattr(self, '_order_check_count'):
                    self._order_check_count = 0
                self._order_check_count += 1
                if self._order_check_count <= 3 or self._order_check_count % 20 == 0:
                    print(f"📍 {info['ema']} EMA order OK: ${info['price']:.4f} vs ${target_price:.4f} (diff: {price_diff_pct*100:.2f}% < {self.order_update_threshold_pct}%)")
                
    def cancel_order(self, order_id: str):
        """Cancel a single order and restore inventory if not filled"""
        was_filled = False
        order_info = self.limit_orders.get(order_id)
        
        try:
            response = self.client.cancel_order(
                category=self.category,
                symbol=self.symbol,
                orderId=order_id
            )
            
            # Check if order was already filled/cancelled (error 110001)
            if response and response.get('retCode') == 110001:
                print(f"  📝 Order {order_id[:8]}... was already filled/cancelled, syncing position")
                self.sync_position()
                was_filled = True
            elif response is None:
                # Client already printed error, treat as potentially filled
                was_filled = True
            else:
                # Order was successfully cancelled
                print(f"  ✅ Order {order_id[:8]}... cancelled successfully")
                
        except Exception as e:
            print(f"  ⚠️ Error cancelling order {order_id[:8]}...: {e}")
            # Don't assume filled on error - check if order still exists
            was_filled = False
        
        # Always remove from tracking regardless of result
        if order_id in self.limit_orders:
            del self.limit_orders[order_id]
        
        return was_filled
            
    def cancel_all_orders(self):
        """Cancel all limit orders"""
        for order_id in list(self.limit_orders.keys()):
            self.cancel_order(order_id)
            
    def cancel_stop_order(self):
        """Cancel existing stop loss order"""
        if self.stop_loss_order_id:
            try:
                self.client.cancel_order(
                    category=self.category,
                    symbol=self.symbol,
                    orderId=self.stop_loss_order_id
                )
                self.stop_loss_order_id = None
            except Exception:
                pass
            
    def calculate_pnl(self, entry: float, exit: float, qty: float) -> float:
        """Calculate P&L for a trade"""
        if self.position > 0:  # Was long
            return (exit - entry) * qty
        else:  # Was short
            return (entry - exit) * qty
            
    def log_trade(self, action: str, price: float, quantity: float, reason: str):
        """Log a trade for analysis"""
        trade = TradeLog(
            timestamp=time.time(),
            action=action,
            price=price,
            quantity=quantity,
            position_before=self.position,
            position_after=self.position,  # Will be updated after trade
            reason=reason,
            pnl=None  # Will be calculated if exit
        )
        self.trades.append(trade)
        
        # Also save to file for persistence
        with open('trades.json', 'a') as f:
            trade_dict = {
                'timestamp': trade.timestamp,
                'datetime': datetime.fromtimestamp(trade.timestamp).isoformat(),
                'action': trade.action,
                'price': trade.price,
                'quantity': trade.quantity,
                'position_before': trade.position_before,
                'reason': trade.reason
            }
            f.write(json.dumps(trade_dict) + '\n')
            
    def get_status(self, current_price: float = None) -> dict:
        """Get current status for monitoring"""
        current_pnl_pct = 0
        current_pnl_usdt = 0
        if current_price and self.position != 0:
            current_pnl_pct = self.calculate_current_pnl_pct(current_price)
            current_pnl_usdt = self.calculate_current_pnl_usdt(current_price)
            
        return {
            'position': self.position,
            'original_position': self.original_position_size,
            'avg_entry': self.avg_entry_price,
            'current_pnl_pct': current_pnl_pct,
            'current_pnl_usdt': current_pnl_usdt,
            'ema_fast': self.ema_fast,
            'ema_slow': self.ema_slow,
            'trend': self.trend,
            'realized_pnl': self.realized_pnl,
            'total_pnl': self.realized_pnl + current_pnl_usdt,
            'active_orders': len(self.limit_orders),
            'trailing_stop': self.trailing_stop_price,
            'hard_stop': self.hard_stop_price,
            'conditional_stop_triggered': self.conditional_stop_triggered,
            'tp_levels_hit': list(self.tp_levels_hit),
            'last_trade': self.trades[-1] if self.trades else None
        }
        
    def calculate_current_pnl_pct(self, current_price: float) -> float:
        """Calculate current unrealized P&L percentage"""
        if self.position == 0 or self.avg_entry_price == 0:
            return 0
            
        if self.position > 0:  # Long position
            return (current_price - self.avg_entry_price) / self.avg_entry_price * 100
        else:  # Short position
            return (self.avg_entry_price - current_price) / self.avg_entry_price * 100
            
    def calculate_current_pnl_usdt(self, current_price: float) -> float:
        """Calculate current unrealized P&L in USDT"""
        if self.position == 0 or self.avg_entry_price == 0:
            return 0
            
        if self.position > 0:  # Long position
            return (current_price - self.avg_entry_price) * abs(self.position)
        else:  # Short position
            return (self.avg_entry_price - current_price) * abs(self.position)
