# position_manager.py
"""
Position and order management module
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import time
import logging

logger = logging.getLogger(__name__)

class OrderType(Enum):
    MARKET = "Market"
    LIMIT = "Limit"
    STOP = "Stop"
    STOP_LIMIT = "StopLimit"

class OrderStatus(Enum):
    PENDING = "Pending"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"
    PARTIAL = "PartiallyFilled"

@dataclass
class Position:
    """Enhanced position tracking"""
    symbol: str
    side: str  # 'long' or 'short'
    entry_price: float
    quantity: float
    entry_time: float
    ema_type: str  # '9', '21', or 'inventory'
    
    # Additional tracking
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    peak_unrealized_pnl: float = 0.0
    time_in_position: float = 0.0
    
    # Risk metrics
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_stop_distance: Optional[float] = None
    
    def update_pnl(self, current_price: float):
        """Update P&L calculations"""
        if self.side == 'long':
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
            
        # Track peak P&L for drawdown calculation
        if self.unrealized_pnl > self.peak_unrealized_pnl:
            self.peak_unrealized_pnl = self.unrealized_pnl
            
        # Update time in position
        self.time_in_position = time.time() - self.entry_time

@dataclass
class Order:
    """Enhanced order tracking"""
    order_id: str
    symbol: str
    side: str  # 'Buy' or 'Sell'
    order_type: OrderType
    quantity: float
    price: Optional[float]
    status: OrderStatus = OrderStatus.PENDING
    
    # Execution details
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    commission: float = 0.0
    
    # Timing
    created_time: float = field(default_factory=time.time)
    updated_time: float = field(default_factory=time.time)
    
    # Metadata
    ema_type: Optional[str] = None
    position_size_usdt: Optional[float] = None
    reduce_only: bool = False

class PositionManager:
    """
    Comprehensive position and order management system
    """
    
    def __init__(self, client, symbol: str, category: str = 'spot'):
        self.client = client
        self.symbol = symbol
        self.category = category
        
        # Position tracking
        self.positions: Dict[str, Position] = {}  # key: position_id
        self.position_counter = 0
        
        # Order tracking
        self.active_orders: Dict[str, Order] = {}  # key: order_id
        self.order_history: List[Order] = []
        
        # Aggregate metrics
        self.total_volume_traded = 0.0
        self.total_commission_paid = 0.0
        
    def add_position(self, side: str, entry_price: float, 
                    quantity: float, ema_type: str,
                    stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None) -> str:
        """Add a new position"""
        position_id = f"pos_{self.position_counter}"
        self.position_counter += 1
        
        position = Position(
            symbol=self.symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=time.time(),
            ema_type=ema_type,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        self.positions[position_id] = position
        logger.info(f"Added position {position_id}: {side} {quantity} @ ${entry_price:.4f}")
        
        return position_id
        
    def update_position_stops(self, position_id: str, 
                            stop_loss: Optional[float] = None,
                            take_profit: Optional[float] = None):
        """Update position stop levels"""
        if position_id not in self.positions:
            return
            
        position = self.positions[position_id]
        
        if stop_loss is not None:
            position.stop_loss = stop_loss
            
        if take_profit is not None:
            position.take_profit = take_profit
            
    def close_position(self, position_id: str, exit_price: float, 
                      exit_quantity: Optional[float] = None) -> float:
        """Close a position and return realized P&L"""
        if position_id not in self.positions:
            logger.warning(f"Position {position_id} not found")
            return 0.0
            
        position = self.positions[position_id]
        
        # Use full position quantity if not specified
        if exit_quantity is None:
            exit_quantity = position.quantity
        else:
            exit_quantity = min(exit_quantity, position.quantity)
            
        # Calculate P&L
        if position.side == 'long':
            pnl = (exit_price - position.entry_price) * exit_quantity
        else:
            pnl = (position.entry_price - exit_price) * exit_quantity
            
        position.realized_pnl += pnl
        position.quantity -= exit_quantity
        
        # Remove position if fully closed
        if position.quantity <= 0:
            del self.positions[position_id]
            logger.info(f"Closed position {position_id}: P&L ${pnl:.2f}")
        else:
            logger.info(f"Partially closed position {position_id}: P&L ${pnl:.2f}")
            
        return pnl
        
    def get_total_exposure(self) -> float:
        """Calculate total market exposure"""
        total = 0.0
        for position in self.positions.values():
            total += position.quantity * position.entry_price
        return total
        
    def get_net_position(self) -> float:
        """Calculate net position (long - short)"""
        long_qty = sum(p.quantity for p in self.positions.values() if p.side == 'long')
        short_qty = sum(p.quantity for p in self.positions.values() if p.side == 'short')
        return long_qty - short_qty
        
    def update_all_pnl(self, current_price: float):
        """Update P&L for all positions"""
        for position in self.positions.values():
            position.update_pnl(current_price)
            
    def check_stop_levels(self, current_price: float) -> List[str]:
        """Check if any positions hit stop levels"""
        positions_to_close = []
        
        for position_id, position in self.positions.items():
            # Check stop loss
            if position.stop_loss:
                if position.side == 'long' and current_price <= position.stop_loss:
                    positions_to_close.append(position_id)
                elif position.side == 'short' and current_price >= position.stop_loss:
                    positions_to_close.append(position_id)
                    
            # Check take profit
            if position.take_profit:
                if position.side == 'long' and current_price >= position.take_profit:
                    positions_to_close.append(position_id)
                elif position.side == 'short' and current_price <= position.take_profit:
                    positions_to_close.append(position_id)
                    
        return positions_to_close
        
    def update_trailing_stops(self, current_price: float, trail_distance_pct: float):
        """Update trailing stops for all positions"""
        for position in self.positions.values():
            if position.side == 'long':
                # For longs, stop trails upward
                new_stop = current_price * (1 - trail_distance_pct / 100)
                if position.stop_loss is None or new_stop > position.stop_loss:
                    position.stop_loss = new_stop
                    position.trailing_stop_distance = trail_distance_pct
                    
            else:  # short
                # For shorts, stop trails downward
                new_stop = current_price * (1 + trail_distance_pct / 100)
                if position.stop_loss is None or new_stop < position.stop_loss:
                    position.stop_loss = new_stop
                    position.trailing_stop_distance = trail_distance_pct
                    
    # Order Management Methods
    
    def place_order(self, side: str, order_type: OrderType, 
                   quantity: float, price: Optional[float] = None,
                   reduce_only: bool = False,
                   ema_type: Optional[str] = None) -> Optional[str]:
        """Place an order through the exchange"""
        try:
            # Format order parameters
            from bot.utils import format_quantity, format_price
            
            qty_formatted = format_quantity(quantity, 3)  # Adjust precision as needed
            
            # Build order request
            order_params = {
                'category': self.category,
                'symbol': self.symbol,
                'side': side,
                'orderType': order_type.value,
                'qty': qty_formatted
            }
            
            if price and order_type != OrderType.MARKET:
                order_params['price'] = format_price(price, 2)  # Adjust precision
                
            if reduce_only:
                order_params['reduceOnly'] = True
                
            # Place order through client
            response = self.client.place_order(**order_params)
            
            if response and response.get('retCode') == 0:
                order_id = response['result']['orderId']
                
                # Track order
                order = Order(
                    order_id=order_id,
                    symbol=self.symbol,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    ema_type=ema_type,
                    reduce_only=reduce_only
                )
                
                self.active_orders[order_id] = order
                logger.info(f"Order placed: {order_id} - {side} {quantity} @ "
                          f"{'Market' if order_type == OrderType.MARKET else price}")
                
                return order_id
            else:
                logger.error(f"Order failed: {response.get('retMsg', 'Unknown error')}")
                return None
                
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None
            
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an active order"""
        try:
            response = self.client.cancel_order(
                category=self.category,
                symbol=self.symbol,
                orderId=order_id
            )
            
            if response and response.get('retCode') in [0, 110001]:
                # 0 = success, 110001 = already filled/cancelled
                if order_id in self.active_orders:
                    order = self.active_orders[order_id]
                    order.status = OrderStatus.CANCELLED
                    order.updated_time = time.time()
                    
                    # Move to history
                    self.order_history.append(order)
                    del self.active_orders[order_id]
                    
                logger.info(f"Order cancelled: {order_id}")
                return True
            else:
                logger.error(f"Failed to cancel order: {response.get('retMsg', 'Unknown')}")
                return False
                
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False
            
    def update_order_status(self, order_id: str):
        """Update order status from exchange"""
        try:
            response = self.client.get_open_orders(
                category=self.category,
                symbol=self.symbol,
                orderId=order_id
            )
            
            if response and response.get('retCode') == 0:
                orders = response['result']['list']
                
                if not orders and order_id in self.active_orders:
                    # Order not in open orders - check if filled
                    self._check_order_execution(order_id)
                elif orders:
                    # Update order details
                    order_data = orders[0]
                    if order_id in self.active_orders:
                        order = self.active_orders[order_id]
                        order.filled_qty = float(order_data.get('cumExecQty', 0))
                        order.avg_fill_price = float(order_data.get('avgPrice', 0))
                        order.updated_time = time.time()
                        
        except Exception as e:
            logger.error(f"Error updating order status: {e}")
            
    def _check_order_execution(self, order_id: str):
        """Check if an order was executed"""
        try:
            response = self.client.get_executions(
                category=self.category,
                symbol=self.symbol,
                orderId=order_id,
                limit=10
            )
            
            if response and response.get('retCode') == 0:
                executions = response['result']['list']
                
                if executions and order_id in self.active_orders:
                    order = self.active_orders[order_id]
                    
                    # Calculate fill details
                    total_qty = 0
                    total_value = 0
                    total_commission = 0
                    
                    for execution in executions:
                        qty = float(execution['execQty'])
                        price = float(execution['execPrice'])
                        commission = float(execution.get('execFee', 0))
                        
                        total_qty += qty
                        total_value += qty * price
                        total_commission += commission
                        
                    if total_qty > 0:
                        order.filled_qty = total_qty
                        order.avg_fill_price = total_value / total_qty
                        order.commission = total_commission
                        order.status = OrderStatus.FILLED if total_qty >= order.quantity else OrderStatus.PARTIAL
                        order.updated_time = time.time()
                        
                        # Update aggregate metrics
                        self.total_volume_traded += total_value
                        self.total_commission_paid += total_commission
                        
                        # Move to history if fully filled
                        if order.status == OrderStatus.FILLED:
                            self.order_history.append(order)
                            del self.active_orders[order_id]
                            logger.info(f"Order filled: {order_id} - {total_qty} @ ${order.avg_fill_price:.4f}")
                            
        except Exception as e:
            logger.error(f"Error checking order execution: {e}")
            
    def get_position_summary(self) -> Dict:
        """Get summary of all positions"""
        long_positions = [p for p in self.positions.values() if p.side == 'long']
        short_positions = [p for p in self.positions.values() if p.side == 'short']
        
        total_unrealized_pnl = sum(p.unrealized_pnl for p in self.positions.values())
        
        return {
            'total_positions': len(self.positions),
            'long_positions': len(long_positions),
            'short_positions': len(short_positions),
            'net_position': self.get_net_position(),
            'total_exposure': self.get_total_exposure(),
            'unrealized_pnl': total_unrealized_pnl,
            'active_orders': len(self.active_orders),
            'total_volume': self.total_volume_traded,
            'total_commission': self.total_commission_paid
        }