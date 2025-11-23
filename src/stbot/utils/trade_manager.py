# /root/utbot2/src/utbot2/utils/trade_manager.py
import json
import logging
import os
import time
from datetime import datetime, timedelta

import ccxt
import numpy as np
import pandas as pd
import ta 
import math

# Imports angepasst auf utbot2
from utbot2.strategy.ichimoku_engine import IchimokuEngine
from utbot2.strategy.trade_logic import get_titan_signal
from utbot2.utils.exchange import Exchange
from utbot2.utils.telegram import send_message
from utbot2.utils.timeframe_utils import determine_htf

# --------------------------------------------------------------------------- #
# Pfade
# --------------------------------------------------------------------------- #
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ARTIFACTS_PATH = os.path.join(PROJECT_ROOT, 'artifacts')
DB_PATH = os.path.join(ARTIFACTS_PATH, 'db')
TRADE_LOCK_FILE = os.path.join(DB_PATH, 'trade_lock.json')

# Hilfsklasse für Bias
class Bias:
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

# --------------------------------------------------------------------------- #
# Trade-Lock-Hilfsfunktionen
# --------------------------------------------------------------------------- #
def load_or_create_trade_lock():
    os.makedirs(DB_PATH, exist_ok=True)
    if os.path.exists(TRADE_LOCK_FILE):
        with open(TRADE_LOCK_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_trade_lock(trade_lock):
    with open(TRADE_LOCK_FILE, 'w') as f:
        json.dump(trade_lock, f, indent=4)

def is_trade_locked(symbol_timeframe):
    trade_lock = load_or_create_trade_lock()
    lock_time_str = trade_lock.get(symbol_timeframe)
    if lock_time_str:
        lock_time = datetime.strptime(lock_time_str, "%Y-%m-%d %H:%M:%S")
        if datetime.now() < lock_time:
            return True
    return False

def set_trade_lock(symbol_timeframe, lock_duration_minutes=60):
    lock_time = datetime.now() + timedelta(minutes=lock_duration_minutes)
    trade_lock = load_or_create_trade_lock()
    trade_lock[symbol_timeframe] = lock_time.strftime("%Y-%m-%d %H:%M:%S")
    save_trade_lock(trade_lock)

# --------------------------------------------------------------------------- #
# MTF-Bias Bestimmung (Ichimoku Cloud Status)
# --------------------------------------------------------------------------- #
def get_market_bias(exchange, symbol, htf, logger):
    """Bestimmt den Markt-Bias basierend auf der Ichimoku Cloud des HTF."""
    try:
        # Wir brauchen genug Daten für die Wolke (52 + 26 shift)
        htf_data = exchange.fetch_recent_ohlcv(symbol, htf, limit=150)
        if htf_data.empty or len(htf_data) < 80:
            logger.warning(f"MTF-Check: Nicht genügend Daten auf {htf} verfügbar.")
            return Bias.NEUTRAL

        engine = IchimokuEngine(settings={}) 
        df = engine.process_dataframe(htf_data)
        last_candle = df.iloc[-1]
        
        close = last_candle['close']
        ssa = last_candle['senkou_span_a']
        ssb = last_candle['senkou_span_b']
        
        if pd.isna(ssa) or pd.isna(ssb):
            return Bias.NEUTRAL

        if close > max(ssa, ssb):
            logger.info(f"MTF-Check ({htf}): Preis über Wolke -> BULLISH")
            return Bias.BULLISH
        elif close < min(ssa, ssb):
            logger.info(f"MTF-Check ({htf}): Preis unter Wolke -> BEARISH")
            return Bias.BEARISH
        else:
            logger.info(f"MTF-Check ({htf}): Preis in der Wolke -> NEUTRAL")
            return Bias.NEUTRAL

    except Exception as e:
        logger.error(f"Fehler bei der MTF-Bias-Bestimmung: {e}")
        return Bias.NEUTRAL

# --------------------------------------------------------------------------- #
# Housekeeper
# --------------------------------------------------------------------------- #
def housekeeper_routine(exchange, symbol, logger):
    try:
        logger.info(f"Housekeeper: Starte Aufräumroutine für {symbol}...")
        exchange.cancel_all_orders_for_symbol(symbol)
        time.sleep(2)

        position = exchange.fetch_open_positions(symbol)
        if position:
            pos_info = position[0]
            close_side = 'sell' if pos_info['side'] == 'long' else 'buy'
            logger.warning(f"Housekeeper: Schließe verwaiste Position ({pos_info['side']} {pos_info['contracts']})...")
            exchange.create_market_order(symbol, close_side, float(pos_info['contracts']), {'reduceOnly': True})
            time.sleep(3)

        if exchange.fetch_open_positions(symbol):
            logger.error("Housekeeper: Position konnte nicht geschlossen werden!")
        else:
            logger.info(f"Housekeeper: {symbol} ist jetzt sauber.")
        return True
    except Exception as e:
        logger.error(f"Housekeeper-Fehler: {e}", exc_info=True)
        return False

# --------------------------------------------------------------------------- #
# Hauptfunktion
# --------------------------------------------------------------------------- #
def check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger):
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    htf = params['market']['htf']
    symbol_timeframe = f"{symbol.replace('/', '-')}_{timeframe}"

    if is_trade_locked(symbol_timeframe):
        logger.info(f"Trade für {symbol_timeframe} gesperrt – überspringe.")
        return

    try:
        logger.info(f"Prüfe Ichimoku-Signal für {symbol} ({timeframe})...")
        
        market_bias = get_market_bias(exchange, symbol, htf, logger)

        recent_data = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=200)
        if recent_data.empty or len(recent_data) < 100:
            logger.warning("Nicht genügend OHLCV-Daten – überspringe.")
            return

        # Indikatoren berechnen
        smc_params = params.get('strategy', {}) 
        
        atr_indicator = ta.volatility.AverageTrueRange(high=recent_data['high'], low=recent_data['low'], close=recent_data['close'], window=14)
        recent_data['atr'] = atr_indicator.average_true_range()
        
        engine = IchimokuEngine(settings=smc_params)
        processed_data = engine.process_dataframe(recent_data)
        
        current_candle = processed_data.iloc[-1]

        # Signal abrufen
        signal_side, signal_price = get_titan_signal(processed_data, current_candle, params, market_bias)

        if not signal_side:
            logger.info("Kein Signal – überspringe.")
            return

        if exchange.fetch_open_positions(symbol):
            logger.info("Position bereits offen – überspringe.")
            return

        # Risk Management
        risk_params = params.get('risk', {})
        leverage = risk_params.get('leverage', 10)
        if not exchange.set_margin_mode(symbol, risk_params.get('margin_mode', 'isolated')): return
        if not exchange.set_leverage(symbol, leverage): return

        balance = exchange.fetch_balance_usdt()
        if balance <= 0:
            logger.error("Kein USDT-Guthaben.")
            return

        ticker = exchange.fetch_ticker(symbol)
        entry_price = signal_price or ticker['last']
        
        rr = risk_params.get('risk_reward_ratio', 2.0)
        risk_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100.0
        risk_usdt = balance * risk_pct

        atr_multiplier_sl = risk_params.get('atr_multiplier_sl', 2.0)
        min_sl_pct = risk_params.get('min_sl_pct', 0.5) / 100.0

        current_atr = current_candle.get('atr')
        if pd.isna(current_atr) or current_atr <= 0:
            sl_distance = entry_price * (1.0 / leverage)
        else:
            sl_distance_atr = current_atr * atr_multiplier_sl
            sl_distance_min = entry_price * min_sl_pct
            sl_distance = max(sl_distance_atr, sl_distance_min)

        if sl_distance <= 0: return

        if signal_side == 'buy':
            sl_price = entry_price - sl_distance
            tp_price = entry_price + sl_distance * rr
            pos_side = 'buy'
            tsl_side = 'sell'
        else:
            sl_price = entry_price + sl_distance
            tp_price = entry_price - sl_distance * rr
            pos_side = 'sell'
            tsl_side = 'buy'

        sl_distance_pct_equivalent = sl_distance / entry_price
        calculated_notional_value = risk_usdt / sl_distance_pct_equivalent
        amount = calculated_notional_value / entry_price
        
        min_amount = exchange.markets[symbol].get('limits', {}).get('amount', {}).get('min', 0.0)
        if amount < min_amount:
            logger.error(f"Ordergröße {amount} < Mindestbetrag {min_amount}.")
            return

        # Orders
        logger.info(f"Eröffne {pos_side.upper()}-Position: {amount:.6f} @ ${entry_price:.6f} | Risk: {risk_usdt:.2f} USDT")
        entry_order = exchange.create_market_order(symbol, pos_side, amount, {'leverage': leverage})
        if not entry_order: return

        time.sleep(2)
        position = exchange.fetch_open_positions(symbol)
        if not position: return

        pos_info = position[0]
        contracts = float(pos_info['contracts'])

        sl_rounded = float(exchange.exchange.price_to_precision(symbol, sl_price))
        tp_rounded = float(exchange.exchange.price_to_precision(symbol, tp_price))
        exchange.place_trigger_market_order(symbol, tsl_side, contracts, sl_rounded, {'reduceOnly': True})

        act_rr = risk_params.get('trailing_stop_activation_rr', 1.5)
        callback_pct = risk_params.get('trailing_stop_callback_rate_pct', 0.5) / 100.0
        
        if pos_side == 'buy':
            act_price = entry_price + sl_distance * act_rr
        else:
            act_price = entry_price - sl_distance * act_rr
            
        exchange.place_trailing_stop_order(symbol, tsl_side, contracts, act_price, callback_pct, {'reduceOnly': True})

        set_trade_lock(symbol_timeframe)

        if telegram_config and telegram_config.get('bot_token') and telegram_config.get('chat_id'):
            msg = (
                f"UTBOT2 ICHIMOKU: {symbol} ({timeframe}) [MTF: {market_bias}]\n"
                f"- Richtung: {pos_side.upper()}\n"
                f"- Entry: ${entry_price:.6f}\n"
                f"- SL: ${sl_rounded:.6f}\n"
                f"- TP: ${tp_rounded:.6f}"
            )
            send_message(telegram_config['bot_token'], telegram_config['chat_id'], msg)

        logger.info("Trade-Eröffnung erfolgreich abgeschlossen.")

    except ccxt.InsufficientFunds as e:
        logger.error(f"InsufficientFunds: {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler: {e}", exc_info=True)
        housekeeper_routine(exchange, symbol, logger)

def full_trade_cycle(exchange, model, scaler, params, telegram_config, logger):
    symbol = params['market']['symbol']
    try:
        pos = exchange.fetch_open_positions(symbol)
        if pos:
            logger.info(f"Position offen – Management via SL/TP/TSL.")
        else:
            housekeeper_routine(exchange, symbol, logger)
            check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger)
    except Exception as e:
        logger.error(f"Fehler im Zyklus: {e}", exc_info=True)
        time.sleep(5)
