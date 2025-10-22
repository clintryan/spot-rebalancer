# main_simple.py
"""
Simplified main runner for clean EMA strategy

Features:
- Automatic position exit on shutdown (configurable via exit_on_shutdown)
- Pause mode: stops trading but holds positions
- Manual controls: pause/resume, exit position, cancel orders
- Real-time keyboard input handling
"""
import time
import signal
import sys
import yaml
import argparse
import select
import threading
from datetime import datetime

class SimpleRunner:
    def __init__(self):
        self.running = True
        self.strategy = None
        self.ws_manager = None
        self.delta_tracker = None
        self.config = None
        self.paused = False
        self.pause_start_time = None
        
    def signal_handler(self, signum, frame):
        """Handle shutdown gracefully"""
        print("\n⛔ Shutting down...")
        self.running = False
        
        if self.strategy:
            self.strategy.cancel_all_orders()
            
            # Check if we should exit positions on shutdown
            exit_on_shutdown = self.config.get('runtime', {}).get('exit_on_shutdown', True)
            
            if self.strategy.position != 0:
                if exit_on_shutdown:
                    print("Closing position before shutdown...")
                    # Get current price from WebSocket if available
                    current_price = None
                    if self.ws_manager:
                        current_price = self.ws_manager.get_latest_price()
                    
                    if current_price:
                        self.strategy.execute_full_exit(current_price, "SHUTDOWN")
                    else:
                        print("Could not get current price for shutdown exit")
                else:
                    print("Holding position as configured (exit_on_shutdown: false)")
                    print(f"Position: {self.strategy.position:.3f} @ ${self.strategy.avg_entry_price:.4f}")
                
        if self.ws_manager:
            self.ws_manager.disconnect()
            
        print(f"\nFinal P&L: ${self.strategy.realized_pnl:.2f}")
        sys.exit(0)
        
    def toggle_pause(self):
        """Toggle pause mode on/off"""
        self.paused = not self.paused
        if self.paused:
            self.pause_start_time = time.time()
            print("\n⏸️ PAUSED - Bot will not place new trades")
            print("   Existing positions will be held")
            print("   Press 'p' again to resume trading")
        else:
            pause_duration = time.time() - self.pause_start_time if self.pause_start_time else 0
            print(f"\n▶️ RESUMED - Trading active (paused for {pause_duration:.0f}s)")
            
    def manual_exit_position(self, price: float):
        """Manually exit current position"""
        if not self.strategy or self.strategy.position == 0:
            print("❌ No position to exit")
            return False
            
        print(f"\n🚨 MANUAL POSITION EXIT")
        print(f"   Current Position: {self.strategy.position:.3f} @ ${self.strategy.avg_entry_price:.4f}")
        print(f"   Exit Price: ${price:.4f}")
        
        # Calculate estimated P&L
        if self.strategy.position > 0:
            estimated_pnl = (price - self.strategy.avg_entry_price) * abs(self.strategy.position)
        else:
            estimated_pnl = (self.strategy.avg_entry_price - price) * abs(self.strategy.position)
            
        print(f"   Estimated P&L: ${estimated_pnl:+.2f}")
        
        # Execute the exit
        self.strategy.execute_full_exit(price, "MANUAL_EXIT")
        return True
        
    def manual_close_orders(self):
        """Manually cancel all orders"""
        if not self.strategy:
            print("❌ No strategy initialized")
            return False
            
        print("\n🚨 MANUAL ORDER CANCELLATION")
        cancelled_count = len(self.strategy.limit_orders)
        self.strategy.cancel_all_orders()
        self.strategy.cancel_tp_orders()
        self.strategy.cancel_stop_order()
        print(f"   Cancelled {cancelled_count} limit orders")
        print(f"   Cancelled take profit orders")
        print(f"   Cancelled stop loss orders")
        return True
        
    def check_keyboard_input(self):
        """Check for keyboard input (non-blocking)"""
        if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
            try:
                command = sys.stdin.readline().strip().lower()
                if command == 'p':
                    self.toggle_pause()
                elif command == 'e':
                    if self.ws_manager:
                        price = self.ws_manager.get_latest_price()
                        if price:
                            self.manual_exit_position(price)
                        else:
                            print("❌ Could not get current price for manual exit")
                    else:
                        print("❌ WebSocket not available for manual exit")
                elif command == 'c':
                    self.manual_close_orders()
                elif command == 's':
                    if self.ws_manager:
                        price = self.ws_manager.get_latest_price()
                        if price:
                            status = self.strategy.get_status(price)
                            self.print_detailed_status(price, status)
                        else:
                            print("❌ Could not get current price for status")
                    else:
                        print("❌ WebSocket not available for status")
                elif command:
                    print(f"❓ Unknown command: '{command}'. Press 'p', 'e', 'c', or 's'")
            except Exception as e:
                print(f"⚠️ Error processing keyboard input: {e}")
        
    def run(self, config_file='config.yaml', symbol=None):
        """Main execution loop"""
        # Load config
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
            
        # Store config for signal handler
        self.config = config
        
        # Initialize pause mode from config
        self.paused = config.get('runtime', {}).get('pause_mode', False)
        if self.paused:
            self.pause_start_time = time.time()
            print("⏸️ Starting in PAUSE MODE - No new trades will be placed")
            
        # Override symbol if provided via command line
        if symbol:
            config['strategy']['symbol'] = symbol
            print(f"🔧 Overriding symbol to: {symbol}")
            
        # Setup signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Initialize API client
        from bot.exchange.client import BybitClient, BybitWebSocketManager
        import os
        from dotenv import load_dotenv
        
        # Load environment variables
        load_dotenv()
        
        # Get API credentials from environment variables using account name
        account_name = config['api']['account_name']
        api_key_env = f"BYBIT_API_KEY_{account_name}"
        api_secret_env = f"BYBIT_API_SECRET_{account_name}"
        
        api_key = os.getenv(api_key_env)
        api_secret = os.getenv(api_secret_env)
        
        if not api_key or not api_secret:
            print(f"❌ Error: API credentials not found in environment variables")
            print(f"Expected: {api_key_env} and {api_secret_env}")
            print(f"For account: {account_name}")
            print("Please set these environment variables or create a .env file")
            print("Format: BYBIT_API_KEY_{ACCOUNT_NAME} and BYBIT_API_SECRET_{ACCOUNT_NAME}")
            return
        
        client = BybitClient(
            api_key=api_key,
            api_secret=api_secret,
            testnet=config['api']['testnet']
        )
        
        # Initialize strategy
        import importlib.util
        import os
        strategy_path = os.path.join(os.path.dirname(__file__), 'bot', 'core', 'strategy.py')
        spec = importlib.util.spec_from_file_location("strategy", strategy_path)
        strategy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(strategy_module)
        SimplifiedEMAStrategy = strategy_module.SimplifiedEMAStrategy
        self.strategy = SimplifiedEMAStrategy(
            client, 
            config['strategy']['symbol'],
            config['strategy']
        )
        
        if not self.strategy.initialize():
            print("Failed to initialize strategy")
            return
            
        # Initialize Delta Tracker
        from bot.core.delta_tracker import DeltaTracker
        self.delta_tracker = DeltaTracker(
            client, 
            config['strategy']['symbol'],
            config
        )
        
        # Connect delta tracker to strategy
        self.strategy.set_delta_tracker(self.delta_tracker)
            
        # Initialize WebSocket with timeframe from config
        timeframe = config['strategy'].get('timeframe', '1')
        print(f"🔧 Using {timeframe}min candles for EMA calculations")
        self.ws_manager = BybitWebSocketManager(
            config['strategy']['symbol'],
            config['strategy']['category'],
            timeframe  # Use the actual timeframe from config
        )
        self.ws_manager.connect()
        time.sleep(3)
        
        # Test connection
        test_price = self.ws_manager.get_latest_price()
        if test_price is None:
            print("Failed to get price from WebSocket")
            return
            
        print(f"✅ Connected! Current price: ${test_price:.4f}")
        
        # Main loop
        last_candle_ts = None
        last_status_time = time.time()
        last_trade_summary_time = time.time()
        update_count = 0
        
        print("\n🚀 Starting bot... Press Ctrl+C to stop\n")
        print("🔄 Entering main loop...")
        print("\n📋 MANUAL CONTROLS:")
        print("   Press 'p' + Enter: Toggle pause/resume")
        print("   Press 'e' + Enter: Manual exit position")
        print("   Press 'c' + Enter: Cancel all orders")
        print("   Press 's' + Enter: Show detailed status")
        print("   Press Ctrl+C: Shutdown (hold positions if exit_on_shutdown=false)")
        print()
        
        while self.running:
            try:
                
                # Check WebSocket health and attempt reconnection if needed
                if not self.ws_manager.is_healthy() and update_count > 10:  # Give initial connection time
                    # Get detailed status for debugging
                    ws_status = self.ws_manager.get_connection_status()
                    print(f"⚠️ WebSocket unhealthy: Connected={ws_status['connected']}, LastMsg={ws_status['last_message_time']}, "
                          f"SecsSinceMsg={ws_status['seconds_since_last_message']}, Attempts={ws_status['reconnect_attempts']}")
                    
                    success = self.ws_manager.reconnect()
                    if not success:
                        print("❌ WebSocket reconnection failed, using REST API fallback")
                    time.sleep(3)  # Give reconnection time
                
                # Get current price with timeout protection
                try:
                    price = self.ws_manager.get_latest_price()
                    
                    # If WebSocket price is None or periodic REST API check
                    if price is None or (update_count > 0 and update_count % 120 == 0):  # Every 60 seconds
                        if price is None:
                            print("🔄 WebSocket price unavailable, trying REST API...")
                        try:
                            # Get current price from REST API as fallback
                            ticker_response = self.strategy.client.get_tickers(
                                category=config['strategy']['category'],
                                symbol=config['strategy']['symbol']
                            )
                            if ticker_response and ticker_response.get('retCode') == 0:
                                rest_price = float(ticker_response['result']['list'][0]['lastPrice'])
                                if price is None:
                                    print(f"✅ REST API price: ${rest_price:.4f}")
                                price = rest_price
                                # Update WebSocket fallback price
                                self.ws_manager.update_fallback_price(rest_price)
                        except Exception as e:
                            print(f"⚠️ REST API fallback failed: {e}")
                    
                    if price is None:
                        print("⚠️ No price data from any source, retrying...")
                        time.sleep(1)
                        continue
                    
                    # Debug: Show initial connection
                    if update_count == 0:
                        print(f"✅ Connected! Current price: ${price:.4f}")
                        
                except Exception as e:
                    print(f"⚠️ Error getting price: {e}")
                    time.sleep(1)
                    continue
                    
                # Check for new candle
                try:
                    closed = self.ws_manager.get_latest_closed_kline()
                    is_new_candle = False
                    
                    if closed:
                        close_ts = closed['ts']
                        if last_candle_ts is None or close_ts > last_candle_ts:
                            is_new_candle = True
                            last_candle_ts = close_ts
                            print(f"🕯️ New candle detected: {datetime.fromtimestamp(close_ts/1000).strftime('%H:%M:%S')}")
                            
                            # Show current levels on new candle if we have a position
                            if self.strategy.position != 0:
                                status = self.strategy.get_status(price)
                                self.print_candle_levels(price, status)
                    # No debug message for waiting for candle completion
                            
                except Exception as e:
                    print(f"⚠️ Error getting candle data: {e}")
                    is_new_candle = False
                        
                # Check for keyboard input
                self.check_keyboard_input()
                
                # Pass candle close price for EMA calculation if we have a new candle
                ema_update_price = price
                candle_close_price = None
                if is_new_candle and closed:
                    ema_update_price = float(closed.get('close', price))
                    candle_close_price = float(closed.get('close', price))
                    
                # Update strategy only if not paused
                if not self.paused:
                    self.strategy.update(ema_update_price, is_new_candle, candle_close_price)
                else:
                    # In pause mode, still update EMAs but don't place trades
                    if is_new_candle:
                        self.strategy.update_emas_only(ema_update_price, candle_close_price)
                
                # Status updates every 60 seconds (1 minute)
                if time.time() - last_status_time > 60:
                    status = self.strategy.get_status(price)
                    self.print_detailed_status(price, status)
                    last_status_time = time.time()
                
                # Trade summary every 10 minutes
                if time.time() - last_trade_summary_time > 600:
                    self.print_trade_summary()
                    last_trade_summary_time = time.time()
                
                # Check for filled orders every 10 updates (5 seconds at 0.5s intervals)
                # This is critical to prevent race conditions where orders fill but aren't detected
                if update_count % 10 == 0:
                    self.check_filled_orders()
                    self.check_filled_tp_orders()
                
                # Check delta status every 120 updates (60 seconds at 0.5s intervals)
                if update_count % 120 == 0:
                    self.check_delta_status(price)
                    
                # Small delay
                time.sleep(0.5)
                update_count += 1
                    
            except KeyboardInterrupt:
                print("\n🛑 Keyboard interrupt received")
                break
            except Exception as e:
                print(f"❌ Error in main loop: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(1)
                
    def check_filled_orders(self):
        """Check if any limit orders were filled"""
        if not self.strategy.limit_orders:
            return
            
        filled_orders = []
        for order_id in list(self.strategy.limit_orders.keys()):
            try:
                response = self.strategy.client.get_open_orders(
                    category=self.strategy.category,
                    symbol=self.strategy.symbol,
                    orderId=order_id
                )
                
                if response and response.get('retCode') == 0:
                    if not response['result']['list']:
                        # Order not in open orders - might be filled
                        filled_orders.append(order_id)
                        
            except Exception as e:
                # If we can't check order status, assume it might be filled
                print(f"⚠️ Could not check order status for {order_id[:8]}...: {e}")
                filled_orders.append(order_id)
        
        # Handle all filled orders
        for order_id in filled_orders:
            self.handle_filled_order(order_id)
            
    def check_filled_tp_orders(self):
        """Check if any TP orders were filled"""
        if not self.strategy.tp_orders:
            return
            
        filled_tp_orders = []
        for tp_name, order_id in list(self.strategy.tp_orders.items()):
            try:
                response = self.strategy.client.get_open_orders(
                    category=self.strategy.category,
                    symbol=self.strategy.symbol,
                    orderId=order_id
                )
                
                if response and response.get('retCode') == 0:
                    if not response['result']['list']:
                        # Order not in open orders - might be filled
                        filled_tp_orders.append((tp_name, order_id))
                        
            except Exception as e:
                # If we can't check order status, assume it might be filled
                print(f"⚠️ Could not check TP order status for {tp_name}: {e}")
                filled_tp_orders.append((tp_name, order_id))
        
        # Handle all filled TP orders
        for tp_name, order_id in filled_tp_orders:
            self.handle_filled_tp_order(tp_name, order_id)
                
    def print_detailed_status(self, price: float, status: dict):
        """Print detailed status with clear position overview"""
        print(f"\n{'='*100}")
        pause_indicator = " ⏸️ PAUSED" if self.paused else ""
        print(f"📊 TRADING STATUS - {datetime.now().strftime('%H:%M:%S')} - ${self.strategy.symbol}{pause_indicator}")
        print(f"{'='*100}")
        
        # Market Info Line
        ema_ratio = status['ema_fast'] / status['ema_slow']
        trend_icon = "🟢" if status['trend'] == "UPTREND" else "🔴" if status['trend'] == "DOWNTREND" else "⚪"
        print(f"💰 PRICE: ${price:.4f} | {trend_icon} {status['trend']} | EMA9: ${status['ema_fast']:.4f} | EMA21: ${status['ema_slow']:.4f} | Ratio: {ema_ratio:.6f}")
        
        # Position Overview
        if status['position'] != 0:
            side = "LONG" if status['position'] > 0 else "SHORT"
            side_icon = "🟢" if status['position'] > 0 else "🔴"
            pnl_icon = "💚" if status['current_pnl_usdt'] >= 0 else "💸"
            
            print(f"\n{side_icon} POSITION: {side} {abs(status['position']):.3f} @ ${status['avg_entry']:.4f}")
            print(f"{pnl_icon} P&L: ${status['current_pnl_usdt']:+.2f} ({status['current_pnl_pct']:+.2f}%) | Realized: ${status['realized_pnl']:.2f} | Total: ${status['total_pnl']:.2f}")
            
            # Take Profit Levels Table
            print(f"\n🎯 TAKE PROFIT LEVELS:")
            print(f"   {'Level':<8} {'Target':<10} {'Distance':<12} {'Exit %':<8} {'Status':<10}")
            print(f"   {'-'*8} {'-'*10} {'-'*12} {'-'*8} {'-'*10}")
            
            entry_price = status['avg_entry']
            is_long = status['position'] > 0
            
            for tp_name, tp_config in self.strategy.take_profit_levels.items():
                if tp_name in status['tp_levels_hit']:
                    print(f"   {tp_name.upper():<8} {'HIT':<10} {'---':<12} {tp_config['exit_pct']:<8}% ✅ TAKEN")
                else:
                    # Calculate target price
                    if is_long:
                        target_price = entry_price * (1 + tp_config['pct'] / 100)
                        distance = target_price - price
                    else:
                        target_price = entry_price * (1 - tp_config['pct'] / 100)
                        distance = price - target_price
                    
                    distance_pct = abs(distance / price * 100)
                    status_text = "PENDING" if distance > 0 else "READY"
                    print(f"   {tp_name.upper():<8} ${target_price:.4f} ${distance:+.4f} {tp_config['exit_pct']:<8}% {status_text}")
            
            # Stop Loss Levels
            print(f"\n🛑 STOP LOSS LEVELS:")
            if status['trailing_stop']:
                cond_distance = (price - status['trailing_stop']) if is_long else (status['trailing_stop'] - price)
                cond_status = "⚠️ TRIGGERED" if status['conditional_stop_triggered'] else "🟢 SAFE"
                print(f"   Conditional: ${status['trailing_stop']:.4f} ({cond_distance:+.4f}) {cond_status}")
            
            if status['hard_stop']:
                hard_distance = (price - status['hard_stop']) if is_long else (status['hard_stop'] - price)
                hard_status = "🟢 SAFE" if hard_distance > 0 else "🚨 DANGER"
                print(f"   Hard Stop:   ${status['hard_stop']:.4f} ({hard_distance:+.4f}) {hard_status}")
        else:
            print(f"\n⚪ NO POSITION | Realized P&L: ${status['realized_pnl']:.2f}")
            print(f"📍 Ready to trade on next {status['trend']} signal")
        
        # Orders & Capital
        current_position_value = abs(status['position']) * status['avg_entry'] if status['avg_entry'] > 0 else 0
        
        # Account for spot positions in available capital calculation
        spot_position_usdt = 0.0
        if self.delta_tracker:
            delta_status = self.delta_tracker.get_status(price)
            spot_position_usdt = abs(delta_status.get('spot_position_usdt', 0.0))
        
        total_exposure = current_position_value + spot_position_usdt
        available_capital = self.strategy.max_allocation_usdt - total_exposure
        
        print(f"\n📋 ORDERS & CAPITAL:")
        tp_method = "Limit Orders" if self.strategy.tp_execution_method == 'limit' else "Market Execution"
        print(f"   Active Orders: {status['active_orders']} | TP Orders: {len(self.strategy.tp_orders)} ({tp_method}) | Available Capital: ${available_capital:.0f}")
        if spot_position_usdt > 0:
            print(f"   Total Exposure: ${total_exposure:.0f} / ${self.strategy.max_allocation_usdt:.0f} (Futures: ${current_position_value:.0f} + Spot: ${spot_position_usdt:.0f})")
        if available_capital > 0:
            ema9_allocation = available_capital * 0.25  # 25% for EMA9
            ema21_allocation = available_capital * 0.75  # 75% for EMA21
            print(f"   EMA9: ${ema9_allocation:.0f} | EMA21: ${ema21_allocation:.0f}")
            
        # Show active TP orders
        if self.strategy.tp_orders:
            print(f"\n🎯 ACTIVE TAKE PROFIT ORDERS:")
            for tp_name, order_id in self.strategy.tp_orders.items():
                tp_config = self.strategy.take_profit_levels.get(tp_name, {})
                if status['position'] > 0:  # Long
                    tp_price = status['avg_entry'] * (1 + tp_config.get('pct', 0) / 100)
                else:  # Short
                    tp_price = status['avg_entry'] * (1 - tp_config.get('pct', 0) / 100)
                print(f"   {tp_name.upper()}: ${tp_price:.4f} ({tp_config.get('exit_pct', 0)}%) - Order: {order_id[:8]}...")
        
        # Delta Status (compact)
        if self.delta_tracker:
            delta_status = self.delta_tracker.get_status(price)
            delta_icon = "⚠️" if delta_status['needs_rebalance'] else "🔴" if delta_status['is_diverging'] else "✅"
            print(f"\n📊 DELTA: {delta_icon} Total: ${delta_status['total_delta']:+,.0f} | Target: ${delta_status['desired_delta']:+,.0f} | Div: ${delta_status['delta_divergence']:+,.0f}")
            if delta_status['is_diverging']:
                duration = delta_status['divergence_duration'] or 0
                print(f"   Futures: ${delta_status['futures_position_usdt']:+,.0f} | Spot: ${delta_status['spot_position_usdt']:+,.0f} | Duration: {duration:.0f}s")
        
        # Connection Status (compact)
        ws_status = self.ws_manager.get_connection_status()
        ws_icon = "✅" if ws_status['healthy'] else "❌"
        print(f"\n📡 CONNECTION: {ws_icon} WebSocket ({'Healthy' if ws_status['healthy'] else 'Unhealthy'}) | Last update: {ws_status['seconds_since_last_message']:.0f}s ago")
        
        print(f"{'='*100}\n")
    
    def print_tp_levels(self, price: float, status: dict):
        """Print detailed take profit levels"""
        if status['position'] == 0 or status['avg_entry'] == 0:
            return
            
        print(f"\n🎯 TAKE PROFIT LEVELS:")
        
        # Calculate TP levels based on entry price
        entry_price = status['avg_entry']
        position_size = abs(status['position'])
        is_long = status['position'] > 0
        
        for tp_name, tp_config in self.strategy.take_profit_levels.items():
            if tp_name in status['tp_levels_hit']:
                print(f"   ✅ {tp_name.upper()}: HIT (exited {tp_config['exit_pct']}%)")
                continue
                
            # Calculate target price
            if is_long:
                target_price = entry_price * (1 + tp_config['pct'] / 100)
            else:
                target_price = entry_price * (1 - tp_config['pct'] / 100)
            
            # Calculate distance
            if is_long:
                distance = target_price - price
                distance_pct = (target_price - price) / price * 100
            else:
                distance = price - target_price
                distance_pct = (price - target_price) / price * 100
            
            # Calculate remaining position after this TP
            remaining_pct = 100
            for hit_tp in status['tp_levels_hit']:
                if hit_tp in self.strategy.take_profit_levels:
                    remaining_pct -= self.strategy.take_profit_levels[hit_tp]['exit_pct']
            
            exit_qty = position_size * (tp_config['exit_pct'] / 100)
            
            status_icon = "🟢" if distance > 0 else "🔴"
            print(f"   {status_icon} {tp_name.upper()}: ${target_price:.4f} ({tp_config['pct']}%) | Distance: ${distance:.4f} ({distance_pct:+.2f}%) | Exit: {exit_qty:.3f} ({tp_config['exit_pct']}%)")
    
    def print_stop_levels(self, price: float, status: dict):
        """Print detailed stop loss levels"""
        if status['position'] == 0:
            return
            
        print(f"\n🛑 STOP LOSS LEVELS:")
        
        is_long = status['position'] > 0
        
        # Conditional stop (trailing stop)
        if status['trailing_stop']:
            cond_stop = status['trailing_stop']
            if is_long:
                distance = price - cond_stop
                distance_pct = (price - cond_stop) / price * 100
            else:
                distance = cond_stop - price
                distance_pct = (cond_stop - price) / price * 100
            
            trigger_status = "⚠️ TRIGGERED" if status['conditional_stop_triggered'] else "🟢 SAFE"
            print(f"   {trigger_status} Conditional Stop: ${cond_stop:.4f} | Distance: ${distance:.4f} ({distance_pct:+.2f}%)")
        
        # Hard stop
        if status['hard_stop']:
            hard_stop = status['hard_stop']
            if is_long:
                distance = price - hard_stop
                distance_pct = (price - hard_stop) / price * 100
            else:
                distance = hard_stop - price
                distance_pct = (hard_stop - price) / price * 100
            
            status_icon = "🟢" if distance > 0 else "🔴"
            print(f"   {status_icon} Hard Stop: ${hard_stop:.4f} | Distance: ${distance:.4f} ({distance_pct:+.2f}%)")
    
    def print_quick_status(self, price: float, status: dict):
        """Print quick status with key info at a glance"""
        ema_ratio = status['ema_fast'] / status['ema_slow']
        trend_icon = "🟢" if status['trend'] == "UPTREND" else "🔴" if status['trend'] == "DOWNTREND" else "⚪"
        
        if status['position'] != 0:
            # Position status
            side = "LONG" if status['position'] > 0 else "SHORT"
            side_icon = "🟢" if status['position'] > 0 else "🔴"
            pnl_icon = "💚" if status['current_pnl_usdt'] >= 0 else "💸"
            
            # Get next TP
            next_tp_info = ""
            for tp_name, tp_config in self.strategy.take_profit_levels.items():
                if tp_name not in status['tp_levels_hit']:
                    if status['position'] > 0:
                        next_tp = status['avg_entry'] * (1 + tp_config['pct'] / 100)
                        distance = next_tp - price
                    else:
                        next_tp = status['avg_entry'] * (1 - tp_config['pct'] / 100)
                        distance = price - next_tp
                    next_tp_info = f" | 🎯 {tp_name.upper()}: ${distance:+.4f}"
                    break
            
            pause_text = " ⏸️" if self.paused else ""
            print(f"{side_icon} {side} {abs(status['position']):.1f} | ${price:.4f} | {trend_icon} {status['trend']} (R:{ema_ratio:.4f}) | "
                  f"{pnl_icon} ${status['current_pnl_usdt']:+.2f} ({status['current_pnl_pct']:+.1f}%){next_tp_info}{pause_text}")
        else:
            # No position status
            current_position_value = abs(status['position']) * status['avg_entry'] if status['avg_entry'] > 0 else 0
            
            # Account for spot positions in available capital calculation
            spot_position_usdt = 0.0
            if self.delta_tracker:
                delta_status = self.delta_tracker.get_status(price)
                spot_position_usdt = abs(delta_status.get('spot_position_usdt', 0.0))
            
            total_exposure = current_position_value + spot_position_usdt
            available_capital = self.strategy.max_allocation_usdt - total_exposure
            
            pause_text = " ⏸️" if self.paused else ""
            spot_text = f" | Spot: ${spot_position_usdt:.0f}" if spot_position_usdt > 0 else ""
            print(f"⚪ FLAT | ${price:.4f} | {trend_icon} {status['trend']} (R:{ema_ratio:.4f}) | "
                  f"P&L: ${status['realized_pnl']:.2f} | Capital: ${available_capital:.0f}{spot_text} | Orders: {status['active_orders']}{pause_text}")
    
    def print_candle_levels(self, price: float, status: dict):
        """Print key levels on new candle for position debugging"""
        if status['position'] == 0:
            return
            
        print(f"   📊 CANDLE LEVELS - Price: ${price:.4f}")
        
        # Show next TP
        entry = status['avg_entry']
        is_long = status['position'] > 0
        
        for tp_name, tp_config in self.strategy.take_profit_levels.items():
            if tp_name not in status['tp_levels_hit']:
                if is_long:
                    target_price = entry * (1 + tp_config['pct'] / 100)
                    distance = target_price - price
                else:
                    target_price = entry * (1 - tp_config['pct'] / 100)
                    distance = price - target_price
                
                print(f"      🎯 Next TP: {tp_name.upper()} @ ${target_price:.4f} ({distance:+.4f})")
                break
        
        # Show stops
        if status['trailing_stop']:
            cond_stop = status['trailing_stop']
            if is_long:
                distance = price - cond_stop
            else:
                distance = cond_stop - price
            trigger_status = "⚠️" if status['conditional_stop_triggered'] else "🛑"
            print(f"      {trigger_status} Conditional Stop: ${cond_stop:.4f} ({distance:+.4f})")
        
        if status['hard_stop']:
            hard_stop = status['hard_stop']
            if is_long:
                distance = price - hard_stop
            else:
                distance = hard_stop - price
            print(f"      🚨 Hard Stop: ${hard_stop:.4f} ({distance:+.4f})")
    
    def calculate_position_breakdown(self, status: dict, current_price: float) -> dict:
        """Calculate how much was invested from each EMA level based on trade history"""
        if status['position'] == 0:
            return {'ema9_invested': 0, 'ema21_invested': 0}
        
        # Get recent trades to determine EMA breakdown
        recent_trades = [trade for trade in self.strategy.trades[-10:]]  # Last 10 trades
        
        ema9_invested = 0
        ema21_invested = 0
        
        # Look for entry trades (BUY/SELL actions)
        for trade in recent_trades:
            if trade.action in ['BUY', 'SELL'] and 'EMA' in trade.reason:
                # Extract EMA type from reason (e.g., "EMA9_ENTRY" -> "9")
                if 'EMA9' in trade.reason:
                    ema9_invested += trade.quantity * trade.price
                elif 'EMA21' in trade.reason:
                    ema21_invested += trade.quantity * trade.price
        
        # If we can't determine from trade history, use proportional allocation
        if ema9_invested == 0 and ema21_invested == 0:
            total_position_value = abs(status['position']) * current_price
            # Use the original allocation percentages from config
            ema9_pct = self.strategy.config.get('ema_allocations', {}).get('ema9_pct', 50) / 100
            ema21_pct = self.strategy.config.get('ema_allocations', {}).get('ema21_pct', 50) / 100
            
            ema9_invested = total_position_value * ema9_pct
            ema21_invested = total_position_value * ema21_pct
        
        return {
            'ema9_invested': ema9_invested,
            'ema21_invested': ema21_invested
        }
    
    def check_delta_status(self, price: float):
        """Check delta status and handle rebalancing if needed"""
        if not self.delta_tracker:
            return
            
        try:
            # Get current delta status
            delta_status = self.delta_tracker.sync_positions(price)
            
            # Print detailed status if diverging or needs rebalance
            if delta_status['is_diverging'] or delta_status['needs_rebalance']:
                self.delta_tracker.print_delta_status(delta_status)
                
                # If rebalancing is needed, calculate and execute adjustment
                if delta_status['needs_rebalance']:
                    adjustment = self.delta_tracker.calculate_futures_adjustment(delta_status, price)
                    
                    if adjustment['adjustment_needed']:
                        print(f"""
        🚨 DELTA REBALANCE NEEDED
        Current Divergence: ${delta_status['delta_divergence']:+,.0f}
        Required Adjustment: {adjustment['adjustment_side']} ${abs(adjustment['adjustment_usdt']):,.0f}
        Quantity: {adjustment['adjustment_quantity']:.3f}
        Reason: {adjustment['reason']}
        
        ⚡ EXECUTING AUTOMATIC REBALANCING...
        """)
                        
                        # Execute the rebalancing trade
                        self.execute_delta_rebalance(adjustment, price)
            
        except Exception as e:
            print(f"⚠️ Error checking delta status: {e}")
            import traceback
            traceback.print_exc()
    
    def execute_delta_rebalance(self, adjustment: dict, price: float):
        """Execute a delta rebalancing trade"""
        try:
            # Format quantity according to instrument specs
            qty = self.strategy.format_quantity(adjustment['adjustment_quantity'])
            
            # Ensure quantity is above minimum
            if qty < self.strategy.min_order_qty:
                print(f"⚠️ Rebalance quantity {qty:.3f} below minimum {self.strategy.min_order_qty}, skipping")
                return
            
            # Execute market order for rebalancing
            # Use reduceOnly=False since this is a delta adjustment, not a position exit
            response = self.strategy.client.place_market_order(
                category=self.strategy.category,
                symbol=self.strategy.symbol,
                side=adjustment['adjustment_side'],
                qty=qty,
                reduce_only=False,  # Delta rebalancing may increase position
                verbose=False
            )
            
            if response and response.get('retCode') == 0:
                print(f"""
        ✅ DELTA REBALANCE EXECUTED
        Side: {adjustment['adjustment_side']}
        Quantity: {qty:.3f}
        Estimated Price: ${price:.4f}
        USDT Value: ${abs(adjustment['adjustment_usdt']):,.0f}
        """)
                
                # Sync position immediately after rebalancing
                self.strategy.sync_position()
                
                # Log the rebalancing trade
                self.strategy.log_trade(
                    action=f"DELTA_REBALANCE_{adjustment['adjustment_side'].upper()}",
                    price=price,
                    quantity=qty,
                    reason="DELTA_REBALANCING"
                )
                
            else:
                print(f"❌ Failed to execute delta rebalance: {response}")
                
        except Exception as e:
            print(f"❌ Error executing delta rebalance: {e}")
            import traceback
            traceback.print_exc()
    
    def handle_filled_order(self, order_id: str):
        """Handle a filled limit order"""
        if order_id not in self.strategy.limit_orders:
            return
            
        order_info = self.strategy.limit_orders[order_id]
        
        # Update per-EMA position tracking
        usdt_amount = order_info.get('usdt_amount', 0)
        if order_info['ema'] == '9':
            self.strategy.ema9_position_value += usdt_amount
            print(f"""
        ✅ ORDER FILLED - EMA9
        Price: ${order_info['price']:.4f}
        Side: {order_info['side']}
        USDT: ${usdt_amount:.0f}
        EMA9 locked: ${self.strategy.ema9_position_value:.0f}
        """)
        elif order_info['ema'] == '21':
            self.strategy.ema21_position_value += usdt_amount
            print(f"""
        ✅ ORDER FILLED - EMA21
        Price: ${order_info['price']:.4f}
        Side: {order_info['side']}
        USDT: ${usdt_amount:.0f}
        EMA21 locked: ${self.strategy.ema21_position_value:.0f}
        """)
        
        # Update strategy state
        del self.strategy.limit_orders[order_id]
        self.strategy.last_entry_time = time.time()
        self.strategy.sync_position()  # Re-sync to get new position
        
    def handle_filled_tp_order(self, tp_name: str, order_id: str):
        """Handle a filled TP order"""
        if tp_name not in self.strategy.tp_orders:
            return
            
        # Mark this TP level as hit
        self.strategy.tp_levels_hit.add(tp_name)
        
        # Remove from active TP orders
        del self.strategy.tp_orders[tp_name]
        
        # Sync position to get updated state
        self.strategy.sync_position()
        
        # Get current price for logging
        current_price = self.ws_manager.get_latest_price() if self.ws_manager else 0
        
        print(f"""
        ✅ TAKE PROFIT {tp_name.upper()} FILLED
        Order ID: {order_id[:8]}...
        Current Price: ${current_price:.4f}
        Position: {self.strategy.position:.3f}
        Remaining TP Levels: {len(self.strategy.tp_orders)}
        """)
        
        # Place new TP orders for remaining levels if position still exists
        if self.strategy.position != 0:
            self.strategy.place_tp_limit_orders(current_price)

    def print_trade_summary(self):
        """Print summary of all trades and their reasons every 10 minutes"""
        if not self.strategy.trades:
            print(f"\n📋 TRADE SUMMARY - {datetime.now().strftime('%H:%M:%S')} - No trades yet")
            return
            
        print(f"\n{'='*100}")
        print(f"📋 TRADE SUMMARY - {datetime.now().strftime('%H:%M:%S')} - {len(self.strategy.trades)} trades")
        print(f"{'='*100}")
        
        # Group trades by reason
        trades_by_reason = {}
        total_pnl = 0
        
        for trade in self.strategy.trades:
            reason = trade.reason
            if reason not in trades_by_reason:
                trades_by_reason[reason] = []
            trades_by_reason[reason].append(trade)
            if trade.pnl:
                total_pnl += trade.pnl
        
        # Print summary by reason
        for reason, trades in trades_by_reason.items():
            count = len(trades)
            reason_pnl = sum(t.pnl for t in trades if t.pnl)
            pnl_icon = "💚" if reason_pnl >= 0 else "💸"
            
            print(f"\n📊 {reason.upper()}: {count} trades | {pnl_icon} P&L: ${reason_pnl:+.2f}")
            
            # Show recent trades for this reason (last 3)
            recent_trades = trades[-3:] if len(trades) > 3 else trades
            for trade in recent_trades:
                side_icon = "🟢" if trade.position_after > 0 else "🔴" if trade.position_after < 0 else "⚪"
                pnl_str = f" | P&L: ${trade.pnl:+.2f}" if trade.pnl else ""
                print(f"   {side_icon} {datetime.fromtimestamp(trade.timestamp).strftime('%H:%M:%S')} | {trade.action} {abs(trade.position_after):.3f} @ ${trade.price:.4f}{pnl_str}")
            
            if len(trades) > 3:
                print(f"   ... and {len(trades) - 3} more {reason.lower()} trades")
        
        print(f"\n💰 TOTAL REALIZED P&L: ${total_pnl:+.2f}")
        print(f"{'='*100}\n")

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Simplified EMA Trading Bot')
    parser.add_argument('--symbol', '-s', type=str, help='Trading symbol (e.g., BTCUSDT, ETHUSDT)')
    parser.add_argument('--config', '-c', type=str, default='config.yaml', help='Config file path')
    
    args = parser.parse_args()
    
    runner = SimpleRunner()
    runner.run(config_file=args.config, symbol=args.symbol)
