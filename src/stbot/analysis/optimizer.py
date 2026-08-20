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

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)
warnings.filterwarnings('ignore', category=UserWarning, module='keras')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from stbot.analysis.backtester import load_data, run_backtest, FINE_TF_MAP, LazyFineData
from stbot.utils.timeframe_utils import determine_htf

optuna.logging.set_verbosity(optuna.logging.WARNING)

HISTORICAL_DATA = None
FINE_DATA = None  # feinere Kerzen fuer Intrabar-Pfad-Aufloesung (oraclebot-Muster)
CURRENT_SYMBOL = None
CURRENT_TIMEFRAME = None
CURRENT_HTF = None
CONFIG_SUFFIX = ""
MAX_DRAWDOWN_CONSTRAINT = 0.30
MIN_WIN_RATE_CONSTRAINT = 55.0
MIN_PNL_CONSTRAINT = 0.0
START_CAPITAL = 1000
OPTIM_MODE = "strict"

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

    result   = run_backtest(HISTORICAL_DATA.copy(), strategy_params, risk_params, START_CAPITAL, verbose=False, fine_data=FINE_DATA)
    pnl      = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0)
    trades   = result.get('trades_count', 0)
    win_rate = result.get('win_rate', 0)

    if OPTIM_MODE == "strict" and (
        drawdown > MAX_DRAWDOWN_CONSTRAINT or win_rate < MIN_WIN_RATE_CONSTRAINT
        or pnl < MIN_PNL_CONSTRAINT or trades < 20
    ):
        raise optuna.exceptions.TrialPruned()
    elif OPTIM_MODE == "best_profit" and (drawdown > MAX_DRAWDOWN_CONSTRAINT or trades < 20):
        raise optuna.exceptions.TrialPruned()

    return pnl


def main():
    global HISTORICAL_DATA, FINE_DATA, CURRENT_SYMBOL, CURRENT_TIMEFRAME, CURRENT_HTF, CONFIG_SUFFIX
    global MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT, START_CAPITAL, OPTIM_MODE

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
    args = parser.parse_args()

    CONFIG_SUFFIX           = args.config_suffix
    MAX_DRAWDOWN_CONSTRAINT = args.max_drawdown / 100.0
    MIN_WIN_RATE_CONSTRAINT = args.min_win_rate
    MIN_PNL_CONSTRAINT      = args.min_pnl
    START_CAPITAL           = args.start_capital
    N_TRIALS                = args.trials
    OPTIM_MODE              = args.mode

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

        # Feinere Kerzen fuer Intrabar-Pfad-Aufloesung (oraclebot-Muster) -- ersetzt
        # die grobe Kerzenfarben-Annaeherung im Backtester, wenn verfuegbar.
        # On-Demand (LazyFineData): laedt nur die Tage, an denen im Backtest
        # tatsaechlich eine offene Position liegt, statt den ganzen Zeitraum
        # vorab herunterzuladen -- eine Instanz wird ueber alle Optuna-Trials
        # dieses Symbols hinweg wiederverwendet (Cache bleibt erhalten).
        fine_tf = FINE_TF_MAP.get(timeframe)
        FINE_DATA = LazyFineData(symbol, fine_tf) if fine_tf else None

        DB_FILE      = os.path.join(PROJECT_ROOT, 'artifacts', 'db', 'optuna_studies_stbot.db')
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        STORAGE_URL  = f"sqlite:///{DB_FILE}?timeout=60"
        study_name   = f"sr_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}_{OPTIM_MODE}"

        study = optuna.create_study(
            storage=STORAGE_URL, study_name=study_name,
            direction="maximize", load_if_exists=True)
        try:
            study.optimize(objective, n_trials=N_TRIALS, n_jobs=args.jobs, show_progress_bar=True)
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

        config_dir         = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')
        os.makedirs(config_dir, exist_ok=True)
        config_filename    = f'config_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}.json'
        config_output_path = os.path.join(config_dir, config_filename)

        # Nur speichern wenn besser als bestehende Config
        existing_pnl = None
        if os.path.exists(config_output_path):
            try:
                with open(config_output_path) as cf:
                    existing_cfg = json.load(cf)
                existing_pnl = existing_cfg.get('_meta', {}).get('pnl_pct')
            except Exception:
                pass

        if existing_pnl is not None and new_pnl <= existing_pnl:
            print(f"  Bestehende Config besser ({existing_pnl:.2f}% vs {new_pnl:.2f}%) — wird nicht ueberschrieben.")
            run_results['failed'].append({
                'symbol': symbol, 'timeframe': timeframe,
                'reason': f'existing_better_{existing_pnl:.2f}pct',
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
                "pnl_pct":      round(new_pnl, 2),
                "optimized_at": _dt.now().isoformat(timespec='seconds'),
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
