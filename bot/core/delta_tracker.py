# bot/core/delta_tracker.py
"""
Delta Tracker - Manages position delta across futures and spot
"""
import time
from typing import Optional, Dict
from datetime import datetime

class DeltaTracker:
    """
    Tracks and manages delta exposure across futures and spot positions.
    Delta is calculated as: futures_position_usdt + spot_position_usdt
    Positive delta = net long exposure, Negative delta = net short exposure
    """
    
    def __init__(self, client, symbol: str, config: dict):
        self.client = client
        self.symbol = symbol
        self.config = config
        
        # Extract base symbol from trading pair (e.g., "AVNT" from "AVNTUSDT")
        self.base_symbol = symbol.replace("USDT", "") if symbol.endswith("USDT") else symbol
        
        # Delta management configuration
        delta_config = config.get('delta_management', {})
        self.desired_delta_usdt = delta_config.get('desired_delta_usdt', 0)
        self.divergence_threshold_usdt = delta_config.get('divergence_threshold_usdt', 1000)
        self.divergence_timeout_seconds = delta_config.get('divergence_timeout_seconds', 360)
        
        # State tracking
        self.last_futures_position_usdt = 0.0
        self.last_spot_position_usdt = 0.0
        self.last_total_delta = 0.0
        self.last_sync_time = 0.0
        
        # Divergence tracking
        self.divergence_start_time = None
        self.is_diverging = False
        
        print(f"""
        ========== DELTA TRACKER INITIALIZED ==========
        Symbol: {self.symbol}
        Base Symbol: {self.base_symbol}
        Desired Delta: ${self.desired_delta_usdt:,.0f}
        Divergence Threshold: ${self.divergence_threshold_usdt:,.0f}
        Divergence Timeout: {self.divergence_timeout_seconds}s
        ===============================================
        """)
    
    def sync_positions(self, current_price: Optional[float] = None) -> Dict:
        """
        Sync futures and spot positions and calculate current delta.
        Returns dict with position information.
        """
        current_time = time.time()
        
        # Get futures position (from existing strategy position sync)
        futures_position_usdt = self._get_futures_position_usdt(current_price)
        
        # Get spot position
        spot_position_usdt = self._get_spot_position_usdt()
        
        # Calculate total delta
        total_delta = futures_position_usdt + spot_position_usdt
        
        # Calculate divergence from desired delta
        delta_divergence = total_delta - self.desired_delta_usdt
        
        # Update state
        self.last_futures_position_usdt = futures_position_usdt
        self.last_spot_position_usdt = spot_position_usdt
        self.last_total_delta = total_delta
        self.last_sync_time = current_time
        
        # Track divergence timing
        self._update_divergence_tracking(abs(delta_divergence), current_time)
        
        return {
            'futures_position_usdt': futures_position_usdt,
            'spot_position_usdt': spot_position_usdt,
            'total_delta': total_delta,
            'desired_delta': self.desired_delta_usdt,
            'delta_divergence': delta_divergence,
            'divergence_magnitude': abs(delta_divergence),
            'is_diverging': self.is_diverging,
            'divergence_duration': self._get_divergence_duration(current_time),
            'needs_rebalance': self._needs_rebalance(delta_divergence, current_time),
            'sync_time': current_time
        }
    
    def _get_futures_position_usdt(self, current_price: Optional[float] = None) -> float:
        """Get the current futures position value in USDT"""
        try:
            response = self.client.get_positions(
                category='linear',
                symbol=self.symbol
            )
            
            if response and response.get('retCode') == 0:
                positions = response['result']['list']
                if positions:
                    pos = positions[0]
                    size = float(pos.get('size', 0))
                    side = pos.get('side', 'None')
                    
                    # Convert to signed position (positive = long, negative = short)
                    position_size = size if side == 'Buy' else -size if side == 'Sell' else 0
                    
                    if position_size == 0:
                        return 0.0
                    
                    # Calculate USDT value
                    if current_price:
                        position_usdt = position_size * current_price
                    else:
                        # Use mark price from position data
                        mark_price = float(pos.get('markPrice', 0))
                        position_usdt = position_size * mark_price
                    
                    return position_usdt
            
            return 0.0
            
        except Exception as e:
            print(f"⚠️ Error getting futures position: {e}")
            return 0.0
    
    def _get_spot_position_usdt(self) -> float:
        """Get the current spot position value in USDT"""
        try:
            spot_value = self.client.get_spot_position_value(self.base_symbol, "USDT")
            
            # Debug output for spot position detection
            if spot_value > 0:
                print(f"📊 Spot position detected: {self.base_symbol} = ${spot_value:+,.2f}")
            else:
                # Let's also check the raw balance to see what's happening
                balance_response = self.client.get_coin_balance(self.base_symbol)
                if balance_response and balance_response.get('retCode') == 0:
                    coin_list = balance_response.get('result', {}).get('list', [])
                    total_balance = 0.0
                    for account in coin_list:
                        coins = account.get('coin', [])
                        for coin_info in coins:
                            if coin_info.get('coin') == self.base_symbol:
                                wallet_balance_str = coin_info.get('walletBalance', '0')
                                if wallet_balance_str and wallet_balance_str != '':
                                    balance = float(wallet_balance_str)
                                    total_balance += balance
                                    if balance > 0:
                                        print(f"🔍 Found {self.base_symbol} balance: {balance:.3f} (account: {account.get('accountType', 'unknown')})")
                    
                    if total_balance > 0:
                        print(f"⚠️ Spot position calculation issue: Found {total_balance:.3f} {self.base_symbol} but value calculation returned ${spot_value:.2f}")
                else:
                    print(f"🔍 No spot balance found for {self.base_symbol}")
            
            return spot_value
        except Exception as e:
            print(f"⚠️ Error getting spot position: {e}")
            import traceback
            traceback.print_exc()
            return 0.0
    
    def _update_divergence_tracking(self, divergence_magnitude: float, current_time: float):
        """Update divergence tracking state"""
        is_above_threshold = divergence_magnitude > self.divergence_threshold_usdt
        
        if is_above_threshold and not self.is_diverging:
            # Start tracking divergence
            self.is_diverging = True
            self.divergence_start_time = current_time
        elif not is_above_threshold and self.is_diverging:
            # Stop tracking divergence
            self.is_diverging = False
            self.divergence_start_time = None
    
    def _get_divergence_duration(self, current_time: float) -> Optional[float]:
        """Get how long we've been diverging"""
        if not self.is_diverging or self.divergence_start_time is None:
            return None
        return current_time - self.divergence_start_time
    
    def _needs_rebalance(self, delta_divergence: float, current_time: float) -> bool:
        """
        Determine if rebalancing is needed based on:
        1. Divergence magnitude exceeding threshold
        2. Divergence duration exceeding timeout
        """
        divergence_magnitude = abs(delta_divergence)
        
        # Check if we're above threshold
        if divergence_magnitude <= self.divergence_threshold_usdt:
            return False
        
        # Check if we've been diverging too long
        divergence_duration = self._get_divergence_duration(current_time)
        if divergence_duration and divergence_duration > self.divergence_timeout_seconds:
            return True
        
        return False
    
    def calculate_futures_adjustment(self, delta_status: Dict, current_price: float) -> Dict:
        """
        Calculate how much to adjust futures position to reach desired delta.
        Returns dict with adjustment details.
        """
        if not delta_status['needs_rebalance']:
            return {
                'adjustment_needed': False,
                'adjustment_usdt': 0.0,
                'adjustment_quantity': 0.0,
                'adjustment_side': None,
                'reason': 'No rebalance needed'
            }
        
        # Calculate required adjustment
        delta_divergence = delta_status['delta_divergence']
        
        # To correct the divergence, we need to adjust futures by the negative of divergence
        # If we're too long (positive divergence), we need to reduce futures position (negative adjustment)
        # If we're too short (negative divergence), we need to increase futures position (positive adjustment)
        adjustment_usdt = -delta_divergence
        
        # Convert to quantity
        adjustment_quantity = abs(adjustment_usdt) / current_price
        
        # Determine side
        if adjustment_usdt > 0:
            side = "Buy"  # Need to increase long position
        else:
            side = "Sell"  # Need to decrease long position or increase short position
        
        return {
            'adjustment_needed': True,
            'adjustment_usdt': adjustment_usdt,
            'adjustment_quantity': adjustment_quantity,
            'adjustment_side': side,
            'current_divergence': delta_divergence,
            'target_delta': self.desired_delta_usdt,
            'reason': f'Delta divergence: ${delta_divergence:+,.0f} (threshold: ${self.divergence_threshold_usdt:,.0f})'
        }
    
    def get_status(self, current_price: Optional[float] = None) -> Dict:
        """Get current delta status for monitoring"""
        if current_price:
            # Refresh positions if price is provided
            return self.sync_positions(current_price)
        else:
            # Return last known status
            current_time = time.time()
            delta_divergence = self.last_total_delta - self.desired_delta_usdt
            
            return {
                'futures_position_usdt': self.last_futures_position_usdt,
                'spot_position_usdt': self.last_spot_position_usdt,
                'total_delta': self.last_total_delta,
                'desired_delta': self.desired_delta_usdt,
                'delta_divergence': delta_divergence,
                'divergence_magnitude': abs(delta_divergence),
                'is_diverging': self.is_diverging,
                'divergence_duration': self._get_divergence_duration(current_time),
                'needs_rebalance': self._needs_rebalance(delta_divergence, current_time),
                'sync_time': self.last_sync_time,
                'last_sync_age': current_time - self.last_sync_time if self.last_sync_time > 0 else None
            }
    
    def print_delta_status(self, status: Dict):
        """Print detailed delta status"""
        print(f"\n{'='*80}")
        print(f"📊 DELTA STATUS - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*80}")
        
        # Position breakdown
        futures_icon = "🟢" if status['futures_position_usdt'] > 0 else "🔴" if status['futures_position_usdt'] < 0 else "⚪"
        spot_icon = "🟢" if status['spot_position_usdt'] > 0 else "⚪"
        
        print(f"{futures_icon} FUTURES: ${status['futures_position_usdt']:+,.0f}")
        print(f"{spot_icon} SPOT:    ${status['spot_position_usdt']:+,.0f}")
        print(f"{'='*20}")
        
        # Total delta
        delta_icon = "🟢" if status['total_delta'] > 0 else "🔴" if status['total_delta'] < 0 else "⚪"
        print(f"{delta_icon} TOTAL:   ${status['total_delta']:+,.0f}")
        print(f"🎯 TARGET:  ${status['desired_delta']:+,.0f}")
        
        # Divergence info
        divergence = status['delta_divergence']
        divergence_icon = "⚠️" if status['is_diverging'] else "✅"
        print(f"{divergence_icon} DIVERGENCE: ${divergence:+,.0f}")
        
        if status['is_diverging']:
            duration = status['divergence_duration']
            timeout = self.divergence_timeout_seconds
            print(f"⏱️ DURATION: {duration:.0f}s / {timeout}s")
            
            if status['needs_rebalance']:
                print(f"🚨 REBALANCE NEEDED!")
            else:
                remaining = timeout - duration if duration else timeout
                print(f"⏳ TIME REMAINING: {remaining:.0f}s")
        
        print(f"{'='*80}\n")
