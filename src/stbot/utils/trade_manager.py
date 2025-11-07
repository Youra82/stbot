# src/stbot/utils/trade_manager.py
# Angepasst für STBot (EMA/MACD/RSI Strategie)
import json
import logging
import os
import time
from datetime import datetime, timedelta

import ccxt
import numpy as np
import pandas as pd
import ta # Für ATR, welches für SL-Berechnung erhalten bleibt
import math

# --- NEUE IMPORTE ---
from stbot.strategy.indicators import STBotEngine # Nutze die neue Indikator-Engine
from stbot.strategy.trade_logic import get_titan_signal # Logik-Funktion bleibt erhalten
from stbot.utils.exchange import Exchange
from stbot.utils.telegram import send_message
# --- ENDE NEUE IMPORTE ---

# --------------------------------------------------------------------------- #
# Pfade (umbenennen von titanbot zu stbot)
# --------------------------------------------------------------------------- #
# ... (Pfade bleiben relativ gleich, nur der Pfad zum Modul ändert sich)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ARTIFACTS_PATH = os.path.join(PROJECT_ROOT, 'artifacts')
DB_PATH = os.path.join(ARTIFACTS_PATH, 'db')
TRADE_LOCK_FILE = os.path.join(DB_PATH, 'trade_lock.json')


# --------------------------------------------------------------------------- #
# Trade-Lock-Hilfsfunktionen (Unverändert)
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
# Housekeeper (Unverändert)
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
# Hauptfunktion: Trade öffnen + SL/TP/TSL setzen (Stark angepasst)
# --------------------------------------------------------------------------- #
def check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger):
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    symbol_timeframe = f"{symbol.replace('/', '-')}_{timeframe}"

    if is_trade_locked(symbol_timeframe):
        logger.info(f"Trade für {symbol_timeframe} gesperrt – überspringe.")
        return

    try:
        # --------------------------------------------------- #
        # 1. Daten holen + Indikatoren berechnen (NEU)
        # --------------------------------------------------- #
        logger.info(f"Prüfe Signal für {symbol} ({timeframe})...")
        # Hole genügend Daten für alle Indikatoren (~200+ Kerzen)
        recent_data = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=300)
        
        if recent_data.empty or len(recent_data) < 150:
            logger.warning("Nicht genügend OHLCV-Daten für Indikatoren – überspringe.")
            return

        # --- Führe Indikator-Berechnung mit der neuen Engine durch ---
        engine = STBotEngine(settings=params.get('strategy', {}))
        # Nutze die 'open', 'high', 'low', 'close', 'volume' Spalten
        data_with_indicators = engine.process_dataframe(recent_data[['open', 'high', 'low', 'close', 'volume']].copy())
        
        # NEU: Aktualisiere recent_data mit den berechneten Indikatoren (insbesondere ATR)
        recent_data = data_with_indicators
        
        if recent_data.empty: return
        current_candle = recent_data.iloc[-1]
        
        # --- Signalprüfung ---
        # data_with_indicators entspricht hier dem SMC-Ergebnis
        signal_side, signal_price = get_titan_signal(recent_data, current_candle, params)

        if not signal_side:
            logger.info("Kein Signal – überspringe.")
            return

        if exchange.fetch_open_positions(symbol):
            logger.info("Position bereits offen – überspringe.")
            return

        # --------------------------------------------------- #
        # 2. Margin & Leverage setzen (Unverändert)
        # --------------------------------------------------- #
        risk_params = params.get('risk', {})
        leverage = risk_params.get('leverage', 10)
        if not exchange.set_margin_mode(symbol, risk_params.get('margin_mode', 'isolated')):
            logger.error("Margin-Modus konnte nicht gesetzt werden.")
            return
        if not exchange.set_leverage(symbol, leverage):
            logger.error("Leverage konnte nicht gesetzt werden.")
            return

        # --------------------------------------------------- #
        # 3. Balance & Risiko berechnen (Unverändert)
        # --------------------------------------------------- #
        balance = exchange.fetch_balance_usdt()
        if balance <= 0:
            logger.error("Kein USDT-Guthaben.")
            return

        ticker = exchange.fetch_ticker(symbol)
        # Entry-Preis ist der Signal-Preis (Close der Signal-Kerze) oder der aktuelle Ticker-Preis
        entry_price = signal_price or ticker['last'] 
        if not entry_price:
            logger.error("Kein Entry-Preis verfügbar.")
            return

        rr = risk_params.get('risk_reward_ratio', 2.0)
        risk_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100.0
        risk_usdt = balance * risk_pct

        # --- SL-Distanz basierend auf ATR und Min_SL (ATR bleibt als robustes SL-Maß) ---
        atr_multiplier_sl = risk_params.get('atr_multiplier_sl', 2.0)
        min_sl_pct = risk_params.get('min_sl_pct', 0.5) / 100.0

        # ACHTUNG: ATR muss in den Daten vorhanden sein, da wir es in indicators.py berechnet haben
        current_atr = current_candle.get('atr') 
        if pd.isna(current_atr) or current_atr <= 0:
            # Fallback (sollte nicht passieren, wenn indicators.py korrekt arbeitet)
            logger.warning("ATR-Daten ungültig, verwende Hebel-basierte SL-Distanz.")
            sl_distance_pct = 1.0 / leverage
            sl_distance = entry_price * sl_distance_pct
        else:
            sl_distance_atr = current_atr * atr_multiplier_sl
            sl_distance_min = entry_price * min_sl_pct
            sl_distance = max(sl_distance_atr, sl_distance_min)

        if sl_distance <= 0: return # Sicherheit

        # --- SL/TP Preise berechnen ---
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

        # Kontraktgröße berechnen (Unverändert)
        sl_distance_pct_equivalent = sl_distance / entry_price
        contract_size = exchange.markets[symbol].get('contractSize', 1.0)

        # Notional Value (USD)
        calculated_notional_value = risk_usdt / sl_distance_pct_equivalent
        # Berechne Contracts (Menge der Basiswährung)
        amount = calculated_notional_value / entry_price

        min_amount = exchange.markets[symbol].get('limits', {}).get('amount', {}).get('min', 0.0)
        if amount < min_amount:
            logger.error(f"Ordergröße {amount} < Mindestbetrag {min_amount}.")
            return

        # --------------------------------------------------- #
        # 4. Market-Order eröffnen (Exceptions werden hier gefangen)
        # --------------------------------------------------- #
        logger.info(f"Eröffne {pos_side.upper()}-Position: {amount:.6f} Contracts @ ${entry_price:.6f} | Risk: {risk_usdt:.2f} USDT")
        # Hier wird die neue Exception-Logik des Exchange-Wrappers genutzt:
        entry_order = exchange.create_market_order(symbol, pos_side, amount, {'leverage': leverage})
        
        time.sleep(2)
        position = exchange.fetch_open_positions(symbol)
        if not position:
            logger.error("Position wurde nicht eröffnet.")
            # Es könnte ein Timing-Problem sein, kein kritischer Absturz
            return

        pos_info = position[0]
        entry_price = float(pos_info.get('entryPrice', entry_price))
        contracts = float(pos_info['contracts'])

        # --------------------------------------------------- #
        # 5. SL & TP (Trigger-Market-Orders)
        # --------------------------------------------------- #
        sl_rounded = float(exchange.exchange.price_to_precision(symbol, sl_price))
        tp_rounded = float(exchange.exchange.price_to_precision(symbol, tp_price))

        # Hier die Trigger-Order platzieren - Fehler werden geworfen und im äußeren Try/Catch behandelt
        exchange.place_trigger_market_order(symbol, tsl_side, contracts, sl_rounded, {'reduceOnly': True})

        # --------------------------------------------------- #
        # 6. Trailing-Stop-Loss
        # --------------------------------------------------- #
        act_rr = risk_params.get('trailing_stop_activation_rr', 1.5)
        callback_pct = risk_params.get('trailing_stop_callback_rate_pct', 0.5) / 100.0

        if pos_side == 'buy':
            act_price = entry_price + sl_distance * act_rr
        else:
            act_price = entry_price - sl_distance * act_rr

        act_price_rounded = float(exchange.exchange.price_to_precision(symbol, act_price))

        # Hier die TSL-Order platzieren - Fehler werden geworfen und im äußeren Try/Catch behandelt
        tsl = exchange.place_trailing_stop_order(
            symbol, tsl_side, contracts, act_price, callback_pct, {'reduceOnly': True}
        )
        
        if tsl:
            logger.info("Trailing-Stop platziert.")
        else:
            # Sollte durch die Exception-Weitergabe nicht mehr passieren, dient als Fallback-Log
            logger.warning("Trailing-Stop fehlgeschlagen – Fallback auf SL.")

        set_trade_lock(symbol_timeframe) # Trade Lock setzen

        # --------------------------------------------------- #
        # 7. Telegram-Benachrichtigung (Unverändert)
        # --------------------------------------------------- #
        if telegram_config and telegram_config.get('bot_token') and telegram_config.get('chat_id'):
            sl_r = float(exchange.exchange.price_to_precision(symbol, sl_price))
            tp_r = float(exchange.exchange.price_to_precision(symbol, tp_price))
            msg = (
                f"NEUER TRADE: {symbol} ({timeframe})\n"
                f"- Richtung: {pos_side.upper()}\n"
                f"- Entry: ${entry_price:.6f}\n"
                f"- SL: ${sl_r:.6f}\n"
                f"- TP: ${tp_r:.6f} (RR: {rr:.2f})\n"
                f"- TSL: Aktivierung @ ${act_price_rounded:.6f}, Callback: {callback_pct*100:.2f}%"
            )
            send_message(telegram_config['bot_token'], telegram_config['chat_id'], msg)


        logger.info("Trade-Eröffnung erfolgreich abgeschlossen.")

    except ccxt.InsufficientFunds as e:
        logger.error(f"InsufficientFunds: {e}")
    except ccxt.ExchangeError as e:
        logger.error(f"Börsenfehler: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unerwarteter Fehler: {e}", exc_info=True)
        # Im Fehlerfall wird immer aufgeräumt, um verwaiste Orders zu verhindern
        housekeeper_routine(exchange, symbol, logger)


# --------------------------------------------------------------------------- #
# Vollständiger Handelszyklus (Unverändert)
# --------------------------------------------------------------------------- #
def full_trade_cycle(exchange, model, scaler, params, telegram_config, logger):
    symbol = params['market']['symbol']
    try:
        pos = exchange.fetch_open_positions(symbol)
        if pos:
            logger.info(f"Position offen – Management via SL/TP/TSL.")
        else:
            housekeeper_routine(exchange, symbol, logger)
            check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger)
    except ccxt.DDoSProtection:
        logger.warning("Rate-Limit – warte 10s.")
        time.sleep(10)
    except ccxt.RequestTimeout:
        logger.warning("Timeout – warte 5s.")
        time.sleep(5)
    except ccxt.NetworkError:
        logger.warning("Netzwerkfehler – warte 10s.")
        time.sleep(10)
    except ccxt.AuthenticationError as e:
        logger.critical(f"Authentifizierungsfehler: {e}")
    except Exception as e:
        logger.error(f"Fehler im Zyklus: {e}", exc_info=True)
        time.sleep(5)
