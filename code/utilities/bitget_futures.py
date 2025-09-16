# code/utilities/bitget_futures.py

import ccxt
import logging
import pandas as pd

logger = logging.getLogger('stbot')

class BitgetFutures:
    """Eine Wrapper-Klasse zur Vereinfachung der Interaktion mit der Bitget Futures API über CCXT."""

    def __init__(self, api_setup):
        """Initialisiert die Session mit den API-Schlüsseln."""
        self.session = ccxt.bitget({
            'apiKey': api_setup['apiKey'],
            'secret': api_setup['secret'],
            'password': api_setup['password'],
            'options': {
                'defaultType': 'swap',
            },
        })

    def fetch_balance(self):
        """Ruft das Guthaben für USDT ab."""
        try:
            balance = self.session.fetch_balance()
            return balance
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des Guthabens: {e}")
            raise

    def fetch_recent_ohlcv(self, symbol, timeframe, limit):
        """Holt die letzten OHLCV-Daten und gibt sie als DataFrame zurück."""
        try:
            ohlcv = self.session.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Fehler beim Laden der Kerzendaten: {e}")
            raise

    def fetch_open_positions(self, symbol: str):
        """Ruft alle offenen Positionen für ein bestimmtes Symbol ab."""
        try:
            all_positions = self.session.fetch_positions()
            symbol_positions = [p for p in all_positions if p['info']['symbol'] == symbol.replace('/', '')]
            return symbol_positions
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der offenen Positionen: {e}")
            raise Exception(f"Failed to fetch open positions: {e}")

    def create_market_order(self, symbol: str, side: str, amount: float, leverage: int, margin_mode: str, params={}):
        """Platziert eine Market-Order mit Hebel und Margin-Modus."""
        try:
            if leverage > 0 and margin_mode:
                self.session.set_leverage(leverage, symbol, {'marginMode': margin_mode})
                self.session.set_margin_mode(margin_mode, symbol)

            order = self.session.create_order(symbol, 'market', side, amount, params=params)
            return order
        except Exception as e:
            logger.error(f"Fehler beim Erstellen der Market-Order: {e}")
            raise

    def place_trigger_market_order(self, symbol: str, side: str, amount: float, trigger_price: float, reduce: bool = False):
        """Platziert eine Trigger-Market-Order (für SL/TP)."""
        try:
            params = {
                'triggerPrice': self.session.price_to_precision(symbol, trigger_price),
                'reduceOnly': reduce,
            }
            # Bitget hat unterschiedliche Anforderungen für Trigger-Orders, dies ist ein allgemeiner Ansatz
            order = self.session.create_order(symbol, 'market', side, amount, params=params)
            logger.info(f"Trigger-Order platziert: {side} {amount} {symbol} @ {trigger_price}")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Trigger-Order: {e}")
            raise

    # =================================================================
    # HIER IST DIE NEUE, KORREKTE FUNKTION
    # =================================================================
    def cancel_all_orders_for_symbol(self, symbol: str):
        """
        Löscht alle offenen Orders (inkl. Trigger-Orders wie SL/TP) für ein bestimmtes Symbol.
        """
        try:
            # Der korrekte CCXT-Befehl für Bitget, um alle Orders für ein Symbol zu löschen.
            product_type = 'USDT-FUTURES'
            self.session.cancel_all_orders(symbol, {'productType': product_type})
            logger.info(f"Alle offenen Orders für {symbol} erfolgreich gelöscht.")
            return True
        except Exception as e:
            logger.error(f"Fehler beim Löschen aller Orders für {symbol}: {e}")
            raise


