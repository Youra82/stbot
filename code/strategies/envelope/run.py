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
    if open_position:
        if not trade_state:
            logger.warning("⚠️ Fremde Position entdeckt! Übernehme die Verwaltung...")
            update_trade_state(open_position['side']) 
            trade_state = get_trade_state()
            send_telegram_message(bot_token, chat_id, f"⚠️ *{symbol}*: Fremde {open_position['side']}-Position entdeckt und Verwaltung übernommen.")

        logger.info("Position auf Börse gefunden. Überprüfe Stop-Loss-Integrität...")
        all_orders = bitget.fetch_open_orders(symbol)
        sl_side = 'sell' if open_position['side'] == 'long' else 'buy'
        stop_orders = [o for o in all_orders if o.get('stopPrice') is not None and o.get('side') == sl_side]
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
        if trade_state:
            logger.info("Position wurde extern geschlossen. Setze internen Status zurück.")
            update_trade_state('none')
        open_orders = bitget.fetch_open_orders(symbol)
        if open_orders:
            logger.warning(f"{len(open_orders)} verwaiste Orders gefunden! Räume auf...")
            for order in open_orders:
                bitget.cancel_order(order['id'], symbol)
    
    return get_trade_state()

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
            update_trade_state(db_side_map[side], sl_price)
            
            message = f"🔥 *{symbol}* {side.upper()} eröffnet!\n- Hebel: {leverage}x\n- Stop-Loss: ${sl_price:.8f}"
            send_telegram_message(bot_token, chat_id, message)
            logger.info(message)
    except Exception as e:
        logger.error(f"🚨 KRITISCHER FEHLER bei der Order-Platzierung: {e}", exc_info=True)
        # ... (error handling) ...

def main():
    logger.info(f">>> Starte Ausführung für {SYMBOL} (stbot v3.2 - Final)")
    
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
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 500)
        data = calculate_stochrsi_indicators(data, params['strategy'])
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]

        positions = bitget.fetch_open_positions(SYMBOL)
        open_position = positions[0] if positions else None
        trade_state = get_trade_state()
        
        trade_state = sync_state_with_exchange(bitget, SYMBOL, open_position, trade_state, prev_candle, bot_token, chat_id)
        open_position = bitget.fetch_open_positions(SYMBOL)[0] if bitget.fetch_open_positions(SYMBOL) else None

        if open_position:
            logger.info(f"Position offen: {trade_state.get('side', 'unbekannt')}. Prüfe auf Take-Profit-Signal...")
            oversold = params['strategy']['oversold_level']; overbought = params['strategy']['overbought_level']
            
            if trade_state.get('side') == 'long' and current_candle['%k'] > overbought:
                logger.info(f"🟢 LONG Take-Profit. Schließe Position.")
                # Hier Ihre Logik zum Schließen
            elif trade_state.get('side') == 'short' and current_candle['%k'] < oversold:
                logger.info(f"🔴 SHORT Take-Profit. Schließe Position.")
                # Hier Ihre Logik zum Schließen
        else:
            logger.info("Keine Position offen. Suche nach neuem Einstieg.")
            # HIER IST DIE VOLLSTÄNDIGE EINSTIEGSLOGIK
            trend_filter_cfg = params['strategy'].get('trend_filter', {}); sideways_filter_cfg = params['strategy'].get('sideways_filter', {})
            trend_allows_long, trend_allows_short, market_is_not_sideways = True, True, True

            if trend_filter_cfg.get('enabled', False) and not pd.isna(prev_candle['ema_trend']):
                if prev_candle['close'] < prev_candle['ema_trend']:
                    logger.info("Trend-Filter (Aktiv): Markt unter EMA. Longs deaktiviert."); trend_allows_long = False
                else:
                    logger.info("Trend-Filter (Aktiv): Markt über EMA. Shorts deaktiviert."); trend_allows_short = False
            
            if sideways_filter_cfg.get('enabled', False) and 'sideways_cross_count' in prev_candle:
                sideways_max_crosses = sideways_filter_cfg.get('max_crosses', 8)
                if prev_candle['sideways_cross_count'] > sideways_max_crosses:
                    logger.warning(f"Seitwärts-Filter (Aktiv): Markt unruhig. Kein Handel."); market_is_not_sideways = False

            base_leverage = params['risk']['base_leverage']; target_atr_pct = params['risk']['target_atr_pct']; max_leverage = params['risk']['max_leverage']
            current_atr_pct = prev_candle.get('atr_pct', 0)
            leverage = base_leverage
            if pd.notna(current_atr_pct) and current_atr_pct > 0:
                leverage = base_leverage * (target_atr_pct / current_atr_pct)
            leverage = int(round(max(1.0, min(leverage, max_leverage))))
            margin_mode = params['risk']['margin_mode']
            logger.info(f"Berechneter Hebel: {leverage}x. Margin-Modus: {margin_mode}")

            oversold = params['strategy']['oversold_level']; overbought = params['strategy']['overbought_level']
            use_longs = params['behavior'].get('use_longs', True); use_shorts = params['behavior'].get('use_shorts', True)
            
            free_balance = bitget.fetch_balance()['USDT']['free']
            capital_to_use = free_balance * (params['risk']['balance_fraction_pct'] / 100.0)
            notional_value = capital_to_use * leverage
            amount = notional_value / current_candle['close']
            
            side = None
            sl_price = None
            if (use_longs and trend_allows_long and market_is_not_sideways and prev_candle['%k'] < prev_candle['%d'] and 
                current_candle['%k'] > current_candle['%d'] and prev_candle['%k'] < oversold):
                logger.info("🟢 LONG-Signal bestätigt. Alle Filter passiert.")
                side = 'buy'
                sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
            elif (use_shorts and trend_allows_short and market_is_not_sideways and prev_candle['%k'] > prev_candle['%d'] and 
                  current_candle['%k'] < current_candle['%d'] and prev_candle['%k'] > overbought):
                logger.info("🔴 SHORT-Signal bestätigt. Alle Filter passiert.")
                side = 'sell'
                sl_price = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
            
            if side and sl_price:
                place_order_and_verify(bitget, SYMBOL, side, amount, sl_price, leverage, margin_mode, bot_token, chat_id)
            else:
                logger.info("Kein gültiges Signal oder von Filtern blockiert.")

    except Exception as e:
        logger.error(f"Unerwarteter Fehler im Haupt-Loop: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im stbot für *{SYMBOL}*!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])

if __name__ == "__main__":
    main()
    logger.info("<<< Ausführung abgeschlossen\n")
