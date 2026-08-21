# /root/stbot/src/stbot/analysis/optimizer.py
import os
import sys
import json
import optuna
import numpy as np
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
    risk_params = {
        'risk_reward_ratio':              trial.suggest_float('risk_reward_ratio', 1.5, 5.0),
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
    is_result = run_backtest(IS_DATA.copy(), strategy_params, risk_params, START_CAPITAL, verbose=False, fine_data=None)
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

    # OOS nur fuer Trials rechnen, die die IS-Kriterien ueberhaupt erfuellen
    # (spart Rechenzeit fuer die vielen von vornherein verworfenen Trials).
    # Bekannte Vereinfachung ggue. dnabot: dort laeuft EIN durchgehender
    # Backtest, dessen Trade-Liste danach nach entry_time gesplittet wird --
    # stbots run_backtest() gibt keine Trade-Liste zurueck (nur Aggregat-
    # Stats), daher hier zwei UNABHAENGIGE Backtests. Nachteil: Indikatoren
    # mit Rolling-Fenstern (z.B. avalanche_percentile, energy_zscore) muessen
    # im OOS-Abschnitt eigenstaendig neu "warmlaufen" statt nahtlos an IS
    # anzuschliessen -- die ersten ~100 OOS-Kerzen sind dadurch etwas
    # konservativer als im echten Live-Betrieb (Filter dort ggf. noch NaN).
    oos_result = run_backtest(OOS_DATA.copy(), strategy_params, risk_params, START_CAPITAL, verbose=False, fine_data=None)
    trial.set_user_attr('is_stats', is_result)
    trial.set_user_attr('oos_stats', oos_result)
    trial.set_user_attr('strategy_params', strategy_params)
    trial.set_user_attr('risk_params', risk_params)

    # Robustheits-Score statt reiner Gesamt-IS-PnL: IS_DATA in K_FOLDS
    # aufeinanderfolgende Teilfenster splitten, jedes einzeln backtesten
    # (weiterhin fine_data=None, billig) und das SCHLECHTESTE Teilfenster
    # als Optuna-Zielwert nehmen. Reine Gesamt-PnL-Optimierung bevorzugt
    # Parameter, die eine einzelne Marktphase zufaellig gut treffen -- genau
    # das Muster, das beim ersten echten Testlauf auffiel (BTC 6h: IS +190%,
    # OOS -11.8%). Das Minimum ueber mehrere Teilfenster bestraft das schon
    # WAEHREND der Suche, nicht erst hinterher im OOS-Check. Pruning-Kriterien
    # oben bleiben auf dem VOLLEN IS-Fenster (genug Daten fuer eine
    # verlaessliche trades>=20/Drawdown-Pruefung; einzelne Teilfenster waeren
    # dafuer oft zu kurz).
    fold_size = len(IS_DATA) // K_FOLDS
    fold_pnls = []
    for k in range(K_FOLDS):
        fold_data = IS_DATA.iloc[k * fold_size: (k + 1) * fold_size if k < K_FOLDS - 1 else len(IS_DATA)]
        fold_result = run_backtest(fold_data.copy(), strategy_params, risk_params, START_CAPITAL, verbose=False, fine_data=None)
        fold_pnls.append(fold_result.get('total_pnl_pct', -1000))
    trial.set_user_attr('fold_pnls', fold_pnls)
    robust_score = min(fold_pnls)

    return robust_score


def main():
    global HISTORICAL_DATA, IS_DATA, OOS_DATA, CURRENT_SYMBOL, CURRENT_TIMEFRAME, CURRENT_HTF, CONFIG_SUFFIX
    global MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT, START_CAPITAL, OPTIM_MODE
    global IS_FRACTION, MIN_OOS_TRADES, K_FOLDS

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
    parser.add_argument('--min_oos_trades', type=int, default=10,
                        help='Mindestanzahl OOS-Trades fuer eine belastbare Bestaetigung, Standard 10')
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

        # Chronologischer IS/OOS-Split (analog dnabot/alphabet_optimizer.py):
        # die ersten IS_FRACTION der Kerzen sieht Optuna (Zielfunktion), der
        # Rest dient ausschliesslich der spaeteren Bestaetigung.
        split_idx = int(len(HISTORICAL_DATA) * IS_FRACTION)
        split_ts  = HISTORICAL_DATA.index[split_idx]
        IS_DATA   = HISTORICAL_DATA.iloc[:split_idx]
        OOS_DATA  = HISTORICAL_DATA.iloc[split_idx:]
        logging.info(
            f"{symbol} ({timeframe}): {len(HISTORICAL_DATA)} Kerzen | "
            f"IS bis {split_ts.date()} ({split_idx} Kerzen) | "
            f"OOS ab {split_ts.date()} ({len(HISTORICAL_DATA) - split_idx} Kerzen)"
        )

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
        fine_data_precise = None
        if fine_tf:
            fine_data_precise = load_data(symbol, fine_tf, args.start_date, args.end_date, quiet=True)
            if fine_data_precise.empty:
                fine_data_precise = None

        best_strategy_params = best_trial.user_attrs.get('strategy_params')
        best_risk_params     = best_trial.user_attrs.get('risk_params')
        if best_strategy_params is not None and best_risk_params is not None:
            best_is  = run_backtest(IS_DATA.copy(), best_strategy_params, best_risk_params, START_CAPITAL, verbose=False, fine_data=fine_data_precise)
            best_oos = run_backtest(OOS_DATA.copy(), best_strategy_params, best_risk_params, START_CAPITAL, verbose=False, fine_data=fine_data_precise)
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
                baseline_is  = run_backtest(IS_DATA.copy(), baseline_strategy, baseline_risk, START_CAPITAL, verbose=False, fine_data=fine_data_precise)
                baseline_oos = run_backtest(OOS_DATA.copy(), baseline_strategy, baseline_risk, START_CAPITAL, verbose=False, fine_data=fine_data_precise)
            except Exception as e:
                print(f"  Warnung: Baseline-Bewertung fehlgeschlagen ({e}) -- werte ohne Baseline-Vergleich.")
                baseline_is, baseline_oos = None, None

        # Bestaetigung (analog dnabot): genug OOS-Trades fuer eine belastbare
        # Aussage, OOS-PnL positiv, UND (falls Baseline vorhanden) besser als
        # die bestehende Config auf denselben OOS-Daten.
        confirmed = (
            best_oos.get('trades_count', 0) >= MIN_OOS_TRADES
            and best_oos.get('total_pnl_pct', -1e9) > 0.0
            and (baseline_oos is None or best_oos.get('total_pnl_pct', -1e9) > baseline_oos.get('total_pnl_pct', -1e9))
        )

        mark = '[BESTAETIGT]' if confirmed else '[nicht bestaetigt -- Ist-Zustand behalten]'
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

        if not confirmed:
            run_results['failed'].append({
                'symbol': symbol, 'timeframe': timeframe,
                'reason': 'not_confirmed_oos',
            })
            continue

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
            'risk_reward_ratio':              round(best_params['risk_reward_ratio'], 2),
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
                "is_oos_split_date": str(split_ts.date()),
                "optimized_at":    _dt.now().isoformat(timespec='seconds'),
            },
        }
        with open(config_output_path, 'w') as f:
            json.dump(config_output, f, indent=4)
        print(f"\n[OK] Beste Konfiguration gespeichert.")

        run_results['saved'].append({
            'symbol':      symbol,
            'timeframe':   timeframe,
            'pnl_pct':     round(new_pnl, 2),
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
