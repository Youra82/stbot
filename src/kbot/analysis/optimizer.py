# src/kbot/analysis/optimizer.py
# =============================================================================
# KBot: Parameter-Optimierung für Stoch‑RSI Strategie
# =============================================================================

import os
import sys
import json
import optuna
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from kbot.analysis.backtester import load_data, run_backtest
# Stoch‑RSI verwendet eigene Engine (siehe src/kbot/strategy/stochrsi_engine.py) -- Optimizer nutzt run_backtest() direkt

optuna.logging.set_verbosity(optuna.logging.WARNING)

# Globale Variablen
HISTORICAL_DATA = None
MAX_DRAWDOWN_CONSTRAINT = 30.0
MIN_WIN_RATE_CONSTRAINT = 50.0
MIN_PNL_CONSTRAINT = 0.0
MIN_TRADES = 10
START_CAPITAL = 1000
OPTIM_MODE = "strict"


def objective(trial):
    """Optuna Objective für Stoch‑RSI Parameter."""
    global HISTORICAL_DATA

    params = {
        'strategy': {
            'rsi_period': trial.suggest_int('rsi_period', 7, 21),
            'stochrsi_len': trial.suggest_int('stochrsi_len', 7, 21),
            'k': trial.suggest_int('k', 1, 5),
            'd': trial.suggest_int('d', 1, 5),
            'ob': trial.suggest_float('ob', 0.6, 0.95, step=0.05),
            'os': trial.suggest_float('os', 0.05, 0.4, step=0.05),
            'atr_period': trial.suggest_int('atr_period', 7, 50),
            'sl_atr_mult': trial.suggest_float('sl_atr_mult', 0.8, 3.0, step=0.1),
            'risk_reward_ratio': trial.suggest_float('risk_reward_ratio', 1.2, 4.0, step=0.1)
        },
        'risk': {
            'risk_per_trade_pct': trial.suggest_float('risk_per_trade_pct', 0.5, 2.0, step=0.25),
            'leverage': trial.suggest_int('leverage', 1, 20),
        },
        'behavior': {
            'use_longs': True,
            'use_shorts': True,
        }
    }

    result = run_backtest(HISTORICAL_DATA.copy(), params, start_capital=START_CAPITAL, verbose=False)

    pnl = result.get('total_pnl_pct', -1000)
    drawdown = result.get('max_drawdown_pct', 100)
    trades = result.get('trades_count', 0)
    win_rate = result.get('win_rate', 0)

    # Basic pruning
    if trades < MIN_TRADES:
        raise optuna.exceptions.TrialPruned()
    if drawdown > MAX_DRAWDOWN_CONSTRAINT:
        raise optuna.exceptions.TrialPruned()

    # Objective: risk-adjusted return
    score = pnl / max(drawdown, 0.1)
    return score


def create_safe_filename(symbol: str, timeframe: str) -> str:
    """Erstellt einen sicheren Dateinamen."""
    return f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"


def save_config(symbol: str, timeframe: str, best_params: dict, 
                result: dict, start_date: str, end_date: str):
    """Speichert die beste Konfiguration."""
    
    safe_filename = create_safe_filename(symbol, timeframe)
    config_dir = os.path.join(PROJECT_ROOT, 'src', 'kbot', 'strategy', 'configs')
    os.makedirs(config_dir, exist_ok=True)
    
    config = {
        "market": {
            "symbol": symbol,
            "timeframe": timeframe
        },
        "strategy": {
            "rsi_period": best_params.get('rsi_period', 14),
            "stochrsi_len": best_params.get('stochrsi_len', 14),
            "k": best_params.get('k', 3),
            "d": best_params.get('d', 3),
            "ob": best_params.get('ob', 0.8),
            "os": best_params.get('os', 0.2),
            "atr_period": best_params.get('atr_period', 14),
            "sl_atr_mult": best_params.get('sl_atr_mult', 1.5),
            "risk_reward_ratio": best_params.get('risk_reward_ratio', 2.0)
        },
        "risk": {
            "margin_mode": "isolated",
            "risk_per_trade_pct": best_params.get('risk_per_trade_pct', 1.0),
            "leverage": best_params.get('leverage', 5)
        },
        "behavior": {
            "use_longs": True,
            "use_shorts": True
        },
        "optimization": {
            "optimized_at": datetime.now().isoformat(),
            "data_range": f"{start_date} to {end_date}",
            "backtest_pnl_pct": round(result.get('total_pnl_pct', 0), 2),
            "backtest_win_rate": round(result.get('win_rate', 0), 1),
            "backtest_max_dd_pct": round(result.get('max_drawdown_pct', 0), 2),
            "backtest_trades": result.get('trades_count', 0),
            "backtest_profit_factor": round(result.get('profit_factor', 0), 2)
        }
    }
    
    config_path = os.path.join(config_dir, f"config_{safe_filename}.json")
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)
    
    print(f"\n✅ Konfiguration gespeichert: {config_path}")
    return config_path


def main():
    global HISTORICAL_DATA, MAX_DRAWDOWN_CONSTRAINT, MIN_WIN_RATE_CONSTRAINT
    global MIN_PNL_CONSTRAINT, MIN_TRADES, START_CAPITAL, OPTIM_MODE
    
    parser = argparse.ArgumentParser(description="KBot Stoch‑RSI Optimizer")
    parser.add_argument('--symbols', required=True, type=str, help="Symbole (z.B. BTC ETH)")
    parser.add_argument('--timeframes', required=True, type=str, help="Timeframes (z.B. 4h 1d)")
    parser.add_argument('--start_date', required=True, type=str, help="Start-Datum")
    parser.add_argument('--end_date', required=True, type=str, help="End-Datum")
    parser.add_argument('--trials', type=int, default=100, help="Anzahl Optuna Trials")
    parser.add_argument('--jobs', type=int, default=-1, help="CPU-Kerne (-1 = alle)")
    parser.add_argument('--max_drawdown', type=float, default=30, help="Max Drawdown %")
    parser.add_argument('--min_win_rate', type=float, default=50, help="Min Win-Rate %")
    parser.add_argument('--min_pnl', type=float, default=0, help="Min PnL %")
    parser.add_argument('--min_trades', type=int, default=10, help="Min Trades")
    parser.add_argument('--start_capital', type=float, default=1000, help="Startkapital")
    parser.add_argument('--mode', type=str, default='strict', choices=['strict', 'best_profit'])
    args = parser.parse_args()
    
    # Globale Constraints setzen
    MAX_DRAWDOWN_CONSTRAINT = args.max_drawdown
    MIN_WIN_RATE_CONSTRAINT = args.min_win_rate
    MIN_PNL_CONSTRAINT = args.min_pnl
    MIN_TRADES = args.min_trades
    START_CAPITAL = args.start_capital
    OPTIM_MODE = args.mode
    
    symbols = [f"{s}/USDT:USDT" for s in args.symbols.split()]
    timeframes = args.timeframes.split()
    
    print("\n" + "=" * 60)
    print("   KBot Stoch‑RSI - Parameter Optimierung")
    print("=" * 60)
    print(f"   Symbole:      {', '.join(symbols)}")
    print(f"   Timeframes:   {', '.join(timeframes)}")
    print(f"   Zeitraum:     {args.start_date} bis {args.end_date}")
    print(f"   Trials:       {args.trials}")
    print(f"   Modus:        {args.mode}")
    print("=" * 60)
    
    for symbol in symbols:
        for timeframe in timeframes:
            print(f"\n{'─' * 50}")
            print(f"🔍 Optimiere: {symbol} ({timeframe})")
            print(f"{'─' * 50}")
            
            # Daten laden
            HISTORICAL_DATA = load_data(symbol, timeframe, args.start_date, args.end_date)
            
            if HISTORICAL_DATA.empty or len(HISTORICAL_DATA) < 100:
                print(f"⚠️ Nicht genug Daten für {symbol} ({timeframe}). Überspringe.")
                continue
            
            print(f"📊 Daten geladen: {len(HISTORICAL_DATA)} Kerzen")
            print(f"📅 Zeitraum: {HISTORICAL_DATA.index.min()} bis {HISTORICAL_DATA.index.max()}")
            
            # Optuna Study erstellen
            safe_filename = create_safe_filename(symbol, timeframe)
            db_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'db')
            os.makedirs(db_dir, exist_ok=True)
            
            storage_url = f"sqlite:///{db_dir}/optuna_stochrsi.db?timeout=60"
            study_name = f"stochrsi_{safe_filename}_{args.mode}"
            
            study = optuna.create_study(
                storage=storage_url,
                study_name=study_name,
                direction="maximize",
                load_if_exists=True
            )
            
            print(f"\n🚀 Starte Optimierung mit {args.trials} Trials...")
            
            study.optimize(
                objective,
                n_trials=args.trials,
                n_jobs=args.jobs if args.jobs > 0 else 1,
                show_progress_bar=True
            )
            
            # Ergebnisse auswerten
            valid_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
            
            if not valid_trials:
                print(f"\n❌ Keine gültigen Parameter gefunden für {symbol} ({timeframe})")
                continue
            
            best = study.best_trial
            print(f"\n✅ Beste Parameter gefunden (Score: {best.value:.2f}):")
            for key, value in best.params.items():
                print(f"   {key}: {value}")
            
            # Finaler Backtest mit besten Parametern
            final_params = {
                'strategy': {
                    'atr_period': best.params.get('atr_period', 200),
                    'channel_width': best.params.get('channel_width', 3.0),
                    'min_channel_length': best.params.get('min_channel_length', 10),
                    'volume_bins': best.params.get('volume_bins', 30),
                    'use_volume_confirmation': best.params.get('use_volume_confirmation', True),
                    'risk_reward_ratio': best.params.get('risk_reward_ratio', 2.0),
                },
                'risk': {
                    'risk_per_trade_pct': best.params.get('risk_per_trade_pct', 1.0),
                    'leverage': best.params.get('leverage', 5),
                },
                'behavior': {
                    'use_longs': True,
                    'use_shorts': True,
                }
            }
            
            final_result = run_backtest(HISTORICAL_DATA.copy(), final_params, 
                                        start_capital=START_CAPITAL, verbose=False)
            
            print(f"\n📊 FINALES BACKTEST-ERGEBNIS:")
            print(f"   Trades:        {final_result['trades_count']}")
            print(f"   Win-Rate:      {final_result['win_rate']:.1f}%")
            print(f"   Rendite:       {final_result['total_pnl_pct']:.2f}%")
            print(f"   Max Drawdown:  {final_result['max_drawdown_pct']:.2f}%")
            print(f"   Profit Factor: {final_result.get('profit_factor', 0):.2f}")
            print(f"   Endkapital:    ${final_result['end_capital']:.2f}")
            
            # Config speichern
            save_config(symbol, timeframe, best.params, final_result,
                       args.start_date, args.end_date)
    
    print("\n" + "=" * 60)
    print("   ✅ Optimierung abgeschlossen!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
