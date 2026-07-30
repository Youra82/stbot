import sys, os, json, glob, re
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
import pandas as pd
import ta

from stbot.analysis.backtester import load_data, run_backtest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')

START_DATE = "2026-05-01"
END_DATE = "2026-07-30"
START_CAPITAL = 100


def main():
    files = sorted(glob.glob(os.path.join(CONFIG_DIR, "config_*.json")))
    results = []

    for f in files:
        base = os.path.basename(f).replace("config_", "").replace(".json", "")
        m = re.match(r"([A-Z]+)USDTUSDT_(.+)", base)
        if not m:
            continue
        sym, tf = m.group(1), m.group(2)
        cfg = json.load(open(f))
        symbol_ccxt = cfg["market"]["symbol"]

        print(f"{sym} ({tf}) ...", end=" ")
        data = load_data(symbol_ccxt, tf, START_DATE, END_DATE)
        if data.empty or len(data) < 60:
            print("keine Daten")
            results.append({"symbol": sym, "timeframe": tf, "trades": 0, "win_rate": None,
                             "pnl_pct": None, "max_dd_pct": None, "note": "keine Daten"})
            continue

        strategy_params = dict(cfg["strategy"])
        strategy_params["symbol"] = symbol_ccxt
        strategy_params["timeframe"] = tf
        strategy_params["htf"] = cfg["market"].get("htf")
        risk_params = dict(cfg["risk"])

        res = run_backtest(data.copy(), strategy_params, risk_params, start_capital=START_CAPITAL)
        print(f"{res['trades_count']} Trades, WR {res['win_rate']:.1f}%, PnL {res['total_pnl_pct']:.1f}%")

        results.append({
            "symbol": sym, "timeframe": tf,
            "trades": res["trades_count"],
            "win_rate": round(res["win_rate"], 1),
            "pnl_pct": round(res["total_pnl_pct"], 1),
            "max_dd_pct": round(res["max_drawdown_pct"] * 100, 1),
            "note": "",
        })

    out_path = os.path.join(os.path.dirname(__file__), "all_configs_evaluation.json")
    with open(out_path, "w") as fp:
        json.dump({
            "start_date": START_DATE, "end_date": END_DATE, "start_capital": START_CAPITAL,
            "results": results,
        }, fp, indent=2)
    print(f"\nGespeichert: {out_path}")


if __name__ == "__main__":
    main()
