# src/stbot/strategy/trade_rules.py
"""
Gemeinsame Handelsregeln (Einstieg, Sizing, Ausstieg) fuer ALLE Pfade:
  - analysis/backtester.py          (Optuna-Optimizer, show_results Einzelanalyse)
  - analysis/portfolio_simulator.py (woechentliche Portfolio-Auswahl, show_results Portfolio)
  - analysis/interactive_status.py  (Trade-Charts)
  - utils/trade_manager.py          (Live)
  - show_chart.py                   (Signal-Chart)
Live fuehrt den Ausstieg ueber Bitget-Orders aus (SL-Trigger + Trailing-Stop mit
Aktivierungspreis und Callback); simulate_exit() bildet genau diese zwei Orders nach.

Hintergrund (Live-Validierung 2026-10-11, 22 Live-Trades seit 28.08.): die drei
Pfade liefen auseinander -- Energie/Avalanche/Streak/Wochentrend-Filter gab es
nur im Backtester, Live filtert zusaetzlich immer per HTF-EMA20/50 (das kein
Backtest kannte), der Volumen-Filter im Backtester/Simulator nahm den Schnitt der
letzten 20 Kerzen des GESAMTEN Datensatzes statt der 20 Kerzen vor dem Signal,
und der Simulator kannte weder die Bitget-Mindestmenge (AAVE/6h war jede Woche im
Team, konnte live aber nie handeln) noch die Liquidation vor dem SL (Hebel x
SL-Abstand > 100 %). Deshalb steckt jede Signal-/Sizing-Regel jetzt genau HIER
und alle drei Pfade rufen dieselben Funktionen auf.
"""
import bisect
import math

import numpy as np
import pandas as pd
import ta

from stbot.strategy.sr_engine import SREngine

BULLISH, BEARISH, NEUTRAL = "BULLISH", "BEARISH", "NEUTRAL"

VOLUME_LOOKBACK = 20          # Kerzen fuer den Volumen-Schnitt (inkl. Signal-Kerze)
VOLUME_MIN_RATIO = 1.2        # Breakout braucht >= 120 % des Schnitts
MAX_EFFECTIVE_LEVERAGE = 10   # Hardcap Notional <= Kapital * 10
LIQ_SAFETY = 0.8              # SL-Abstand darf hoechstens 80 % des Liquidationsabstands (1/Hebel) sein
WEEKLY_HISTORY_DAYS = 400     # Live: Tageskerzen-Historie fuer den Wochentrend

_PD_RESAMPLE = {
    '5m': '5min', '15m': '15min', '30m': '30min',
    '1h': '1h', '2h': '2h', '4h': '4h', '6h': '6h', '1d': '1D',
    # Wochen fest Mo 00:00 -> Mo 00:00 UTC (closed/label='left'): liefert fuer
    # Signal-Kerzen (Backtest) und Tageskerzen (Live) exakt dieselben Wochen.
    # Das fruehere '1W' (= W-SUN, closed='right') schob Sonntags-Intraday-Kerzen
    # in die Folgewoche und machte den Bias erst eine Woche spaeter gueltig.
    '1w': 'W-MON',
}

MIN_NOTIONAL_USDT = 5.0       # Bitget-Mindest-Ordervolumen


# ---------------------------------------------------------------------------
# Indikatoren / Signal-Frame
# ---------------------------------------------------------------------------
def prepare_signal_frame(df: pd.DataFrame, strategy_params: dict) -> pd.DataFrame:
    """OHLCV (nur abgeschlossene Kerzen) -> ATR, Volumen-Schnitt, Momentum-Spalten, sr_signal."""
    if df is None or df.empty:
        return df
    df = df.copy()
    df['atr'] = ta.volatility.AverageTrueRange(
        high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
    df['vol_avg'] = df['volume'].rolling(VOLUME_LOOKBACK, min_periods=1).mean()

    if strategy_params.get('use_avalanche_filter', False):
        abs_ret = df['close'].pct_change().fillna(0.0).abs()
        df['avalanche_percentile'] = abs_ret.rolling(window=101, min_periods=51).apply(
            lambda x: (x[:-1] < x[-1]).mean() * 100.0, raw=True)
    if strategy_params.get('use_energy_filter', False) or strategy_params.get('use_energy_streak_filter', False):
        energy = df['close'].diff().fillna(0.0) ** 2
        df['energy_zscore'] = (energy - energy.rolling(50).mean()) / energy.rolling(50).std()
    if strategy_params.get('use_energy_streak_filter', False):
        ez = df['energy_zscore']
        df['energy_rising_streak'] = (ez > ez.shift(1)) & (ez.shift(1) > ez.shift(2))

    df.dropna(subset=['atr'], inplace=True)
    return SREngine(settings=strategy_params).process_dataframe(df)


# ---------------------------------------------------------------------------
# Trend-Bias (HTF / Woche)
# ---------------------------------------------------------------------------
def compute_bias_series(ohlc: pd.DataFrame, rule_tf: str, ema_period: int = 20):
    """
    EMA-Bias auf resampleten Kerzen. Bias einer Periode gilt erst NACH ihrem Schluss
    (kein Look-Ahead).
    Rueckgabe (times, values) -- leer, wenn nicht berechenbar (-> NEUTRAL).
    """
    pd_rule = _PD_RESAMPLE.get(rule_tf)
    if not pd_rule or ohlc is None or ohlc.empty:
        return [], []
    try:
        htf = ohlc[['open', 'high', 'low', 'close']].resample(pd_rule, closed='left', label='left').agg(
            {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        if len(htf) < ema_period + 5:
            return [], []
        ema = htf['close'].ewm(span=ema_period, adjust=False).mean()
        vals = np.where(htf['close'] > ema, BULLISH, np.where(htf['close'] < ema, BEARISH, NEUTRAL))
        # Gueltig ab dem ENDE der eigenen Periode (Label + Periodenlaenge). Frueher:
        # ab dem Label der naechsten vorhandenen Periode -- live gibt es die erst,
        # wenn deren erste Kerze geschlossen ist (Wochentrend: Montag 24:00 statt
        # 00:00), im Backtest sofort -> Live und Backtest wichen einen Tag ab.
        # Eine noch laufende letzte Periode endet in der Zukunft und wird von
        # bias_at() nie gewaehlt.
        ends = htf.index + pd.tseries.frequencies.to_offset(pd_rule)
        return list(ends), list(vals)
    except Exception:
        return [], []


def bias_at(times, values, ts) -> str:
    if not times:
        return NEUTRAL
    pos = bisect.bisect_right(times, ts) - 1
    return values[pos] if pos >= 0 else NEUTRAL


def build_bias_context(signal_df: pd.DataFrame, timeframe: str, strategy_params: dict,
                       htf: str = None, weekly_source: pd.DataFrame = None) -> dict:
    """
    Bias-Zeitreihen fuer use_htf_filter / use_weekly_trend_filter.
    weekly_source: optionaler laengerer OHLC-Frame (Live: Tageskerzen), sonst signal_df.
    """
    ctx = {'htf': ([], []), 'weekly': ([], [])}
    if strategy_params.get('use_htf_filter', False) and htf:
        ctx['htf'] = compute_bias_series(signal_df, htf, 20)
    if strategy_params.get('use_weekly_trend_filter', False):
        if weekly_source is None:
            weekly_source = signal_df
        ctx['weekly'] = compute_bias_series(weekly_source, '1w', strategy_params.get('weekly_trend_ema', 8))
        # Ohne berechenbaren Wochentrend wird NICHT gehandelt -- in allen Pfaden gleich.
        # (Vorher fiel der Backtest still auf die Signal-Kerzen zurueck, waehrend Live
        # mit leerer Tagesquelle abbrach.)
        ctx['weekly_missing'] = not ctx['weekly'][0]
    return ctx


# ---------------------------------------------------------------------------
# Einstiegsentscheidung
# ---------------------------------------------------------------------------
def entry_side(candle: pd.Series, ts, strategy_params: dict, bias_ctx: dict = None,
               apply_weekly: bool = True):
    """'buy' / 'sell' / None fuer die abgeschlossene Kerze `candle` (Index-Zeit `ts`)."""
    sig = candle.get('sr_signal', 0)
    if sig not in (1, -1):
        return None
    vol_avg = candle.get('vol_avg', np.nan)
    if pd.notna(vol_avg) and candle.get('volume', 0) < vol_avg * VOLUME_MIN_RATIO:
        return None
    side = 'buy' if sig == 1 else 'sell'
    bias_ctx = bias_ctx or {}

    htf_bias = bias_at(*bias_ctx.get('htf', ([], [])), ts)
    if (htf_bias == BULLISH and side == 'sell') or (htf_bias == BEARISH and side == 'buy'):
        return None

    if strategy_params.get('use_avalanche_filter', False):
        v = candle.get('avalanche_percentile', np.nan)
        if not (pd.notna(v) and v > strategy_params.get('avalanche_percentile_threshold', 60)):
            return None
    if strategy_params.get('use_energy_filter', False):
        v = candle.get('energy_zscore', np.nan)
        if not (pd.notna(v) and v > strategy_params.get('min_energy_zscore', 0.0)):
            return None
    if strategy_params.get('use_energy_streak_filter', False):
        if not bool(candle.get('energy_rising_streak', False)):
            return None

    if apply_weekly and strategy_params.get('use_weekly_trend_filter', False):
        if bias_ctx.get('weekly_missing', False):
            return None
        wb = bias_at(*bias_ctx.get('weekly', ([], [])), ts)
        if (wb == BEARISH and side == 'buy') or (wb == BULLISH and side == 'sell'):
            return None
    return side


# ---------------------------------------------------------------------------
# Risiko / Sizing / Ausstieg
# ---------------------------------------------------------------------------
def sl_distance(candle: pd.Series, risk_params: dict) -> float:
    entry = candle['close']
    atr = candle.get('atr', np.nan)
    min_sl = entry * risk_params.get('min_sl_pct', 0.3) / 100.0
    if pd.isna(atr) or atr <= 0:
        return min_sl
    return max(atr * risk_params.get('atr_multiplier_sl', 2.0), min_sl)


def position_notional(equity: float, risk_params: dict, sl_pct: float) -> float:
    if sl_pct <= 0 or equity <= 0:
        return 0.0
    risk_usd = equity * risk_params.get('risk_per_trade_pct', 1.0) / 100.0
    return min(risk_usd / sl_pct, equity * MAX_EFFECTIVE_LEVERAGE)


def safe_leverage(config_leverage, sl_pct: float) -> int:
    """
    Hebel so begrenzen, dass der SL sicher VOR der Isolated-Liquidation (~1/Hebel)
    liegt. Positionsgroesse/Risiko aendern sich dadurch nicht -- nur die gebundene
    Margin. Live beobachtet: ARB/4h (Hebel 18, SL 6.9 %) am 28.09. bei -5.5 %
    liquidiert statt beim SL.
    """
    lev = int(config_leverage or 10)
    if sl_pct > 0:
        lev = min(lev, max(1, math.floor(LIQ_SAFETY / sl_pct)))
    return max(1, lev)


def meets_min_amount(notional: float, entry_price: float, min_amount) -> bool:
    if not min_amount or entry_price <= 0:
        return True
    return notional / entry_price >= float(min_amount)


FEE_PCT = 0.06 / 100          # Bitget Taker je Seite


def new_position(side: str, entry_price: float, sl_dist: float, risk_params: dict) -> dict:
    """
    Positions-Zustand wie die beiden Live-Orders ihn festlegen: harter SL-Trigger bei
    entry -/+ sl_dist, Trailing-Stop aktiv ab entry +/- sl_dist*activation_rr mit
    Rueckzug callback_rate. Es gibt KEINE feste TP-Order (live nie platziert).
    side: 'buy'/'sell' oder 'long'/'short'.
    """
    long_ = side in ('buy', 'long')
    act_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
    return {
        'side': 'long' if long_ else 'short',
        'entry_price': entry_price,
        'sl_dist': sl_dist,
        'stop_loss': entry_price - sl_dist if long_ else entry_price + sl_dist,
        'activation_price': entry_price + sl_dist * act_rr if long_ else entry_price - sl_dist * act_rr,
        'callback_rate': risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100.0,
        'trailing_active': False,
        'peak_price': entry_price,
    }


def candle_path(o, h, l, c, fine_slice=None) -> list:
    """
    Preis-Pfad innerhalb einer Kerze: bevorzugt aus echten Feinkerzen (je Feinkerze
    [open, Extrem in Farbrichtung, anderes Extrem, close]), sonst 4-Punkte-Naeherung.
    """
    if fine_slice is not None and not fine_slice.empty:
        path = []
        for fo, fh, fl, fc in fine_slice[['open', 'high', 'low', 'close']].itertuples(index=False):
            path.extend([fo, fl, fh, fc] if fc >= fo else [fo, fh, fl, fc])
        if path:
            return path
    return [o, l, h, c] if c >= o else [o, h, l, c]


def simulate_exit(pos: dict, path) -> float | None:
    """Laeuft den Preis-Pfad ab und mutiert pos (Trailing). Rueckgabe Exit-Preis oder None."""
    cb = pos['callback_rate']
    for p in path:
        if pos['side'] == 'long':
            if p <= pos['stop_loss']:
                return pos['stop_loss']
            if not pos['trailing_active'] and p >= pos['activation_price']:
                pos['trailing_active'] = True
                pos['peak_price'] = p
            if pos['trailing_active']:
                pos['peak_price'] = max(pos['peak_price'], p)
                trail = pos['peak_price'] * (1 - cb)
                if p <= trail:
                    return trail
        else:
            if p >= pos['stop_loss']:
                return pos['stop_loss']
            if not pos['trailing_active'] and p <= pos['activation_price']:
                pos['trailing_active'] = True
                pos['peak_price'] = p
            if pos['trailing_active']:
                pos['peak_price'] = min(pos['peak_price'], p)
                trail = pos['peak_price'] * (1 + cb)
                if p >= trail:
                    return trail
    return None


def trade_net_pnl(pos: dict, exit_price: float, notional: float) -> float:
    move = (exit_price / pos['entry_price'] - 1) if pos['side'] == 'long' else (1 - exit_price / pos['entry_price'])
    return notional * move - notional * FEE_PCT * 2


_MIN_AMOUNT_CACHE = None


def bitget_min_amounts() -> dict:
    """
    Bitget-Mindestmengen (Kontrakte) je Symbol, einmal pro Prozess aus den
    oeffentlichen Marktdaten. Live bricht bei Unterschreitung ab -- ohne diese
    Pruefung waehlte die Portfolio-Simulation Strategien, die beim echten
    Kontostand nie handeln koennen (AAVE/6h, 06.09.-11.10.2026 jede Woche im
    Team, 0 Live-Trades).
    """
    global _MIN_AMOUNT_CACHE
    if _MIN_AMOUNT_CACHE is None:
        try:
            import ccxt
            ex = ccxt.bitget({'options': {'defaultType': 'swap'}})
            ex.load_markets()
            _MIN_AMOUNT_CACHE = {
                sym: ((m.get('limits') or {}).get('amount') or {}).get('min')
                for sym, m in ex.markets.items()
            }
        except Exception as e:
            print(f"WARNUNG: Bitget-Mindestmengen nicht ladbar ({e}) -- OHNE Mindestmengen-Pruefung.")
            _MIN_AMOUNT_CACHE = {}
    return _MIN_AMOUNT_CACHE


def order_allowed(notional: float, entry_price: float, min_amount) -> bool:
    """Gleiche Ablehnungsregel wie Live: Bitget-Mindestvolumen (USDT) und Mindestmenge (Kontrakte)."""
    return notional >= MIN_NOTIONAL_USDT and meets_min_amount(notional, entry_price, min_amount)
