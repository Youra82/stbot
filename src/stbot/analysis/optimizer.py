# /root/utbot2/src/stbot/analysis/optimizer.py
import os
import sys
import json
import optuna
import numpy as np
import argparse
import logging
import warnings

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)
warnings.filterwarnings('ignore', category=UserWarning, module='keras')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# Imports auf stbot angepasst
from stbot.analysis.backtester import load_data, run_backtest
from stbot.utils.timeframe_utils import determine_htf

optuna.logging.set_verbosity(optuna.logging.WARNING)

HISTORICAL_DATA = None
CURRENT_SYMBOL = None
CURRENT_TIMEFRAME = None
CURRENT_HTF = None
CONFIG_SUFFIX = ""
MAX_DRAWDOWN_CONSTRAINT = 0.30
MIN_WIN_RATE_CONSTRAINT = 55.0
MIN_PNL_CONSTRAINT = 0.0
START_CAPITAL = 1000
OPTIM_MODE = "strict"

def create_safe_filename(symbol, timeframe):
    return f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"

def objective(trial):
    # --- SRv2 Strategie Parameter (StBot) ---
    strategy_params = {
        'pivot_period': trial.suggest_int('pivot_period', 5, 30),        # Prd
        'max_pivots': trial.suggest_int('max_pivots', 10, 60),           # Max Num PP
        'channel_width_pct': trial.suggest_int('channel_width_pct', 5, 25), # ChannelW
        'max_sr_levels': 5, # Fest lassen, um Komplexität zu begrenzen
        'min_strength': trial.suggest_int('min_strength', 1, 4),         # Min Strength
        'source': trial.suggest_categorical('source', ['High/Low', 'Close/Open']),

        'symbol': CURRENT_SYMBOL,
        'timeframe': CURRENT_TIMEFRAME,
        'htf': CURRENT_HTF
    }

    # --- Risiko Parameter ---
    risk_params = {
        'risk_reward_ratio': trial.suggest_float('risk_reward_ratio', 1.5, 5.0),
        'risk_per_trade_pct': trial.suggest_float('risk_per_trade_pct', 0.5, 3.0),
        'leverage': trial.suggest_int('leverage', 5, 20),
        'trailing_stop_activation_rr': trial.suggest_float('trailing_stop_activation_rr', 1.0, 3.0),
        'trailing_stop_callback_rate_pct': trial.suggest_float('trailing_stop_callback_rate_pct', 0.2, 2.0),
        'atr_multiplier_sl': trial.suggest_float('atr_multiplier_sl', 1.5, 5.0),
        'min_sl_pct': 0.3
    }

    result = run_backtest(HISTORICAL_DATA.copy(), strategy_params, risk_params, START_CAPITAL, verbose=False)

    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 1.0)
    trades = result.get('trades_count', 0)
    win_rate = result.get('win_rate', 0)

    if OPTIM_MODE == "strict" and (
        drawdown > MAX_DRAWDOWN_CONSTRAINT or win_rate < MIN_WIN_RATE_CONSTRAINT or
        pnl < MIN_PNL_CONSTRAINT or trades < 20): # Etwas weniger Trades erlaubt bei SR
        raise optuna.exceptions.TrialPruned()
    elif OPTIM_MODE == "best_profit" and (
        drawdown > MAX_DRAWDOWN_CONSTRAINT or trades < 20):
        raise optuna.exceptions.TrialPruned()

    return pnl

def main():
    global HISTORICAL_DATA, CURRENT_SYMBOL, CURRENT_TIMEFRAME, CURRENT_HTF, CONFIG_SUFFIX, MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT, START_CAPITAL, OPTIM_MODE
    parser = argparse.ArgumentParser(description="Parameter-Optimierung für StBot (SRv2)")
    parser.add_argument('--symbols', required=True, type=str)
    parser.add_argument('--timeframes', required=True, type=str)
    parser.add_argument('--start_date', required=True, type=str)
    parser.add_argument('--end_date', required=True, type=str)
    parser.add_argument('--jobs', required=True, type=int)
    parser.add_argument('--max_drawdown', required=True, type=float)
    parser.add_argument('--start_capital', required=True, type=float)
    parser.add_argument('--min_win_rate', required=True, type=float)
    parser.add_argument('--trials', required=True, type=int)
    parser.add_argument('--min_pnl', required=True, type=float)
    parser.add_argument('--mode', required=True, type=str)
    parser.add_argument('--config_suffix', type=str, default="")
    args = parser.parse_args()

    CONFIG_SUFFIX = args.config_suffix
    MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT, MIN_PNL_CONSTRAINT = args.max_drawdown / 100.0, args.min_win_rate, args.min_pnl
    START_CAPITAL, N_TRIALS, OPTIM_MODE = args.start_capital, args.trials, args.mode

    symbols, timeframes = args.symbols.split(), args.timeframes.split()
    TASKS = [{'symbol': f"{s}/USDT:USDT", 'timeframe': tf} for s in symbols for tf in timeframes]

    for task in TASKS:
        symbol, timeframe = task['symbol'], task['timeframe']
        CURRENT_SYMBOL = symbol
        CURRENT_TIMEFRAME = timeframe
        CURRENT_HTF = determine_htf(timeframe)

        print(f"\n===== Optimiere: {symbol} ({timeframe}) [SRv2] =====")
        HISTORICAL_DATA = load_data(symbol, timeframe, args.start_date, args.end_date)
        if HISTORICAL_DATA.empty: continue

        DB_FILE = os.path.join(PROJECT_ROOT, 'artifacts', 'db', 'optuna_studies_stbot.db')
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        STORAGE_URL = f"sqlite:///{DB_FILE}?timeout=60"
        study_name = f"sr_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}_{OPTIM_MODE}"

        study = optuna.create_study(storage=STORAGE_URL, study_name=study_name, direction="maximize", load_if_exists=True)
        try:
            study.optimize(objective, n_trials=N_TRIALS, n_jobs=args.jobs, show_progress_bar=True)
        except Exception as e:
            print(f"FEHLER: {e}")
            continue

        valid_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not valid_trials: continue

        best_trial = max(valid_trials, key=lambda t: t.value)
        best_params = best_trial.params

        config_dir = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')
        os.makedirs(config_dir, exist_ok=True)
        config_output_path = os.path.join(config_dir, f'config_{create_safe_filename(symbol, timeframe)}{CONFIG_SUFFIX}.json')

        strategy_config = {
            'pivot_period': best_params['pivot_period'],
            'max_pivots': best_params['max_pivots'],
            'channel_width_pct': best_params['channel_width_pct'],
            'max_sr_levels': 5,
            'min_strength': best_params['min_strength'],
            'source': best_params['source']
        }

        risk_config = {
            'margin_mode': "isolated",
            'risk_per_trade_pct': round(best_params['risk_per_trade_pct'], 2),
            'risk_reward_ratio': round(best_params['risk_reward_ratio'], 2),
            'leverage': best_params['leverage'],
            'trailing_stop_activation_rr': round(best_params['trailing_stop_activation_rr'], 2),
            'trailing_stop_callback_rate_pct': round(best_params['trailing_stop_callback_rate_pct'], 2),
            'atr_multiplier_sl': round(best_params['atr_multiplier_sl'], 2),
            'min_sl_pct': 0.3
        }
        behavior_config = {"use_longs": True, "use_shorts": True}

        config_output = {
            "market": {"symbol": symbol, "timeframe": timeframe, "htf": CURRENT_HTF},
            "strategy": strategy_config,
            "risk": risk_config, "behavior": behavior_config
        }
        with open(config_output_path, 'w') as f: json.dump(config_output, f, indent=4)
        print(f"\n✔ Beste Konfiguration gespeichert.")

if __name__ == "__main__":
    main()
