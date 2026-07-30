"""
Wie trial_convergence_study.py, aber:
  1) strict-Modus-Constraints (MaxDD<30%, min_win_rate>=35%, min_pnl>=0, min_trades>=20)
     statt des unbeschraenkten best_profit-Modus (der Ausreisser statt Robustheit belohnt hatte).
  2) Echte Multiprocessing-Parallelisierung ueber (Paar, Variante, Seed)-Kombinationen statt
     sequentiell auf einem Kern (Optunas n_jobs ist Thread-basiert und hilft bei diesem CPU-
     gebundenen reinen Python-Loop wegen des GIL kaum).

Aufruf:
    python daten/trial_convergence_study_strict.py --pairs "BTC|1h,BTC|4h,XRP|6h" \
        --trials 250 --seeds 3 --workers 10 --min_win_rate 35
"""
import sys, os, json, time, argparse
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
from concurrent.futures import ProcessPoolExecutor, as_completed

import optuna
import pandas as pd

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


def run_one(task):
    """Laeuft in einem eigenen Prozess: laedt Daten (aus Cache) und optimiert einen Seed komplett."""
    sym, tf, include_rr, seed, n_trials, min_win_rate = task
    symbol_ccxt = f"{sym}/USDT:USDT"
    htf = determine_htf(tf)
    lookback = LOOKBACK_DAYS.get(tf, 365)
    start_date = (pd.Timestamp(END_DATE) - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")

    data = load_data(symbol_ccxt, tf, start_date, END_DATE)
    if data.empty or len(data) < 100:
        return {"sym": sym, "tf": tf, "include_rr": include_rr, "seed": seed, "history": [], "n_candles": 0}

    def objective(trial):
        strategy_params = {
            'pivot_period':      trial.suggest_int('pivot_period', 5, 30),
            'max_pivots':        trial.suggest_int('max_pivots', 10, 60),
            'channel_width_pct': trial.suggest_int('channel_width_pct', 5, 25),
            'max_sr_levels':     5,
            'min_strength':      trial.suggest_int('min_strength', 1, 4),
            'source':            trial.suggest_categorical('source', ['High/Low', 'Close/Open']),
            'symbol': symbol_ccxt, 'timeframe': tf, 'htf': htf,
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
            risk_params['risk_reward_ratio'] = 2.0

        result = run_backtest(data.copy(), strategy_params, risk_params, START_CAPITAL, verbose=False)
        pnl     = result.get('total_pnl_pct', -1000)
        dd      = result.get('max_drawdown_pct', 1.0)
        trades  = result.get('trades_count', 0)
        wr      = result.get('win_rate', 0)

        if dd > MAX_DD_CONSTRAINT or wr < min_win_rate or pnl < 0 or trades < MIN_TRADES:
            raise optuna.exceptions.TrialPruned()
        return pnl

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)

    history = []
    best = None
    t0 = time.time()

    def callback(study, trial):
        nonlocal best
        if trial.value is not None:
            best = trial.value if best is None else max(best, trial.value)
        history.append({"trial": trial.number, "best_so_far": best})

    study.optimize(objective, n_trials=n_trials, callbacks=[callback], show_progress_bar=False)
    elapsed = time.time() - t0

    return {"sym": sym, "tf": tf, "include_rr": include_rr, "seed": seed,
            "history": history, "elapsed_s": round(elapsed, 1), "n_candles": len(data)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=str, default="BTC|1h,BTC|4h,XRP|6h")
    ap.add_argument("--trials", type=int, default=250)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 4))
    ap.add_argument("--min_win_rate", type=float, default=35.0)
    args = ap.parse_args()

    pairs = [tuple(p.strip().split("|")) for p in args.pairs.split(",")]

    tasks = []
    for sym, tf in pairs:
        for include_rr in (True, False):
            for seed in range(args.seeds):
                tasks.append((sym, tf, include_rr, seed, args.trials, args.min_win_rate))

    print(f"{len(tasks)} Aufgaben auf {args.workers} Worker-Prozessen (von {os.cpu_count()} logischen Kernen)...")
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(run_one, t): t for t in tasks}
        done = 0
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            final = r["history"][-1]["best_so_far"] if r["history"] else None
            print(f"  [{done}/{len(tasks)}] {r['sym']} {r['tf']} rr={r['include_rr']} seed={r['seed']} "
                  f"-> {r.get('elapsed_s','?')}s, final best={final}")

    print(f"\nGesamtzeit: {time.time()-t0:.1f}s (vorher sequentiell: ~3300s)")

    # In die gleiche Struktur wie beim best_profit-Lauf umbauen
    out = {}
    for r in results:
        key = f"{r['sym']}_{r['tf']}"
        if key not in out:
            out[key] = {"symbol": r["sym"], "timeframe": r["tf"], "n_candles": r["n_candles"], "variants": {}}
        vname = "mit_risk_reward_ratio" if r["include_rr"] else "ohne_risk_reward_ratio"
        out[key]["variants"].setdefault(vname, []).append(
            [{"trial": h["trial"], "best_so_far": h["best_so_far"], "elapsed_s": r.get("elapsed_s")} for h in r["history"]]
        )

    out_path = os.path.join(os.path.dirname(__file__), "trial_convergence_results_strict.json")
    with open(out_path, "w") as f:
        json.dump(out, f)
    print(f"Gespeichert: {out_path}")


if __name__ == "__main__":
    main()
