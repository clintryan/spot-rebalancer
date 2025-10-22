import time
import signal
import sys
import yaml
import argparse

from bot.exchange.client import BybitClient, BybitWebSocketManager
from bot.core.rebalancer import SpotRebalancer


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
        from dotenv import load_dotenv
        import os
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
        self.ws = BybitWebSocketManager(symbol, category='spot', interval=r.get('timeframe', '1'))
        self.ws.connect()
        time.sleep(2)

        # Warm-up EMA with initial klines
        # Optional: we keep it simple; EMATrendBias handles updates as candles close

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


