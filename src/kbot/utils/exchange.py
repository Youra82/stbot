# src/kbot/utils/exchange.py
# VERSION - Restored from stbot implementation to provide Exchange abstraction for kbot
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import logging
import os

logger = logging.getLogger(__name__)

# --- Pfad für Fallback-Cache ---
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

def load_data_from_cache_or_fetch(symbol, timeframe, start_date_str, end_date_str, exchange_instance=None):
    # Fallback-Funktion für Notfälle (wenn API down ist)
    data_dir = os.path.join(PROJECT_ROOT, 'data')
    cache_dir = os.path.join(data_dir, 'cache')
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")

    if os.path.exists(cache_file):
        try:
            data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
            data.index = pd.to_datetime(data.index, utc=True)
            return data.loc[data.index.min():data.index.max()]
        except Exception as e:
            logger.warning(f"Fehler beim Laden des Caches: {e}")
            pass
    return pd.DataFrame()


class Exchange:
    def __init__(self, account_config):
        self.account = account_config
        self.exchange = getattr(ccxt, 'bitget')({
            'apiKey': self.account.get('apiKey'),
            'secret': self.account.get('secret'),
            'password': self.account.get('password'),
            'options': {
                'defaultType': 'swap',
            },
            'enableRateLimit': True,
        })
        try:
            self.markets = self.exchange.load_markets()
            logger.info("Bitget Märkte erfolgreich geladen.")
        except Exception as e:
            logger.critical(f"FATAL: Fehler beim Laden der Märkte: {e}")
            self.markets = None

    # --- 1. DATA FETCHING (Live Data Priority) ---

    def fetch_recent_ohlcv(self, symbol, timeframe, limit=300):
        if not self.markets: return pd.DataFrame()

        # IMMER zuerst Live-API versuchen!
        try:
            effective_limit = min(limit, 1000)
            data = self.exchange.fetch_ohlcv(symbol, timeframe, limit=effective_limit)

            if data:
                df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
                df.set_index('timestamp', inplace=True)
                df.sort_index(inplace=True)
                return df

        except Exception as e:
            logger.error(f"FEHLER bei Live-API-Abruf für {symbol}: {e}. Versuche Fallback.")

        # Fallback auf Cache
        data = load_data_from_cache_or_fetch(symbol, timeframe, '2021-01-01', datetime.now().strftime('%Y-%m-%d'))
        if not data.empty:
            logger.warning(f"WARNUNG: Verwende veraltete Cache-Daten für {symbol}!")
            return data.tail(limit)

        return pd.DataFrame()

    def fetch_historical_ohlcv(self, symbol, timeframe, start_date_str, end_date_str, max_retries=3):
        if not self.markets: 
            return pd.DataFrame()
            
        try:
            start_dt = pd.to_datetime(start_date_str + 'T00:00:00Z', utc=True)
            end_dt = pd.to_datetime(end_date_str + 'T23:59:59Z', utc=True)
            start_ts = int(start_dt.timestamp() * 1000)
            end_ts = int(end_dt.timestamp() * 1000)
        except ValueError as e:
            logger.error(f"FEHLER: Ungültiges Datumsformat: {e}")
            return pd.DataFrame()

        all_ohlcv = []
        current_ts = start_ts
        retries = 0
        limit = 1000
        
        # Nutze ccxt's parse_timeframe für korrekte Timeframe-Duration
        timeframe_duration_ms = self.exchange.parse_timeframe(timeframe) * 1000 if self.exchange.parse_timeframe(timeframe) else 60000

        while current_ts < end_ts and retries < max_retries:
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=current_ts, limit=limit)
                if not ohlcv:
                    logger.warning(f"Keine OHLCV-Daten für {symbol} {timeframe} ab {pd.to_datetime(current_ts, unit='ms', utc=True)} erhalten.")
                    current_ts += limit * timeframe_duration_ms
                    continue

                # Filtere Kerzen die nach end_ts liegen
                ohlcv = [candle for candle in ohlcv if candle[0] <= end_ts]
                if not ohlcv: 
                    break

                all_ohlcv.extend(ohlcv)
                last_ts = ohlcv[-1][0]

                # Springe zur nächsten Kerze
                if last_ts >= current_ts:
                    current_ts = last_ts + timeframe_duration_ms
                else:
                    logger.warning("WARNUNG: Kein Zeitfortschritt beim Datenabruf, breche ab.")
                    break
                    
                retries = 0
                
            except (ccxt.RateLimitExceeded, ccxt.NetworkError) as e:
                logger.warning(f"Netzwerk/Ratelimit-Fehler: {e}. Versuch {retries+1}/{max_retries}. Warte...")
                time.sleep(5 * (retries + 1))
                retries += 1
            except ccxt.BadSymbol as e:
                logger.error(f"FEHLER: Ungültiges Symbol: {symbol}. {e}")
                return pd.DataFrame()
            except Exception as e:
                logger.error(f"Unerwarteter Fehler: {e}. Versuch {retries+1}/{max_retries}.")
                time.sleep(5)
                retries += 1

        if not all_ohlcv:
            logger.warning(f"Keine historischen Daten für {symbol} ({timeframe}) im Zeitraum {start_date_str} - {end_date_str} gefunden.")
            return pd.DataFrame()

        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        df = df[~df.index.duplicated(keep='first')].sort_index()
        return df.loc[start_dt:end_dt]

    def fetch_ticker(self, symbol):
        if not self.markets: return None
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Fehler bei Ticker: {e}")
            return None

    def validate_timeframe(self, timeframe: str) -> bool:
        """Prüft ob das angegebene Timeframe vom Exchange-Objekt unterstützt wird.
        Wir verwenden ccxt.parse_timeframe als primären Test; falls nicht verfügbar,
        vergleichen wir gegen eine bekannte Liste gängiger TFs.
        Raises ValueError on invalid timeframe.
        """
        # Versuche ccxt-parse_timeframe wenn verfügbar
        try:
            if hasattr(self.exchange, 'parse_timeframe') and self.exchange.parse_timeframe(timeframe):
                return True
        except Exception:
            pass

        # Fallback: einfache Whitelist
        allowed = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"}
        if timeframe in allowed:
            return True
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    # --- 2. EXECUTION LOGIC (Robust & Forcing Params) ---

    def set_margin_mode(self, symbol, mode='isolated'):
        if not self.markets: return False
        try:
            self.exchange.set_margin_mode(mode, symbol)
            return True
        except Exception as e:
            # Wir ignorieren den Fehler "Margin mode is the same" und machen trotzdem weiter
            if 'Margin mode is the same' in str(e): return True
            logger.warning(f"Info: Margin-Modus ({mode}) konnte nicht explizit gesetzt werden: {e}")
            return True # WICHTIG: Return True, damit der Bot nicht abbricht!

    def set_leverage(self, symbol, level=10):
        if not self.markets: return False
        try:
            self.exchange.set_leverage(level, symbol)
            return True
        except Exception as e:
            # Wir ignorieren den Fehler "Leverage not changed" und machen trotzdem weiter
            if 'Leverage not changed' in str(e): return True
            logger.warning(f"Info: Hebel ({level}x) konnte nicht explizit gesetzt werden: {e}")
            return True # WICHTIG: Return True, damit der Bot nicht abbricht!

    def create_market_order(self, symbol, side, amount, params={}):
        # WICHTIG: Params werden hier durchgereicht (z.B. marginMode, leverage)
        if not self.markets: return None
        try:
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if rounded_amount <= 0: return None

            clean_params = params.copy()
            # Entferne Parameter, die Bitget via CCXT stören könnten, falls nötig
            if 'instId' in clean_params: del clean_params['instId']
            if 'symbol' in clean_params: del clean_params['symbol']

            return self.exchange.create_order(symbol, 'market', side, rounded_amount, params=clean_params)
        except ccxt.InsufficientFunds as e:
            logger.error("Zu wenig Guthaben für Order.")
            raise e
        except Exception as e:
            logger.error(f"Fehler bei Market Order ({symbol}): {e}")
            return None

    def place_trigger_market_order(self, symbol, side, amount, trigger_price, params={}):
        if not self.markets: return None
        try:
            rounded_price = float(self.exchange.price_to_precision(symbol, trigger_price))
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))

            order_params = {
                'triggerPrice': rounded_price,
                'reduceOnly': params.get('reduceOnly', False)
            }
            order_params.update(params)

            if 'instId' in order_params: del order_params['instId']
            if 'symbol' in order_params: del order_params['symbol']

            logger.info(f"Sende Trigger Order: Side={side}, Price={rounded_price}")
            return self.exchange.create_order(symbol, 'market', side, rounded_amount, params=order_params)
        except Exception as e:
            logger.error(f"Fehler bei Trigger Order: {e}")
            return None

    def place_trailing_stop_order(self, symbol, side, amount, activation_price, callback_rate_decimal, params={}):
        if not self.markets: return None
        try:
            rounded_activation = float(self.exchange.price_to_precision(symbol, activation_price))
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            callback_rate_float = callback_rate_decimal * 100

            order_params = {
                **params,
                'trailingTriggerPrice': rounded_activation,
                'trailingPercent': callback_rate_float,
                'productType': 'USDT-FUTURES'
            }
            return self.exchange.create_order(symbol, 'market', side, rounded_amount, params=order_params)
        except Exception as e:
            logger.error(f"Fehler bei Trailing Stop: {e}")
            return None

    # --- 3. MANAGEMENT & CLEANUP (Brute Force Logic) ---

    def fetch_open_positions(self, symbol):
        if not self.markets: return []
        try:
            params = {'productType': 'USDT-FUTURES'}
            positions = self.exchange.fetch_positions([symbol], params=params)
            return [p for p in positions if float(p.get('contracts', 0)) > 0]
        except Exception as e:
            logger.error(f"Fehler bei fetch_open_positions: {e}")
            return []

    def fetch_open_trigger_orders(self, symbol):
        if not self.markets: return []
        try:
            params = {'productType': 'USDT-FUTURES', 'stop': True}
            return self.exchange.fetch_open_orders(symbol, params=params)
        except Exception as e:
            logger.error(f"Fehler bei Trigger Orders: {e}")
            return []

    def fetch_balance_usdt(self):
        try:
            balance = self.exchange.fetch_balance()
            # Einige Exchanges geben nested data, handle robust
            if isinstance(balance, dict) and ('total' in balance):
                usdt = balance['total'].get('USDT') or balance['total'].get('USD')
                return float(usdt) if usdt else 0.0

            # Fallback
            if isinstance(balance, dict):
                for k, v in balance.items():
                    if isinstance(v, dict) and 'USDT' in v:
                        return float(v['USDT'])
            return 0.0
        except Exception as e:
            logger.warning(f"Fehler beim Abruf des Balances: {e}")
            return 0.0

    def cleanup_all_open_orders(self, symbol):
        try:
            orders = self.exchange.fetch_open_orders(symbol)
            cancelled = 0
            for o in orders:
                try:
                    self.exchange.cancel_order(o['id'], symbol)
                    cancelled += 1
                except Exception:
                    pass
            return cancelled
        except Exception as e:
            logger.error(f"Fehler beim Aufräumen offener Orders: {e}")
            return 0

    # --- Hilfsfunktionen ---
    def symbol_sanitize(self, symbol: str) -> str:
        return symbol.replace('/', '-')

    def price_to_precision(self, symbol, price):
        try:
            return float(self.exchange.price_to_precision(symbol, price))
        except Exception:
            return float(price)

    def amount_to_precision(self, symbol, amount):
        try:
            return float(self.exchange.amount_to_precision(symbol, amount))
        except Exception:
            return float(amount)
