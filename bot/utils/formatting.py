# bot/utils.py
import math

def get_qty_precision(qty_step: str) -> int:
    """Calculates the number of decimal places for quantity based on qtyStep."""
    if '.' in qty_step:
        return len(qty_step.split('.')[1])
    return 0

def format_quantity(quantity: float, precision: int) -> str:
    """Formats the quantity to the required precision without rounding up."""
    factor = 10 ** precision
    # We use floor to always round down, preventing "insufficient balance" errors
    formatted_qty = math.floor(quantity * factor) / factor
    return f"{formatted_qty:.{precision}f}"

def get_price_precision(price_step: str) -> int:
    """Calculates the number of decimal places for price based on tickSize."""
    if '.' in price_step:
        return len(price_step.split('.')[1])
    return 0

def format_price(price: float, precision: int) -> str:
    """Formats the price to the required precision."""
    return f"{price:.{precision}f}"