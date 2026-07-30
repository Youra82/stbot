import sys, os, json
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
import pandas as pd

from stbot.analysis.backtester import load_data, run_backtest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')

START_DATE = "2026-05-01"
END_DATE = "2026-07-30"

# Symbol -> aktuelle Live-Timeframe laut settings.json active_strategies
# SOL ist aktuell NICHT aktiv, wird aber zum Vergleich mit seiner Config mitgenommen (Timeframe unbekannt -> 4h als Referenz)
SYMBOL_TF = {
    "BTC": "4h",
    "XRP": "6h",
    "ADA": "6h",
    "ETH": "2h",
    "AAVE": "2h",
    "DOGE": "1d",
    "SOL": "4h",
}

def load_live_trades():
    f = os.path.join(os.path.dirname(__file__), "Exported USDT-M Futures position history 1615043591-2026-07-31 00_56_15.705.xls")
    df = pd.ExcelFile(f).parse("Sheet0", header=0)
    df["sym"] = df["Futures"].str.extract(r"^(\w+?)USDT")
    df["Opening time"] = pd.to_datetime(df["Opening time"])
    df["Closed time"] = pd.to_datetime(df["Closed time"])
    df = df[df["sym"].isin(SYMBOL_TF.keys())]
    df = df[(df["Opening time"] >= START_DATE) & (df["Opening time"] <= END_DATE + " 23:59:59")]
    df["Realized PnL"] = df["Realized PnL"].astype(str).str.replace("USDT", "", regex=False).astype(float)
    return df

def live_stats(df, sym):
    sub = df[df["sym"] == sym]
    n = len(sub)
    if n == 0:
        return {"trades": 0, "win_rate": 0, "realized_pnl": 0.0, "avg_pnl": 0.0}
    wins = (sub["Realized PnL"] > 0).sum()
    return {
        "trades": n,
        "win_rate": wins / n * 100,
        "realized_pnl": sub["Realized PnL"].sum(),
        "avg_pnl": sub["Realized PnL"].mean(),
    }

def backtest_stats(sym, tf):
    cfg_path = os.path.join(CONFIG_DIR, f"config_{sym}USDTUSDT_{tf}.json")
    if not os.path.exists(cfg_path):
        return None
    cfg = json.load(open(cfg_path))
    symbol_ccxt = cfg["market"]["symbol"]
    timeframe = cfg["market"]["timeframe"]

    data = load_data(symbol_ccxt, timeframe, START_DATE, END_DATE)
    if data.empty:
        return {"trades_count": 0, "win_rate": 0, "total_pnl_pct": 0, "max_drawdown_pct": 0, "note": "keine Daten"}

    strategy_params = dict(cfg["strategy"])
    strategy_params["symbol"] = symbol_ccxt
    strategy_params["timeframe"] = timeframe
    strategy_params["htf"] = cfg["market"].get("htf")

    risk_params = dict(cfg["risk"])

    result = run_backtest(data, strategy_params, risk_params, start_capital=100)
    return result

def main():
    live_df = load_live_trades()
    rows = []
    for sym, tf in SYMBOL_TF.items():
        ls = live_stats(live_df, sym)
        bt = backtest_stats(sym, tf)
        rows.append({
            "symbol": sym,
            "tf": tf,
            "live_trades": ls["trades"],
            "live_winrate": round(ls["win_rate"], 1),
            "live_pnl_usdt": round(ls["realized_pnl"], 2),
            "live_avg_pnl": round(ls["avg_pnl"], 3),
            "bt_trades": bt["trades_count"] if bt else None,
            "bt_winrate": round(bt["win_rate"], 1) if bt else None,
            "bt_pnl_pct": round(bt["total_pnl_pct"], 1) if bt else None,
            "bt_maxdd_pct": round(bt["max_drawdown_pct"] * 100, 1) if bt else None,
        })
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))

    total_live_trades = out["live_trades"].sum()
    total_live_pnl = out["live_pnl_usdt"].sum()
    total_live_wins = sum(
        (live_df[live_df["sym"] == s]["Realized PnL"] > 0).sum() for s in SYMBOL_TF
    )
    overall_live_wr = total_live_wins / total_live_trades * 100 if total_live_trades else 0
    print(f"\nGESAMT LIVE: {total_live_trades} Trades, Winrate {overall_live_wr:.1f}%, PnL {total_live_pnl:.2f} USDT")

if __name__ == "__main__":
    main()
