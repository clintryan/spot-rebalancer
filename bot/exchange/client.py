# bot/client.py

from pybit.unified_trading import HTTP
import websocket
import json
import threading
import time
from datetime import datetime

class BybitClient:
    def __init__(self, api_key, api_secret, testnet=False):
        try:
            self.session = HTTP(
                testnet=testnet,
                api_key=api_key,
                api_secret=api_secret,
            )
            print("✅ Bybit client initialized successfully.")
        except Exception as e:
            print(f"❌ Error initializing Bybit client: {e}")
            self.session = None

    # --- NEW METHOD ---
    # Gets historical candle data
    def get_kline(self, category, symbol, interval, limit=200):
        if not self.session:
            print("  ❌ API session not initialized.")
            return None
        try:
            return self.session.get_kline(
                category=category,
                symbol=symbol,
                interval=interval,
                limit=limit
            )
        except Exception as e:
            print(f"  ❌ An exception occurred while fetching k-line data: {e}")
            return None

    # --- NEW METHOD ---
    # Gets your current open positions (for futures)
    def get_positions(self, category, symbol):
        if not self.session:
            print("  ❌ API session not initialized.")
            return None
        try:
            return self.session.get_positions(
                category=category,
                symbol=symbol,
            )
        except Exception as e:
            print(f"  ❌ An exception occurred while fetching positions: {e}")
            return None

    def get_tickers(self, category="linear", symbol=None):
        if not self.session:
            print("  ❌ API session not initialized.")
            return None
        try:
            return self.session.get_tickers(category=category, symbol=symbol)
        except Exception as e:
            print(f"  ❌ An exception occurred while fetching tickers: {e}")
            return None

    def get_wallet_balance(self, accountType="UNIFIED", coin=None):
        if not self.session:
            print("  ❌ API session not initialized.")
            return None
        try:
            return self.session.get_wallet_balance(accountType=accountType, coin=coin)
        except Exception as e:
            print(f"  ❌ An exception occurred while fetching wallet balance: {e}")
            return None

    def get_instruments_info(self, category="linear", symbol=None):
        if not self.session:
            print("  ❌ API session not initialized.")
            return None
        try:
            return self.session.get_instruments_info(category=category, symbol=symbol)
        except Exception as e:
            print(f"  ❌ An exception occurred while fetching instruments info: {e}")
            return None

    # --- MODIFIED METHOD ---
    # Now accepts a 'category' parameter to place spot or futures orders
    def place_market_order(self, category, symbol, side, qty, market_unit=None, reduce_only=None, position_idx=None, verbose=True):
        if not self.session:
            print("  ❌ API session not initialized.")
            return None

        print(f"  Attempting to place {category.upper()} MARKET {side} order for {qty} of {symbol}...")

        try:
            params = {
                "category": category,
                "symbol": symbol,
                "side": side,
                "orderType": "Market",
                "qty": str(qty),
            }
            if market_unit:
                params["marketUnit"] = market_unit
            if reduce_only is not None:
                params["reduceOnly"] = bool(reduce_only)
            if position_idx is not None:
                params["positionIdx"] = position_idx

            response = self.session.place_order(**params)
            
            if response and response.get("retCode") == 0:
                order_id = response["result"].get("orderId", "N/A")
                if verbose:
                    print(f"  ✅ Order placed successfully! Order ID: {order_id}")
            else:
                if verbose:
                    print(f"  ❌ API Error: {response.get('retMsg', 'Unknown error')}")
            return response
        except Exception as e:
            print(f"  ❌ An exception occurred: {e}")
            return None

    def place_order(self, category, symbol, side, orderType, qty, price=None, timeInForce="GTC", reduce_only=None, position_idx=None, triggerPrice=None, triggerDirection=None, verbose=True):
        """Place a general order (Market, Limit, etc.) or conditional order"""
        if not self.session:
            print("  ❌ API session not initialized.")
            return None

        if verbose:
            print(f"  Attempting to place {category.upper()} {orderType.upper()} {side} order for {qty} of {symbol}...")

        try:
            params = {
                "category": category,
                "symbol": symbol,
                "side": side,
                "orderType": orderType,
                "qty": str(qty),
            }
            
            if price is not None:
                params["price"] = str(price)
            if timeInForce:
                params["timeInForce"] = timeInForce
            if reduce_only is not None:
                params["reduceOnly"] = bool(reduce_only)
            if position_idx is not None:
                params["positionIdx"] = position_idx
            if triggerPrice is not None:
                params["triggerPrice"] = str(triggerPrice)
            if triggerDirection is not None:
                params["triggerDirection"] = triggerDirection

            response = self.session.place_order(**params)
            
            if response and response.get("retCode") == 0:
                order_id = response["result"].get("orderId", "N/A")
                if verbose:
                    print(f"  ✅ Order placed successfully! Order ID: {order_id}")
            else:
                if verbose:
                    print(f"  ❌ API Error: {response.get('retMsg', 'Unknown error')}")
            return response
        except Exception as e:
            print(f"  ❌ An exception occurred: {e}")
            return None

    def cancel_order(self, category, symbol, orderId):
        """Cancel an order"""
        if not self.session:
            print("  ❌ API session not initialized.")
            return None

        try:
            response = self.session.cancel_order(
                category=category,
                symbol=symbol,
                orderId=orderId
            )
            return response
        except Exception as e:
            print(f"  ❌ An exception occurred while cancelling order: {e}")
            return None

    def get_open_orders(self, category, symbol=None, orderId=None):
        """Get open orders"""
        if not self.session:
            print("  ❌ API session not initialized.")
            return None

        try:
            params = {"category": category}
            if symbol:
                params["symbol"] = symbol
            if orderId:
                params["orderId"] = orderId
            
            response = self.session.get_open_orders(**params)
            return response
        except Exception as e:
            print(f"  ❌ An exception occurred while fetching open orders: {e}")
            return None

    def get_executions(self, category, symbol=None, orderId=None, limit=50):
        """Get order executions/fills"""
        if not self.session:
            print("  ❌ API session not initialized.")
            return None

        try:
            params = {"category": category}
            if symbol:
                params["symbol"] = symbol
            if orderId:
                params["orderId"] = orderId
            if limit:
                params["limit"] = limit
            
            response = self.session.get_executions(**params)
            return response
        except Exception as e:
            print(f"  ❌ An exception occurred while fetching executions: {e}")
            return None

    def get_coin_balance(self, coin=None, accountType="UNIFIED"):
        """Get coin balance for spot positions"""
        if not self.session:
            print("  ❌ API session not initialized.")
            return None
        try:
            response = self.session.get_wallet_balance(accountType=accountType, coin=coin)
            return response
        except Exception as e:
            print(f"  ❌ An exception occurred while fetching coin balance: {e}")
            return None

    def get_spot_position_value(self, base_symbol, quote_symbol="USDT"):
        """
        Calculate the USDT value of a spot position.
        For example, for AVNTUSDT, get AVNT balance and calculate its USDT value.
        """
        if not self.session:
            print("  ❌ API session not initialized.")
            return 0.0

        try:
            # Get the coin balance
            balance_response = self.get_coin_balance(base_symbol)
            if not balance_response or balance_response.get('retCode') != 0:
                return 0.0

            coin_list = balance_response.get('result', {}).get('list', [])
            if not coin_list:
                return 0.0

            # Find the specific coin
            coin_balance = 0.0
            for account in coin_list:
                coins = account.get('coin', [])
                for coin_info in coins:
                    if coin_info.get('coin') == base_symbol:
                        # Get wallet balance (available + locked) - handle empty strings
                        wallet_balance_str = coin_info.get('walletBalance', '0')
                        if wallet_balance_str == '' or wallet_balance_str is None:
                            wallet_balance = 0.0
                        else:
                            try:
                                wallet_balance = float(wallet_balance_str)
                            except (ValueError, TypeError):
                                wallet_balance = 0.0
                        coin_balance = wallet_balance
                        break
                if coin_balance > 0:
                    break

            if coin_balance <= 0:
                return 0.0

            # Get current price to calculate USDT value
            ticker_symbol = f"{base_symbol}{quote_symbol}"
            ticker_response = self.get_tickers(category="spot", symbol=ticker_symbol)
            
            if not ticker_response or ticker_response.get('retCode') != 0:
                print(f"  ⚠️ Could not get price for {ticker_symbol}")
                return 0.0

            ticker_list = ticker_response.get('result', {}).get('list', [])
            if not ticker_list:
                return 0.0

            current_price_str = ticker_list[0].get('lastPrice', '0')
            if current_price_str == '' or current_price_str is None:
                current_price = 0.0
            else:
                try:
                    current_price = float(current_price_str)
                except (ValueError, TypeError):
                    current_price = 0.0
            
            if current_price <= 0:
                return 0.0

            spot_value_usdt = coin_balance * current_price
            
            return spot_value_usdt

        except Exception as e:
            print(f"  ❌ Error calculating spot position value: {e}")
            return 0.0

# Helper function to create an instance of your class
def get_bybit_client():
    from . import config
    api_key = config.BYBIT_API_KEY
    api_secret = config.BYBIT_API_SECRET

    if not api_key or not api_secret:
        print("API key or secret not found. Check your .env file.")
        return None
    
    client = BybitClient(api_key=api_key, api_secret=api_secret, testnet=config.TESTNET)
    return client if client.session else None

class BybitWebSocketManager:
    """
    Manages the WebSocket connection for real-time data from Bybit.
    """
    def __init__(self, symbol: str, category: str = "linear", interval: str = "1"):
        # Select correct public stream based on category
        self.ws_url = (
            "wss://stream.bybit.com/v5/public/linear" if category == "linear" else "wss://stream.bybit.com/v5/public/spot"
        )
        self.symbol = symbol
        self.category = category
        self.interval = interval
        self.ws = None
        self.latest_ticker = None
        self.latest_candle_close = None
        self.latest_candle_ts = None
        self.is_connected = False
        self.fallback_mode = False
        self.fallback_price = None
        self.last_price = None  # cache last known price
        
        # Connection health monitoring
        self.last_message_time = None
        self.last_ping_time = None
        self.ping_interval = 20  # Send ping every 20 seconds
        self.connection_timeout = 60  # Consider connection dead after 60 seconds of no messages
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # Start with 5 second delay

    def _on_message(self, ws, message):
        """Callback function to handle incoming messages."""
        try:
            # Update last message time for connection health monitoring
            self.last_message_time = time.time()
            
            data = json.loads(message)
            
            # Handle connection acknowledgments and pings
            if data.get("op") == "subscribe":
                if data.get("success"):
                    print(f"✅ WebSocket: Successfully subscribed to {data.get('req_id', 'unknown topic')}")
                else:
                    print(f"❌ WebSocket: Failed to subscribe: {data}")
                return
            
            if data.get("op") == "ping":
                pong_msg = {"op": "pong"}
                self.ws.send(json.dumps(pong_msg))
                # Only log ping responses occasionally to reduce noise
                if not hasattr(self, '_ping_count'):
                    self._ping_count = 0
                self._ping_count += 1
                if self._ping_count <= 2 or self._ping_count % 20 == 0:
                    print(f"🏓 WebSocket: Responded to ping at {datetime.now().strftime('%H:%M:%S')} (#{self._ping_count})")
                return
            
            # Ticker updates
            if "topic" in data and "tickers" in data["topic"]:
                self.latest_ticker = data['data']
                # Extract and cache the last price
                try:
                    if isinstance(self.latest_ticker, dict) and "lastPrice" in self.latest_ticker:
                        self.last_price = float(self.latest_ticker["lastPrice"])
                        # Only show price updates occasionally to avoid spam
                        if not hasattr(self, '_price_update_count'):
                            self._price_update_count = 0
                        self._price_update_count += 1
                        # Only show first few price updates, then every 500th update
                        if self._price_update_count <= 3 or self._price_update_count % 500 == 0:
                            print(f"📊 WebSocket: Price update ${self.last_price:.4f} (#{self._price_update_count})")
                except (TypeError, ValueError) as e:
                    print(f"⚠️ WebSocket: Error parsing ticker price: {e}")
                    
            # Kline/candle updates
            if "topic" in data and "kline" in data["topic"]:
                kline_list = data.get("data", [])
                if kline_list:
                    k = kline_list[0]
                    # Bybit v5 kline structure: {start, end, interval, open, close, high, low, volume, turnover, confirm}
                    # confirm == True indicates closed candle
                    confirm = k.get("confirm")
                    if confirm:
                        try:
                            self.latest_candle_close = float(k.get("close"))
                            self.latest_candle_ts = int(k.get("end"))
                            # capture volume/turnover if present
                            self.latest_candle_volume = float(k.get("volume")) if k.get("volume") is not None else None
                            self.latest_candle_turnover = float(k.get("turnover")) if k.get("turnover") is not None else None
                            candle_time = datetime.fromtimestamp(self.latest_candle_ts / 1000).strftime('%H:%M:%S')
                            print(f"🕯️ WebSocket: New {self.interval}min candle closed: ${self.latest_candle_close:.4f} at {candle_time}")
                        except (TypeError, ValueError) as e:
                            print(f"⚠️ WebSocket: Error parsing kline data: {e}")
                            
        except json.JSONDecodeError as e:
            print(f"❌ Error decoding WebSocket message: {e}")
        except Exception as e:
            print(f"❌ Unexpected error in _on_message: {e}")
            
    def _on_error(self, ws, error):
        """Callback for WebSocket errors."""
        print(f"❌ WebSocket Error: {error}")
        self.is_connected = False

    def _on_close(self, ws, close_status_code, close_msg):
        """Callback for when the connection is closed."""
        print(f"⚠️ WebSocket Closed - Code: {close_status_code}, Message: {close_msg}")
        self.is_connected = False
        self.last_message_time = None

    def _on_open(self, ws):
        """Callback for when the connection is opened."""
        print(f"✅ WebSocket Connection Opened to {self.ws_url}")
        
        # Reset connection monitoring
        self.last_message_time = time.time()
        self.last_ping_time = None
        self.reconnect_attempts = 0
        
        # Subscribe to the ticker stream and kline for our symbol
        subs = {
            "op": "subscribe",
            "args": [
                f"tickers.{self.symbol}",
                f"kline.{self.interval}.{self.symbol}"
            ]
        }
        
        try:
            self.ws.send(json.dumps(subs))
            print(f"📡 WebSocket: Subscribed to tickers.{self.symbol} and kline.{self.interval}.{self.symbol}")
            self.is_connected = True
        except Exception as e:
            print(f"❌ WebSocket: Failed to send subscription: {e}")
            self.is_connected = False

    def connect(self):
        """Initializes and starts the WebSocket connection."""
        print(f"🔌 WebSocket: Connecting to {self.ws_url}...")
        print(f"🔌 WebSocket: Symbol={self.symbol}, Interval={self.interval}min")
        
        try:
            self.ws = websocket.WebSocketApp(self.ws_url,
                                             on_open=self._on_open,
                                             on_message=self._on_message,
                                             on_error=self._on_error,
                                             on_close=self._on_close)
            
            # Run the WebSocket connection in a separate thread to avoid blocking the main bot logic
            wst = threading.Thread(target=self._run_with_keepalive)
            wst.daemon = True  # Allows the main program to exit even if the thread is running
            wst.start()

            # Wait a moment for the connection to establish
            time.sleep(3)
            
            if self.is_connected:
                print("✅ WebSocket: Initial connection established")
            else:
                print("⚠️ WebSocket: Connection attempt completed, but status unclear")
                
        except Exception as e:
            print(f"❌ WebSocket: Failed to initialize connection: {e}")
            self.is_connected = False
    
    def _run_with_keepalive(self):
        """Run WebSocket with periodic keepalive pings"""
        try:
            # Use ping_interval of 20 seconds and ping_timeout of 10 seconds
            self.ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print(f"❌ WebSocket: run_forever failed: {e}")
            self.is_connected = False

    def get_latest_price(self) -> float:
        """Returns the last traded price from the latest ticker data."""
        # Use fallback mode if WebSocket failed
        if self.fallback_mode and self.fallback_price is not None:
            return self.fallback_price
            
        # Return cached last price if available (updated by _on_message)
        if self.last_price is not None:
            return self.last_price
            
        # Fallback: try to extract price from ticker data if not already cached
        if self.latest_ticker and isinstance(self.latest_ticker, dict):
            # Try different possible field names for the price
            price_fields = ['lastPrice', 'last_price', 'price', 'close', 'last']
            for field in price_fields:
                if field in self.latest_ticker:
                    try:
                        price = float(self.latest_ticker[field])
                        self.last_price = price
                        return price
                    except (ValueError, TypeError):
                        continue
            
            # If no direct price field, use mid price from bid/ask
            if 'bid1Price' in self.latest_ticker and 'ask1Price' in self.latest_ticker:
                try:
                    bid_price = float(self.latest_ticker['bid1Price'])
                    ask_price = float(self.latest_ticker['ask1Price'])
                    price = (bid_price + ask_price) / 2
                    self.last_price = price
                    return price
                except (ValueError, TypeError):
                    pass
        
        # Return None if no price data available
        return None

    def get_latest_closed_candle(self):
        """Returns (close_price, close_ts_ms) for the latest closed 1m candle, if available."""
        if self.latest_candle_close is not None and self.latest_candle_ts is not None:
            return self.latest_candle_close, self.latest_candle_ts
        return None, None

    def get_latest_closed_kline(self):
        """Returns dict with keys: close, ts, volume, turnover for the latest closed 1m candle, if available."""
        if self.latest_candle_close is None or self.latest_candle_ts is None:
            return None
        return {
            "close": self.latest_candle_close,
            "ts": self.latest_candle_ts,
            "volume": getattr(self, "latest_candle_volume", None),
            "turnover": getattr(self, "latest_candle_turnover", None),
        }
    
    def update_fallback_price(self, price: float):
        """Update the fallback price when in fallback mode or as backup"""
        self.fallback_price = price
        # If we don't have any WebSocket price, use this as current price
        if self.last_price is None:
            self.last_price = price

    def is_healthy(self) -> bool:
        """Check if WebSocket connection is healthy"""
        if not self.is_connected:
            return False
            
        # Check if we've received any messages recently
        if self.last_message_time is None:
            return False
            
        time_since_last_message = time.time() - self.last_message_time
        is_stale = time_since_last_message > self.connection_timeout
        
        if is_stale:
            print(f"⚠️ WebSocket: Connection appears stale ({time_since_last_message:.1f}s since last message)")
            return False
            
        return self.last_price is not None
    
    def reconnect(self):
        """Reconnect the WebSocket with exponential backoff"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            print(f"❌ WebSocket: Max reconnect attempts ({self.max_reconnect_attempts}) reached. Entering fallback mode.")
            self.fallback_mode = True
            return False
            
        self.reconnect_attempts += 1
        backoff_delay = min(self.reconnect_delay * (2 ** (self.reconnect_attempts - 1)), 60)  # Max 60 seconds
        
        print(f"🔄 WebSocket: Reconnecting (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts}) in {backoff_delay}s...")
        
        self.disconnect()
        time.sleep(backoff_delay)
        self.connect()
        
        # Give connection time to establish
        time.sleep(3)
        
        if self.is_connected:
            print("✅ WebSocket: Reconnection successful!")
            self.reconnect_attempts = 0  # Reset on success
            self.fallback_mode = False
            return True
        else:
            print(f"❌ WebSocket: Reconnection attempt {self.reconnect_attempts} failed")
            return False

    def disconnect(self):
        """Close the WebSocket connection gracefully."""
        try:
            if self.ws:
                self.ws.close()
                print("🔌 WebSocket: Connection closed")
        except Exception as e:
            print(f"⚠️ WebSocket: Error during disconnect: {e}")
        finally:
            self.is_connected = False
            self.last_message_time = None
    
    def get_connection_status(self) -> dict:
        """Get detailed connection status information"""
        current_time = time.time()
        
        status = {
            'connected': self.is_connected,
            'healthy': self.is_healthy(),
            'fallback_mode': self.fallback_mode,
            'reconnect_attempts': self.reconnect_attempts,
            'has_price': self.last_price is not None,
            'last_price': self.last_price,
            'symbol': self.symbol,
            'interval': self.interval
        }
        
        if self.last_message_time:
            status['seconds_since_last_message'] = current_time - self.last_message_time
            status['last_message_time'] = datetime.fromtimestamp(self.last_message_time).strftime('%H:%M:%S')
        else:
            status['seconds_since_last_message'] = None
            status['last_message_time'] = 'Never'
            
        return status

