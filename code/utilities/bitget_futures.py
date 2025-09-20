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
    
    def fetch_open_orders(self, symbol: str):
        """Ruft alle offenen (nicht-getriggerten) Orders für ein Symbol ab."""
        try:
            return self.session.fetch_open_orders(symbol)
        except Exception as e:
            logger.error(f"Fehler beim Abrufen offener Orders: {e}")
            raise

    def fetch_open_trigger_orders(self, symbol: str):
        """
        Sucht gezielt und zuverlässig nach offenen Trigger-Orders (SL/TP) für Bitget.
        """
        try:
            # Bitget erfordert einen speziellen API-Endpunkt für Trigger-Orders.
            # Wir rufen diesen direkt auf.
            clean_symbol = symbol.replace('/', '').replace(':USDT', '') # z.B. PEPEUSDT
            params = {'productType': 'USDT-FUTURES'}
            trigger_orders = self.session.privateMixGetV2MixOrderQueryAllPlanOrder(params)
            
            # Filtere die Liste nach dem korrekten Symbol und nur nach "Stop"-Orders
            symbol_trigger_orders = [
                o for o in trigger_orders['data'] 
                if o.get('symbol') == clean_symbol 
                and o.get('planType') == 'stop'
            ]
            
            # Wandel das Format in das Standard-ccxt-Format um, damit run.py es versteht
            return [self.session.parse_order(o) for o in symbol_trigger_orders]
            
        except Exception as e:
            logger.error(f"Fehler beim Abrufen von Trigger-Orders: {e}")
            return [] 

    def cancel_order(self, order_id: str, symbol: str):
        """Löscht eine einzelne Order anhand ihrer ID."""
        try:
            # Für Trigger-Orders benötigt Bitget einen speziellen Befehl
            params = {'productType': 'USDT-FUTURES'}
            return self.session.privateMixPostV2MixOrderCancelPlanOrder(self.extend({'symbol': symbol, 'orderId': order_id}, params))
        except Exception as e:
            logger.error(f"Fehler beim Löschen der Order {order_id}: {e}")
            # Versuche als Fallback die Standard-Löschfunktion
            try:
                return self.session.cancel_order(order_id, symbol)
            except Exception as e2:
                 logger.error(f"Auch Standard-Löschung für Order {order_id} fehlgeschlagen: {e2}")
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
            params = {
                'triggerPrice': self.session.price_to_precision(symbol, trigger_price),
                'planType': 'stop', # Explizit als Stop-Order deklarieren
            }
            if reduce:
                params['reduceOnly'] = 'true'

            # Verwende den speziellen Endpunkt für Trigger-Orders
            order = self.session.create_order(symbol, 'market', side, amount, params=params)
            logger.info(f"Trigger-Order platziert: {side} {amount} {symbol} @ {trigger_price}")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Trigger-Order: {e}")
            raise
