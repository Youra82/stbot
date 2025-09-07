# code/utilities/bitget_futures.py

import ccxt
import time
import pandas as pd
import logging
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)

class BitgetFutures():
    def __init__(self, api_setup: Optional[Dict[str, Any]] = None, demo_mode: bool = False) -> None:
        if api_setup is None:
            self.session = ccxt.bitget()
        else:
            api_setup.setdefault("options", {"defaultType": "future"})
            if demo_mode:
                api_setup["options"]["productType"] = "SUSDT-FUTURES"
            self.session = ccxt.bitget(api_setup)
            if demo_mode:
                self.session.set_sandbox_mode(True)
        self.markets = self.session.load_markets()
    
    # ... (Alle 'fetch' und 'cancel' Funktionen bleiben unverändert) ...

    def set_margin_mode(self, symbol: str, margin_mode: str = 'isolated') -> None:
        try:
            # Diese Funktion bleibt für manuelle Aufrufe oder andere Zwecke erhalten
            self.session.set_margin_mode(margin_mode, symbol, params={'productType': 'USDT-FUTURES', 'marginCoin': 'USDT'})
            logger.info(f"Margin-Modus für {symbol} auf '{margin_mode}' gesetzt.")
        except Exception as e:
            if 'repeat submit' in str(e):
                logger.info(f"Margin-Modus für {symbol} ist bereits auf '{margin_mode}' gesetzt.")
            else:
                raise Exception(f"Fehler beim Setzen des Margin-Modus: {e}")

    def set_leverage(self, symbol: str, leverage: int, margin_mode: str) -> None:
        try:
            # Diese Funktion bleibt ebenfalls erhalten
            if margin_mode == 'isolated':
                self.session.set_leverage(leverage, symbol, params={'holdSide': 'long'})
                self.session.set_leverage(leverage, symbol, params={'holdSide': 'short'})
            else:
                 self.session.set_leverage(leverage, symbol)
            logger.info(f"Hebel für {symbol} auf {leverage}x gesetzt.")
        except Exception as e:
            if 'repeat submit' in str(e):
                logger.info(f"Hebel für {symbol} ist bereits auf {leverage}x gesetzt.")
            else:
                raise Exception(f"Fehler beim Setzen des Hebels: {e}")
    
    # --- GEÄNDERT: Order-Funktionen akzeptieren jetzt Hebel und Margin-Modus ---

    def create_market_order(self, symbol: str, side: str, amount: float, leverage: int, margin_mode: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Platziert eine Market Order und sendet Hebel/Margin-Modus direkt mit."""
        if params is None:
            params = {}
        try:
            # Füge Hebel und Margin-Modus zu den Order-Parametern hinzu
            params['marginMode'] = margin_mode
            params['leverage'] = leverage
            
            amount_str = self.session.amount_to_precision(symbol, amount)
            return self.session.create_order(symbol, 'market', side, float(amount_str), price=None, params=params)
        except Exception as e:
            raise Exception(f"Failed to place market order of {amount} {symbol}: {e}")

    def place_trigger_market_order(self, symbol: str, side: str, amount: float, trigger_price: float, leverage: int, margin_mode: str, reduce: bool = False) -> Optional[Dict[str, Any]]:
        """Platziert eine Trigger Market Order (TP/SL) und sendet Hebel/Margin-Modus mit."""
        try:
            amount_str = self.session.amount_to_precision(symbol, amount)
            trigger_price_str = self.session.price_to_precision(symbol, trigger_price)
            params = { 
                'reduceOnly': reduce, 
                'stopPrice': trigger_price_str,
                'marginMode': margin_mode,
                'leverage': leverage
            }
            return self.session.create_order(symbol, 'market', side, float(amount_str), price=None, params=params)
        except Exception as err:
            raise err
            
    # ... (Rest der Datei bleibt unverändert) ...
