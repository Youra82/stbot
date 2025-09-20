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
    cursor.execute('CREATE TABLE IF NOT EXISTS bot_state (symbol TEXT PRIMARY KEY, side TEXT)')
    conn.commit()
    conn.close()

def get_bot_status():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT side FROM bot_state WHERE symbol = ?", (SYMBOL,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result and result[0] != 'none' else None

def update_bot_status(side: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO bot_state (symbol, side) VALUES (?, ?)", (SYMBOL, side))
    conn.commit()
    conn.close()

# --- CORE LOGIC FUNCTIONS ---
def place_order_and_verify(bitget, symbol, side, amount, sl_price, leverage, margin_mode, bot_token, chat_id):
    try:
        logger.info(f"Sende {side.upper()}-Market-Order über {amount:.5f} {symbol.split('/')[0]}...")
        order_result = bitget.create_market_order(symbol, side, amount, leverage, margin_mode)
        
        if order_result and order_result.get('id'):
            logger.info(f"✅ Market-Order an Bitget übermittelt (ID: {order_result.get('id')}). Warte auf Ausführung...")
            time.sleep(5)
            
            new_pos = bitget.fetch_open_positions(symbol)
            if not new_pos:
                logger.error("🚨 FEHLER: Order übermittelt, aber nach 5s keine offene Position gefunden!")
                send_telegram_message(bot_token, chat_id, f"🚨 FEHLER bei *{symbol}*: Order übermittelt, aber Position nicht gefunden!")
                return
            
            logger.info("✅ Positionseröffnung erfolgreich bestätigt.")
            new_pos = new_pos[0]
            close_side = 'sell' if side == 'buy' else 'buy'
            bitget.place_stop_order(symbol, close_side, float(new_pos['contracts']), sl_price)
            db_side_map = {'buy': 'long', 'sell': 'short'}
            update_bot_status(db_side_map[side])
            
            message = f"🔥 *{symbol}* {side.upper()} eröffnet!\n- Hebel: {leverage}x\n- Stop-Loss: ${sl_price:.8f}"
            send_telegram_message(bot_token, chat_id, message)
            logger.info(message)
    except Exception as e:
        logger.error(f"🚨 KRITISCHER FEHLER bei der Order-Platzierung: {e}", exc_info=True)
        # ... (Error-Handling)

def main():
    logger.info(f">>> Starte Ausführung für {SYMBOL} (stbot v4.1 - Final)")
    
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
        # --- PHASE 1: DATEN LADEN & ZUSTAND PRÜFEN ---
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 500)
        data = calculate_stochrsi_indicators(data, params['strategy'])
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]

        open_position = bitget.fetch_open_positions(SYMBOL)
        open_position = open_position[0] if open_position else None
        
        # --- PHASE 2: SYNCHRONISIEREN & AUFRÄUMEN ---
        if open_position:
            logger.info("Position auf Börse gefunden. Überprüfe Stop-Loss-Integrität...")
            # Korrekter Aufruf, um NUR Stop-Orders zu finden
            stop_orders = bitget.fetch_open_orders(SYMBOL, params={'stop': True})
            sl_side_to_find = 'sell' if open_position['side'] == 'long' else 'buy'
            relevant_sl_orders = [o for o in stop_orders if o.get('side') == sl_side_to_find]
            
            if len(relevant_sl_orders) > 1:
                logger.warning(f"⚠️ {len(relevant_sl_orders)} Stop-Loss-Orders gefunden! Räume auf...")
                for order in relevant_sl_orders:
                    bitget.cancel_order(order['id'], SYMBOL, params={'stop': True})
                # Nach dem Löschen erneut prüfen, um einen neuen zu setzen
                relevant_sl_orders = []

            if not relevant_sl_orders:
                logger.warning("⚠️ Kein gültiger Stop-Loss gefunden! Platziere Notfall-SL...")
                if open_position['side'] == 'long':
                    sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
                else:
                    sl_price = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
                
                bitget.place_stop_order(SYMBOL, sl_side_to_find, float(open_position['contracts']), sl_price)
                update_bot_status(open_position['side']) # Stelle sicher, dass der Status korrekt ist
                send_telegram_message(bot_token, chat_id, f"⚠️ *{SYMBOL}*: Position war ungeschützt! Notfall-Stop-Loss wurde platziert.")
            
            else:
                 logger.info("✅ Ein korrekter Stop-Loss ist bereits vorhanden.")
        
        else: # Keine Position offen
            bot_status = get_bot_status()
            if bot_status:
                logger.info("Position wurde extern geschlossen. Setze internen Status zurück.")
                update_bot_status('none')
            # Prüfe auf verwaiste Orders und lösche sie
            all_orders = bitget.fetch_open_orders(SYMBOL) + bitget.fetch_open_orders(SYMBOL, params={'stop': True})
            if all_orders:
                logger.warning(f"{len(all_orders)} verwaiste Orders gefunden! Räume auf...")
                unique_orders = {order['id']: order for order in all_orders}.values()
                for order in unique_orders:
                    is_stop = order.get('stopPrice') is not None
                    bitget.cancel_order(order['id'], SYMBOL, params={'stop': is_stop})
        
        # --- PHASE 3: HANDELSENTSCHEIDUNG TREFFEN ---
        # Lese den Zustand neu, da er sich geändert haben könnte
        open_position = bitget.fetch_open_positions(SYMBOL)
        open_position = open_position[0] if open_position else None

        if open_position:
            # Hier Logik für Take-Profit
            pass
        else:
            # Hier Logik für neuen Einstieg
            pass

    except Exception as e:
        logger.error(f"Unerwarteter Fehler im Haupt-Loop: {e}", exc_info=True)
        # ... (Error-Handling)

if __name__ == "__main__":
    main()
    logger.info("<<< Ausführung abgeschlossen\n")
