# /root/stbot/src/stbot/utils/trade_manager.py
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

# Imports angepasst auf stbot
from stbot.strategy.trade_rules import (prepare_signal_frame, build_bias_context, entry_side,
                                        sl_distance, position_notional, safe_leverage,
                                        order_allowed, WEEKLY_HISTORY_DAYS)
from stbot.utils.exchange import Exchange
from stbot.utils.telegram import send_message, send_photo
from stbot.utils.timeframe_utils import determine_htf

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ARTIFACTS_PATH = os.path.join(PROJECT_ROOT, 'artifacts')
DB_PATH = os.path.join(ARTIFACTS_PATH, 'db')
TRADE_LOCK_FILE = os.path.join(DB_PATH, 'trade_lock.json')

class Bias:
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

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

# ─── Chart-Generierung: SR-Breakout-Kerzendiagramm ───────────────────────────

def _generate_stbot_chart_png(processed_df: pd.DataFrame, signal_side: str,
                               entry_price: float, sl_price: float, tp_price: float,
                               symbol: str, timeframe: str, rr: float,
                               market_bias: str, n_candles: int = 60) -> str:
    """
    Zeichnet Kerzendiagramm mit EMA20/50, SR-Breakout-Marker und Entry/SL/TP-Tags.
    Gibt Pfad zur temporaeren PNG-Datei zurueck (oder None bei Fehler).
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        return None

    if processed_df is None or processed_df.empty:
        return None

    display_df = processed_df[
        [c for c in ['open', 'high', 'low', 'close', 'volume', 'atr', 'sr_signal']
         if c in processed_df.columns]
    ].iloc[-n_candles:].reset_index(drop=True)

    n = len(display_df)
    if n < 5:
        return None

    opens  = display_df['open'].values
    highs  = display_df['high'].values
    lows   = display_df['low'].values
    closes = display_df['close'].values

    fig, ax = plt.subplots(figsize=(14, 7))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')
    bar_w = 0.6

    # 1. Y-Limits
    y_min = float(lows.min())
    y_max = float(highs.max())
    for p in filter(None, [entry_price, sl_price, tp_price]):
        y_min = min(y_min, float(p) * 0.999)
        y_max = max(y_max, float(p) * 1.001)
    margin = (y_max - y_min) * 0.12
    y_lo, y_hi = y_min - margin, y_max + margin
    ax.set_xlim(-1, n + 1)
    ax.set_ylim(y_lo, y_hi)

    def _in_range(price):
        return y_lo < float(price) < y_hi

    # 2. Risiko/Reward-Zonen
    if sl_price and _in_range(sl_price):
        ax.axhspan(min(sl_price, entry_price), max(sl_price, entry_price),
                   color='#ff1744', alpha=0.07, zorder=1)
    if tp_price and _in_range(tp_price):
        ax.axhspan(min(tp_price, entry_price), max(tp_price, entry_price),
                   color='#00c853', alpha=0.07, zorder=1)

    # 3. EMA20 / EMA50
    close_series = pd.Series(closes)
    ema20 = close_series.ewm(span=20, adjust=False).mean()
    ema50 = close_series.ewm(span=50, adjust=False).mean()
    xs = list(range(n))
    ax.plot(xs, ema20, color='#00bcd4', linewidth=1.1, label='EMA20', zorder=4, alpha=0.85)
    ax.plot(xs, ema50, color='#ff9800', linewidth=1.1, label='EMA50', zorder=4, alpha=0.85)

    # 4. Kerzen + SR-Signal-Marker
    sr_signals = display_df['sr_signal'].values if 'sr_signal' in display_df.columns else None
    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        color = '#26a69a' if c >= o else '#ef5350'
        ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=2)
        body_h = max(abs(c - o), (h - l) * 0.005)
        ax.add_patch(mpatches.FancyBboxPatch(
            (i - bar_w / 2, min(o, c)), bar_w, body_h,
            boxstyle="square,pad=0", linewidth=0, facecolor=color, zorder=3,
        ))
        # Vorherige SR-Breakout-Punkte als kleine Dreiecke
        if sr_signals is not None and i < n - 1 and sr_signals[i] != 0:
            marker = '^' if sr_signals[i] == 1 else 'v'
            ypos = l * 0.9985 if sr_signals[i] == 1 else h * 1.0015
            if _in_range(ypos):
                ax.scatter(i, ypos, marker=marker, color='#888888', s=20, zorder=5, alpha=0.6)

    # 5. Breakout-Pfeil am letzten Kerze
    arrow_color = '#00e676' if signal_side == 'buy' else '#ff1744'
    arrow_dir = 1 if signal_side == 'buy' else -1
    arrow_y = lows[-1] - (y_hi - y_lo) * 0.04 if signal_side == 'buy' else highs[-1] + (y_hi - y_lo) * 0.04
    if _in_range(arrow_y):
        ax.annotate('', xy=(n - 1, closes[-1]), xytext=(n - 1, arrow_y),
                    arrowprops=dict(arrowstyle=f'->', color=arrow_color,
                                   lw=2.0, mutation_scale=16),
                    zorder=9)

    # 6. Entry/SL/TP Preis-Tags
    def _price_tag(price, label, color, lw=1.5, ls='--'):
        if not price or not _in_range(price):
            return
        ax.axhline(price, color=color, linewidth=lw, linestyle=ls, zorder=6)
        ax.text(n - 0.3, price, f'  {label}: {price:.6g}  ',
                color='#0d1117', fontsize=8.5, va='center', ha='right',
                fontweight='bold', zorder=8,
                bbox=dict(facecolor=color, edgecolor='none', alpha=0.92,
                          boxstyle='square,pad=0.25'))

    _price_tag(tp_price,    'TSL',   '#00c853')
    _price_tag(entry_price, 'Entry', '#ffd700')
    _price_tag(sl_price,    'SL',    '#ff1744')

    # 7. SR-Zone-Band um Entry (Breakout-Zone)
    current_atr = float(display_df['atr'].iloc[-1]) if 'atr' in display_df.columns else (y_hi - y_lo) * 0.01
    if not pd.isna(current_atr) and current_atr > 0:
        zone_hi = entry_price + current_atr * 0.3
        zone_lo = entry_price - current_atr * 0.3
        if _in_range(zone_hi) and _in_range(zone_lo):
            ax.axhspan(zone_lo, zone_hi, color='#ffd700', alpha=0.06, zorder=1)
            ax.text(0.5, 0.01, 'SR-Breakout-Zone', transform=ax.transAxes,
                    fontsize=7, color='#ffd700', ha='center', va='bottom',
                    alpha=0.55, zorder=7)

    # 8. Infobox
    side_label = 'LONG  (BUY)  ▲' if signal_side == 'buy' else 'SHORT (SELL) ▼'
    sl_pct = abs(entry_price - sl_price) / entry_price * 100 if sl_price else 0
    tp_pct = abs(tp_price - entry_price) / entry_price * 100 if tp_price else 0
    atr_pct = current_atr / entry_price * 100 if entry_price else 0
    bias_str = f'  [{market_bias}]' if market_bias and market_bias != 'NEUTRAL' else ''
    info_lines = [
        f"{side_label}   TSL ab {rr:.2f}R{bias_str}",
        f"Signal:  SR-Breakout (Vol.-bestaetigt)",
        f"ATR:     {current_atr:.6g}  ({atr_pct:.2f}%)",
        f"SL:      {sl_pct:.2f}%   TSL-Akt.: {tp_pct:.2f}%",
        "EMA20 (cyan)  EMA50 (orange)",
    ]
    ax.text(0.01, 0.98, '\n'.join(info_lines),
            transform=ax.transAxes, fontsize=8, va='top', ha='left',
            color='#cccccc', fontfamily='monospace',
            bbox=dict(facecolor='#1a2332', edgecolor='#2a3a4a',
                      alpha=0.88, boxstyle='round,pad=0.5'),
            zorder=10)

    # 9. Styling
    ax.set_title(
        f"STBOT  |  {symbol}  {timeframe}  |  {side_label.strip()}  |  letzte {n} Kerzen",
        color='#e0e0e0', fontsize=11, pad=10,
    )
    ax.tick_params(colors='#888888', labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor('#2a3a4a')
    ax.set_xticks([])
    ax.yaxis.tick_right()
    ax.grid(axis='y', color='#1e2a3a', linewidth=0.4, zorder=0)
    plt.tight_layout()

    tmp_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    from datetime import timezone
    ts       = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    sym_safe = symbol.replace('/', '-').replace(':', '-')
    path     = os.path.join(tmp_dir, f'stbot_entry_{sym_safe}_{timeframe}_{ts}.png')
    fig.savefig(path, dpi=130, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _send_stbot_chart(processed_df: pd.DataFrame, signal_side: str,
                       entry_price: float, sl_price: float, tp_price: float,
                       symbol: str, timeframe: str, rr: float,
                       market_bias: str, telegram_config: dict, logger):
    """Generiert SR-Breakout-Chart und sendet ihn via Telegram."""
    if not telegram_config or not telegram_config.get('bot_token') or not telegram_config.get('chat_id'):
        return
    try:
        path = _generate_stbot_chart_png(
            processed_df, signal_side, entry_price, sl_price, tp_price,
            symbol, timeframe, rr, market_bias,
        )
        if path and os.path.exists(path):
            side_label = 'LONG' if signal_side == 'buy' else 'SHORT'
            caption = (
                f"STBOT | {symbol} ({timeframe})\n"
                f"{side_label} @ {entry_price:.6g}  |  SL: {sl_price:.6g}  |  TSL-Akt.: {tp_price:.6g}\n"
                f"TSL-Aktivierung bei {rr:.2f}R (keine TP-Order)"
            )
            send_photo(telegram_config.get('bot_token'), telegram_config.get('chat_id'),
                       path, caption)
            os.remove(path)
    except Exception as e:
        logger.warning(f"stbot-Chart senden fehlgeschlagen: {e}")


MAX_SIGNAL_AGE = pd.Timedelta(minutes=20)   # Cron alle 15 Min + Puffer


def _candle_key(symbol_timeframe):
    return f"{symbol_timeframe}_last_evaluated_candle"


def is_candle_evaluated(symbol_timeframe, candle_ts) -> bool:
    return load_or_create_trade_lock().get(_candle_key(symbol_timeframe)) == pd.Timestamp(candle_ts).isoformat()


def mark_candle_evaluated(symbol_timeframe, candle_ts):
    trade_lock = load_or_create_trade_lock()
    trade_lock[_candle_key(symbol_timeframe)] = pd.Timestamp(candle_ts).isoformat()
    save_trade_lock(trade_lock)


def last_closed_candle_ts(exchange, timeframe):
    """Open-Zeit der zuletzt geschlossenen Kerze (UTC), rein aus der Uhrzeit."""
    tf_sec = exchange.exchange.parse_timeframe(timeframe)
    now = pd.Timestamp.now(tz='UTC')
    return now.floor(f"{tf_sec}s") - pd.Timedelta(seconds=tf_sec)


def check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger):
    """
    Live-Einstieg -- gleiche Regeln wie Optimizer-Backtester und Portfolio-Simulator
    (strategy/trade_rules.py). Jede geschlossene Kerze wird genau EINMAL bewertet,
    so wie im Backtest: ein Signal, das waehrend einer offenen Position kam, verfaellt
    (vorher stieg Live nach dem Exit verspaetet auf das alte Signal ein, z.B. SOL
    18.09.2026 05:15). Die frueheren Live-only-Sperren (1.5 %-Re-Entry-Abstand,
    60-Min-Trade-Lock) und der Live-only HTF-EMA20/50-Filter sind entfernt -- kein
    Backtest kannte sie (Live-Validierung 2026-10-11).
    """
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    symbol_timeframe = f"{symbol.replace('/', '-')}_{timeframe}"

    try:
        logger.info(f"Prüfe StBot (SRv2) Signal für {symbol} ({timeframe})...")

        recent_data = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=1000)
        if recent_data.empty or len(recent_data) < 50:
            logger.warning(f"Nicht genügend OHLCV-Daten (gefunden: {len(recent_data)}) – überspringe.")
            return

        strat_params = params.get('strategy', {})
        risk_params = params.get('risk', {})

        signal_ts = recent_data.index[-1]
        if is_candle_evaluated(symbol_timeframe, signal_ts):
            logger.info(f"Kerze {signal_ts} bereits ausgewertet – warte auf die nächste.")
            return

        # Nur frische Signale handeln: der Backtest steigt immer direkt bei Kerzenschluss
        # ein. Ist die Kerze schon laenger zu (Strategie gerade neu ins Team gekommen,
        # verpasster Cron-Lauf), verfaellt das Signal -- sonst Einstieg zu spaeterem Preis
        # (live gesehen 2026-10-14: XRP/4h nach Team-Wechsel 2h45 nach Kerzenschluss).
        candle_close = signal_ts + pd.Timedelta(seconds=exchange.exchange.parse_timeframe(timeframe))
        signal_age = pd.Timestamp.now(tz='UTC') - candle_close
        if signal_age > MAX_SIGNAL_AGE:
            logger.info(f"Kerze {signal_ts} schloss vor {signal_age} (> {MAX_SIGNAL_AGE}) -- Signal verfaellt.")
            mark_candle_evaluated(symbol_timeframe, signal_ts)
            return

        processed_data = prepare_signal_frame(recent_data, strat_params)
        if processed_data is None or processed_data.empty or processed_data.index[-1] != signal_ts:
            logger.warning("Signal-Frame leer/unvollständig – überspringe.")
            return
        current_candle = processed_data.iloc[-1]

        weekly_source = None
        if strat_params.get('use_weekly_trend_filter', False):
            weekly_source = exchange.fetch_recent_ohlcv(symbol, '1d', limit=WEEKLY_HISTORY_DAYS)
        bias_ctx = build_bias_context(recent_data, timeframe, strat_params,
                                      params['market'].get('htf'), weekly_source=weekly_source)

        signal_side = entry_side(current_candle, signal_ts, strat_params, bias_ctx)
        if not signal_side:
            logger.info("Kein Signal – überspringe.")
            mark_candle_evaluated(symbol_timeframe, signal_ts)
            return

        if exchange.fetch_open_positions(symbol):
            logger.info("Position bereits offen – Signal verfällt.")
            mark_candle_evaluated(symbol_timeframe, signal_ts)
            return

        # Positionsgroesse auf Basis des realisierten Kapitals -- wie Backtester/Simulator
        balance = exchange.fetch_realized_equity_usdt()
        if balance <= 0:
            logger.error("Kein USDT-Guthaben.")
            return

        entry_price = current_candle['close']
        sl_dist = sl_distance(current_candle, risk_params)
        if sl_dist <= 0:
            mark_candle_evaluated(symbol_timeframe, signal_ts)
            return
        sl_pct = sl_dist / entry_price

        notional = position_notional(balance, risk_params, sl_pct)
        amount = notional / entry_price
        min_amount = exchange.markets[symbol].get('limits', {}).get('amount', {}).get('min', 0.0)
        if not order_allowed(notional, entry_price, min_amount):
            logger.error(f"Ordergröße {amount:.6f} ({notional:.2f} USDT) < Mindestmenge {min_amount} – überspringe.")
            mark_candle_evaluated(symbol_timeframe, signal_ts)
            return

        leverage = safe_leverage(risk_params.get('leverage', 10), sl_pct)
        if leverage < int(risk_params.get('leverage', 10)):
            logger.info(f"Hebel {risk_params.get('leverage')} -> {leverage} begrenzt "
                        f"(SL {sl_pct * 100:.2f} % muss vor der Liquidation liegen).")
        margin_mode = risk_params.get('margin_mode', 'isolated')
        exchange.set_margin_mode(symbol, margin_mode)
        exchange.set_leverage(symbol, leverage)

        act_rr = risk_params.get('trailing_stop_activation_rr', 1.5)
        callback_pct = risk_params.get('trailing_stop_callback_rate_pct', 0.5) / 100.0
        if signal_side == 'buy':
            sl_price = entry_price - sl_dist
            act_price = entry_price + sl_dist * act_rr
            pos_side, tsl_side = 'buy', 'sell'
        else:
            sl_price = entry_price + sl_dist
            act_price = entry_price - sl_dist * act_rr
            pos_side, tsl_side = 'sell', 'buy'

        logger.info(f"Eröffne {pos_side.upper()}-Position: {amount:.6f} @ ${entry_price:.6f} | "
                    f"Risk: {balance * risk_params.get('risk_per_trade_pct', 1.0) / 100:.2f} USDT | Hebel {leverage}")

        entry_order = exchange.create_market_order(
            symbol, pos_side, amount, {'leverage': leverage, 'marginMode': margin_mode})
        mark_candle_evaluated(symbol_timeframe, signal_ts)
        if not entry_order: return

        time.sleep(2)
        position = exchange.fetch_open_positions(symbol)
        if not position: return

        contracts = float(position[0]['contracts'])
        sl_rounded = float(exchange.exchange.price_to_precision(symbol, sl_price))
        act_rounded = float(exchange.exchange.price_to_precision(symbol, act_price))
        exchange.place_trigger_market_order(symbol, tsl_side, contracts, sl_rounded, {'reduceOnly': True})
        exchange.place_trailing_stop_order(symbol, tsl_side, contracts, act_price, callback_pct, {'reduceOnly': True})

        if telegram_config and telegram_config.get('bot_token') and telegram_config.get('chat_id'):
            msg = (
                f"STBOT (SRv2): {symbol} ({timeframe})\n"
                f"- Richtung: {pos_side.upper()}\n"
                f"- Entry: ${entry_price:.6f}\n"
                f"- SL: ${sl_rounded:.6f}\n"
                f"- TSL-Aktivierung: ${act_rounded:.6f} (Callback {callback_pct * 100:.2f} %)"
            )
            send_message(telegram_config['bot_token'], telegram_config['chat_id'], msg)
            _send_stbot_chart(processed_data, signal_side, entry_price,
                              float(sl_rounded), float(act_rounded),
                              symbol, timeframe, act_rr, "NEUTRAL", telegram_config, logger)

        logger.info("Trade-Eröffnung erfolgreich abgeschlossen.")

    except ccxt.InsufficientFunds as e:
        logger.error(f"InsufficientFunds: {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler: {e}", exc_info=True)
        housekeeper_routine(exchange, symbol, logger)

def full_trade_cycle(exchange, model, scaler, params, telegram_config, logger):
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    symbol_timeframe = f"{symbol.replace('/', '-')}_{timeframe}"
    try:
        pos = exchange.fetch_open_positions(symbol)
        if pos:
            logger.info(f"Position offen – Management via SL/TSL.")
            # Wie im Backtest: Signale waehrend einer offenen Position verfallen.
            mark_candle_evaluated(symbol_timeframe, last_closed_candle_ts(exchange, timeframe))
        else:
            housekeeper_routine(exchange, symbol, logger)
            check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger)
    except Exception as e:
        logger.error(f"Fehler im Zyklus: {e}", exc_info=True)
        time.sleep(5)
