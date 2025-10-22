"""
Simple Spot Rebalancer - Keeps spot and futures positions balanced

Core Logic:
1. Track trend using EMAs (optional but recommended)
2. Check spot position vs futures position
3. Calculate delta (divergence from target)
4. Adjust threshold based on trend:
   - In uptrend: more tolerant of long exposure
   - In downtrend: more tolerant of short exposure
5. If divergence exceeds adjusted threshold, rebalance
6. Use limit orders first, market orders if urgency increases
"""
import time
import signal
import sys
import yaml
import argparse
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv
import os

from bot.exchange.client import BybitClient, BybitWebSocketManager


class SpotRebalancer:
    def __init__(self, client, ws_manager, cfg: dict):
        self.client = client
        self.ws = ws_manager
        self.cfg = cfg

        r = cfg["rebalancer"]
        self.symbol = cfg["strategy"]["symbol"]
        self.base_symbol = self.symbol.replace('USDT', '').replace('PERP', '')
        
        # Simple config
        self.target_delta_usdt = float(r.get("target_delta_usdt", 0.0))
        self.rebalance_threshold_usdt = float(r.get("rebalance_threshold_usdt", 100.0))
        self.max_wait_seconds = int(r.get("max_wait_seconds", 30))
        self.use_limit_orders = bool(r.get("use_limit_orders", True))
        self.cooldown_seconds = int(r.get("cooldown_seconds", 10))
        
        # Trend awareness
        self.use_trend = bool(r.get("use_trend", True))
        self.ema_fast_period = int(r.get("ema_fast_period", 9))
        self.ema_slow_period = int(r.get("ema_slow_period", 21))
        self.trend_threshold_pct = float(r.get("trend_threshold_pct", 0.1))
        self.trend_multiplier = float(r.get("trend_multiplier", 1.5))  # How much more tolerant in favorable trend
        
        # EMA-based opportunistic rebalancing config
        self.ema_rebalance_config = r.get("ema_rebalance", {})
        self.last_ema_rebalance_time = 0
        
        # EMA state
        self.ema_fast = None
        self.ema_slow = None
        self.trend = "NEUTRAL"
        
        # State tracking
        self.last_rebalance_time = 0
        self.rebalance_wait_start: Optional[float] = None
        self.last_status_time = 0
        self.status_interval = 30  # Show status every 30 seconds
        
        # Order tracking to prevent duplicates
        self.active_orders = set()  # Track active order IDs
        self.last_order_cleanup = 0
        
        # Initialize EMAs if trend is enabled
        if self.use_trend:
            self.initialize_emas()
        
        # Get instrument info for proper quantity formatting
        self.get_instrument_info()
        
        print(f"""
        ========== SPOT REBALANCER ==========
        Symbol: {self.symbol}
        Target Delta: ${self.target_delta_usdt:,.0f}
        Rebalance Threshold: ${self.rebalance_threshold_usdt:,.0f}
        Trend Awareness: {self.use_trend}
        {'EMA Periods: ' + str(self.ema_fast_period) + '/' + str(self.ema_slow_period) if self.use_trend else ''}
        {'Trend Multiplier: ' + str(self.trend_multiplier) + 'x' if self.use_trend else ''}
        Max Wait (limit orders): {self.max_wait_seconds}s
        Use Limit Orders: {self.use_limit_orders}
        Cooldown: {self.cooldown_seconds}s
        =====================================
        """)

    def initialize_emas(self):
        """Initialize EMAs with historical data"""
        try:
            # Get historical klines
            timeframe = self.cfg.get('rebalancer', {}).get('timeframe', '5')
            resp = self.client.get_kline(
                category='spot',
                symbol=self.symbol,
                interval=timeframe,
                limit=200
            )
            
            if resp and resp.get('retCode') == 0:
                closes = [float(k[4]) for k in reversed(resp['result']['list'])]
                if len(closes) >= self.ema_slow_period:
                    # Calculate initial EMAs
                    self.ema_fast = sum(closes[-self.ema_fast_period:]) / self.ema_fast_period
                    self.ema_slow = sum(closes[-self.ema_slow_period:]) / self.ema_slow_period
                    
                    # Apply EMA formula for smoothing
                    alpha_fast = 2 / (self.ema_fast_period + 1)
                    alpha_slow = 2 / (self.ema_slow_period + 1)
                    
                    for close in closes[-50:]:
                        self.ema_fast = close * alpha_fast + self.ema_fast * (1 - alpha_fast)
                        self.ema_slow = close * alpha_slow + self.ema_slow * (1 - alpha_slow)
                    
                    self.update_trend()
                    print(f"✅ EMAs initialized: Fast=${self.ema_fast:.4f}, Slow=${self.ema_slow:.4f}, Trend={self.trend}")
        except Exception as e:
            print(f"⚠️ Error initializing EMAs: {e}")
            self.use_trend = False

    def update_emas(self, price: float):
        """Update EMAs with new price"""
        if not self.use_trend or self.ema_fast is None or self.ema_slow is None:
            return
        
        alpha_fast = 2 / (self.ema_fast_period + 1)
        alpha_slow = 2 / (self.ema_slow_period + 1)
        
        self.ema_fast = price * alpha_fast + self.ema_fast * (1 - alpha_fast)
        self.ema_slow = price * alpha_slow + self.ema_slow * (1 - alpha_slow)
        
        self.update_trend()

    def update_trend(self):
        """Update trend based on EMA relationship"""
        if self.ema_fast is None or self.ema_slow is None:
            self.trend = "NEUTRAL"
            return
        
        ratio = self.ema_fast / self.ema_slow
        threshold = self.trend_threshold_pct / 100
        
        if ratio > (1 + threshold):
            self.trend = "UPTREND"
        elif ratio < (1 - threshold):
            self.trend = "DOWNTREND"
        else:
            self.trend = "NEUTRAL"

    def get_adjusted_threshold(self, divergence: float) -> float:
        """
        Adjust rebalance threshold based on trend
        - If divergence aligns with trend, use higher threshold (more tolerant)
        - If divergence opposes trend, use standard threshold (less tolerant)
        """
        if not self.use_trend or self.trend == "NEUTRAL":
            return self.rebalance_threshold_usdt
        
        # Positive divergence = too much long exposure
        # Negative divergence = too much short exposure
        
        if divergence > 0 and self.trend == "UPTREND":
            # Long exposure in uptrend - be more tolerant
            return self.rebalance_threshold_usdt * self.trend_multiplier
        elif divergence < 0 and self.trend == "DOWNTREND":
            # Short exposure in downtrend - be more tolerant
            return self.rebalance_threshold_usdt * self.trend_multiplier
        else:
            # Divergence opposes trend - use standard threshold
            return self.rebalance_threshold_usdt

    def check_ema_rebalance_opportunity(self, current_price: float, position_usdt: float, current_time: float) -> dict:
        """
        Check if EMA-based opportunistic rebalancing should trigger.
        
        Logic:
        - If long in uptrend: rebalance when price breaks X% above fast EMA (take profits defensively)
        - If long in downtrend: rebalance when price comes back to fast EMA (defensive exit opportunity)
        """
        config = self.ema_rebalance_config
        
        # Check if feature is enabled
        if not config.get('enabled', False):
            return {"should_rebalance": False, "reason": "", "suggested_ratio": 0.0}
        
        # Check if EMAs are initialized
        if self.ema_fast is None or not self.use_trend:
            return {"should_rebalance": False, "reason": "EMAs not initialized", "suggested_ratio": 0.0}
        
        # Only trigger if we have a meaningful position
        min_position_usdt = float(config.get('min_position_usdt', 100.0))
        if abs(position_usdt) < min_position_usdt:
            return {"should_rebalance": False, "reason": "Position too small", "suggested_ratio": 0.0}
        
        # Cooldown check (prevent too frequent EMA-based rebalances)
        min_cooldown = float(config.get('cooldown_seconds', 60.0))
        if current_time - self.last_ema_rebalance_time < min_cooldown:
            return {"should_rebalance": False, "reason": "Cooldown active", "suggested_ratio": 0.0}
        
        is_long = position_usdt > 0
        price_vs_ema_pct = ((current_price - self.ema_fast) / self.ema_fast) * 100.0 if self.ema_fast > 0 else 0.0
        
        uptrend_breakout_pct = float(config.get('uptrend_breakout_pct', 1.0))
        downtrend_ema_touch_pct = float(config.get('downtrend_ema_touch_pct', 0.2))
        ema_partial_ratio = float(config.get('ema_partial_ratio', 0.3))
        
        # Case 1: Long position in uptrend - rebalance when price breaks X% above fast EMA
        if is_long and self.trend == "UPTREND":
            if price_vs_ema_pct >= uptrend_breakout_pct:
                return {
                    "should_rebalance": True,
                    "reason": f"Long in uptrend: price {price_vs_ema_pct:.2f}% above EMA{self.ema_fast_period} (defensive profit-taking)",
                    "suggested_ratio": ema_partial_ratio
                }
        
        # Case 2: Long position in downtrend - rebalance when price comes back to fast EMA
        elif is_long and self.trend == "DOWNTREND":
            # In downtrend, we want to exit when price rallies back near the EMA
            # Check if price is within X% of the EMA (either side)
            if abs(price_vs_ema_pct) <= downtrend_ema_touch_pct:
                return {
                    "should_rebalance": True,
                    "reason": f"Long in downtrend: price near EMA{self.ema_fast_period} ({price_vs_ema_pct:.2f}% - defensive exit)",
                    "suggested_ratio": ema_partial_ratio
                }
        
        return {"should_rebalance": False, "reason": "No EMA trigger", "suggested_ratio": 0.0}

    def execute_ema_rebalance(self, side: str, qty_usdt: float, price: float, reason: str):
        """Execute an EMA-triggered rebalance"""
        try:
            # Calculate quantity in base units
            qty_base = qty_usdt / price
            
            # Check balance before placing orders
            if side == "Sell":
                available_balance = self.get_available_balance()
                if available_balance <= 0:
                    print(f"⚠️ No {self.base_symbol} balance available for EMA rebalance")
                    return
                if qty_base > available_balance:
                    print(f"⚠️ Reducing EMA rebalance quantity: {qty_base:.3f} → {available_balance:.3f} {self.base_symbol}")
                    qty_base = available_balance
            else:
                usdt_balance = self.get_usdt_balance()
                qty_usdt = min(qty_usdt, usdt_balance)
                qty_base = qty_usdt / price
            
            print(f"\n🎯 EXECUTING EMA REBALANCE")
            print(f"   Side: {side}")
            print(f"   Quantity: {qty_base:.3f} {self.base_symbol} (${qty_usdt:.0f})")
            print(f"   Price: ${price:.4f}")
            print(f"   Reason: {reason}")
            
            # Execute market order for immediate execution
            response = self.client.place_market_order(
                category='spot',
                symbol=self.symbol,
                side=side,
                qty=qty_base,
                verbose=True
            )
            
            if response and response.get('retCode') == 0:
                print(f"✅ EMA rebalance order executed successfully")
                self.last_ema_rebalance_time = time.time()
                self.last_rebalance_time = time.time()  # Update general rebalance time too
            else:
                print(f"❌ EMA rebalance order failed: {response.get('retMsg', 'Unknown error')}")
                
        except Exception as e:
            print(f"❌ Error executing EMA rebalance: {e}")

    def get_spot_position_usdt(self, price: float) -> float:
        """Get spot position value in USDT"""
        try:
            resp = self.client.get_coin_balance(self.base_symbol)
            total_base = 0.0
            if resp and resp.get('retCode') == 0:
                for acct in resp.get('result', {}).get('list', []) or []:
                    for coin in acct.get('coin', []) or []:
                        if coin.get('coin') == self.base_symbol:
                            v = float(coin.get('walletBalance') or 0)
                            total_base += v
            return total_base * price
        except Exception as e:
            print(f"⚠️ Error getting spot position: {e}")
            return 0.0

    def get_available_balance(self) -> float:
        """Get available balance for the base symbol"""
        try:
            resp = self.client.get_coin_balance(self.base_symbol)
            total_base = 0.0
            if resp and resp.get('retCode') == 0:
                for acct in resp.get('result', {}).get('list', []) or []:
                    for coin in acct.get('coin', []) or []:
                        if coin.get('coin') == self.base_symbol:
                            # Safely convert balance strings to float, handling empty strings
                            wallet_balance_str = coin.get('walletBalance', '0')
                            available_str = coin.get('availableToWithdraw', wallet_balance_str)
                            
                            # Handle empty strings and None values
                            if available_str == '' or available_str is None:
                                available_str = '0'
                            
                            try:
                                available = float(available_str)
                                total_base += available
                            except (ValueError, TypeError):
                                print(f"⚠️ Invalid balance value for {self.base_symbol}: '{available_str}'")
                                continue
            return total_base
        except Exception as e:
            print(f"⚠️ Error getting available balance: {e}")
            return 0.0

    def get_usdt_balance(self) -> float:
        """Get available USDT balance"""
        try:
            resp = self.client.get_coin_balance('USDT')
            total_usdt = 0.0
            if resp and resp.get('retCode') == 0:
                for acct in resp.get('result', {}).get('list', []) or []:
                    for coin in acct.get('coin', []) or []:
                        if coin.get('coin') == 'USDT':
                            # Safely convert balance strings to float, handling empty strings
                            wallet_balance_str = coin.get('walletBalance', '0')
                            available_str = coin.get('availableToWithdraw', wallet_balance_str)
                            
                            # Handle empty strings and None values
                            if available_str == '' or available_str is None:
                                available_str = '0'
                            
                            try:
                                available = float(available_str)
                                total_usdt += available
                            except (ValueError, TypeError):
                                print(f"⚠️ Invalid USDT balance value: '{available_str}'")
                                continue
            return total_usdt
        except Exception as e:
            print(f"⚠️ Error getting USDT balance: {e}")
            return 0.0

    def get_futures_position_usdt(self, price: float) -> float:
        """Get futures position value in USDT"""
        try:
            # Check for both linear and inverse futures
            resp = self.client.get_positions(category='linear', symbol=self.symbol)
            if resp and resp.get('retCode') == 0:
                positions = resp['result']['list']
                if positions:
                    pos = positions[0]
                    size = float(pos.get('size', 0))
                    side = pos.get('side', 'None')
                    # Positive for long, negative for short
                    signed_size = size if side == 'Buy' else -size if side == 'Sell' else 0
                    return signed_size * price
        except Exception as e:
            print(f"⚠️ Error getting futures position: {e}")
        return 0.0

    def step(self):
        """Main rebalancer loop"""
        now = time.time()
        
        # Get current price
        price = self.ws.get_latest_price()
        if price is None:
            return
        
        # Update EMAs on new candle close
        if self.use_trend:
            closed = self.ws.get_latest_closed_kline()
            if closed:
                close_price = float(closed['close'])
                self.update_emas(close_price)
        
        # Get positions
        spot_usdt = self.get_spot_position_usdt(price)
        futures_usdt = self.get_futures_position_usdt(price)
        
        # Calculate delta (total exposure)
        total_delta = spot_usdt + futures_usdt
        divergence = total_delta - self.target_delta_usdt
        
        # Get trend-adjusted threshold
        adjusted_threshold = self.get_adjusted_threshold(divergence)
        
        # Show status periodically
        if now - self.last_status_time >= self.status_interval:
            self.print_status(price, spot_usdt, futures_usdt, total_delta, divergence, adjusted_threshold)
            self.last_status_time = now
        
        # Check EMA-based opportunistic rebalancing BEFORE normal threshold check
        # This allows defensive rebalancing even when within normal thresholds
        if hasattr(self, 'ema_rebalance_config') and self.ema_rebalance_config.get('enabled', False):
            ema_check = self.check_ema_rebalance_opportunity(price, spot_usdt, now)
            if ema_check['should_rebalance']:
                print(f"\n🎯 EMA OPPORTUNISTIC REBALANCE TRIGGERED")
                print(f"   Reason: {ema_check['reason']}")
                print(f"   Suggested reduction: {ema_check['suggested_ratio']*100:.0f}% of position")
                
                # Calculate rebalance quantity based on suggested ratio
                # We want to reduce our exposure, so if long, sell; if short, buy
                if spot_usdt > 0:  # Long exposure
                    rebalance_qty_usdt = spot_usdt * ema_check['suggested_ratio']
                    self.execute_ema_rebalance("Sell", rebalance_qty_usdt, price, ema_check['reason'])
                    return
                elif spot_usdt < 0:  # Short exposure (shouldn't happen with spot, but for completeness)
                    rebalance_qty_usdt = abs(spot_usdt) * ema_check['suggested_ratio']
                    self.execute_ema_rebalance("Buy", rebalance_qty_usdt, price, ema_check['reason'])
                    return
        
        # Check if we need to rebalance (using adjusted threshold)
        if abs(divergence) < adjusted_threshold:
            self.rebalance_wait_start = None
            return
        
        # Check cooldown
        if now - self.last_rebalance_time < self.cooldown_seconds:
            return
        
        # Clean up old orders periodically
        if now - self.last_order_cleanup > 60:  # Every minute
            self.cleanup_old_orders()
            self.last_order_cleanup = now
        
        # Check if we already have active orders for this divergence
        if self.active_orders:
            print(f"⚠️ Already have {len(self.active_orders)} active orders, skipping new order")
            return
        
        # Determine what action to take
        if divergence > 0:
            # Too much long exposure - need to SELL spot
            side = "Sell"
            qty_usdt = divergence
        else:
            # Too much short exposure - need to BUY spot
            side = "Buy"
            qty_usdt = abs(divergence)
        
        # Calculate quantity in base units
        qty_base = qty_usdt / price
        
        # Check balance before placing orders
        if side == "Sell":
            # For SELL orders, check if we have enough base currency balance
            try:
                available_balance = self.get_available_balance()
                if available_balance <= 0:
                    print(f"⚠️ No {self.base_symbol} balance available for selling")
                    return
                
                # Limit sell quantity to available balance
                if qty_base > available_balance:
                    print(f"⚠️ Reducing sell quantity: {qty_base:.3f} → {available_balance:.3f} {self.base_symbol} (insufficient balance)")
                    qty_base = available_balance
                    qty_usdt = qty_base * price  # Recalculate USDT amount
            except Exception as e:
                print(f"⚠️ Error checking balance for sell order: {e}")
                return
        else:
            # For BUY orders, check if we have enough USDT balance
            try:
                usdt_balance = self.get_usdt_balance()
                if usdt_balance <= 0:
                    print(f"⚠️ No USDT balance available for buying")
                    return
                
                # Limit buy quantity to available USDT
                if qty_usdt > usdt_balance:
                    print(f"⚠️ Reducing buy quantity: ${qty_usdt:.0f} → ${usdt_balance:.0f} USDT (insufficient balance)")
                    qty_usdt = usdt_balance
                    qty_base = qty_usdt / price  # Recalculate base amount
            except Exception as e:
                print(f"⚠️ Error checking USDT balance for buy order: {e}")
                return
        
        # Format quantity according to instrument requirements
        qty_base = self.format_quantity(qty_base)
        
        if qty_base < 1:  # Minimum order size
            print(f"⚠️ Order size too small: {qty_base:.0f} {self.base_symbol}")
            return
        
        # Decide between limit and market orders
        use_market = False
        
        if self.use_limit_orders:
            # Start wait timer if not already started
            if self.rebalance_wait_start is None:
                self.rebalance_wait_start = now
            
            # Check how long we've been waiting
            wait_time = now - self.rebalance_wait_start
            
            if wait_time >= self.max_wait_seconds:
                # Waited too long, use market order
                use_market = True
                print(f"⏱️ Wait timeout ({wait_time:.0f}s), switching to market order")
            else:
                # Place/update limit order
                self.place_limit_order(side, price, qty_base, qty_usdt, divergence)
                self.last_rebalance_time = now  # Update cooldown after placing limit order
                return
        else:
            # Always use market orders
            use_market = True
        
        if use_market:
            self.place_market_order(side, qty_base, qty_usdt, divergence)
            self.last_rebalance_time = now
            self.rebalance_wait_start = None

    def place_limit_order(self, side: str, price: float, qty_base: float, qty_usdt: float, divergence: float):
        """Place a limit order slightly better than current price"""
        # Adjust price slightly to increase fill probability
        if side == "Buy":
            order_price = price * 0.999  # Bid slightly below
        else:
            order_price = price * 1.001  # Ask slightly above
        
        order_price = round(order_price, 4)
        
        print(f"\n{'='*60}")
        print(f"🎯 REBALANCING - {side.upper()} ${qty_usdt:,.0f} (divergence: ${divergence:+,.0f})")
        print(f"   Limit Order: {qty_base:.3f} {self.base_symbol} @ ${order_price:.4f}")
        print(f"{'='*60}\n")
        
        try:
            resp = self.client.place_order(
                category='spot',
                symbol=self.symbol,
                side=side,
                orderType="Limit",
                qty=qty_base,
                price=order_price,
                timeInForce="GTC"
            )
            
            if resp and resp.get('retCode') == 0:
                order_id = resp['result']['orderId']
                self.active_orders.add(order_id)
                print(f"✅ Limit order placed successfully - Order ID: {order_id}")
            else:
                error_msg = resp.get('retMsg', 'Unknown error') if resp else 'No response'
                print(f"❌ Failed to place limit order: {error_msg}")
                
        except Exception as e:
            print(f"❌ Error placing limit order: {e}")

    def place_market_order(self, side: str, qty_base: float, qty_usdt: float, divergence: float):
        """Place a market order for immediate execution"""
        print(f"\n{'='*60}")
        print(f"🚨 URGENT REBALANCING - {side.upper()} ${qty_usdt:,.0f} (divergence: ${divergence:+,.0f})")
        print(f"   Market Order: {qty_base:.3f} {self.base_symbol}")
        print(f"{'='*60}\n")
        
        try:
            resp = self.client.place_market_order(
                category='spot',
                symbol=self.symbol,
                side=side,
                qty=qty_base
            )
            
            if resp and resp.get('retCode') == 0:
                order_id = resp['result']['orderId']
                self.active_orders.add(order_id)
                print(f"✅ Market order executed successfully - Order ID: {order_id}")
            else:
                error_msg = resp.get('retMsg', 'Unknown error') if resp else 'No response'
                print(f"❌ Failed to place market order: {error_msg}")
                
        except Exception as e:
            print(f"❌ Error placing market order: {e}")

    def print_status(self, price: float, spot_usdt: float, futures_usdt: float, total_delta: float, divergence: float, adjusted_threshold: float = None):
        """Print current status"""
        if adjusted_threshold is None:
            adjusted_threshold = self.rebalance_threshold_usdt
            
        print(f"\n{'='*60}")
        print(f"📊 REBALANCER STATUS - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*60}")
        print(f"Price: ${price:.4f}")
        
        if self.use_trend and self.ema_fast and self.ema_slow:
            trend_emoji = "🟢" if self.trend == "UPTREND" else "🔴" if self.trend == "DOWNTREND" else "⚪"
            print(f"Trend: {trend_emoji} {self.trend} (EMA{self.ema_fast_period}: ${self.ema_fast:.4f}, EMA{self.ema_slow_period}: ${self.ema_slow:.4f})")
        
        print(f"Spot Position: ${spot_usdt:+,.0f}")
        print(f"Futures Position: ${futures_usdt:+,.0f}")
        print(f"Total Delta: ${total_delta:+,.0f} (Target: ${self.target_delta_usdt:,.0f})")
        print(f"Divergence: ${divergence:+,.0f}")
        
        if adjusted_threshold != self.rebalance_threshold_usdt:
            print(f"Threshold: ${adjusted_threshold:,.0f} (Base: ${self.rebalance_threshold_usdt:,.0f}, adjusted by trend)")
        else:
            print(f"Threshold: ${adjusted_threshold:,.0f}")
        
        # Debug: Show threshold calculation details
        if self.use_trend and self.trend != "NEUTRAL":
            print(f"Debug: Trend={self.trend}, Divergence=${divergence:+.0f}, Multiplier={self.trend_multiplier}x")
        
        if abs(divergence) >= adjusted_threshold:
            print(f"⚠️ NEEDS REBALANCING")
        else:
            print(f"✅ Within threshold")
        print(f"{'='*60}\n")

    def get_instrument_info(self):
        """Get instrument specifications for proper quantity formatting"""
        try:
            response = self.client.get_instruments_info(
                category='spot',
                symbol=self.symbol
            )
            
            if response and response.get('retCode') == 0:
                instruments = response['result']['list']
                if instruments:
                    info = instruments[0]
                    
                    # Parse lot size filter for quantity step
                    lot_size = info.get('lotSizeFilter', {})
                    self.qty_step = float(lot_size.get('qtyStep', '1'))
                    self.min_order_qty = float(lot_size.get('minOrderQty', '1'))
                    
                    print(f"📏 Instrument specs for {self.symbol}: qtyStep={self.qty_step}, minOrderQty={self.min_order_qty}")
                    
        except Exception as e:
            print(f"⚠️ Could not get instrument info for {self.symbol}: {e}")
            # Use conservative defaults
            self.qty_step = 1.0
            self.min_order_qty = 1.0
            print(f"📏 Using default specs: qtyStep={self.qty_step}, minOrderQty={self.min_order_qty}")

    def format_quantity(self, qty: float) -> float:
        """Format quantity according to instrument specifications"""
        if qty <= 0:
            return 0.0
            
        # Round to the correct step size
        steps = round(qty / self.qty_step)
        formatted_qty = steps * self.qty_step
        
        # Ensure minimum order quantity
        if formatted_qty < self.min_order_qty:
            formatted_qty = self.min_order_qty
            
        return formatted_qty

    def cleanup_old_orders(self):
        """Clean up old orders that are no longer active"""
        try:
            # Get open orders from exchange
            resp = self.client.get_open_orders(category='spot', symbol=self.symbol)
            if resp and resp.get('retCode') == 0:
                active_order_ids = {order['orderId'] for order in resp['result']['list']}
                
                # Remove orders that are no longer active
                self.active_orders = self.active_orders.intersection(active_order_ids)
                
                if len(self.active_orders) > 0:
                    print(f"🧹 Cleaned up orders, {len(self.active_orders)} still active")
                else:
                    print(f"🧹 All orders cleaned up")
        except Exception as e:
            print(f"⚠️ Error cleaning up orders: {e}")
            # Clear all tracked orders on error to prevent blocking
            self.active_orders.clear()


class RebalancerRunner:
    def __init__(self):
        self.running = True
        self.rebalancer = None
        self.ws = None
        self.config = None

    def signal_handler(self, signum, frame):
        print("\n⛔ Shutting down rebalancer...")
        self.running = False
        if self.ws:
            self.ws.disconnect()
        sys.exit(0)

    def run(self, config_file='config.yaml', symbol=None):
        with open(config_file, 'r') as f:
            cfg = yaml.safe_load(f)
        self.config = cfg

        # API
        load_dotenv()
        account = cfg['api']['account_name']
        key = os.getenv(f"BYBIT_API_KEY_{account}")
        sec = os.getenv(f"BYBIT_API_SECRET_{account}")
        if not key or not sec:
            print("❌ Missing API credentials in env")
            return
        client = BybitClient(api_key=key, api_secret=sec, testnet=cfg['api']['testnet'])

        r = cfg['rebalancer']
        # Use main symbol from strategy section, allow override from command line
        symbol = symbol or cfg['strategy']['symbol']
        print(f"🔧 Using symbol: {symbol}")

        # WS for spot ticker and candles
        self.ws = BybitWebSocketManager(symbol, category='spot', interval=r.get('timeframe', '5'))
        self.ws.connect()
        time.sleep(2)

        self.rebalancer = SpotRebalancer(client, self.ws, cfg)

        # signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        print("\n🚀 Spot Rebalancer running... Ctrl+C to stop\n")
        while self.running:
            try:
                self.rebalancer.step()
                time.sleep(0.5)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"❌ Rebalancer loop error: {e}")
                time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Spot Rebalancer')
    parser.add_argument('--symbol', '-s', type=str, help='Spot symbol (e.g., BTCUSDT) - overrides config')
    parser.add_argument('--config', '-c', type=str, default='config.yaml', help='Config file path')
    args = parser.parse_args()

    runner = RebalancerRunner()
    runner.run(config_file=args.config, symbol=args.symbol)