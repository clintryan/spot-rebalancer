# bot/config.py

import os
from dotenv import load_dotenv

# Load the .env file from the root directory
# The ../ tells the script to look one directory up from the current file's location
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path)

# Get the API keys from the environment variables
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY_bybitwood")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET_bybitwood")

# Trading configuration parameters
DEFAULT_SYMBOL = "SOLUSDT"
DEFAULT_CATEGORY = "spot"

# Default trading parameters (can be overridden in strategy configs)
DEFAULT_ORDER_AMOUNT_USDT = 10.0
DEFAULT_RUNTIME_SECONDS = 3600  # 1 hour

# Risk management
MAX_POSITION_SIZE_USDT = 1000.0
MIN_ORDER_VALUE_USDT = 10.0  # Bybit minimum order value

# Environment settings
TESTNET = False  # Set to True for testnet trading