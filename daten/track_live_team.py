"""
Vergleicht Live-Trades des AKTUELL in settings.json aktiven Teams gegen die
Backtest-Erwartung der jeweiligen Config, ab dem Datum, seit dem das Team aktiv ist.

Nutzung:
  1. Frischen Bitget-Export ("Exported USDT-M Futures position history ...xls")
     in daten/ ablegen (aeltere Exports werden ignoriert/ueberschrieben erkannt).
  2. python daten/track_live_team.py
     Optional: --since 2026-07-31  (Default: automatisch aus dem aeltesten
     'aktiv seit'-Zeitpunkt, hier vereinfacht als CLI-Override, da settings.json
     kein Aktivierungsdatum je Strategie speichert)
     Optional: --xls "Pfad/zur/Datei.xls"  (Default: neuester Export im daten/-Ordner)

Hinweis: Live-Trades lassen sich von diesem Rechner aus nicht per API ziehen
(Bitget-Key ist vermutlich auf die VPS-IP beschraenkt) -> Excel-Export noetig.
"""
import sys, os, json, glob, argparse
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
import pandas as pd

from stbot.analysis.backtester import load_data, run_backtest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')
SETTINGS_PATH = os.path.join(PROJECT_ROOT, 'settings.json')


def get_active_team():
    settings = json.load(open(SETTINGS_PATH))
    strategies = settings['live_trading_settings']['active_strategies']
    team = []
    for s in strategies:
        if not s.get('active', True):
            continue
        sym = s['symbol'].split('/')[0]
        tf = s['timeframe']
        team.append((sym, tf))
    return team


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=str, default="2026-07-31", help="Datum, seit dem das aktuelle Team aktiv ist")
    ap.add_argument("--xls", type=str, default=None)
    args = ap.parse_args()

    team = get_active_team()
    print(f"Aktuelles Team (aus settings.json): {team}")

    xls_path = args.xls or find_latest_xls()
    if not xls_path:
        print("\nKein Bitget-Export im daten/-Ordner gefunden.")
        print("Bitte 'Exported USDT-M Futures position history ...xls' dort ablegen und erneut ausfuehren.")
        return
    print(f"Nutze Export: {os.path.basename(xls_path)} (Stand: {pd.Timestamp(os.path.getmtime(xls_path), unit='s')})")

    team_symbols = [s for s, tf in team]
    live = load_live_trades(xls_path, team_symbols, args.since)
    print(f"\n{len(live)} Live-Trades seit {args.since} fuer das aktuelle Team gefunden.\n")

    end_str = pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d')
    rows = []
    for sym, tf in team:
        sub = live[live["sym"] == sym]
        n_live = len(sub)
        wins_live = (sub["Realized PnL"] > 0).sum() if n_live else 0
        pnl_live = sub["Realized PnL"].sum() if n_live else 0.0

        bt = backtest_since(sym, tf, args.since, end_str)

        rows.append({
            "symbol": sym, "tf": tf,
            "live_trades": n_live,
            "live_winrate": round(wins_live / n_live * 100, 1) if n_live else None,
            "live_pnl_usdt": round(pnl_live, 2),
            "bt_trades": bt["trades_count"] if bt else None,
            "bt_winrate": round(bt["win_rate"], 1) if bt and bt.get("trades_count") else None,
            "bt_pnl_pct": round(bt["total_pnl_pct"], 1) if bt and bt.get("trades_count") else None,
        })

    out = pd.DataFrame(rows)
    print(out.to_string(index=False))

    total_live_trades = out["live_trades"].sum()
    if total_live_trades < 15:
        print(f"\nHinweis: Nur {total_live_trades} Live-Trades insgesamt seit {args.since} — "
              f"fuer eine belastbare Aussage sind das noch zu wenige. Einfach dieses Skript "
              f"in ein paar Wochen mit einem frischen Export erneut laufen lassen.")


if __name__ == "__main__":
    main()
