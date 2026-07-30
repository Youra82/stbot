"""
Untersucht, wie viele Optuna-Trials fuer die stbot-Parametersuche tatsaechlich
noetig sind: misst best-PnL-so-far ueber die Trial-Anzahl, gemittelt ueber
mehrere Zufalls-Seeds (fuer eine belastbare Kurve statt einem verrauschten
Einzellauf). Vergleicht zusaetzlich den aktuellen 11-Parameter-Suchraum gegen
einen 10-Parameter-Suchraum ohne 'risk_reward_ratio' (seit dem Backtester-Fix
fuer den echten Trailing-Stop wirkungslos, siehe backtester.py).

Aufruf:
    python daten/trial_convergence_study.py --pairs "BTC|1h,BTC|4h,XRP|6h" \
        --trials 600 --seeds 3
"""
import sys, os, json, time, argparse
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
import optuna

from stbot.analysis.backtester import load_data, run_backtest
from stbot.utils.timeframe_utils import determine_htf

optuna.logging.set_verbosity(optuna.logging.WARNING)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

LOOKBACK_DAYS = {
    '5m': 60, '15m': 60, '30m': 365, '1h': 365,
    '2h': 730, '4h': 730, '6h': 1095, '1d': 1095,
}
END_DATE = "2026-07-30"
START_CAPITAL = 100
MAX_DD_CONSTRAINT = 0.30
MIN_TRADES = 20


def make_objective(data, symbol, timeframe, htf, include_rr):
    def objective(trial):
        strategy_params = {
            'pivot_period':      trial.suggest_int('pivot_period', 5, 30),
            'max_pivots':        trial.suggest_int('max_pivots', 10, 60),
            'channel_width_pct': trial.suggest_int('channel_width_pct', 5, 25),
            'max_sr_levels':     5,
            'min_strength':      trial.suggest_int('min_strength', 1, 4),
            'source':            trial.suggest_categorical('source', ['High/Low', 'Close/Open']),
            'symbol': symbol, 'timeframe': timeframe, 'htf': htf,
        }
        risk_params = {
            'risk_per_trade_pct':              trial.suggest_float('risk_per_trade_pct', 0.5, 3.0),
            'leverage':                        trial.suggest_int('leverage', 5, 20),
            'trailing_stop_activation_rr':     trial.suggest_float('trailing_stop_activation_rr', 1.0, 3.0),
            'trailing_stop_callback_rate_pct': trial.suggest_float('trailing_stop_callback_rate_pct', 0.2, 2.0),
            'atr_multiplier_sl':               trial.suggest_float('atr_multiplier_sl', 1.5, 5.0),
            'min_sl_pct': 0.3,
        }
        if include_rr:
            risk_params['risk_reward_ratio'] = trial.suggest_float('risk_reward_ratio', 1.5, 5.0)
        else:
            risk_params['risk_reward_ratio'] = 2.0  # fix, wird im Exit nicht mehr benutzt

        result = run_backtest(data.copy(), strategy_params, risk_params, START_CAPITAL, verbose=False)
        pnl     = result.get('total_pnl_pct', -1000)
        dd      = result.get('max_drawdown_pct', 1.0)
        trades  = result.get('trades_count', 0)

        # best_profit-Charakteristik: kein Winrate-Gate, nur MaxDD + Mindest-Tradezahl
        # (vermeidet die 55%-WR-Huerde, die aktuell separat diskutiert wird)
        if dd > MAX_DD_CONSTRAINT or trades < MIN_TRADES:
            raise optuna.exceptions.TrialPruned()
        return pnl
    return objective


def run_seeded_study(data, symbol, timeframe, htf, include_rr, n_trials, seed):
    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    objective = make_objective(data, symbol, timeframe, htf, include_rr)

    history = []
    best = None
    t0 = time.time()

    def callback(study, trial):
        nonlocal best
        if trial.value is not None:
            best = trial.value if best is None else max(best, trial.value)
        history.append({"trial": trial.number, "best_so_far": best, "elapsed_s": round(time.time() - t0, 2)})

    study.optimize(objective, n_trials=n_trials, callbacks=[callback], show_progress_bar=False)
    return history


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=str, default="BTC|1h,BTC|4h,XRP|6h")
    ap.add_argument("--trials", type=int, default=600)
    ap.add_argument("--seeds", type=int, default=3)
    args = ap.parse_args()

    pairs = []
    for p in args.pairs.split(","):
        sym, tf = p.strip().split("|")
        pairs.append((sym, tf))

    all_results = {}

    for sym, tf in pairs:
        symbol_ccxt = f"{sym}/USDT:USDT"
        htf = determine_htf(tf)
        lookback = LOOKBACK_DAYS.get(tf, 365)
        import pandas as pd
        start_date = (pd.Timestamp(END_DATE) - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")

        print(f"\n{sym} ({tf}) — lade Daten {start_date}..{END_DATE} ({lookback} Tage Lookback)...")
        data = load_data(symbol_ccxt, tf, start_date, END_DATE)
        if data.empty or len(data) < 100:
            print("  keine ausreichenden Daten, ueberspringe.")
            continue
        print(f"  {len(data)} Kerzen geladen.")

        key = f"{sym}_{tf}"
        all_results[key] = {"symbol": sym, "timeframe": tf, "n_candles": len(data), "variants": {}}

        for variant_name, include_rr in [("mit_risk_reward_ratio", True), ("ohne_risk_reward_ratio", False)]:
            print(f"  Variante '{variant_name}' — {args.seeds} Seeds x {args.trials} Trials...")
            seed_histories = []
            for seed in range(args.seeds):
                t0 = time.time()
                hist = run_seeded_study(data, symbol_ccxt, tf, htf, include_rr, args.trials, seed)
                elapsed = time.time() - t0
                final_best = hist[-1]["best_so_far"] if hist else None
                print(f"    Seed {seed}: {elapsed:.1f}s, finaler Best-PnL: {final_best}")
                seed_histories.append(hist)
            all_results[key]["variants"][variant_name] = seed_histories

    out_path = os.path.join(os.path.dirname(__file__), "trial_convergence_results.json")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nGespeichert: {out_path}")


if __name__ == "__main__":
    main()
