# bot/__init__.py
from bot.core.strategy import SimplifiedEMAStrategy
from bot.core.risk_manager import RiskManager
from bot.core.position_manager import PositionManager

__version__ = "2.0.0"
__all__ = ["SimplifiedEMAStrategy", "RiskManager", "PositionManager"]