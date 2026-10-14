# /root/stbot/src/stbot/analysis/optimizer.py
import os
import sys
import time
import json
import optuna
import numpy as np
import pandas as pd
import argparse
import logging
import warnings
from datetime import datetime as _dt
from tqdm import tqdm

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)
warnings.filterwarnings('ignore', category=UserWarning, module='keras')

# Ohne explizit konfigurierten Handler verschluckt Python logger.info()-Aufrufe
# komplett (nur WARNING+ kommt ueber den lastResort-Handler durch) -- der
# Fortschritts-Log aus exchange.py::fetch_historical_ohlcv (2026-08-21,
# quiet=False-Pfad) blieb dadurch unsichtbar. Einmal hier konfigurieren, da
# optimizer.py als eigenstaendiges CLI-Skript laeuft (kein setup_logging()
# wie run.py fuer den Live-Betrieb).
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from stbot.analysis.backtester import load_data, run_backtest, FINE_TF_MAP
from stbot.utils.timeframe_utils import determine_htf

optuna.logging.set_verbosity(optuna.logging.WARNING)

HISTORICAL_DATA = None
IS_DATA = None   # vorderer Teil (IS_FRACTION) der Historie -- das sieht Optuna, wird optimiert
OOS_DATA = None  # hinterer Teil -- fliesst NIE in die Zielfunktion ein, nur Bestaetigung danach
CURRENT_SYMBOL = None
CURRENT_TIMEFRAME = None
CURRENT_HTF = None
CONFIG_SUFFIX = ""
EVAL_START = None      # erster Handelstag (start_date); davor nur Vorlauf
MIN_OOS_PNL = 10.0     # Bestaetigung: OOS-PnL in %
MAX_OOS_DD = 30.0      # Bestaetigung: OOS-MaxDD in % (strikt kleiner)
MIN_IS_PNL = 0.0       # Bestaetigung: IS-PnL muss darueber liegen (Strategie muss auch im Training funktionieren)
MAX_DRAWDOWN_CONSTRAINT = 0.30
MIN_WIN_RATE_CONSTRAINT = 55.0
MIN_PNL_CONSTRAINT = 0.0
START_CAPITAL = 1000
OPTIM_MODE = "strict"
IS_FRACTION = 0.70       # analog dnabot/alphabet_optimizer.py: 70% In-Sample, 30% Out-of-Sample
MIN_OOS_TRADES = 10      # Bestaetigung erfordert genug OOS-Trades fuer eine belastbare Aussage
K_FOLDS = 3              # IS-Teilfenster fuer den Robustheits-Score (siehe objective())

# Ergebnisdatei fuer den Scheduler (Telegram-Benachrichtigung)
RESULTS_FILE = os.path.join(PROJECT_ROOT, 'artifacts', 'results', 'last_optimizer_run.json')


def create_safe_filename(symbol, timeframe):
    return f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"


def objective(trial):
    # Wochentrend-Filter (EMA auf Wochenkerzen, nur Trades in Trendrichtung
    # zulassen) -- per Backtest ueber Baer/Bulle/Seitwaerts validiert: hilft
    # in Trendphasen deutlich, kostet im Seitwaertsmarkt etwas PnL. Optuna
    # entscheidet pro Symbol/Timeframe selbst, ob sich das lohnt.
    use_weekly_trend_filter = trial.suggest_categorical('use_weekly_trend_filter', [True, False])
    weekly_trend_ema = trial.suggest_int('weekly_trend_ema', 3, 12) if use_weekly_trend_filter else 4

    # Momentum-Filter (avalanche_percentile / energy_zscore / energy_rising_streak):
    # aus Live-Trade-Forensik 2026-08-20 abgeleitet, siehe Memory
    # research_stbot_energy_zscore_filter -- alle drei unabhaengig, Optuna waehlt
    # pro Symbol/Timeframe, welche (falls ueberhaupt) sich lohnen.
    use_avalanche_filter = trial.suggest_categorical('use_avalanche_filter', [True, False])
    avalanche_percentile_threshold = trial.suggest_int('avalanche_percentile_threshold', 50, 85) if use_avalanche_filter else 60

    use_energy_filter = trial.suggest_categorical('use_energy_filter', [True, False])
    min_energy_zscore = trial.suggest_float('min_energy_zscore', -0.5, 1.0) if use_energy_filter else 0.0

    use_energy_streak_filter = trial.suggest_categorical('use_energy_streak_filter', [True, False])

    strategy_params = {
        'pivot_period':      trial.suggest_int('pivot_period', 5, 30),
        'max_pivots':        trial.suggest_int('max_pivots', 10, 60),
        'channel_width_pct': trial.suggest_int('channel_width_pct', 5, 25),
        'max_sr_levels':     5,
        'min_strength':      trial.suggest_int('min_strength', 1, 4),
        'source':            trial.suggest_categorical('source', ['High/Low', 'Close/Open']),
        'use_weekly_trend_filter': use_weekly_trend_filter,
        'weekly_trend_ema':  weekly_trend_ema,
        'use_avalanche_filter': use_avalanche_filter,
        'avalanche_percentile_threshold': avalanche_percentile_threshold,
        'use_energy_filter': use_energy_filter,
        'min_energy_zscore': min_energy_zscore,
        'use_energy_streak_filter': use_energy_streak_filter,
        'symbol':    CURRENT_SYMBOL,
        'timeframe': CURRENT_TIMEFRAME,
        'htf':       CURRENT_HTF,
    }
    # Kein risk_reward_ratio mehr im Suchraum: live gibt es keine feste TP-Order
    # (nur SL + Trailing), der Parameter hatte seit 653b602 nirgends eine Wirkung
    # und war fuer Optuna eine reine Rausch-Dimension.
    risk_params = {
        'risk_per_trade_pct':             trial.suggest_float('risk_per_trade_pct', 0.5, 3.0),
        'leverage':                       trial.suggest_int('leverage', 5, 20),
        'trailing_stop_activation_rr':    trial.suggest_float('trailing_stop_activation_rr', 1.0, 3.0),
        'trailing_stop_callback_rate_pct':trial.suggest_float('trailing_stop_callback_rate_pct', 0.2, 2.0),
        'atr_multiplier_sl':              trial.suggest_float('atr_multiplier_sl', 1.5, 5.0),
        'min_sl_pct': 0.3,
    }

    # Zielfunktion sieht NUR IS-Daten -- OOS fliesst nie in die Optimierung
    # ein, nur in die Bestaetigung des besten Trials danach (siehe main()).
    # fine_data=None waehrend der SUCHE (grobe 4-Punkte-Kerzennaeherung statt
    # Intrabar-Fein-Aufloesung) -- analog dnabot/alphabet_optimizer.py
    # (fine_df=None fuer Trials, praezise Aufloesung nur fuer den finalen
    # Trial). Ein einzelner Backtest MIT LazyFineData brauchte gemessen >140s
    # (viele einzelne Tages-Netzwerk-Fetches), OHNE nur 0.1s -- bei 100+
    # Trials ist das der Unterschied zwischen Minuten und Stunden. Die
    # praezisen Zahlen (fuer Tabelle + Config) kommen aus einer einmaligen
    # Nachbewertung des besten Trials nach der Suche, siehe main().
    is_result = run_backtest(IS_DATA.copy(), strategy_params, risk_params, START_CAPITAL, verbose=False,
                             fine_data=None, trade_start=EVAL_START, return_trades=True)
    pnl      = is_result.get('total_pnl_pct', -1000)
    drawdown = is_result.get('max_drawdown_pct', 1.0)
    trades   = is_result.get('trades_count', 0)
    win_rate = is_result.get('win_rate', 0)

    if OPTIM_MODE == "strict" and (
        drawdown > MAX_DRAWDOWN_CONSTRAINT or win_rate < MIN_WIN_RATE_CONSTRAINT
        or pnl < MIN_PNL_CONSTRAINT or trades < 20
    ):
        raise optuna.exceptions.TrialPruned()
    elif OPTIM_MODE == "best_profit" and (drawdown > MAX_DRAWDOWN_CONSTRAINT or trades < 20):
        raise optuna.exceptions.TrialPruned()

    trial.set_user_attr('strategy_params', strategy_params)
    trial.set_user_attr('risk_params', risk_params)

    # Robustheits-Score: IS-Handelszeitraum in K_FOLDS aufeinanderfolgende Teilfenster
    # teilen und das SCHLECHTESTE als Optuna-Zielwert nehmen (bestraft Parameter, die
    # nur eine Marktphase treffen). Aus EINEM durchgehenden IS-Lauf (mit Vorlauf): Trades
    # nach Einstiegszeit den Fenstern zuordnen, PnL relativ zum Kapital am Fensteranfang.
    # (Vorher: je Fenster ein eigener Backtest ueber die ganze Historie davor -- gleiches
    # Ergebnis bis auf den Zinseszins, aber ~2,5x langsamer.)
    is_idx = IS_DATA.index[IS_DATA.index >= EVAL_START]
    fold_size = max(1, len(is_idx) // K_FOLDS)
    bounds = [is_idx[min(k * fold_size, len(is_idx) - 1)] for k in range(K_FOLDS)] + [IS_DATA.index[-1] + pd.Timedelta(days=1)]
    trades = sorted(is_result.get('trades', []), key=lambda t: t['entry_time'])
    equity = START_CAPITAL
    fold_pnls = []
    for k in range(K_FOLDS):
        f_trades = [t for t in trades if bounds[k] <= t['entry_time'] < bounds[k + 1]]
        f_pnl = sum(t['pnl_usd'] for t in f_trades)
        fold_pnls.append(f_pnl / equity * 100 if equity > 0 else -1000)
        equity += f_pnl
    is_result.pop('trades', None)
    trial.set_user_attr('is_stats', is_result)
    trial.set_user_attr('fold_pnls', fold_pnls)
    robust_score = min(fold_pnls)

    return robust_score


def _criteria_str():
    return (f"IS: PnL > {MIN_IS_PNL:g} % | OOS: >= {MIN_OOS_TRADES} Trades, PnL >= {MIN_OOS_PNL:g} %, "
            f"MaxDD < {MAX_OOS_DD:g} %")


def _passes(is_stats, oos):
    """
    Bestaetigungs-Kriterien (vom Nutzer festgelegt, 2026-10-13): im OOS-Zeitraum
    >= MIN_OOS_TRADES Trades, PnL >= MIN_OOS_PNL, MaxDD < MAX_OOS_DD -- und die
    Strategie muss auch im IS-Zeitraum verdient haben (IS-PnL > MIN_IS_PNL), sonst
    ist ein OOS-Treffer eher Zufall (Anlass: LINK/1h IS -3.5 %, OOS +10.6 %).
    """
    if not oos or not is_stats:
        return False
    return bool(is_stats.get('total_pnl_pct', -1e9) > MIN_IS_PNL
                and oos.get('trades_count', 0) >= MIN_OOS_TRADES
                and oos.get('total_pnl_pct', -1e9) >= MIN_OOS_PNL
                and oos.get('max_drawdown_pct', 1.0) * 100 < MAX_OOS_DD)


def _write_meta(path, oos, split_ts, confirmed, extra=None, is_stats=None):
    with open(path) as f:
        cfg = json.load(f)
    meta = cfg.setdefault('_meta', {})
    if is_stats is not None:
        meta['pnl_pct'] = round(is_stats.get('total_pnl_pct', 0), 2)
    meta.update({
        'oos_pnl_pct':      round(oos.get('total_pnl_pct', 0), 2),
        'oos_trades':       oos.get('trades_count', 0),
        'oos_max_dd_pct':   round(oos.get('max_drawdown_pct', 0) * 100, 2),
        'oos_win_rate':     round(oos.get('win_rate', 0), 1),
        'criteria':         _criteria_str(),
        'is_oos_split_date': str(split_ts.date()),
        'confirmed':        bool(confirmed),
    })
    for k in ('unconfirmed_reason', 'unconfirmed_at'):
        meta.pop(k, None)
    if not confirmed:
        meta['unconfirmed_reason'] = 'oos_criteria'
        meta['unconfirmed_at'] = _dt.now().isoformat(timespec='seconds')
    if extra:
        meta.update(extra)
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=4)


def _evaluate_existing(symbol, timeframe, args, split_ts, run_results):
    """--evaluate_only: bestehende Config auf IS/OOS (mit Vorlauf, Feinkerzen) bewerten, confirmed neu setzen."""
    safe = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    path = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs', f"config_{safe}{CONFIG_SUFFIX}.json")
    if not os.path.exists(path):
        run_results['failed'].append({'symbol': symbol, 'timeframe': timeframe, 'reason': 'no_config'})
        return
    with open(path) as f:
        cfg = json.load(f)
    sp = dict(cfg['strategy']); sp.update({'symbol': symbol, 'timeframe': timeframe, 'htf': determine_htf(timeframe)})
    rp = dict(cfg['risk'])
    fine_tf = FINE_TF_MAP.get(timeframe)
    fine = load_data(symbol, fine_tf, args.start_date, args.end_date, quiet=True) if fine_tf else None
    if fine is not None and fine.empty:
        fine = None
    is_stats = run_backtest(IS_DATA.copy(), sp, rp, START_CAPITAL, fine_data=fine, trade_start=EVAL_START)
    oos = run_backtest(OOS_DATA.copy(), sp, rp, START_CAPITAL, fine_data=fine, trade_start=split_ts)
    ok = _passes(is_stats, oos)
    _write_meta(path, oos, split_ts, ok, extra={'evaluated_at': _dt.now().isoformat(timespec='seconds')}, is_stats=is_stats)
    print(f"  {symbol} {timeframe}: IS {is_stats.get('total_pnl_pct', 0):+.1f}% | OOS {oos.get('trades_count', 0)} Trades, "
          f"{oos.get('total_pnl_pct', 0):+.1f}%, DD {oos.get('max_drawdown_pct', 0) * 100:.1f}% -> "
          f"{'BESTAETIGT' if ok else 'nicht bestaetigt'}")
    run_results['saved'].append({'symbol': symbol, 'timeframe': timeframe, 'oos_pnl_pct': round(oos.get('total_pnl_pct', 0), 2),
                                 'confirmed': ok, 'config_file': os.path.basename(path), 'evaluate_only': True})


def _mark_unconfirmed(symbol, timeframe, reason):
    """Setzt _meta.confirmed=false in der bestehenden Config (falls vorhanden)."""
    safe = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    path = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs', f"config_{safe}{CONFIG_SUFFIX}.json")
    if not os.path.exists(path):
        return
    with open(path) as f:
        cfg = json.load(f)
    meta = cfg.setdefault('_meta', {})
    meta['confirmed'] = False
    meta['unconfirmed_reason'] = reason
    meta['unconfirmed_at'] = _dt.now().isoformat(timespec='seconds')
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=4)


def main():
    global HISTORICAL_DATA, IS_DATA, OOS_DATA, CURRENT_SYMBOL, CURRENT_TIMEFRAME, CURRENT_HTF, CONFIG_SUFFIX
    global MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT, START_CAPITAL, OPTIM_MODE
    global IS_FRACTION, MIN_OOS_TRADES, K_FOLDS, EVAL_START, MIN_OOS_PNL, MAX_OOS_DD, MIN_IS_PNL

    parser = argparse.ArgumentParser(description="Parameter-Optimierung fuer StBot (SRv2)")
    parser.add_argument('--symbols',    type=str, default="",
                        help='Space-getrennte Symbole, z.B. "BTC ETH"')
    parser.add_argument('--timeframes', type=str, default="",
                        help='Space-getrennte Timeframes, z.B. "1h 4h"')
    parser.add_argument('--pairs',      type=str, default="",
                        help='Explizite Paare: "BTC/USDT:USDT|1h ETH/USDT:USDT|4h" '
                             '(ueberschreibt --symbols/--timeframes)')
    parser.add_argument('--start_date',    required=True, type=str)
    parser.add_argument('--end_date',      required=True, type=str)
    parser.add_argument('--jobs',          required=True, type=int)
    parser.add_argument('--max_drawdown',  required=True, type=float)
    parser.add_argument('--start_capital', required=True, type=float)
    parser.add_argument('--min_win_rate',  required=True, type=float)
    parser.add_argument('--trials',        required=True, type=int)
    parser.add_argument('--min_pnl',       required=True, type=float)
    parser.add_argument('--mode',          required=True, type=str)
    parser.add_argument('--config_suffix', type=str, default="")
    parser.add_argument('--is_fraction',   type=float, default=0.70,
                        help='Anteil In-Sample (Rest ist Out-of-Sample-Validierung), Standard 0.70')
    parser.add_argument('--min_oos_trades', type=int, default=30,
                        help='Bestaetigung: Mindestanzahl OOS-Trades, Standard 30')
    parser.add_argument('--min_oos_pnl', type=float, default=10.0,
                        help='Bestaetigung: Mindest-OOS-PnL in %%, Standard 10')
    parser.add_argument('--max_oos_dd', type=float, default=30.0,
                        help='Bestaetigung: OOS-MaxDD muss unter diesem Wert (%%) liegen, Standard 30')
    parser.add_argument('--min_is_pnl', type=float, default=0.0,
                        help='Bestaetigung: IS-PnL muss ueber diesem Wert (%%) liegen, Standard 0')
    parser.add_argument('--evaluate_only', action='store_true',
                        help='Keine Optimierung: bestehende Configs auf IS/OOS bewerten und confirmed neu setzen')
    parser.add_argument('--k_folds',       type=int, default=3,
                        help='Anzahl IS-Teilfenster fuer den Robustheits-Score (Minimum ueber alle Fenster), Standard 3')
    args = parser.parse_args()

    CONFIG_SUFFIX           = args.config_suffix
    MAX_DRAWDOWN_CONSTRAINT = args.max_drawdown / 100.0
    MIN_WIN_RATE_CONSTRAINT = args.min_win_rate
    MIN_PNL_CONSTRAINT      = args.min_pnl
    START_CAPITAL           = args.start_capital
    N_TRIALS                = args.trials
    OPTIM_MODE              = args.mode
    IS_FRACTION             = args.is_fraction
    MIN_OOS_TRADES          = args.min_oos_trades
    MIN_OOS_PNL             = args.min_oos_pnl
    MAX_OOS_DD              = args.max_oos_dd
    MIN_IS_PNL              = args.min_is_pnl
    K_FOLDS                 = args.k_folds

    # TASKS aufbauen: --pairs hat Vorrang vor --symbols/--timeframes
    if args.pairs.strip():
        TASKS = []
        for p in args.pairs.strip().split():
            sym, tf = p.rsplit('|', 1)
            TASKS.append({'symbol': sym, 'timeframe': tf})
    elif args.symbols and args.timeframes:
        symbols    = args.symbols.split()
        timeframes = args.timeframes.split()
        TASKS = [{'symbol': f"{s}/USDT:USDT", 'timeframe': tf}
                 for s in symbols for tf in timeframes]
    else:
        print("Fehler: --pairs oder --symbols + --timeframes muss angegeben werden.")
        return

    run_results = {
        'run_start': _dt.now().isoformat(timespec='seconds'),
        'run_end':   None,
        'saved':     [],
        'failed':    [],
    }

    for task in TASKS:
        symbol, timeframe = task['symbol'], task['timeframe']
        CURRENT_SYMBOL    = symbol
        CURRENT_TIMEFRAME = timeframe
        CURRENT_HTF       = determine_htf(timeframe)

        print(f"\n===== Optimiere: {symbol} ({timeframe}) [SRv2] =====")

        HISTORICAL_DATA = load_data(symbol, timeframe, args.start_date, args.end_date)
        if HISTORICAL_DATA.empty:
            print("  Keine Daten verfuegbar.")
            run_results['failed'].append(
                {'symbol': symbol, 'timeframe': timeframe, 'reason': 'no_data'})
            continue

        # Chronologischer IS/OOS-Split auf dem HANDELSZEITRAUM (ab start_date; die
        # Kerzen davor sind nur Vorlauf aus load_data). IS = [start_date, Split),
        # OOS = [Split, Ende] -- OOS wird mit der kompletten Historie davor als
        # Vorlauf bewertet (trade_start), so wie ein Live-Bot, der am Split-Tag
        # startet. Vorher lief OOS als abgeschnittener Datenblock mit Kaltstart.
        EVAL_START = pd.Timestamp(args.start_date, tz='UTC')
        eval_idx   = HISTORICAL_DATA.index[HISTORICAL_DATA.index >= EVAL_START]
        if len(eval_idx) < 100:
            print("  Zu wenig Daten im Handelszeitraum.")
            run_results['failed'].append({'symbol': symbol, 'timeframe': timeframe, 'reason': 'too_little_data'})
            continue
        split_ts  = eval_idx[int(len(eval_idx) * IS_FRACTION)]
        IS_DATA   = HISTORICAL_DATA[HISTORICAL_DATA.index < split_ts]
        OOS_DATA  = HISTORICAL_DATA
        logging.info(
            f"{symbol} ({timeframe}): {len(HISTORICAL_DATA)} Kerzen | Handel ab {EVAL_START.date()} | "
            f"IS bis {split_ts.date()} | OOS ab {split_ts.date()} ({len(eval_idx) - int(len(eval_idx) * IS_FRACTION)} Kerzen)"
        )

        if args.evaluate_only:
            _evaluate_existing(symbol, timeframe, args, split_ts, run_results)
            continue

        # Fein-Timeframe fuer die spaetere praezise Nachbewertung (siehe unten,
        # nach der Suche) -- waehrend der Suche selbst nutzt objective() bewusst
        # KEINE Fein-Daten (fine_data=None), siehe dortiger Kommentar.
        fine_tf = FINE_TF_MAP.get(timeframe)

        DB_FILE      = os.path.join(PROJECT_ROOT, 'artifacts', 'db', 'optuna_studies_stbot.db')
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        STORAGE_URL  = f"sqlite:///{DB_FILE}?timeout=60"
        study_name   = f"sr_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}_{OPTIM_MODE}"

        # Jeder Lauf startet frisch (analog dnabot/alphabet_optimizer.py) --
        # KEIN load_if_exists=True mehr. Grund: eine gefundene alte Studie
        # (sr_BTCUSDTUSDT_6h_best_profit, 695 Trials seit 2025-11-23) enthielt
        # Trials aus VOR-IS/OOS-Codeversionen ohne user_attrs['strategy_params']
        # -- max(trials, key=value) waehlte davon den hoechsten rohen PnL-Wert
        # (476.8%, Monate alt) als "besten Trial", die anschliessende
        # Nachbewertung griff auf leere user_attrs zurueck -> 0 Trades/0% ueberall
        # in der Tabelle. Alte Studien sind nach Aenderungen an der Zielfunktion
        # (wie heute: roh-PnL -> K-Fold-Robustheits-Score) ohnehin nicht mehr
        # mit neuen Trials vergleichbar.
        try:
            optuna.delete_study(study_name=study_name, storage=STORAGE_URL)
        except KeyError:
            pass  # existierte noch nicht
        study = optuna.create_study(
            storage=STORAGE_URL, study_name=study_name,
            direction="maximize")
        # Eigener tqdm-Fortschrittsbalken statt Optunas generischem
        # show_progress_bar=True -- zeigt Symbol/Timeframe als Beschriftung
        # und das bisher beste gefundene PnL als Zusatzinfo live mit, statt
        # nur eines nackten Prozentbalkens.
        with tqdm(total=N_TRIALS, desc=f"{symbol} {timeframe}", unit="trial") as pbar:
            def _progress(study, trial):
                pbar.update(1)
                try:
                    pbar.set_postfix({'bestes PnL': f"{study.best_value:.1f}%"})
                except ValueError:
                    pass  # noch kein abgeschlossener (nicht-gepruneter) Trial

            try:
                study.optimize(objective, n_trials=N_TRIALS, n_jobs=args.jobs, callbacks=[_progress])
            except Exception as e:
                print(f"FEHLER: {e}")
                run_results['failed'].append(
                    {'symbol': symbol, 'timeframe': timeframe, 'reason': str(e)[:80]})
                continue

        valid_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not valid_trials:
            run_results['failed'].append(
                {'symbol': symbol, 'timeframe': timeframe, 'reason': 'no_valid_trials'})
            # Bestehende Config NICHT mit altem confirmed-Status stehen lassen: kein
            # einziger Trial hat die IS-Kriterien erfuellt -> Paar fuer die Portfolio-
            # Auswahl sperren (vorher blieb z.B. AAVE/6h nach dem Neu-Lauf vom
            # 2026-10-13 mit confirmed=true aus der alten Logik im Pool).
            _mark_unconfirmed(symbol, timeframe, 'no_valid_trials')
            print(f"  [nicht bestaetigt] Kein Trial erfuellt die Kriterien -- bestehende Config fuer die Portfolio-Auswahl gesperrt.")
            continue

        best_trial  = max(valid_trials, key=lambda t: t.value)
        best_params = best_trial.params
        new_pnl     = best_trial.value

        # Praezise Nachbewertung NUR des besten Trials mit echter Intrabar-
        # Aufloesung -- waehrend der Suche liefen alle Trials bewusst mit
        # fine_data=None (grobe Naeherung, siehe objective()) fuer Geschwindigkeit.
        #
        # WICHTIG: hier bewusst EIN grosser Bulk-Fetch (load_data, wie im
        # normalen Backtest-Modus) statt LazyFineData -- LazyFineData holt
        # Tag fuer Tag einzeln, was fuer EINEN durchgehenden Nachbewertungs-
        # Lauf ueber Monate/Jahre viel zu viele Einzel-Requests bedeutet
        # (gemessen: >12 Min fuer ein 3-Jahres-Fenster, dabei >80% reine
        # Netzwerk-Wartezeit). Ausserdem teilen sich alle LazyFineData-Tage
        # DIESELBE Cache-Datei (siehe load_data()) -- jeder neue Tag ueberschreibt
        # den Cache des vorherigen, der Disk-Cache bringt beim Tag-fuer-Tag-
        # Muster also nichts. Ein einziger zusammenhaengender Bulk-Fetch nutzt
        # Bitgets 200-Kerzen-Pagination viel effizienter (wenige grosse statt
        # viele kleine Requests) UND landet in einem wiederverwendbaren Cache.
        # Diese Phase ist bewusst quiet=True beim Fetch (siehe oben), dauert
        # aber laut Profiling mehrere Minuten (IS ~207s, OOS ~112s) -- ohne
        # sichtbaren Fortschritt sah das im Live-Betrieb wie ein Haenger aus.
        print(f"  Praezise Nachbewertung mit Feindaten laeuft (kann mehrere Minuten dauern)...")
        _pnb_start = time.time()
        fine_data_precise = None
        if fine_tf:
            fine_data_precise = load_data(symbol, fine_tf, args.start_date, args.end_date, quiet=True)
            if fine_data_precise.empty:
                fine_data_precise = None
            print(f"    ... Feindaten geladen ({time.time()-_pnb_start:.0f}s)")

        best_strategy_params = best_trial.user_attrs.get('strategy_params')
        best_risk_params     = best_trial.user_attrs.get('risk_params')
        if best_strategy_params is not None and best_risk_params is not None:
            best_is  = run_backtest(IS_DATA.copy(), best_strategy_params, best_risk_params, START_CAPITAL, verbose=False, fine_data=fine_data_precise, trade_start=EVAL_START)
            print(f"    ... IS-Nachbewertung fertig ({time.time()-_pnb_start:.0f}s)")
            best_oos = run_backtest(OOS_DATA.copy(), best_strategy_params, best_risk_params, START_CAPITAL, verbose=False, fine_data=fine_data_precise, trade_start=split_ts)
            print(f"    ... OOS-Nachbewertung fertig ({time.time()-_pnb_start:.0f}s)")
            new_pnl  = best_is.get('total_pnl_pct', new_pnl)
        else:
            # Fallback (sollte nicht vorkommen): grobe Such-Werte verwenden
            best_is  = best_trial.user_attrs.get('is_stats', {})
            best_oos = best_trial.user_attrs.get('oos_stats', {})

        config_dir         = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')
        os.makedirs(config_dir, exist_ok=True)
        config_filename    = f'config_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}.json'
        config_output_path = os.path.join(config_dir, config_filename)

        # Baseline = bestehende Config (falls vorhanden), auf denselben IS/OOS-
        # Daten ausgewertet -- das ist der "Ist-Zustand", gegen den der beste
        # Trial bestaetigt werden muss (analog dnabots DEFAULT_ALPHABET-Baseline,
        # aber stbot hat keinen universellen Default -- die aktuell aktive
        # Config IST hier der sinnvolle Vergleichsmassstab).
        baseline_is, baseline_oos = None, None
        if os.path.exists(config_output_path):
            try:
                with open(config_output_path) as cf:
                    existing_cfg = json.load(cf)
                baseline_strategy = dict(existing_cfg['strategy'])
                baseline_strategy.update({'symbol': symbol, 'timeframe': timeframe, 'htf': CURRENT_HTF})
                baseline_risk = dict(existing_cfg['risk'])
                print(f"  Bestehende Config als Baseline auf denselben Daten nachbewerten...")
                baseline_is  = run_backtest(IS_DATA.copy(), baseline_strategy, baseline_risk, START_CAPITAL, verbose=False, fine_data=fine_data_precise, trade_start=EVAL_START)
                baseline_oos = run_backtest(OOS_DATA.copy(), baseline_strategy, baseline_risk, START_CAPITAL, verbose=False, fine_data=fine_data_precise, trade_start=split_ts)
                print(f"    ... Baseline-Nachbewertung fertig ({time.time()-_pnb_start:.0f}s)")
            except Exception as e:
                print(f"  Warnung: Baseline-Bewertung fehlgeschlagen ({e}) -- werte ohne Baseline-Vergleich.")
                baseline_is, baseline_oos = None, None

        # Bestaetigung (analog dnabot): genug OOS-Trades fuer eine belastbare
        # Aussage, OOS-PnL positiv, UND (falls Baseline vorhanden) besser als
        # die bestehende Config auf denselben OOS-Daten.
        # (Kurz am 2026-08-21 versuchsweise auf nur die ersten beiden Bedingungen
        # gelockert wegen eines Leakage-Bedenkens bei alten, ungeteilt gefitteten
        # Baselines -- auf User-Entscheidung noch am selben Tag wieder auf die volle
        # 3-Bedingungen-Regel zurückgesetzt, siehe [[project_statebot]].)
        # bool(...) UM DEN GESAMTAUSDRUCK: die einzelnen Vergleiche liefern bei
        # numpy.float64-Operanden (total_pnl_pct kommt aus run_backtest(), das
        # mit pandas/numpy rechnet) ein numpy.bool_ statt eines echten Python
        # bool. Je nachdem, an welchem "and" die Kurzschluss-Auswertung landet,
        # ist das Ergebnis mal ein echter bool (z.B. wenn schon die erste
        # Bedingung False ist), mal ein numpy.bool_ -- und json.dump() bricht
        # bei numpy.bool_ mit "Object of type bool is not JSON serializable"
        # ab (im Live-Lauf beobachtet: BTC 4h/2h stuerzten NACH dem vollen
        # Optuna-Lauf beim Speichern ab, mehrere Minuten Rechenzeit verschenkt).
        confirmed = bool(
            _passes(best_is, best_oos)
            and (baseline_oos is None or best_oos.get('total_pnl_pct', -1e9) > baseline_oos.get('total_pnl_pct', -1e9))
        )
        # Neue Config besteht nicht oder ist schlechter als die bestehende, die bestehende
        # erfuellt die Kriterien aber selbst -> bestehende behalten statt ueberschreiben.
        keep_baseline = (not confirmed) and baseline_oos is not None and _passes(baseline_is, baseline_oos)

        mark = ('[BESTAETIGT]' if confirmed else
                '[nicht besser -- bestehende Config erfuellt die Kriterien und bleibt]' if keep_baseline else
                '[nicht bestaetigt -- wird gespeichert, aber NICHT fuer die Portfolio-Auswahl zugelassen]')
        print(f"\n  --- {symbol} ({timeframe}) --- {mark}")
        fold_pnls = best_trial.user_attrs.get('fold_pnls')
        if fold_pnls:
            fold_str = " / ".join(f"{p:+.1f}%" for p in fold_pnls)
            print(f"  IS-Teilfenster ({K_FOLDS}x, Robustheits-Score=Minimum): {fold_str}")
        if baseline_is is not None:
            print(f"  {'Metrik':<14}{'Baseline IS':>13}{'Best IS':>13}   |{'Baseline OOS':>14}{'Best OOS':>13}")
            print(f"  {'Trades':<14}{baseline_is.get('trades_count',0):>13}{best_is.get('trades_count',0):>13}   |"
                  f"{baseline_oos.get('trades_count',0):>14}{best_oos.get('trades_count',0):>13}")
            print(f"  {'WinRate':<14}{baseline_is.get('win_rate',0):>12.1f}%{best_is.get('win_rate',0):>12.1f}%   |"
                  f"{baseline_oos.get('win_rate',0):>13.1f}%{best_oos.get('win_rate',0):>12.1f}%")
            print(f"  {'PnL %':<14}{baseline_is.get('total_pnl_pct',0):>+12.1f}%{best_is.get('total_pnl_pct',0):>+12.1f}%   |"
                  f"{baseline_oos.get('total_pnl_pct',0):>+13.1f}%{best_oos.get('total_pnl_pct',0):>+12.1f}%")
            print(f"  {'MaxDD %':<14}{baseline_is.get('max_drawdown_pct',0)*100:>12.1f}%{best_is.get('max_drawdown_pct',0)*100:>12.1f}%   |"
                  f"{baseline_oos.get('max_drawdown_pct',0)*100:>13.1f}%{best_oos.get('max_drawdown_pct',0)*100:>12.1f}%")
        else:
            print(f"  (kein bestehender Config zum Vergleich -- erster Lauf fuer dieses Paar)")
            print(f"  {'Metrik':<14}{'Best IS':>13}   |{'Best OOS':>13}")
            print(f"  {'Trades':<14}{best_is.get('trades_count',0):>13}   |{best_oos.get('trades_count',0):>13}")
            print(f"  {'WinRate':<14}{best_is.get('win_rate',0):>12.1f}%   |{best_oos.get('win_rate',0):>12.1f}%")
            print(f"  {'PnL %':<14}{best_is.get('total_pnl_pct',0):>+12.1f}%   |{best_oos.get('total_pnl_pct',0):>+12.1f}%")
            print(f"  {'MaxDD %':<14}{best_is.get('max_drawdown_pct',0)*100:>12.1f}%   |{best_oos.get('max_drawdown_pct',0)*100:>12.1f}%")

        if keep_baseline:
            _write_meta(config_output_path, baseline_oos, split_ts, True, extra={'kept_over_reopt': _dt.now().isoformat(timespec='seconds')}, is_stats=baseline_is)
            print(f"\n[OK] Bestehende Konfiguration behalten (OOS {baseline_oos.get('total_pnl_pct', 0):+.1f}%).")
            run_results['saved'].append({'symbol': symbol, 'timeframe': timeframe, 'oos_pnl_pct': round(baseline_oos.get('total_pnl_pct', 0), 2),
                                         'confirmed': True, 'config_file': config_filename, 'kept_baseline': True})
            continue

        # Config wird IMMER geschrieben (auch unbestaetigt) -- auf Nutzerwunsch,
        # damit nie ein Paar komplett ohne Config dasteht. Die Bestaetigung wird
        # aber weiterhin klar in _meta.confirmed + der Konsolen-Ausgabe markiert
        # -- die Entscheidung, ein unbestaetigtes Ergebnis live zu verwenden,
        # liegt bewusst beim Nutzer, nicht bei einem automatischen Gate.
        strategy_config = {
            'pivot_period':      best_params['pivot_period'],
            'max_pivots':        best_params['max_pivots'],
            'channel_width_pct': best_params['channel_width_pct'],
            'max_sr_levels':     5,
            'min_strength':      best_params['min_strength'],
            'source':            best_params['source'],
            'use_weekly_trend_filter': best_params['use_weekly_trend_filter'],
            'weekly_trend_ema':  best_params.get('weekly_trend_ema', 4),
            'use_avalanche_filter': best_params.get('use_avalanche_filter', False),
            'avalanche_percentile_threshold': best_params.get('avalanche_percentile_threshold', 60),
            'use_energy_filter': best_params.get('use_energy_filter', False),
            'min_energy_zscore': best_params.get('min_energy_zscore', 0.0),
            'use_energy_streak_filter': best_params.get('use_energy_streak_filter', False),
        }
        risk_config = {
            'margin_mode':                    "isolated",
            'risk_per_trade_pct':             round(best_params['risk_per_trade_pct'], 2),
            'leverage':                       best_params['leverage'],
            'trailing_stop_activation_rr':    round(best_params['trailing_stop_activation_rr'], 2),
            'trailing_stop_callback_rate_pct':round(best_params['trailing_stop_callback_rate_pct'], 2),
            'atr_multiplier_sl':              round(best_params['atr_multiplier_sl'], 2),
            'min_sl_pct':                     0.3,
        }
        behavior_config = {"use_longs": True, "use_shorts": True}

        config_output = {
            "market":   {"symbol": symbol, "timeframe": timeframe, "htf": CURRENT_HTF},
            "strategy": strategy_config,
            "risk":     risk_config,
            "behavior": behavior_config,
            "_meta": {
                "pnl_pct":         round(new_pnl, 2),
                "oos_pnl_pct":     round(best_oos.get('total_pnl_pct', 0), 2),
                "oos_trades":      best_oos.get('trades_count', 0),
                "oos_max_dd_pct":  round(best_oos.get('max_drawdown_pct', 0) * 100, 2),
                "oos_win_rate":    round(best_oos.get('win_rate', 0), 1),
                "criteria":        _criteria_str(),
                "is_oos_split_date": str(split_ts.date()),
                "confirmed":       confirmed,
                "optimized_at":    _dt.now().isoformat(timespec='seconds'),
            },
        }
        with open(config_output_path, 'w') as f:
            json.dump(config_output, f, indent=4)
        if confirmed:
            print(f"\n[OK] Bestaetigte Konfiguration gespeichert.")
        else:
            print(f"\n[WARNUNG] NICHT bestaetigte Konfiguration trotzdem gespeichert "
                  f"(OOS-PnL {best_oos.get('total_pnl_pct', 0):+.1f}%) -- vor Live-Einsatz pruefen.")

        run_results['saved'].append({
            'symbol':      symbol,
            'timeframe':   timeframe,
            'pnl_pct':     round(new_pnl, 2),
            'oos_pnl_pct': round(best_oos.get('total_pnl_pct', 0), 2),
            'confirmed':   confirmed,
            'config_file': config_filename,
        })

    # Lauf-Ergebnisse fuer Scheduler speichern
    run_results['run_end'] = _dt.now().isoformat(timespec='seconds')
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(run_results, f, indent=2, ensure_ascii=False)
    print(f"\nErgebnisse gespeichert: {RESULTS_FILE}")


if __name__ == "__main__":
    main()
