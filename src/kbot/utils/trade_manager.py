# src/kbot/utils/trade_manager.py
# =============================================================================
# KBot Trade Manager: Stoch‑RSI Strategy
# =============================================================================

import logging
import time
import os
import json
import math
from datetime import datetime, timedelta

from kbot.utils.telegram import send_message
from kbot.utils.exchange import Exchange
from kbot.strategy.stochrsi_engine import StochRSIEngine

# --------------------------------------------------------------------------- #
# Pfade
# --------------------------------------------------------------------------- #
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ARTIFACTS_PATH = os.path.join(PROJECT_ROOT, 'artifacts')
DB_PATH = os.path.join(ARTIFACTS_PATH, 'db')
TRADE_LOCK_FILE = os.path.join(DB_PATH, 'trade_lock.json')


# --------------------------------------------------------------------------- #
# Trade-Lock-Hilfsfunktionen
# --------------------------------------------------------------------------- #
def load_or_create_trade_lock():
    """Lädt oder erstellt die Trade-Lock Datei."""
    os.makedirs(DB_PATH, exist_ok=True)
    if os.path.exists(TRADE_LOCK_FILE):
        try:
            with open(TRADE_LOCK_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    return {}


def save_trade_lock(trade_lock):
    """Speichert die Trade-Lock Datei."""
    with open(TRADE_LOCK_FILE, 'w') as f:
        json.dump(trade_lock, f, indent=4)


def is_trade_locked(symbol_timeframe: str) -> bool:
    """Prüft ob ein Trade für dieses Symbol/Timeframe gesperrt ist."""
    trade_lock = load_or_create_trade_lock()
    lock_time_str = trade_lock.get(symbol_timeframe)
    if lock_time_str:
        lock_time = datetime.strptime(lock_time_str, "%Y-%m-%d %H:%M:%S")
        if datetime.now() < lock_time:
            return True
    return False


def set_trade_lock(symbol_timeframe: str, lock_duration_minutes: int = 60):
    """Setzt eine Trade-Sperre für die angegebene Dauer."""
    lock_time = datetime.now() + timedelta(minutes=lock_duration_minutes)
    trade_lock = load_or_create_trade_lock()
    trade_lock[symbol_timeframe] = lock_time.strftime("%Y-%m-%d %H:%M:%S")
    save_trade_lock(trade_lock)


def clear_trade_lock(symbol_timeframe: str):
    """Löscht die Trade-Sperre für ein Symbol/Timeframe."""
    trade_lock = load_or_create_trade_lock()
    if symbol_timeframe in trade_lock:
        del trade_lock[symbol_timeframe]
        save_trade_lock(trade_lock)


def calculate_lock_duration(timeframe: str) -> int:
    """Berechnet dynamische Lock-Duration basierend auf Timeframe (2.5x)."""
    tf_minutes = {
        '5m': 5, '15m': 15, '30m': 30, 
        '1h': 60, '2h': 120, '4h': 240, 
        '6h': 360, '1d': 1440
    }
    base_minutes = tf_minutes.get(timeframe, 60)
    return int(base_minutes * 2.5)


# --------------------------------------------------------------------------- #
# Housekeeper
# --------------------------------------------------------------------------- #
def housekeeper_routine(exchange: Exchange, symbol: str, logger: logging.Logger) -> bool:
    """
    Räumt verwaiste Orders und Positionen auf.
    
    Args:
        exchange: Exchange-Objekt
        symbol: Trading-Symbol
        logger: Logger
    
    Returns:
        True wenn erfolgreich, False bei Fehler
    """
    try:
        logger.info(f"Housekeeper: Starte Aufräumroutine für {symbol}...")
        
        # 1. Alle offenen Orders stornieren
        try:
            cancelled = exchange.cleanup_all_open_orders(symbol)
            if cancelled > 0:
                logger.info(f"Housekeeper: {cancelled} Order(s) storniert.")
        except Exception as e:
            logger.warning(f"Housekeeper: Order-Stornierung fehlgeschlagen: {e}")
        
        time.sleep(1)
        
        # 2. Verwaiste Position schließen
        position = exchange.fetch_open_positions(symbol)
        if position:
            pos_info = position[0]
            close_side = 'sell' if pos_info['side'] == 'long' else 'buy'
            contracts = float(pos_info['contracts'])
            
            logger.warning(f"Housekeeper: Schließe verwaiste Position ({pos_info['side']} {contracts:.6f})...")
            exchange.create_market_order(symbol, close_side, contracts, {'reduceOnly': True})
            time.sleep(2)
            
            if exchange.fetch_open_positions(symbol):
                logger.error("Housekeeper: Position konnte nicht geschlossen werden!")
                return False
        
        logger.info(f"Housekeeper: {symbol} ist jetzt sauber.")
        return True
        
    except Exception as e:
        logger.error(f"Housekeeper-Fehler: {e}", exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# Signal-Analyse und Logging
# --------------------------------------------------------------------------- #
def analyze_and_log_signal(engine: StochRSIEngine, df, params: dict, logger: logging.Logger):
    """
    Analysiert das aktuelle Signal und gibt detaillierte Logs aus.
    
    Returns:
        Tuple: (signal, reason)
    """
    use_volume_confirmation = params.get('strategy', {}).get('use_volume_confirmation', True)
    
    # Signal ermitteln
    signal, reason = engine.get_signal(df, use_volume_confirmation)
    
    # Aktuelle Werte für Logging
    current = df.iloc[-1]
    prev = df.iloc[-2]
    
    channel_top = current['channel_top']
    channel_bot = current['channel_bot']
    channel_avg = current['channel_avg']
    trend = "BULLISH" if current['channel_trend'] == 1 else "BEARISH" if current['channel_trend'] == -1 else "NEUTRAL"
    
    # Detailliertes Logging (für cron.log sichtbar)
    print("\n" + "=" * 60)
    print("📊 KBOT STOCH‑RSI ANALYSE")
    print("=" * 60)
    print(f"Symbol:         {params['market']['symbol']}")
    print(f"Timeframe:      {params['market']['timeframe']}")
    print(f"Aktueller Preis: {current['close']:.2f}")
    print("-" * 40)
    print(f"Channel Top:    {channel_top:.2f}")
    print(f"Channel Avg:    {channel_avg:.2f}")
    print(f"Channel Bot:    {channel_bot:.2f}")
    print(f"Channel Trend:  {trend}")
    print("-" * 40)
    print(f"Signal (stochrsi): {prev.get('stochrsi_signal', prev.get('breakout_signal', 0))}")
    print(f"Volume Delta:    {prev.get('volume_delta', 0):.0f}")
    print("-" * 40)
    
    if signal:
        print(f"🎯 SIGNAL: {signal.upper()}")
        print(f"   Grund: {reason}")
    else:
        print(f"⏸️  Kein Signal: {reason}")
    
    print("=" * 60 + "\n")
    
    return signal, reason


# --------------------------------------------------------------------------- #
# Position eröffnen
# --------------------------------------------------------------------------- #
def open_position(exchange: Exchange, engine: StochRSIEngine, df,
                  side: str, params: dict, telegram_config: dict, 
                  logger: logging.Logger) -> bool:
    """
    Eröffnet eine neue Position mit SL/TP.
    
    Returns:
        True wenn erfolgreich, False bei Fehler
    """
    symbol = params['market']['symbol']
    
    # Parameter holen
    risk_pct = params.get('risk', {}).get('risk_per_trade_pct', 1.0) / 100
    leverage = params.get('risk', {}).get('leverage', 10)
    risk_reward = params.get('strategy', {}).get('risk_reward_ratio', 2.0)
    
    # Aktuellen Preis holen
    current_price = df['close'].iloc[-1]
    
    # SL/TP berechnen
    stop_loss, take_profit = engine.get_stop_loss_take_profit(df, side, risk_reward)
    
    # Position Sizing
    balance = exchange.fetch_balance_usdt()
    risk_amount = balance * risk_pct
    
    sl_distance_pct = abs(current_price - stop_loss) / current_price
    if sl_distance_pct == 0:
        logger.warning("SL-Distance ist 0 - überspringe Trade")
        return False
    
    position_size_usd = risk_amount / sl_distance_pct
    position_size_usd = min(position_size_usd, balance * 0.9)  # Max 90% des Kapitals
    
    # Contracts berechnen
    contracts = position_size_usd / current_price
    
    # Min Notional prüfen
    min_notional = 5.0  # Minimum für die meisten Exchanges
    if position_size_usd < min_notional:
        logger.warning(f"Position zu klein: {position_size_usd:.2f} < {min_notional}")
        return False
    
    try:
        # Margin Mode setzen (isolated) vor Leverage
        margin_mode = params.get('risk', {}).get('margin_mode', 'isolated')
        logger.info(f"Setze Margin-Modus auf '{margin_mode}' für {symbol}")
        exchange.set_margin_mode(symbol, margin_mode)

        # Leverage setzen
        exchange.set_leverage(symbol, leverage, margin_mode=margin_mode)
        
        # Market Order
        order_side = 'buy' if side == 'long' else 'sell'
        logger.info(f"Eröffne {side.upper()} Position: {contracts:.6f} Contracts @ ~{current_price:.2f}")
        
        order = exchange.create_market_order(symbol, order_side, contracts)
        
        if not order or 'id' not in order:
            logger.error("Market Order fehlgeschlagen")
            return False
        
        time.sleep(1)
        
        # Tatsächlichen Entry-Preis holen
        avg_price = order.get('average') or order.get('price') or current_price
        entry_price = float(avg_price) if avg_price else current_price
        
        # SL/TP Sides
        sl_side = 'sell' if side == 'long' else 'buy'
        tp_side = sl_side
        
        # ===== TRIGGER MARKET ORDER für Static SL =====
        sl_rounded = float(exchange.exchange.price_to_precision(symbol, stop_loss))
        exchange.place_trigger_market_order(symbol, sl_side, contracts, sl_rounded, {'reduceOnly': True})
        logger.info(f"Stop-Loss (Trigger Order) gesetzt: {stop_loss:.2f}")
        
        # ===== TRAILING STOP LOSS =====
        # Aktiviert sich bei 1.5x RR vom Entry
        sl_distance = abs(entry_price - stop_loss)
        act_rr = params.get('risk', {}).get('trailing_stop_activation_rr', 1.5)
        callback_pct = params.get('risk', {}).get('trailing_stop_callback_rate_pct', 0.5) / 100.0
        
        if side == 'long':
            activation_price = entry_price + sl_distance * act_rr
        else:
            activation_price = entry_price - sl_distance * act_rr
        
        activation_rounded = float(exchange.exchange.price_to_precision(symbol, activation_price))
        
        tsl = exchange.place_trailing_stop_order(
            symbol, sl_side, contracts, activation_rounded, callback_pct, {'reduceOnly': True}
        )
        
        if tsl:
            logger.info(f"Trailing-Stop aktiviert @ {activation_price:.2f} (Callback: {callback_pct*100:.2f}%)")
        else:
            logger.warning("Trailing-Stop fehlgeschlagen - läuft mit Static SL")
        
        # Telegram Benachrichtigung
        channel_state = engine.get_channel_state(df)
        msg = (
            f"🚀 KBot Trade eröffnet\n\n"
            f"Symbol: {symbol}\n"
            f"Richtung: {side.upper()}\n"
            f"Entry: {entry_price:.2f}\n"
            f"Stop-Loss: {stop_loss:.2f}\n"
            f"Größe: {contracts:.6f}\n"
            f"Risiko: {risk_pct*100:.1f}%\n\n"
            f"Channel: {channel_state.bot:.2f} - {channel_state.top:.2f}\n"
            f"Trend: {'🟢 Bullish' if channel_state.trend else '🔴 Bearish'}"
        )
        send_message(telegram_config['bot_token'], telegram_config['chat_id'], msg)
        
        print(f"\n✅ POSITION ERÖFFNET: {side.upper()} @ {entry_price:.2f}")
        print(f"   SL: {stop_loss:.2f}")
        print(f"   TSL aktiviert @ {activation_price:.2f}\n")
        
        return True
        
    except Exception as e:
        logger.error(f"Fehler beim Eröffnen der Position: {e}", exc_info=True)
        # Aufräumen bei Fehler
        housekeeper_routine(exchange, symbol, logger)
        return False


# --------------------------------------------------------------------------- #
# Hauptfunktion: Signal prüfen und handeln
# --------------------------------------------------------------------------- #
def check_and_open_new_position(exchange: Exchange, params: dict, 
                                 telegram_config: dict, logger: logging.Logger):
    """
    Prüft auf neue Handelssignale und eröffnet ggf. eine Position.
    
    Args:
        exchange: Exchange-Objekt
        params: Strategie-Parameter
        telegram_config: Telegram-Konfiguration
        logger: Logger
    """
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    symbol_timeframe = f"{symbol.replace('/', '-')}_{timeframe}"
    
    # Trade-Lock prüfen
    if is_trade_locked(symbol_timeframe):
        print(f"⏳ Trade für {symbol_timeframe} gesperrt – überspringe.")
        return
    
    # Prüfen ob bereits eine Position offen ist
    existing_positions = exchange.fetch_open_positions(symbol)
    if existing_positions:
        logger.info(f"Position bereits offen für {symbol} – überspringe Signal-Check.")
        return
    
    try:
        print(f"\n🔍 Prüfe Signal für {symbol} ({timeframe})...")
        
        # Daten laden
        strategy_params = params.get('strategy', {})
        atr_period = strategy_params.get('atr_period', 200)
        
        data = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=max(500, atr_period + 100))
        
        if data.empty or len(data) < atr_period + 10:
            logger.warning(f"Nicht genug Daten: {len(data)} < {atr_period + 10}")
            return
        
        # Stoch‑RSI Engine initialisieren
        engine = StochRSIEngine(settings=strategy_params)
        
        # Daten verarbeiten
        processed_data = engine.process_dataframe(data)
        
        # Signal analysieren
        signal, reason = analyze_and_log_signal(engine, processed_data, params, logger)
        
        # Richtungsfilter prüfen
        use_longs = params.get('behavior', {}).get('use_longs', True)
        use_shorts = params.get('behavior', {}).get('use_shorts', True)
        
        if signal == 'long' and not use_longs:
            print("⚠️ Long-Signale deaktiviert in Config")
            return
        
        if signal == 'short' and not use_shorts:
            print("⚠️ Short-Signale deaktiviert in Config")
            return
        
        # Trade ausführen wenn Signal vorhanden
        if signal:
            success = open_position(exchange, engine, processed_data, signal, 
                                   params, telegram_config, logger)
            
            if success:
                # Trade-Lock setzen
                lock_duration = calculate_lock_duration(timeframe)
                set_trade_lock(symbol_timeframe, lock_duration)
                logger.info(f"Trade-Lock gesetzt für {lock_duration} Minuten")
        
    except Exception as e:
        logger.error(f"Fehler bei Signal-Check: {e}", exc_info=True)


# --------------------------------------------------------------------------- #
# Voller Trade-Zyklus
# --------------------------------------------------------------------------- #
def full_trade_cycle(exchange: Exchange, params: dict, 
                     telegram_config: dict, logger: logging.Logger):
    """
    Führt einen kompletten Trade-Zyklus durch:
    1. Bestehende Position managen (wenn vorhanden)
    2. Neue Signale prüfen (wenn keine Position)
    
    Args:
        exchange: Exchange-Objekt
        params: Strategie-Parameter
        telegram_config: Telegram-Konfiguration
        logger: Logger
    """
    symbol = params['market']['symbol']
    
    try:
        # Bestehende Position prüfen
        positions = exchange.fetch_open_positions(symbol)
        
        if positions:
            pos = positions[0]
            pnl = float(pos.get('unrealizedPnl', 0))
            pnl_pct = float(pos.get('percentage', 0))
            
            print(f"\n📈 Offene Position: {pos['side'].upper()}")
            print(f"   Entry: {pos['entryPrice']:.2f}")
            print(f"   PnL: {pnl:.2f} USDT ({pnl_pct:.2f}%)")
            
            logger.info(f"Position aktiv: {pos['side']} @ {pos['entryPrice']}, PnL: {pnl_pct:.2f}%")
            return
        
        # Keine Position - Signal prüfen
        check_and_open_new_position(exchange, params, telegram_config, logger)
        
    except Exception as e:
        logger.error(f"Fehler im Trade-Zyklus: {e}", exc_info=True)
