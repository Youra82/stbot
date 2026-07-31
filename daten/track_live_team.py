"""
Vergleicht Live-Trades gegen die Backtest-Erwartung — auf Basis des GESAMTEN
Config-Pools (alle config_*.json in strategy/configs/), nicht nur der aktuell
in settings.json aktiven 7 Strategien. Grund: active_strategies rotiert
woechentlich - wer nur das aktuelle Team filtert, verliert Live-Trades von
Symbolen/Timeframes, die inzwischen wieder aus dem Team rausgefallen sind.

Nutzung:
  1. Frischen Bitget-Export ("Exported USDT-M Futures position history ...xls")
     in daten/ ablegen (aeltere Exports werden ignoriert/ueberschrieben erkannt).
  2. python daten/track_live_team.py
     Optional: --since 2026-07-31  (Default: Datum des letzten Config-Resets)
     Optional: --xls "Pfad/zur/Datei.xls"  (Default: neuester Export im daten/-Ordner)

Hinweis: Live-Trades lassen sich von diesem Rechner aus nicht per API ziehen
(Bitget-Key ist vermutlich auf die VPS-IP beschraenkt) -> Excel-Export noetig.
"""
import sys, os, json, glob, re, argparse
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
import pandas as pd

from stbot.analysis.backtester import load_data, run_backtest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')


def get_full_pool():
    """Alle (Symbol, Timeframe)-Paare, fuer die aktuell eine Config existiert."""
    files = sorted(glob.glob(os.path.join(CONFIG_DIR, "config_*.json")))
    pool = []
    for f in files:
        base = os.path.basename(f).replace("config_", "").replace(".json", "")
        m = re.match(r"([A-Z]+)USDTUSDT_(.+)", base)
        if m:
            pool.append((m.group(1), m.group(2)))
    return pool


def find_latest_xls():
    files = glob.glob(os.path.join(os.path.dirname(__file__), "Exported USDT-M Futures position history *.xls"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def load_live_trades(xls_path, team_symbols, since):
    df = pd.ExcelFile(xls_path).parse("Sheet0", header=0)
    df["sym"] = df["Futures"].str.extract(r"^(\w+?)USDT")
    df["side"] = df["Futures"].apply(lambda s: "buy" if "Long" in s else ("sell" if "Short" in s else None))
    df["Opening time"] = pd.to_datetime(df["Opening time"], utc=True)
    df["Closed time"] = pd.to_datetime(df["Closed time"], utc=True)
    df["Realized PnL"] = df["Realized PnL"].astype(str).str.replace("USDT", "", regex=False).astype(float)
    df = df[df["sym"].isin(team_symbols)]
    df = df[df["Opening time"] >= since]
    return df.sort_values("Opening time").reset_index(drop=True)


def backtest_since(sym, tf, since_str, end_str, start_capital=100):
    cfg_path = os.path.join(CONFIG_DIR, f"config_{sym}USDTUSDT_{tf}.json")
    if not os.path.exists(cfg_path):
        return None
    cfg = json.load(open(cfg_path))
    symbol_ccxt = cfg["market"]["symbol"]
    data = load_data(symbol_ccxt, tf, since_str, end_str)
    if data.empty or len(data) < 60:
        return {"trades_count": 0, "win_rate": 0, "total_pnl_pct": 0, "note": "zu wenig Historie seit Aktivierung"}
    strategy_params = dict(cfg["strategy"])
    strategy_params["symbol"] = symbol_ccxt
    strategy_params["timeframe"] = tf
    strategy_params["htf"] = cfg["market"].get("htf")
    risk_params = dict(cfg["risk"])
    return run_backtest(data.copy(), strategy_params, risk_params, start_capital=start_capital)


def get_active_set():
    """Nur zur Anzeige/Markierung - NICHT zum Filtern des Pools."""
    try:
        settings = json.load(open(os.path.join(PROJECT_ROOT, 'settings.json')))
        strategies = settings['live_trading_settings']['active_strategies']
        return {(s['symbol'].split('/')[0], s['timeframe']) for s in strategies if s.get('active', True)}
    except Exception:
        return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=str, default="2026-07-31", help="Datum, ab dem verglichen wird")
    ap.add_argument("--xls", type=str, default=None)
    args = ap.parse_args()

    pool = get_full_pool()
    active_now = get_active_set()
    print(f"Gesamtpool: {len(pool)} Symbol/Timeframe-Configs (davon {len(active_now)} aktuell aktiv)")

    xls_path = args.xls or find_latest_xls()
    if not xls_path:
        print("\nKein Bitget-Export im daten/-Ordner gefunden.")
        print("Bitte 'Exported USDT-M Futures position history ...xls' dort ablegen und erneut ausfuehren.")
        return
    print(f"Nutze Export: {os.path.basename(xls_path)} (Stand: {pd.Timestamp(os.path.getmtime(xls_path), unit='s')})")

    pool_symbols = sorted(set(s for s, tf in pool))
    live = load_live_trades(xls_path, pool_symbols, args.since)
    print(f"\n{len(live)} Live-Trades seit {args.since} fuer Symbole aus dem Gesamtpool gefunden.\n")

    end_str = pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d')
    rows = []
    for sym, tf in pool:
        sub = live[live["sym"] == sym]
        n_live = len(sub)
        wins_live = (sub["Realized PnL"] > 0).sum() if n_live else 0
        pnl_live = sub["Realized PnL"].sum() if n_live else 0.0

        bt = backtest_since(sym, tf, args.since, end_str)

        rows.append({
            "symbol": sym, "tf": tf,
            "aktiv": (sym, tf) in active_now,
            "live_trades": n_live,
            "live_winrate": round(wins_live / n_live * 100, 1) if n_live else None,
            "live_pnl_usdt": round(pnl_live, 2),
            "bt_trades": bt["trades_count"] if bt else None,
            "bt_winrate": round(bt["win_rate"], 1) if bt and bt.get("trades_count") else None,
            "bt_pnl_pct": round(bt["total_pnl_pct"], 1) if bt and bt.get("trades_count") else None,
        })

    out = pd.DataFrame(rows)
    # Symbole mit Live-Trades zuerst, dann Rest
    out = out.sort_values(["live_trades", "aktiv"], ascending=[False, False])
    print(out.to_string(index=False))

    total_live_trades = out["live_trades"].sum()
    if total_live_trades < 15:
        print(f"\nHinweis: Nur {total_live_trades} Live-Trades insgesamt seit {args.since} — "
              f"fuer eine belastbare Aussage sind das noch zu wenige. Einfach dieses Skript "
              f"in ein paar Wochen mit einem frischen Export erneut laufen lassen.")


if __name__ == "__main__":
    main()
