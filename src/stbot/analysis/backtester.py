# /root/utbot2/src/stbot/analysis/backtester.py
import os
import pandas as pd
import numpy as np
import json
import sys
import bisect
from tqdm import tqdm
import ta
import math

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from stbot.utils.exchange import Exchange
from stbot.strategy.sr_engine import SREngine # NEU
from stbot.strategy.trade_logic import get_titan_signal
from stbot.utils.timeframe_utils import determine_htf

secrets_cache = None

class Bias:
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

# Feinere Timeframe je Strategie-Timeframe fuer die Intrabar-Pfad-Aufloesung
# (oraclebot-Muster). Verhaeltnis ~8-30:1, an verfuegbare Exchange-Granularitaeten
# angepasst.
FINE_TF_MAP = {
    '5m': '1m', '15m': '1m', '30m': '1m',
    '1h': '5m', '2h': '5m',
    '4h': '15m', '6h': '15m',
    '1d': '1h',
}


# Pandas-Resample-Regel je Timeframe, fuer die HTF-Trend-Bias-Berechnung.
_PD_RESAMPLE = {
    '5m': '5min', '15m': '15min', '30m': '30min',
    '1h': '1h', '2h': '2h', '4h': '4h', '6h': '6h', '1d': '1D', '1w': '1W',
}


def _compute_htf_bias(data: pd.DataFrame, timeframe: str, htf: str, ema_period: int = 20):
    """
    Berechnet einen einfachen HTF-Trend-Bias (EMA-basiert) fuer den optionalen
    MTF-Filter in trade_logic.py. Bisher wurde dort immer Bias.NEUTRAL uebergeben
    (kein Look-Ahead: Bias einer HTF-Kerze gilt erst ab ihrem Close, daher per
    bisect auf die vorherige abgeschlossene HTF-Kerze gemappt).
    Rueckgabe: (htf_bias_times, htf_bias_values) -- beide leer, wenn Berechnung
    nicht moeglich ist (Aufrufer faellt dann auf NEUTRAL zurueck).
    """
    pd_rule = _PD_RESAMPLE.get(htf)
    if not pd_rule:
        return [], []
    try:
        htf_data = data[['open', 'high', 'low', 'close']].resample(pd_rule).agg(
            {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
        ).dropna()
        if len(htf_data) < ema_period + 5:
            return [], []
        ema = htf_data['close'].ewm(span=ema_period, adjust=False).mean()
        bias_series = np.where(htf_data['close'] > ema, Bias.BULLISH,
                       np.where(htf_data['close'] < ema, Bias.BEARISH, Bias.NEUTRAL))
        # Bias einer HTF-Kerze ist erst NACH ihrem Schluss bekannt -> Timestamps
        # um eine HTF-Kerze verschieben, damit bisect keine zukuenftigen Daten sieht.
        return list(htf_data.index[1:]), list(bias_series[:-1])
    except Exception:
        return [], []


# --- Regime-Klassifikation (TREND / RANGE / CHAOS), portiert aus superbots
# regime_gate.py (Hurst+ADX-Kombination, dort bereits aus apexbot/pbot/mbot/
# ltbbot-Ansaetzen konsolidiert und die Hurst-Formel dort schon korrigiert;
# die Entropie-Komponente wird bewusst NICHT uebernommen -- superbots eigene
# Tests zeigten, dass sie kaum zwischen Regimes trennt).
_REGIME_CFG = {
    "hurst_trend_min": 0.55, "adx_trend_min": 25.0,
    "hurst_range_max": 0.50, "adx_range_max": 20.0,
}


def _compute_hurst(close: pd.Series, lags: int = 20, min_lookback: int = 60) -> float:
    """R/S-Analyse auf Log-Returns (nicht auf rohen Preisen -- das waere
    methodisch falsch, siehe superbot-Docstring). H>0.5 trending, H<0.5
    mean-reverting, H=0.5 Random Walk."""
    n_needed = max(min_lookback, lags * 4)
    if len(close) < n_needed + 1:
        return 0.5
    prices = close.values[-(n_needed + 1):]
    log_returns = np.diff(np.log(np.maximum(prices, 1e-10)))
    tau, lag_list = [], []
    max_lag = min(lags, len(log_returns) // 4)
    for lag in range(2, max(max_lag, 3)):
        n_chunks = len(log_returns) // lag
        if n_chunks < 4:
            continue
        rs_vals = []
        for c in range(n_chunks):
            chunk = log_returns[c * lag:(c + 1) * lag]
            dev = np.cumsum(chunk - chunk.mean())
            r = dev.max() - dev.min()
            s = chunk.std()
            if s > 1e-10:
                rs_vals.append(r / s)
        if rs_vals:
            tau.append(float(np.mean(rs_vals)))
            lag_list.append(lag)
    if len(tau) < 2:
        return 0.5
    try:
        poly = np.polyfit(np.log(lag_list), np.log(np.maximum(tau, 1e-10)), 1)
        return float(np.clip(poly[0], 0.0, 1.0))
    except Exception:
        return 0.5


def _classify_regime(window: pd.DataFrame) -> str:
    """TREND / RANGE / uneindeutig=RANGE (kein hartes CHAOS-Handelsverbot hier
    -- s. Anmerkung unten: staerker haette Tradezahl zu stark reduziert)."""
    close = window['close']
    hurst = _compute_hurst(close)
    adx_ind = ta.trend.ADXIndicator(high=window['high'], low=window['low'], close=close, window=14)
    adx = adx_ind.adx().iloc[-1]
    adx = float(adx) if pd.notna(adx) else 0.0
    if hurst >= _REGIME_CFG["hurst_trend_min"] and adx >= _REGIME_CFG["adx_trend_min"]:
        return "TREND"
    return "RANGE"


def _compute_regime_series(data: pd.DataFrame, lookback_days: int = 90):
    """Tages-Kadenz TREND/RANGE-Serie (Hurst braucht laengere Historie, taeglich
    neu berechnen ist ausreichend feinfuehlig und bleibt performant -- kein
    Look-Ahead: Regime eines Tages gilt erst ab dessen Schluss).
    lookback_days muss > _compute_hurst()'s intern benoetigte Mindestlaenge
    (lags*4+1 = 81 bei Default lags=20) sein, sonst liefert Hurst IMMER den
    0.5-Fallback und TREND wird nie erkannt (gefundener Bug: 60-Tage-Fenster
    war knapp zu kurz, jedes Regime wurde faelschlich als RANGE klassifiziert)."""
    try:
        daily = data[['open', 'high', 'low', 'close']].resample('1D').agg(
            {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
        ).dropna()
        if len(daily) < lookback_days + 5:
            return [], []
        times, regimes = [], []
        for i in range(lookback_days, len(daily)):
            window = daily.iloc[i - lookback_days:i + 1]
            regimes.append(_classify_regime(window))
            times.append(daily.index[i])
        return times[1:], regimes[:-1]
    except Exception:
        return [], []


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
    def __init__(self, symbol, fine_tf):
        self.symbol = symbol
        self.fine_tf = fine_tf
        self._days = {}
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
        if day not in self._days:
            day_str = day.strftime('%Y-%m-%d')
            self.current_day = day_str
            next_day_str = (day + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            try:
                df = load_data(self.symbol, self.fine_tf, day_str, next_day_str, quiet=True)
                self._days[day] = df if df is not None and not df.empty else None
            except Exception:
                self._days[day] = None
            self.days_loaded = len(self._days)
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
    """
    Baut aus den feineren Kerzen innerhalb einer Coarse-Kerze eine granulare
    Preis-Pfad-Liste (statt der 4-Punkte-Annaeherung [o,l,h,c]/[o,h,l,c] anhand
    der Kerzenfarbe). Jede Fein-Kerze traegt ihrerseits [open, low/high in
    Farbrichtung, close] bei -- deutlich naeher an der Realitaet als eine
    einzelne Grobkerze.
    """
    path = []
    for _, bar in fine_slice.iterrows():
        o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
        path.extend([o, l, h, c] if c >= o else [o, h, l, c])
    return path

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
            req_start_buffer = req_start - pd.Timedelta(days=20)

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
        start_dt = pd.to_datetime(start_date_str, utc=True) - pd.Timedelta(days=30)
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
            buffer_dt = req_start_dt - pd.Timedelta(days=20)
            return full_data.loc[buffer_dt:req_end_dt]
        return pd.DataFrame()
    except Exception: return pd.DataFrame()


def run_backtest(data, strategy_params, risk_params, start_capital=1000, verbose=False, fine_data=None, regime_data=None):
    if data.empty or len(data) < 100:
        return {"total_pnl_pct": -100, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}

    symbol = strategy_params.get('symbol', '')
    timeframe = strategy_params.get('timeframe', '')
    htf = strategy_params.get('htf')

    # --- HTF-Trend-Bias (optional, standardmaessig aus) ---
    use_htf_filter = strategy_params.get('use_htf_filter', False)
    htf_bias_times, htf_bias_values = ([], [])
    if use_htf_filter and htf:
        htf_bias_times, htf_bias_values = _compute_htf_bias(data, timeframe, htf)

    # --- Wochentrend-Filter (optional, standardmaessig aus) ---
    # Grobe, langsame Trendrichtung (EMA auf Wochenkerzen) -- nur Trades in
    # Richtung des uebergeordneten Trends zulassen. Getrennt vom schnellen
    # use_htf_filter oben (der nutzt den naechsthoeheren, schnell wechselnden
    # Timeframe und zeigte in Tests eher negative Wirkung).
    use_weekly_trend_filter = strategy_params.get('use_weekly_trend_filter', False)
    weekly_bias_times, weekly_bias_values = ([], [])
    if use_weekly_trend_filter:
        weekly_ema = strategy_params.get('weekly_trend_ema', 8)
        weekly_bias_times, weekly_bias_values = _compute_htf_bias(data, timeframe, '1w', ema_period=weekly_ema)

    # --- Regime-Gate (optional, standardmaessig aus) ---
    # Der Wochentrend-Filter oben hilft in Trendphasen, schadet aber im Range
    # (validiert per Backtest ueber Baer/Bulle/Seitwaerts) -- der Wochentrend
    # wird deshalb nur angewendet, wenn Hurst+ADX tatsaechlich TREND anzeigen;
    # im RANGE-Regime bleiben beide Richtungen offen wie ohne Filter.
    use_regime_gate = strategy_params.get('use_regime_gate', False)
    regime_times, regime_values = ([], [])
    if use_regime_gate and use_weekly_trend_filter:
        regime_lookback_days = strategy_params.get('regime_lookback_days', 90)
        # regime_data (falls uebergeben): laengere Vorlauf-Historie NUR fuer die
        # Regime-Klassifikation, unabhaengig vom eigentlichen Handels-/Auswertungs-
        # zeitraum in `data` -- sonst braeuchte ein langes Lookback-Fenster (z.B.
        # 180 Tage) selbst schon einen 180+ Tage langen Testzeitraum, um ueberhaupt
        # einen einzigen Regime-Punkt zu liefern.
        regime_source = regime_data if regime_data is not None and not regime_data.empty else data
        regime_times, regime_values = _compute_regime_series(regime_source, lookback_days=regime_lookback_days)

    # --- ATR Berechnung ---
    try:
        atr_indicator = ta.volatility.AverageTrueRange(high=data['high'], low=data['low'], close=data['close'], window=14)
        data['atr'] = atr_indicator.average_true_range()
        data.dropna(subset=['atr'], inplace=True)
    except Exception:
        return {"total_pnl_pct": -100, "end_capital": start_capital}

    # --- Momentum-Filter (optional, standardmaessig aus) ---
    # Aus Live-Trade-Forensik (2026-08-20, 172 echte stbot-Trades, siehe Memory
    # research_stbot_energy_zscore_filter): der reine SR-Breakout unterscheidet nicht,
    # ob die durchbrechende Kerze eine statistisch aussergewoehnliche Bewegung war
    # (avalanche_percentile, Gutenberg-Richter-inspiriert: Perzentil der |Rendite|
    # ggue. der letzten 100 Kerzen -- p=0.0038, LOO-CV-Test verlustfrei) bzw. ob
    # echtes kinetisches Momentum dahintersteckt (energy_zscore = Geschwindigkeit^2
    # als Z-Score ggue. eigener 50-Kerzen-Verteilung, p=0.0008 / energy_rising_streak
    # = Energie steigt 3 Kerzen in Folge, p=0.02-0.03, haelt auf mehreren Teildatensaetzen
    # unabhaengig). Alle drei mechanistisch bestaetigt: gefilterte Trades zeigen in der
    # MAE/MFE-Rekonstruktion deutlich weniger Drawdown und mehr Favorable Excursion.
    use_avalanche_filter = strategy_params.get('use_avalanche_filter', False)
    if use_avalanche_filter:
        abs_ret = data['close'].pct_change().fillna(0.0).abs()

        def _pct_rank_last(x):
            return (x[:-1] < x[-1]).mean() * 100.0

        data['avalanche_percentile'] = abs_ret.rolling(window=101, min_periods=51).apply(_pct_rank_last, raw=True)

    use_energy_filter = strategy_params.get('use_energy_filter', False)
    use_energy_streak_filter = strategy_params.get('use_energy_streak_filter', False)
    if use_energy_filter or use_energy_streak_filter:
        velocity = data['close'].diff().fillna(0.0)
        energy = velocity ** 2
        data['energy_zscore'] = (energy - energy.rolling(50).mean()) / energy.rolling(50).std()
    if use_energy_streak_filter:
        ez = data['energy_zscore']
        data['energy_rising_streak'] = (ez > ez.shift(1)) & (ez.shift(1) > ez.shift(2))

    # --- SREngine (Neu) ---
    engine = SREngine(settings=strategy_params)
    processed_data = engine.process_dataframe(data)

    current_capital = start_capital
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    trades_count = 0
    wins_count = 0
    position = None

    # Parameter-Extraction
    risk_reward_ratio = risk_params.get('risk_reward_ratio', 2.0)
    risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
    activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
    callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100
    leverage = risk_params.get('leverage', 10)
    fee_pct = 0.06 / 100 # Bitget Taker ca.
    atr_multiplier_sl = risk_params.get('atr_multiplier_sl', 2.0)
    min_sl_pct = risk_params.get('min_sl_pct', 0.3) / 100.0

    # Profit-Protection (optional, standardmaessig aus): schuetzt Gewinne, BEVOR
    # der Trailing-Stop aktiviert. Ohne das faellt ein Trade, der z.B. 0.7R im
    # Plus war und dann komplett zurueckdreht, auf den vollen urspruenglichen
    # SL zurueck -- der zwischenzeitliche Gewinn ist ungeschuetzt.
    breakeven_trigger_rr = risk_params.get('breakeven_trigger_rr', 0)  # 0 = aus
    # Zweite, engere Trailing-Stufe ab einem hoeheren RR-Vielfachen -- sichert
    # mehr vom Gewinn, wenn der Trend deutlich weiterlaeuft.
    tight_trail_rr = risk_params.get('tight_trail_rr', 0)  # 0 = aus
    tight_callback_rate = risk_params.get('tight_callback_rate_pct', 0) / 100

    absolute_max_notional_value = 1000000
    
    params_for_logic = {"strategy": strategy_params, "risk": risk_params}

    coarse_duration = processed_data.index[1] - processed_data.index[0] if len(processed_data.index) >= 2 else None
    iterator = processed_data.iterrows()

    for timestamp, current_candle in iterator:
        if current_capital <= 0: break

        # --- Positions-Management ---
        # Live platziert KEINE feste TP-Order (siehe trade_manager.py) - nur ein harter SL-Trigger
        # und ein Trailing-Stop (Aktivierung bei activation_rr, Rueckzug bei callback_rate).
        # Die frueher hier simulierte statische TP-Order existiert live nicht und wurde entfernt
        # (Live-vs-Backtest-Analyse 2026-07-30 zeigte dadurch stark ueberzeichnete Backtest-PnL).
        if position:
            o, h, l, c = current_candle['open'], current_candle['high'], current_candle['low'], current_candle['close']
            # Intracandle-Pfad: bevorzugt aus echten feineren Kerzen aufgebaut (oraclebot-Muster),
            # sonst Fallback auf die alte 4-Punkte-Annaeherung anhand der Kerzenfarbe.
            path = None
            if fine_data is not None and coarse_duration is not None:
                fine_slice = _get_fine_slice(fine_data, timestamp, timestamp + coarse_duration)
                if fine_slice is not None and not fine_slice.empty:
                    path = _build_fine_path(fine_slice)
            if not path:
                path = [o, l, h, c] if c >= o else [o, h, l, c]

            exit_price = None
            for p in path:
                if position['side'] == 'long':
                    if p <= position['stop_loss']:
                        exit_price = position['stop_loss']
                        break
                    if (breakeven_trigger_rr > 0 and not position['breakeven_done']
                            and p >= position['entry_price'] + position['sl_dist'] * breakeven_trigger_rr):
                        position['stop_loss'] = max(position['stop_loss'], position['entry_price'])
                        position['breakeven_done'] = True
                    if not position['trailing_active'] and p >= position['activation_price']:
                        position['trailing_active'] = True
                        position['peak_price'] = p
                    if position['trailing_active']:
                        position['peak_price'] = max(position['peak_price'], p)
                        active_callback = callback_rate
                        if tight_trail_rr > 0 and p >= position['entry_price'] + position['sl_dist'] * tight_trail_rr:
                            active_callback = tight_callback_rate
                        trail_level = position['peak_price'] * (1 - active_callback)
                        if p <= trail_level:
                            exit_price = trail_level
                            break
                else:
                    if p >= position['stop_loss']:
                        exit_price = position['stop_loss']
                        break
                    if (breakeven_trigger_rr > 0 and not position['breakeven_done']
                            and p <= position['entry_price'] - position['sl_dist'] * breakeven_trigger_rr):
                        position['stop_loss'] = min(position['stop_loss'], position['entry_price'])
                        position['breakeven_done'] = True
                    if not position['trailing_active'] and p <= position['activation_price']:
                        position['trailing_active'] = True
                        position['peak_price'] = p
                    if position['trailing_active']:
                        position['peak_price'] = min(position['peak_price'], p)
                        active_callback = callback_rate
                        if tight_trail_rr > 0 and p <= position['entry_price'] - position['sl_dist'] * tight_trail_rr:
                            active_callback = tight_callback_rate
                        trail_level = position['peak_price'] * (1 + active_callback)
                        if p >= trail_level:
                            exit_price = trail_level
                            break

            if exit_price:
                pnl_pct = (exit_price / position['entry_price'] - 1) if position['side'] == 'long' else (1 - exit_price / position['entry_price'])
                notional_value = position['notional_value']
                pnl_usd = notional_value * pnl_pct
                total_fees = notional_value * fee_pct * 2
                current_capital += (pnl_usd - total_fees)
                if (pnl_usd - total_fees) > 0: wins_count += 1
                trades_count += 1
                position = None
                peak_capital = max(peak_capital, current_capital)
                if peak_capital > 0:
                    drawdown = (peak_capital - current_capital) / peak_capital
                    max_drawdown_pct = max(max_drawdown_pct, drawdown)
                continue  # Kein Re-Entry auf derselben Kerze nach Trade-Schluss

        # --- Einstiegs-Logik ---
        if not position and current_capital > 0:
            # Slicing ist teuer, aber notwendig für korrekte Simulation, wenn Logik Historie braucht.
            # Für reine Indikator-basierte Signale wie 'sr_signal' reicht current_candle oft.
            # Wir nutzen hier slice für Kompatibilität.
            
            # HTF-Bias per bisect auf den letzten abgeschlossenen HTF-Balken (kein
            # Look-Ahead) -- NEUTRAL, wenn Filter aus oder Bias nicht berechenbar.
            market_bias = Bias.NEUTRAL
            if htf_bias_times:
                _pos = bisect.bisect_right(htf_bias_times, timestamp) - 1
                if _pos >= 0:
                    market_bias = htf_bias_values[_pos]

            # data_slice = processed_data.loc[:timestamp] # Performance-Killer
            # Stattdessen:
            
            side, price = get_titan_signal(processed_data, current_candle, params_for_logic, market_bias)

            if side and use_avalanche_filter:
                threshold = strategy_params.get('avalanche_percentile_threshold', 60)
                cur_val = current_candle.get('avalanche_percentile', np.nan)
                if not (pd.notna(cur_val) and cur_val > threshold):
                    side = None

            if side and use_energy_filter:
                min_ez = strategy_params.get('min_energy_zscore', 0.0)
                cur_ez = current_candle.get('energy_zscore', np.nan)
                if not (pd.notna(cur_ez) and cur_ez > min_ez):
                    side = None

            if side and use_energy_streak_filter:
                cur_streak = current_candle.get('energy_rising_streak', False)
                if not bool(cur_streak):
                    side = None

            if side and use_weekly_trend_filter and weekly_bias_times:
                apply_trend_filter = True
                if use_regime_gate and regime_times:
                    _rpos = bisect.bisect_right(regime_times, timestamp) - 1
                    apply_trend_filter = _rpos >= 0 and regime_values[_rpos] == "TREND"
                if apply_trend_filter:
                    _wpos = bisect.bisect_right(weekly_bias_times, timestamp) - 1
                    if _wpos >= 0:
                        weekly_bias = weekly_bias_values[_wpos]
                        if weekly_bias == Bias.BEARISH and side == 'buy':
                            side = None
                        elif weekly_bias == Bias.BULLISH and side == 'sell':
                            side = None

            if side:
                entry_price = current_candle['close']
                current_atr = current_candle.get('atr', 0)
                if current_atr <= 0: continue

                sl_dist = max(current_atr * atr_multiplier_sl, entry_price * min_sl_pct)
                risk_amount_usd = current_capital * risk_per_trade_pct
                sl_pct = sl_dist / entry_price
                if sl_pct <= 0: continue

                calc_notional = risk_amount_usd / sl_pct
                max_notional = current_capital * 10 # Hardcap effektiver Hebel
                final_notional = min(calc_notional, max_notional, absolute_max_notional_value)

                margin_needed = final_notional / leverage
                if margin_needed > current_capital: continue

                if side == 'buy':
                    sl = entry_price - sl_dist
                    tp = entry_price + sl_dist * risk_reward_ratio
                    act = entry_price + sl_dist * activation_rr
                else:
                    sl = entry_price + sl_dist
                    tp = entry_price - sl_dist * risk_reward_ratio
                    act = entry_price - sl_dist * activation_rr

                position = {
                    'side': 'long' if side == 'buy' else 'short',
                    'entry_price': entry_price, 'stop_loss': sl,
                    'take_profit': tp, 'margin_used': margin_needed,
                    'notional_value': final_notional,
                    'trailing_active': False, 'activation_price': act,
                    'peak_price': entry_price,
                    'sl_dist': sl_dist, 'breakeven_done': False,
                }

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital - start_capital) / start_capital) * 100 if start_capital > 0 else 0
    final_capital = max(0, current_capital)

    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count,
        "win_rate": win_rate, "max_drawdown_pct": max_drawdown_pct,
        "end_capital": final_capital
    }
