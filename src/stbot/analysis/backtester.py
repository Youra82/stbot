# /root/utbot2/src/stbot/analysis/backtester.py
import os
import pandas as pd
import numpy as np
import json
import sys
import bisect
import collections
from tqdm import tqdm
import ta
import math

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from stbot.utils.exchange import Exchange
from stbot.strategy.trade_rules import (prepare_signal_frame, build_bias_context, entry_side,
                                        sl_distance, position_notional, safe_leverage,
                                        new_position, candle_path, simulate_exit, trade_net_pnl,
                                        bitget_min_amounts, order_allowed, WEEKLY_HISTORY_DAYS)
from stbot.utils.timeframe_utils import determine_htf
from stbot.strategy.sr_engine import MIN_HISTORY_CANDLES

secrets_cache = None

class Bias:
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

# Feinere Timeframe je Strategie-Timeframe fuer die Intrabar-Pfad-Aufloesung
# (oraclebot-Muster). Verhaeltnis ~8-30:1, an verfuegbare Exchange-Granularitaeten
# angepasst.
FINE_TF_MAP = {
    '5m': '1m', '15m': '5m', '30m': '5m',
    '1h': '5m', '2h': '5m',
    '4h': '15m', '6h': '15m',
    '1d': '1h',
}


class LazyFineData:
    """
    On-Demand-Fetcher fuer Fein-Daten (Intrabar-Aufloesung). Laedt Fein-Kerzen
    nur fuer die Tage, an denen im Backtest tatsaechlich eine offene Position
    liegt, statt den kompletten Backtest-Zeitraum vorab herunterzuladen -- der
    weit ueberwiegende Teil der Kerzen braucht nie eine Intrabar-Aufloesung.
    Ergebnis ist identisch zum eagerly geladenen DataFrame, nur die Reihenfolge
    und Groesse der Netzwerk-Fetches aendert sich (viele kleine Tages-Fetches
    statt einem grossen Fetch ueber den gesamten Zeitraum).
    Pro (symbol, fine_tf)-Instanz wiederverwendbar -- z.B. ueber alle Optuna-
    Trials eines Optimizer-Laufs hinweg, damit einmal geladene Tage nicht
    mehrfach abgerufen werden.
    """
    # Obergrenze fuer gleichzeitig im Speicher gehaltene Tage (LRU-Eviction).
    # Live beobachtet (2026-08-24, Mini-PC/WSL, 6.3GB RAM): run_portfolio_optimizer.py
    # haelt pro bestaetigter Config eine eigene LazyFineData-Instanz offen, deren
    # _days-Cache unbegrenzt wuchs -- bei ~19 gleichzeitigen Instanzen (viele davon
    # 30m-Paare mit 1m-Feinaufloesung, der speicherhungrigsten Stufe in FINE_TF_MAP)
    # stieg der Python-Prozess auf 5.1GB RSS und wurde vom Kernel-OOM-Killer
    # abgeschossen (kompletter Absturz inkl. aller offenen Terminalfenster).
    # Ein Cache-Limit pro Instanz deckelt den Speicher unabhaengig von der Anzahl
    # der Trades/Tage; kostet im Extremfall einen erneuten Netzwerk-Fetch, falls
    # ein verdraengter Tag spaeter nochmal gebraucht wird (z.B. Schritt 3,
    # Team-Kombinationstests) -- klar der bessere Trade-off als ein Voll-Absturz.
    MAX_CACHED_DAYS = 90

    def __init__(self, symbol, fine_tf):
        self.symbol = symbol
        self.fine_tf = fine_tf
        self._days = collections.OrderedDict()
        # Fuer Live-Status-Anzeigen von aussen (siehe _LiveTicker in
        # portfolio_optimizer.py) -- zeigt, welcher Tag gerade nachgeladen
        # wird bzw. zuletzt fertig war, damit eine lange Pause bei vielen
        # offenen Positionen nicht wie ein Haenger aussieht.
        self.current_day = None
        self.days_loaded = 0

    def get_slice(self, start_ts, end_ts):
        if self.fine_tf is None:
            return None
        day = pd.Timestamp(start_ts).floor('D')
        if day in self._days:
            self._days.move_to_end(day)
        else:
            day_str = day.strftime('%Y-%m-%d')
            self.current_day = day_str
            next_day_str = (day + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            try:
                df = load_data(self.symbol, self.fine_tf, day_str, next_day_str, quiet=True)
                self._days[day] = df if df is not None and not df.empty else None
            except Exception:
                self._days[day] = None
            self.days_loaded += 1
            if len(self._days) > self.MAX_CACHED_DAYS:
                self._days.popitem(last=False)
        df = self._days[day]
        if df is None:
            return None
        return df.loc[(df.index >= start_ts) & (df.index < end_ts)]


def _get_fine_slice(fine_data, start_ts, end_ts):
    """Liest ein Fein-Daten-Fenster aus -- akzeptiert sowohl einen bereits
    komplett geladenen DataFrame (alte, eager Nutzung) als auch ein
    LazyFineData-Objekt (neue, on-demand Nutzung), per Duck-Typing."""
    if fine_data is None:
        return None
    if hasattr(fine_data, 'get_slice'):
        return fine_data.get_slice(start_ts, end_ts)
    return fine_data.loc[(fine_data.index >= start_ts) & (fine_data.index < end_ts)]


def _build_fine_path(fine_slice):
    """Kompatibilitaets-Alias -- Pfad-Logik liegt in strategy/trade_rules.candle_path."""
    return candle_path(None, None, None, None, fine_slice)

_TF_MINUTES = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '2h': 120, '4h': 240, '6h': 360, '1d': 1440}


def _warmup(timeframe):
    """
    Vorlauf vor dem Startdatum: mindestens so viele Kerzen, wie Live fuer ein Signal
    sieht (MIN_HISTORY_CANDLES), damit SR-Zonen/Indikatoren am ersten Auswertungstag
    schon identisch zu Live sind -- frueher pauschal 20 Tage (bei 2h nur 240 Kerzen).
    """
    minutes = _TF_MINUTES.get(timeframe, 60)
    return max(pd.Timedelta(days=20), pd.Timedelta(minutes=minutes * MIN_HISTORY_CANDLES))


def load_data(symbol, timeframe, start_date_str, end_date_str, quiet=False):
    global secrets_cache
    data_dir = os.path.join(PROJECT_ROOT, 'data')
    cache_dir = os.path.join(data_dir, 'cache')
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")

    try:
        if not os.path.exists(data_dir): os.makedirs(data_dir)
        os.makedirs(cache_dir, exist_ok=True)
    except OSError: return pd.DataFrame()

    if os.path.exists(cache_file):
        try:
            data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
            # Konvertiere Index zu Datetime falls nötig
            if not isinstance(data.index, pd.DatetimeIndex):
                data.index = pd.to_datetime(data.index, utc=True)

            data_start = data.index.min()
            data_end = data.index.max()
            req_start = pd.to_datetime(start_date_str, utc=True)
            req_end = pd.to_datetime(end_date_str, utc=True)

            # Puffer hinzufügen für Indikatoren
            req_start_buffer = req_start - _warmup(timeframe)

            if data_start <= req_start_buffer and data_end >= req_end:
                return data.loc[req_start_buffer:req_end]
        except Exception:
            # NICHT die Cache-Datei loeschen: bei paralleler Nutzung (z.B. mehrere
            # Optuna-/Multiprocessing-Worker) fuehrt ein einzelner transienter Lesefehler
            # sonst dazu, dass eine gueltige, von anderen Prozessen genutzte Cache-Datei
            # geloescht wird -> Netzwerk-Fetch-Sturm + Rate-Limiting (siehe 2026-07-30).
            # Ein zu kurzer/kaputter Cache wird unten ohnehin per atomarem Replace ersetzt.
            pass

    try:
        if secrets_cache is None:
            with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f: secrets_cache = json.load(f)
        
        # Prüfe auf alte 'utbot2' oder neue 'stbot' Keys
        api_setup = None
        if 'stbot' in secrets_cache: api_setup = secrets_cache['stbot'][0]
        elif 'utbot2' in secrets_cache: api_setup = secrets_cache['utbot2'][0]
        elif 'titanbot' in secrets_cache: api_setup = secrets_cache['titanbot'][0]
        
        if not api_setup: return pd.DataFrame()

        exchange = Exchange(api_setup)
        if not exchange.markets: return pd.DataFrame()

        # Hole mehr Daten für den Vorlauf (Puffer für Pivots)
        start_dt = pd.to_datetime(start_date_str, utc=True) - _warmup(timeframe) - pd.Timedelta(days=10)
        full_data = exchange.fetch_historical_ohlcv(symbol, timeframe, start_dt.strftime('%Y-%m-%d'), end_date_str, quiet=quiet)
        
        if not full_data.empty:
            # Atomar schreiben (temp-Datei + os.replace), damit parallele Leser nie eine
            # halb geschriebene Cache-Datei sehen (siehe Kommentar oben zum Race-Condition-Fix).
            tmp_file = f"{cache_file}.tmp.{os.getpid()}"
            full_data.to_csv(tmp_file)
            os.replace(tmp_file, cache_file)
            req_start_dt = pd.to_datetime(start_date_str, utc=True)
            req_end_dt = pd.to_datetime(end_date_str, utc=True)
            # Wir geben Daten AB Puffer zurück
            buffer_dt = req_start_dt - _warmup(timeframe)
            return full_data.loc[buffer_dt:req_end_dt]
        return pd.DataFrame()
    except Exception: return pd.DataFrame()


_WEEKLY_SOURCE_CACHE = {}


def load_weekly_source(symbol, first_ts, last_ts):
    """
    Tageskerzen als Quelle fuer den Wochentrend-Bias -- dieselbe Quelle wie Live
    (trade_manager: fetch_recent_ohlcv('1d', WEEKLY_HISTORY_DAYS)). Mit ~400 Tagen
    Vorlauf ist der Wochen-EMA eingeschwungen und damit unabhaengig davon, wie lang
    das jeweilige Backtest-Fenster ist (vorher: Live-Bias aus 400 Tagen, Backtest-
    Bias aus dem kurzen Signal-Fenster -> abweichende Trendwechsel).
    Gecacht, weil der Optimizer run_backtest pro Trial erneut aufruft.
    """
    start = (pd.Timestamp(first_ts) - pd.Timedelta(days=WEEKLY_HISTORY_DAYS)).strftime('%Y-%m-%d')
    end = pd.Timestamp(last_ts).strftime('%Y-%m-%d')
    key = (symbol, start, end)
    if key not in _WEEKLY_SOURCE_CACHE:
        _WEEKLY_SOURCE_CACHE[key] = load_data(symbol, '1d', start, end, quiet=True)
    return _WEEKLY_SOURCE_CACHE[key]


def run_backtest(data, strategy_params, risk_params, start_capital=1000, verbose=False, fine_data=None,
                 return_trades=False):
    """
    Einzel-Strategie-Backtest (Optuna-Optimizer, show_results Einzelanalyse).
    Einstieg, Sizing und Ausstieg ausschliesslich ueber strategy/trade_rules.py --
    dieselben Funktionen nutzen Portfolio-Simulator und Live (trade_manager.py).
    return_trades=True liefert zusaetzlich die Trade-Liste (Charts, Paritaetstests).
    """
    if data.empty or len(data) < 100:
        return {"total_pnl_pct": -100, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0,
                "end_capital": start_capital, "trades": []}

    symbol = strategy_params.get('symbol', '')
    timeframe = strategy_params.get('timeframe', '')
    htf = strategy_params.get('htf')

    try:
        processed_data = prepare_signal_frame(data, strategy_params)
    except Exception:
        return {"total_pnl_pct": -100, "end_capital": start_capital, "trades_count": 0, "trades": []}
    if processed_data is None or processed_data.empty:
        return {"total_pnl_pct": -100, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0,
                "end_capital": start_capital, "trades": []}
    weekly_source = None
    if strategy_params.get('use_weekly_trend_filter', False) and symbol:
        weekly_source = load_weekly_source(symbol, data.index[0], data.index[-1])
    bias_ctx = build_bias_context(data, timeframe, strategy_params, htf, weekly_source=weekly_source)
    min_amount = bitget_min_amounts().get(symbol) if symbol else None

    current_capital = start_capital
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    trades_count = 0
    wins_count = 0
    position = None
    trades = []
    leverage = risk_params.get('leverage', 10)
    absolute_max_notional_value = 1000000

    coarse_duration = processed_data.index[1] - processed_data.index[0] if len(processed_data.index) >= 2 else None

    for timestamp, current_candle in processed_data.iterrows():
        if current_capital <= 0: break

        # --- Ausstieg: SL-Trigger + Trailing-Stop wie die beiden Live-Orders ---
        if position:
            fine_slice = None
            if fine_data is not None and coarse_duration is not None:
                fine_slice = _get_fine_slice(fine_data, timestamp, timestamp + coarse_duration)
            path = candle_path(current_candle['open'], current_candle['high'], current_candle['low'],
                               current_candle['close'], fine_slice)
            exit_price = simulate_exit(position, path)
            if exit_price:
                net = trade_net_pnl(position, exit_price, position['notional_value'])
                current_capital += net
                if net > 0: wins_count += 1
                trades_count += 1
                if return_trades:
                    trades.append({'side': position['side'], 'entry_time': position['entry_time'],
                                   'entry_price': position['entry_price'], 'exit_time': timestamp,
                                   'exit_price': exit_price, 'pnl_usd': net,
                                   'notional': position['notional_value']})
                position = None
                peak_capital = max(peak_capital, current_capital)
                if peak_capital > 0:
                    max_drawdown_pct = max(max_drawdown_pct, (peak_capital - current_capital) / peak_capital)
                # Kein 'continue': Live wertet die gerade geschlossene Kerze auch dann aus,
                # wenn die vorige Position WAEHREND dieser Kerze geschlossen wurde.

        # --- Einstieg ---
        if not position and current_capital > 0:
            side = entry_side(current_candle, timestamp, strategy_params, bias_ctx)
            if side:
                entry_price = current_candle['close']
                sl_dist = sl_distance(current_candle, risk_params)
                sl_pct = sl_dist / entry_price
                if sl_pct <= 0: continue

                final_notional = min(position_notional(current_capital, risk_params, sl_pct), absolute_max_notional_value)
                if not order_allowed(final_notional, entry_price, min_amount): continue
                margin_needed = final_notional / safe_leverage(leverage, sl_pct)
                if margin_needed > current_capital: continue

                position = new_position(side, entry_price, sl_dist, risk_params)
                position.update(notional_value=final_notional, margin_used=margin_needed, entry_time=timestamp)

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital - start_capital) / start_capital) * 100 if start_capital > 0 else 0

    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count,
        "win_rate": win_rate, "max_drawdown_pct": max_drawdown_pct,
        "end_capital": max(0, current_capital), "trades": trades,
    }
