# risk_manager.py
"""
Risk management module for trading strategy
"""
from dataclasses import dataclass
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

@dataclass
class RiskMetrics:
    """Risk metrics tracking"""
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    peak_balance: float = 0.0
    current_balance: float = 0.0
    
@dataclass
class RiskLimits:
    """Risk limit configuration"""
    max_drawdown_pct: float = 20.0  # Maximum drawdown allowed
    daily_loss_limit: float = 100.0  # Maximum daily loss in USDT
    max_consecutive_losses: int = 5  # Maximum consecutive losing trades
    max_position_pct: float = 30.0  # Maximum position size as % of balance
    min_risk_reward_ratio: float = 1.5  # Minimum risk/reward ratio
    max_correlation_exposure: float = 0.7  # Maximum correlated exposure
    
class RiskManager:
    """
    Comprehensive risk management system
    """
    
    def __init__(self, initial_balance: float, limits: RiskLimits = None):
        self.initial_balance = initial_balance
        self.limits = limits or RiskLimits()
        self.metrics = RiskMetrics(
            peak_balance=initial_balance,
            current_balance=initial_balance
        )
        
        # Daily tracking
        self.daily_pnl_history: Dict[str, float] = {}
        self.trade_history: List[Dict] = []
        
        # Circuit breaker states
        self.trading_halted = False
        self.halt_reason = None
        self.halt_time = None
        
    def update_balance(self, new_balance: float):
        """Update current balance and calculate metrics"""
        self.metrics.current_balance = new_balance
        
        # Update peak balance
        if new_balance > self.metrics.peak_balance:
            self.metrics.peak_balance = new_balance
            
        # Calculate drawdown
        if self.metrics.peak_balance > 0:
            self.metrics.current_drawdown_pct = (
                (self.metrics.peak_balance - new_balance) / 
                self.metrics.peak_balance * 100
            )
            
        # Update max drawdown
        if self.metrics.current_drawdown_pct > self.metrics.max_drawdown_pct:
            self.metrics.max_drawdown_pct = self.metrics.current_drawdown_pct
            
    def record_trade(self, pnl: float, entry_price: float, 
                    exit_price: float, quantity: float):
        """Record a completed trade"""
        trade = {
            'timestamp': datetime.now(),
            'pnl': pnl,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'quantity': quantity,
            'return_pct': (pnl / (entry_price * quantity)) * 100 if entry_price > 0 else 0
        }
        
        self.trade_history.append(trade)
        
        # Update consecutive losses
        if pnl < 0:
            self.metrics.consecutive_losses += 1
        else:
            self.metrics.consecutive_losses = 0
            
        # Update daily P&L
        today = datetime.now().date().isoformat()
        if today not in self.daily_pnl_history:
            self.daily_pnl_history[today] = 0
        self.daily_pnl_history[today] += pnl
        self.metrics.daily_pnl = self.daily_pnl_history[today]
        
        # Update balance
        self.update_balance(self.metrics.current_balance + pnl)
        
    def check_risk_limits(self) -> tuple[bool, Optional[str]]:
        """
        Check if any risk limits are breached
        Returns: (is_safe, breach_reason)
        """
        # Check if already halted
        if self.trading_halted:
            if self._should_resume_trading():
                self.trading_halted = False
                self.halt_reason = None
                logger.info("Trading resumed after cooldown")
            else:
                return False, self.halt_reason
                
        # Check max drawdown
        if self.metrics.current_drawdown_pct > self.limits.max_drawdown_pct:
            reason = f"Max drawdown exceeded: {self.metrics.current_drawdown_pct:.2f}%"
            self._halt_trading(reason)
            return False, reason
            
        # Check daily loss limit
        if self.metrics.daily_pnl < -self.limits.daily_loss_limit:
            reason = f"Daily loss limit exceeded: ${self.metrics.daily_pnl:.2f}"
            self._halt_trading(reason)
            return False, reason
            
        # Check consecutive losses
        if self.metrics.consecutive_losses >= self.limits.max_consecutive_losses:
            reason = f"Too many consecutive losses: {self.metrics.consecutive_losses}"
            self._halt_trading(reason)
            return False, reason
            
        return True, None
        
    def validate_position_size(self, position_size_usdt: float, 
                             current_exposure: float) -> tuple[bool, float]:
        """
        Validate and adjust position size based on risk limits
        Returns: (is_valid, adjusted_size)
        """
        # Check position size as percentage of balance
        max_position_usdt = self.metrics.current_balance * (self.limits.max_position_pct / 100)
        
        if position_size_usdt > max_position_usdt:
            logger.warning(f"Position size ${position_size_usdt:.2f} exceeds limit, "
                         f"adjusting to ${max_position_usdt:.2f}")
            return True, max_position_usdt
            
        # Check total exposure
        total_exposure = current_exposure + position_size_usdt
        if total_exposure > self.metrics.current_balance:
            available = self.metrics.current_balance - current_exposure
            if available > 0:
                logger.warning(f"Adjusting position size to available balance: ${available:.2f}")
                return True, available
            else:
                return False, 0
                
        return True, position_size_usdt
        
    def calculate_position_size_kelly(self, win_rate: float, 
                                     avg_win: float, avg_loss: float) -> float:
        """
        Calculate optimal position size using Kelly Criterion
        Kelly % = (p * b - q) / b
        where:
            p = probability of winning
            q = probability of losing (1 - p)
            b = ratio of win to loss
        """
        if avg_loss == 0 or win_rate == 0:
            return 0.1  # Default to 10% if no history
            
        p = win_rate / 100
        q = 1 - p
        b = abs(avg_win / avg_loss) if avg_loss != 0 else 1
        
        kelly_pct = (p * b - q) / b
        
        # Apply Kelly fraction (typically 0.25 to be conservative)
        kelly_fraction = 0.25
        position_pct = kelly_pct * kelly_fraction
        
        # Cap at maximum position size
        position_pct = min(position_pct, self.limits.max_position_pct / 100)
        position_pct = max(position_pct, 0.01)  # Minimum 1%
        
        return position_pct
        
    def check_risk_reward_ratio(self, entry_price: float, 
                               take_profit: float, stop_loss: float) -> bool:
        """Check if trade meets minimum risk/reward ratio"""
        potential_profit = abs(take_profit - entry_price)
        potential_loss = abs(entry_price - stop_loss)
        
        if potential_loss == 0:
            return False
            
        risk_reward_ratio = potential_profit / potential_loss
        
        return risk_reward_ratio >= self.limits.min_risk_reward_ratio
        
    def get_performance_metrics(self) -> Dict:
        """Calculate comprehensive performance metrics"""
        if not self.trade_history:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'profit_factor': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'avg_win': 0,
                'avg_loss': 0
            }
            
        wins = [t for t in self.trade_history if t['pnl'] > 0]
        losses = [t for t in self.trade_history if t['pnl'] < 0]
        
        total_wins = sum(t['pnl'] for t in wins)
        total_losses = abs(sum(t['pnl'] for t in losses))
        
        metrics = {
            'total_trades': len(self.trade_history),
            'win_rate': (len(wins) / len(self.trade_history) * 100) if self.trade_history else 0,
            'profit_factor': (total_wins / total_losses) if total_losses > 0 else 0,
            'max_drawdown': self.metrics.max_drawdown_pct,
            'avg_win': (total_wins / len(wins)) if wins else 0,
            'avg_loss': (total_losses / len(losses)) if losses else 0,
            'current_drawdown': self.metrics.current_drawdown_pct,
            'consecutive_losses': self.metrics.consecutive_losses
        }
        
        # Calculate Sharpe ratio (simplified)
        if len(self.trade_history) > 1:
            returns = [t['return_pct'] for t in self.trade_history]
            avg_return = sum(returns) / len(returns)
            std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
            metrics['sharpe_ratio'] = (avg_return / std_return) if std_return > 0 else 0
        else:
            metrics['sharpe_ratio'] = 0
            
        return metrics
        
    def _halt_trading(self, reason: str):
        """Halt trading due to risk breach"""
        self.trading_halted = True
        self.halt_reason = reason
        self.halt_time = datetime.now()
        logger.error(f"TRADING HALTED: {reason}")
        
    def _should_resume_trading(self) -> bool:
        """Check if trading should resume after halt"""
        if not self.halt_time:
            return True
            
        # Resume after 1 hour cooldown
        cooldown_period = timedelta(hours=1)
        return datetime.now() - self.halt_time > cooldown_period
        
    def generate_risk_report(self) -> str:
        """Generate a risk report"""
        metrics = self.get_performance_metrics()
        
        report = f"""
        ═══════════════════════════════════════
                    RISK REPORT
        ═══════════════════════════════════════
        
        📊 Performance Metrics:
        • Total P&L: ${metrics['total_pnl']:.2f}
        • Win Rate: {metrics['win_rate']:.1f}%
        • Max Drawdown: {metrics['max_drawdown']:.1f}%
        • Sharpe Ratio: {metrics['sharpe_ratio']:.2f}
        
        🛡️ Risk Controls:
        • Max Allocation: ${self.max_allocation:.2f}
        • Current Allocation: ${metrics['current_allocation']:.2f}
        • Position Count: {metrics['position_count']}
        • Risk Level: {self.get_risk_level()}
        
        ═══════════════════════════════════════
        """
        
        return report