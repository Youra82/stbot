# code/utilities/bitget_futures.py

import ccxt
import logging
import pandas as pd

logger = logging.getLogger('stbot')

class BitgetFutures:
    def __init__(self, api_setup):
        self.session = ccxt.bitget({
            'apiKey': api_setup['apiKey'],
            'secret': api_setup['secret'],
            'password': api_setup['password'],
            'options': {
                'defaultType': 'swap',
            },
        })
        self.session.load_markets()

    def fetch_balance(self):
        try:
            balance = self.session.fetch_balance()
            return balance
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des Guthabens: {e}")
            raise

    def fetch_recent_ohlcv(self, symbol, timeframe, limit):
        try:
            ohlcv = self.session.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Fehler beim Laden der Kerzendaten: {e}")
            raise

    def fetch_open_positions(self, symbol: str):
        try:
            all_positions = self.session.fetch_positions([symbol])
            symbol_positions = [
                p for p in all_positions 
                if p.get('contracts') is not None 
                and float(p['contracts']) > 0
            ]
            return symbol_positions
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der offenen Positionen: {e}")
            raise Exception(f"Failed to fetch open positions: {e}")
    
    def fetch_open_orders(self, symbol: str, params={}):
        """Ruft alle offenen Orders für ein Symbol ab, kann durch params spezifiziert werden."""
        try:
            return self.session.fetch_open_orders(symbol, params=params)
        except Exception as e:
            logger.error(f"Fehler beim Abrufen offener Orders: {e}")
            raise

    def cancel_order(self, order_id: str, symbol: str, params={}):
        """Löscht eine einzelne Order anhand ihrer ID, kann durch params spezifiziert werden."""
        try:
            return self.session.cancel_order(order_id, symbol, params=params)
        except Exception as e:
            logger.error(f"Fehler beim Löschen der Order {order_id}: {e}")
            raise

    def create_market_order(self, symbol: str, side: str, amount: float, leverage: int, margin_mode: str, params={}):
        """
        Platziert eine Market-Order und sendet Hebel/Margin-Modus als Teil der Order.
        """
        try:
            order_params = {}
            if params:
                order_params.update(params)

            order_params['marginMode'] = margin_mode.lower()
            if leverage > 0:
                order_params['leverage'] = leverage

            order = self.session.create_order(symbol, 'market', side, amount, params=order_params)
            return order
        except Exception as e:
            logger.error(f"Fehler beim Erstellen der Market-Order: {e}")
            raise

    def place_trigger_market_order(self, symbol: str, side: str, amount: float, trigger_price: float, reduce: bool = False):
        try:
            # Dies ist der korrekte Weg, eine Stop-Order bei Bitget via ccxt zu platzieren
            params = {
                'stopPrice': self.session.price_to_precision(symbol, trigger_price),
                'reduceOnly': reduce,
            }
            order = self.session.create_order(symbol, 'market', side, amount, params=params)
            logger.info(f"Trigger-Order platziert: {side} {amount} {symbol} @ {trigger_price}")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Trigger-Order: {e}")
            raise
