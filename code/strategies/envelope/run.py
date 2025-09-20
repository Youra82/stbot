# code/strategies/envelope/run.py

import os
import sys
import json
import logging
import pandas as pd
import traceback
import sqlite3
import time

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.append(os.path.join(PROJECT_ROOT, 'code'))

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_stochrsi_indicators
from utilities.telegram_handler import send_telegram_message

# --- SETUP ---
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'stbot.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('stbot')

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f: return json.load(f)

params = load_config()
SYMBOL = params['market']['symbol']
DB_FILE = os.path.join(os.path.dirname(__file__), f"bot_state_{SYMBOL.replace('/', '-')}.db")

# --- DATABASE FUNCTIONS ---
def setup_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS bot_state (symbol TEXT PRIMARY KEY, side TEXT, sl_price REAL)')
    try:
        cursor.execute('ALTER TABLE bot_state ADD COLUMN sl_price REAL')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def get_trade_state():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT side, sl_price FROM bot_state WHERE symbol = ?", (SYMBOL,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0] != 'none':
        return {'side': result[0], 'sl_price': result[1]}
    return None

def update_trade_state(side: str, sl_price: float = 0.0):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO bot_state (symbol, side, sl_price) VALUES (?, ?, ?)", (SYMBOL, side, sl_price))
    conn.commit()
    conn.close()

# --- CORE LOGIC FUNCTIONS ---

def sync_state_with_exchange(bitget, symbol, open_position, trade_state, prev_candle, bot_token, chat_id):
    """Gleicht den Zustand ab und führt notwendige Korrekturen durch."""
    if open_position:
        if not trade_state:
            logger.warning("⚠️ Fremde Position entdeckt! Übernehme die Verwaltung...")
            update_trade_state(open_position['side']) 
            trade_state = get_trade_state()
            send_telegram_message(bot_token, chat_id, f"⚠️ *{symbol}*: Fremde {open_position['side']}-Position entdeckt und Verwaltung übernommen.")

        logger.info("Position auf Börse gefunden. Überprüfe Stop-Loss-Integrität...")
        all_orders = bitget.fetch_open_orders(symbol)
        sl_side = 'sell' if open_position['side'] == 'long' else 'buy'
        stop_orders = [o for o in all_orders if o.get('stopPrice') is not None and o['side'] == sl_side]
        sl_count = len(stop_orders)
        
        if sl_count > 1:
            logger.warning(f"⚠️ {sl_count} Stop-Loss-Orders gefunden! Räume auf...")
            for order in stop_orders:
                bitget.cancel_order(order['id'], symbol)
            sl_count = 0
        
        if sl_count == 0:
            logger.warning("⚠️ Kein gültiger Stop-Loss gefunden! Platziere Notfall-SL...")
            if open_position['side'] == 'long':
                sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
            else:
                sl_price = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
            
            bitget.place_stop_order(symbol, sl_side, float(open_position['contracts']), sl_price)
            update_trade_state(open_position['side'], sl_price)
            send_telegram_message(bot_token, chat_id, f"⚠️ *{symbol}*: Position war ungeschützt! Notfall-Stop-Loss wurde platziert.")
        
        else:
             logger.info("✅ Ein korrekter Stop-Loss ist bereits vorhanden.")
            
    else: # Keine Position offen
        open_orders = bitget.fetch_open_orders(symbol)
        if open_orders:
            logger.warning("Keine Position, aber verwaiste Orders gefunden! Räume auf...")
            for order in open_orders:
                bitget.cancel_order(order['id'], symbol)
        
        if trade_state:
            logger.info("Position wurde extern geschlossen. Setze internen Status zurück.")
            update_trade_state('none')
    
    return get_trade_state()

def main():
    logger.info(f">>> Starte Ausführung für {SYMBOL} (stbot v3.1 - Proaktive Synchronisation)")
    
    try:
        key_path = os.path.abspath(os.path.join(PROJECT_ROOT, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        api_setup = secrets['envelope']
        telegram_config = secrets.get('telegram', {})
        bot_token = telegram_config.get('bot_token')
        chat_id = telegram_config.get('chat_id')
    except Exception as e:
        logger.critical(f"Fehler beim Laden der API-Schlüssel: {e}"); sys.exit(1)

    bitget = BitgetFutures(api_setup)
    setup_database()
    
    try:
        # 1. Daten laden
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 500)
        data = calculate_stochrsi_indicators(data, params['strategy'])
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]

        # 2. Realität prüfen
        positions = bitget.fetch_open_positions(SYMBOL)
        open_position = positions[0] if positions else None
        trade_state = get_trade_state()
        
        # 3. Synchronisieren & Aufräumen
        trade_state = sync_state_with_exchange(bitget, SYMBOL, open_position, trade_state, prev_candle, bot_token, chat_id)
        open_position = bitget.fetch_open_positions(SYMBOL)[0] if bitget.fetch_open_positions(SYMBOL) else None

        # 4. Handeln basierend auf dem sauberen Zustand
        if open_position:
            logger.info(f"Position offen: {trade_state.get('side', 'unbekannt')}. Prüfe auf Take-Profit-Signal...")
            oversold = params['strategy']['oversold_level']; overbought = params['strategy']['overbought_level']
            
            if trade_state.get('side') == 'long' and current_candle['%k'] > overbought:
                logger.info(f"🟢 LONG Take-Profit. Schließe Position.")
                # Hier würde Ihre `close_position_and_cleanup` Funktion aufgerufen
                pass # Platzhalter
            elif trade_state.get('side') == 'short' and current_candle['%k'] < oversold:
                logger.info(f"🔴 SHORT Take-Profit. Schließe Position.")
                # Hier würde Ihre `close_position_and_cleanup` Funktion aufgerufen
                pass # Platzhalter
        else:
            logger.info("Keine Position offen. Suche nach neuem Einstieg.")
            # Hier würde Ihre Einstiegslogik (`place_order_and_verify` etc.) stehen
            pass # Platzhalter

    except Exception as e:
        logger.error(f"Unerwarteter Fehler im Haupt-Loop: {e}", exc_info=True)
        # ... (Ihr Error-Handling) ...

if __name__ == "__main__":
    main()
    logger.info("<<< Ausführung abgeschlossen\n")
