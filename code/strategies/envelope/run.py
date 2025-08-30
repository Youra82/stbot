# code/strategies/envelope/run.py
import os
import sys
import json
import time
import logging
import requests
import pandas as pd

# --- PATH SETUP ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_signals
from utilities.state_manager import StateManager

# --- LOAD CONFIGURATION ---
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.critical(f"Critical Error: Could not load configuration file config.json: {e}")
        sys.exit(1)

params = load_config()

# --- PATH SETTINGS ---
BASE_DIR = os.path.expanduser(os.path.join("~", "stbot"))
KEY_PATH = os.path.join(BASE_DIR, 'secret.json')
DB_PATH = os.path.join(os.path.dirname(__file__), f"tracker_{params['symbol'].replace('/', '-').replace(':', '-')}.db")
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'supertrend.log')

# --- LOGGING & TELEGRAM ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('supertrend_bot')

telegram_bot_token = None
telegram_chat_id = None

def send_telegram_message(message):
    if not telegram_bot_token or not telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
    payload = {'chat_id': telegram_chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10).raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending Telegram message: {e}")

# --- AUTHENTICATION & SETUP ---
try:
    with open(KEY_PATH, "r") as f:
        secrets = json.load(f)
    api_setup = secrets['envelope']
    telegram_setup = secrets.get('telegram', {})
    telegram_bot_token = telegram_setup.get('bot_token')
    telegram_chat_id = telegram_setup.get('chat_id')
except Exception as e:
    logger.critical(f"Critical error loading keys: {e}")
    sys.exit(1)

state_manager = StateManager(DB_PATH)

def create_bitget_connection():
    for attempt in range(params['max_retries']):
        try:
            return BitgetFutures(api_setup)
        except Exception as e:
            logger.error(f"Connection error (Attempt {attempt+1}/{params['max_retries']}): {e}")
            if attempt < params['max_retries'] - 1: time.sleep(params['retry_delay'])
    logger.critical("API connection failed")
    send_telegram_message(f"❌ *Critical Error:* API connection to Bitget failed for {params['symbol']}.")
    sys.exit(1)

bitget = create_bitget_connection()

# --- HAUPTFUNKTIONEN ---

def get_dynamic_leverage(data):
    hebel_params = params.get('hebel_einstellungen', {})
    if not hebel_params.get('enable_dynamic_leverage', False):
        return hebel_params.get('fallback_leverage', 10)

    last_adx = data.iloc[-1].get('adx')
    if pd.isna(last_adx):
        logger.warning("ADX-Wert nicht verfügbar, nutze Fallback-Hebel.")
        return hebel_params.get('fallback_leverage', 10)
    
    if last_adx >= hebel_params.get('adx_strong_trend_threshold', 25):
        leverage = hebel_params.get('leverage_strong_trend', 15)
        logger.info(f"Starker Trend erkannt (ADX: {last_adx:.2f}). Setze Hebel auf {leverage}x.")
        return leverage
    else:
        leverage = hebel_params.get('leverage_weak_trend', 5)
        logger.info(f"Schwacher/Seitwärts-Trend erkannt (ADX: {last_adx:.2f}). Setze Hebel auf {leverage}x.")
        return leverage

def get_stop_loss_price(side, entry_price, data):
    sl_params = params.get('stop_loss_einstellungen', {})
    st_params = params.get('supertrend_einstellungen', {})

    if sl_params.get('enable_donchian_channel_sl', False):
        if side == 'buy':
            sl_price = data.iloc[-1]['donchian_lower']
            logger.info(f"Dynamischer SL via Donchian Channel: {sl_price:.4f}")
            return sl_price
        else: # short
            sl_price = data.iloc[-1]['donchian_upper']
            logger.info(f"Dynamischer SL via Donchian Channel: {sl_price:.4f}")
            return sl_price
    else: # Fallback auf ATR-basierten Stop-Loss
        sl_multiplier = st_params.get('sl_atr_multiplier', 1.5)
        current_atr = data.iloc[-1]['atr']
        if side == 'buy':
            sl_price = entry_price - (current_atr * sl_multiplier)
            logger.info(f"Statischer SL via ATR: {sl_price:.4f}")
            return sl_price
        else: # short
            sl_price = entry_price + (current_atr * sl_multiplier)
            logger.info(f"Statischer SL via ATR: {sl_price:.4f}")
            return sl_price

def open_new_position(side, data):
    try:
        symbol = params['symbol']
        margin_mode = params['margin_mode']
        leverage = get_dynamic_leverage(data)
        
        # Parameter, die direkt mit der Order gesendet werden
        order_params = {
            'leverage': leverage,
            'marginMode': margin_mode
        }
        
        balance = bitget.fetch_balance().get('USDT', {}).get('total', 0.0)
        trade_size_usdt = (balance * (params['trade_size_pct'] / 100)) * leverage
        min_trade_cost = 5.0

        if trade_size_usdt < min_trade_cost:
            msg = f"Trade size ({trade_size_usdt:.2f} USDT) zu klein. Minimum: {min_trade_cost} USDT."
            logger.error(msg)
            send_telegram_message(f"⚠️ *Trade nicht eröffnet ({symbol}):* {msg}")
            return

        current_price = data.iloc[-1]['close']
        amount_to_trade = trade_size_usdt / current_price
        
        # Market-Order mit Hebel-Parametern platzieren
        bitget.place_market_order(symbol, side, amount_to_trade, params=order_params)
        
        stop_loss_price = get_stop_loss_price(side, current_price, data)
        sl_side = 'sell' if side == 'buy' else 'buy'
        
        sl_order = bitget.place_trigger_market_order(symbol, sl_side, amount_to_trade, stop_loss_price, reduce=True)
        
        if sl_order and sl_order.get('id'):
            stop_loss_id = sl_order.get('id')
            state_manager.set_state(status="in_trade", last_side=side, stop_loss_ids=[stop_loss_id], peak_pnl_pct=0.0)
            position_type = 'Long' if side == 'buy' else 'Short'
            sl_text = f"mit initialem Stop-Loss bei {stop_loss_price:.4f}"
            
            msg = f"✅ *{position_type}-Position eröffnet ({symbol})*\n- @ {current_price:.4f} USDT\n- Hebel: *{leverage}x*\n- {sl_text}"
            logger.info(msg.replace('*', ''))
            send_telegram_message(msg)
        else:
            raise Exception("Konnte Stop-Loss ID nicht aus der Order-Antwort extrahieren.")

    except Exception as e:
        msg = f"Fehler beim Eröffnen der {side}-Position: {e}"
        logger.error(msg)
        send_telegram_message(f"❌ *Fehler bei Positionseröffnung ({symbol}):* {msg}")
        state_manager.set_state(status="ok_to_trade")

def manage_trailing_stop(position_info, data):
    st_params = params.get('supertrend_einstellungen', {})
    if not st_params.get('enable_trailing_stop_loss', False):
        return

    state = state_manager.get_state()
    if not state.get('stop_loss_ids'):
        logger.info("Keine Stop-Loss ID für Trailing Stop gefunden.")
        return
    
    try:
        new_trailing_stop_price = data.iloc[-2]['supertrend_line']
        current_sl_id = state['stop_loss_ids'][0]
        current_sl_price = 0.0

        open_orders = bitget.fetch_open_trigger_orders(params['symbol'])
        current_sl_order = next((o for o in open_orders if o['id'] == current_sl_id), None)
        
        if current_sl_order:
            current_sl_price = float(current_sl_order['stopPrice'])
        else:
            logger.warning(f"Gespeicherte SL-Order {current_sl_id} nicht mehr gefunden. Trailing Stop wird für diesen Trade deaktiviert.")
            return
        
        should_trail = False
        if position_info['side'] == 'long' and new_trailing_stop_price > current_sl_price:
            should_trail = True
        elif position_info['side'] == 'short' and new_trailing_stop_price < current_sl_price:
            should_trail = True
        
        if should_trail:
            logger.info(f"Trailing Stop: Verschiebe SL von {current_sl_price:.4f} auf {new_trailing_stop_price:.4f}")
            bitget.cancel_trigger_order(current_sl_id, params['symbol'])
            
            amount = float(position_info['contracts'])
            sl_side = 'sell' if position_info['side'] == 'long' else 'buy'
            new_sl_order = bitget.place_trigger_market_order(params['symbol'], sl_side, amount, new_trailing_stop_price, reduce=True)
            
            if new_sl_order and new_sl_order.get('id'):
                state_manager.set_state("in_trade", last_side=state['last_side'], stop_loss_ids=[new_sl_order['id']])
                send_telegram_message(f"📈 *Trailing Stop Update ({params['symbol']}):* Neuer SL bei {new_trailing_stop_price:.4f} USDT")
            else:
                raise Exception("Konnte neue Trailing Stop-Loss ID nicht extrahieren.")

    except Exception as e:
        logger.error(f"Fehler beim Trailing-Stop-Management: {e}")
        send_telegram_message(f"⚠️ *Warnung ({params['symbol']}):* Fehler im Trailing-Stop-Management: {e}")


def manage_trailing_take_profit(position_info):
    ttp_params = params.get('trailing_tp_einstellungen', {})
    if not ttp_params.get('enable_trailing_take_profit', False):
        return False

    try:
        state = state_manager.get_state()
        peak_pnl_pct = state.get('peak_pnl_pct', 0.0)
        
        initial_margin = float(position_info.get('initialMargin', 0))
        unrealized_pnl = float(position_info.get('unrealizedPnl', 0))

        if initial_margin == 0:
            logger.warning("PnL % kann nicht berechnet werden: initialMargin ist null.")
            return False

        current_pnl_pct = (unrealized_pnl / initial_margin) * 100
        
        if current_pnl_pct > peak_pnl_pct:
            state_manager.set_state(status="in_trade", last_side=state['last_side'], peak_pnl_pct=current_pnl_pct)
            logger.info(f"Neuer PnL-Peak erreicht: {current_pnl_pct:.2f}%")
            peak_pnl_pct = current_pnl_pct

        drawdown_trigger_pct = ttp_params.get('trailing_take_profit_drawdown_pct', 1.5)
        profit_drawdown = peak_pnl_pct - current_pnl_pct
        
        if peak_pnl_pct > drawdown_trigger_pct and profit_drawdown >= drawdown_trigger_pct:
            logger.info(f"Trailing Take-Profit ausgelöst! Peak PnL war {peak_pnl_pct:.2f}%, aktuell {current_pnl_pct:.2f}%. Schließe Position.")
            send_telegram_message(f"💰 *Trailing Take-Profit ({params['symbol']}):* Position geschlossen bei {current_pnl_pct:.2f}% PnL (Peak war {peak_pnl_pct:.2f}%).")

            if state.get('stop_loss_ids'):
                for sl_id in state['stop_loss_ids']: bitget.cancel_trigger_order(sl_id, params['symbol'])

            bitget.flash_close_position(params['symbol'])
            state_manager.set_state(status="ok_to_trade")
            return True

    except Exception as e:
        logger.error(f"Fehler beim Trailing-Take-Profit-Management: {e}")
        send_telegram_message(f"⚠️ *Warnung ({params['symbol']}):* Fehler im Trailing-Take-Profit-Management: {e}")
    
    return False

def run_leverage_test():
    test_params = params.get('hebel_test_modus', {})
    if not test_params.get('enabled', False):
        return False
        
    logger.warning(">>> HEBEL-TESTMODUS AKTIV <<<")
    send_telegram_message("🔬 *Hebel-Testmodus wird gestartet...*")
    
    symbol = params['symbol']
    margin_mode = params['margin_mode']
    leverage_to_test = test_params.get('leverage_to_test', 10)
    
    try:
        logger.info(f"Setze Hebel für {symbol} ({margin_mode}) auf {leverage_to_test}x...")
        
        if margin_mode == 'isolated':
            bitget.set_leverage(symbol, leverage_to_test, {'holdSide': 'long'})
            bitget.set_leverage(symbol, leverage_to_test, {'holdSide': 'short'})
        else:
            bitget.set_leverage(symbol, leverage_to_test)
        
        logger.info("Warte 2 Sekunden...")
        time.sleep(2)
        
        logger.info("Frage aktuellen Hebel von der Börse ab...")
        current_settings = bitget.fetch_current_leverage(symbol)
        
        actual_leverage = current_settings.get('leverage')
        actual_margin_mode = current_settings.get('marginMode')

        logger.info(f"API-Antwort: Hebel={actual_leverage}x, Margin-Modus={actual_margin_mode}")

        if actual_leverage == leverage_to_test and actual_margin_mode == margin_mode:
            msg = f"✅ *Hebel-Test ERFOLGREICH* für {symbol}.\n- Gewünscht: {leverage_to_test}x, {margin_mode}\n- Tatsächlich: {actual_leverage}x, {actual_margin_mode}"
            logger.info(msg.replace('*', '')); send_telegram_message(msg)
        else:
            msg = f"❌ *Hebel-Test FEHLGESCHLAGEN* für {symbol}.\n- Gewünscht: {leverage_to_test}x, {margin_mode}\n- Tatsächlich: {actual_leverage}x, {actual_margin_mode}"
            logger.error(msg.replace('*', '')); send_telegram_message(msg)

    except Exception as e:
        logger.error(f"Fehler im Hebel-Testmodus: {e}")
        send_telegram_message(f"❌ *Fehler im Hebel-Test:* {e}")
        
    logger.warning(">>> HEBEL-TESTMODUS BEENDET. Bot wird gestoppt. <<<")
    return True

def main():
    if run_leverage_test():
        return
        
    try:
        data = bitget.fetch_recent_ohlcv(params['symbol'], params['timeframe'], 200)
        data = calculate_signals(data, params)
        last_candle = data.iloc[-2]
        
        positions = bitget.fetch_open_positions(params['symbol'])
        is_position_open = len(positions) > 0
        state = state_manager.get_state()

        if state['status'] == "in_trade" and not is_position_open:
            logger.warning("Tracker war 'in_trade', aber keine Position gefunden. Setze zurück.")
            send_telegram_message(f"ℹ️ *Info ({params['symbol']}):* Position extern geschlossen. Bot-Status zurückgesetzt.")
            state_manager.set_state(status="ok_to_trade")
        
        if is_position_open:
            pos_info = positions[0]
            pnl_info = f"PnL: {pos_info.get('unrealizedPnl', 0):.2f} USDT"
            try:
                pnl_pct = (pos_info.get('unrealizedPnl', 0) / pos_info.get('initialMargin', 1)) * 100
                pnl_info += f" ({pnl_pct:.2f}%)"
            except: pass
            logger.info(f"Halte offene {pos_info['side']}-Position. {pnl_info}")
            
            if not manage_trailing_take_profit(pos_info):
                should_close_long = pos_info['side'] == 'long' and last_candle['sell_signal']
                should_close_short = pos_info['side'] == 'short' and last_candle['buy_signal']
                
                if should_close_long or should_close_short:
                    closed_side_msg = "LONG" if should_close_long else "SHORT"
                    logger.info(f"{closed_side_msg}-Position wird aufgrund eines Gegensignals geschlossen.")
                    send_telegram_message(f"🚪 *Position geschlossen ({params['symbol']}):* {closed_side_msg} wegen Gegensignal.")
                    
                    if state.get('stop_loss_ids'):
                        for sl_id in state['stop_loss_ids']: bitget.cancel_trigger_order(sl_id, params['symbol'])
                    
                    bitget.flash_close_position(params['symbol'])
                    
                    if should_close_long and params.get('use_shorts', True):
                        open_new_position('sell', data)
                    elif should_close_short and params.get('use_longs', True):
                        open_new_position('buy', data)
                    else:
                        state_manager.set_state(status="ok_to_trade")
                else:
                    manage_trailing_stop(pos_info, data)
        else:
            if last_candle['buy_signal'] and params.get('use_longs', True):
                open_new_position('buy', data)
            elif last_candle['sell_signal'] and params.get('use_shorts', True):
                open_new_position('sell', data)
            else:
                logger.info("Kein neues Handelssignal gefunden.")

    except Exception as e:
        logger.error(f"Ein unerwarteter Fehler ist in der Hauptschleife aufgetreten: {e}")
        send_telegram_message(f"❌ *Unerwarteter Fehler ({params['symbol']}):* {e}")

if __name__ == "__main__":
    logger.info(f">>> Starte Ausführung für {params['symbol']} (Supertrend Strategy v2)")
    main()
    logger.info(f"<<< Ausführung abgeschlossen\n")
