import sys, os, json, glob, re
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
import pandas as pd
import numpy as np
import ta

from stbot.analysis.backtester import load_data
from stbot.strategy.sr_engine import SREngine

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')

START_DATE = "2026-05-01"
END_DATE = "2026-07-30"

CURRENT_TF = {
    "BTC": "4h", "XRP": "6h", "ADA": "6h", "ETH": "2h",
    "AAVE": "2h", "DOGE": "1d", "SOL": "4h",
}

TF_MINUTES = {'5m': 5, '15m': 15, '30m': 30, '1h': 60, '2h': 120, '4h': 240, '6h': 360, '1d': 1440}


def load_live_trades():
    f = os.path.join(os.path.dirname(__file__),
                      "Exported USDT-M Futures position history 1615043591-2026-07-31 00_56_15.705.xls")
    df = pd.ExcelFile(f).parse("Sheet0", header=0)
    df["sym"] = df["Futures"].str.extract(r"^(\w+?)USDT")
    df["side"] = df["Futures"].apply(lambda s: "buy" if "Long" in s else ("sell" if "Short" in s else None))
    df["Opening time"] = pd.to_datetime(df["Opening time"], utc=True)
    df["Closed time"] = pd.to_datetime(df["Closed time"], utc=True)
    df["Realized PnL"] = df["Realized PnL"].astype(str).str.replace("USDT", "", regex=False).astype(float)
    df["Average entry price"] = df["Average entry price"].astype(float)
    df["Average closing price"] = df["Average closing price"].astype(float)
    df = df[df["sym"].isin(CURRENT_TF.keys())]
    df = df[(df["Opening time"] >= START_DATE) & (df["Opening time"] <= END_DATE + " 23:59:59")]
    return df.sort_values("Opening time").reset_index(drop=True)


def config_path(sym, tf):
    return os.path.join(CONFIG_DIR, f"config_{sym}USDTUSDT_{tf}.json")


def available_tfs(sym):
    files = glob.glob(os.path.join(CONFIG_DIR, f"config_{sym}USDTUSDT_*.json"))
    return [os.path.basename(f).split(f"config_{sym}USDTUSDT_")[1].replace(".json", "") for f in files]


_data_cache = {}

def get_processed(sym, tf):
    key = (sym, tf)
    if key in _data_cache:
        return _data_cache[key]
    cfg = json.load(open(config_path(sym, tf)))
    symbol_ccxt = cfg["market"]["symbol"]
    data = load_data(symbol_ccxt, tf, START_DATE, END_DATE)
    if data.empty or len(data) < 60:
        _data_cache[key] = (None, cfg)
        return None, cfg
    atr_ind = ta.volatility.AverageTrueRange(data['high'], data['low'], data['close'], window=14)
    data['atr'] = atr_ind.average_true_range()
    data = data.dropna(subset=['atr'])
    engine = SREngine(settings=cfg['strategy'])
    processed = engine.process_dataframe(data)
    _data_cache[key] = (processed, cfg)
    return processed, cfg


def signal_check(processed, cfg, tf, entry_time, entry_price, live_side):
    """Returns dict with closed_signal_side, incomplete_signal_side, atr_at_signal, computed SL/TP."""
    if processed is None:
        return None

    tf_dur = pd.Timedelta(minutes=TF_MINUTES[tf])
    # Kerze, die zum entry_time bereits VOLLSTAENDIG geschlossen war
    closed_mask = (processed.index + tf_dur) <= entry_time
    if not closed_mask.any():
        return None
    last_closed_idx = processed.index[closed_mask][-1]
    closed_row = processed.loc[last_closed_idx]

    # Kerze, die zum entry_time noch "lief" (falls vorhanden) -> Bug-Simulation
    partial_mask = (processed.index <= entry_time) & (~closed_mask)
    incomplete_side = None
    incomplete_row = None
    if partial_mask.any():
        partial_idx = processed.index[partial_mask][-1]
        pos = processed.index.get_loc(partial_idx)
        sub = processed.iloc[:pos + 1].copy()
        # Simuliere den Kerzenstand zum Entry-Zeitpunkt: close = live entry_price
        open_p = sub.iloc[-1]['open']
        sub.iloc[-1, sub.columns.get_loc('close')] = entry_price
        sub.iloc[-1, sub.columns.get_loc('high')] = max(open_p, entry_price, sub.iloc[-1]['high'])
        sub.iloc[-1, sub.columns.get_loc('low')] = min(open_p, entry_price, sub.iloc[-1]['low'])
        atr_ind = ta.volatility.AverageTrueRange(sub['high'], sub['low'], sub['close'], window=14)
        sub['atr'] = atr_ind.average_true_range()
        engine = SREngine(settings=cfg['strategy'])
        sub_proc = engine.process_dataframe(sub)
        last = sub_proc.iloc[-1]
        sig = last.get('sr_signal', 0)
        # Volumenfilter wie in trade_logic.py nachbilden
        vol_avg = sub_proc['volume'].tail(20).mean()
        cur_vol = last.get('volume', 0)
        if sig != 0 and cur_vol >= vol_avg * 1.2:
            incomplete_side = "buy" if sig == 1 else "sell"
        incomplete_row = last

    closed_sig = closed_row.get('sr_signal', 0)
    vol_avg_c = processed['volume'].loc[:last_closed_idx].tail(20).mean()
    cur_vol_c = closed_row.get('volume', 0)
    closed_side = None
    if closed_sig != 0 and cur_vol_c >= vol_avg_c * 1.2:
        closed_side = "buy" if closed_sig == 1 else "sell"

    # SL/TP Berechnung wie im Backtester (fix RR) mit ATR der geschlossenen Signal-Kerze
    risk = cfg['risk']
    atr_c = closed_row.get('atr', np.nan)
    sl_dist_fix = max(atr_c * risk.get('atr_multiplier_sl', 2.0), entry_price * risk.get('min_sl_pct', 0.3) / 100.0) if not pd.isna(atr_c) else None

    fixed_sl = fixed_tp = None
    if sl_dist_fix and sl_dist_fix > 0:
        rr = risk.get('risk_reward_ratio', 2.0)
        if live_side == 'buy':
            fixed_sl = entry_price - sl_dist_fix
            fixed_tp = entry_price + sl_dist_fix * rr
        else:
            fixed_sl = entry_price + sl_dist_fix
            fixed_tp = entry_price - sl_dist_fix * rr

    return {
        "closed_time": last_closed_idx,
        "closed_side": closed_side,
        "incomplete_side": incomplete_side,
        "fixed_sl": fixed_sl,
        "fixed_tp": fixed_tp,
        "atr_at_signal": atr_c,
    }


def classify_exit(exit_price, fixed_sl, fixed_tp, side, tol=0.008):
    if fixed_sl is None or fixed_tp is None:
        return "n/a (keine ATR-Daten)"
    def close(a, b):
        return abs(a - b) / b < tol if b else False
    if close(exit_price, fixed_sl):
        return "SL (fix, Backtest-Formel)"
    if close(exit_price, fixed_tp):
        return "TP (fix, Backtest-RR)"
    in_profit = (exit_price > fixed_sl) if side == 'buy' else (exit_price < fixed_sl)
    beyond_tp = (exit_price > fixed_tp) if side == 'buy' else (exit_price < fixed_tp)
    if beyond_tp:
        return "> fix-TP (adaptive RR / Trailing lief weiter)"
    if in_profit:
        return "Trailing-Stop (Gewinn zwischen SL/TP gesichert)"
    return "SL ueberschritten (Slippage?)"


def main():
    live = load_live_trades()
    print(f"{len(live)} Live-Trades im Zeitraum {START_DATE} .. {END_DATE} fuer stbot-Symbole gefunden.\n")

    summary_rows = []

    for sym in CURRENT_TF:
        sub = live[live["sym"] == sym]
        if sub.empty:
            continue
        cur_tf = CURRENT_TF[sym]
        print(f"\n{'='*100}\n{sym} — aktuelle Config-TF: {cur_tf} ({len(sub)} Live-Trades)\n{'='*100}")

        processed_cur, cfg_cur = get_processed(sym, cur_tf)

        for _, tr in sub.iterrows():
            entry_time = tr["Opening time"]
            exit_time = tr["Closed time"]
            entry_price = tr["Average entry price"]
            exit_price = tr["Average closing price"]
            side = tr["side"]
            pnl = tr["Realized PnL"]

            result_tf = cur_tf
            chk = signal_check(processed_cur, cfg_cur, cur_tf, entry_time, entry_price, side)
            match_type = None
            if chk:
                if chk["closed_side"] == side:
                    match_type = "CLOSED-Kerze stimmt ueberein (sauber erklaerbar)"
                elif chk["incomplete_side"] == side:
                    match_type = "NUR laufende/unfertige Kerze stimmt (Bug-Hypothese)"

            # Falls aktueller TF nicht passt: andere TFs des Symbols testen
            if not match_type:
                for alt_tf in available_tfs(sym):
                    if alt_tf == cur_tf:
                        continue
                    processed_alt, cfg_alt = get_processed(sym, alt_tf)
                    chk_alt = signal_check(processed_alt, cfg_alt, alt_tf, entry_time, entry_price, side)
                    if chk_alt:
                        if chk_alt["closed_side"] == side:
                            chk = chk_alt
                            result_tf = alt_tf
                            match_type = f"CLOSED-Kerze auf ANDEREM TF ({alt_tf}) stimmt"
                            break
                        elif chk_alt["incomplete_side"] == side and not match_type:
                            chk = chk_alt
                            result_tf = alt_tf
                            match_type = f"NUR laufende Kerze auf ANDEREM TF ({alt_tf}) stimmt"

            if not match_type:
                match_type = "KEIN Signal-Match auf irgendeinem bekannten TF"

            exit_class = classify_exit(exit_price, chk["fixed_sl"], chk["fixed_tp"], side) if chk else "n/a"

            print(f"\n[{entry_time}] {side.upper():4s} @ {entry_price:<12} -> Exit [{exit_time}] @ {exit_price:<12} | PnL {pnl:+.3f} USDT")
            print(f"   Signal-Check: {match_type}")
            if chk:
                print(f"   Referenz-TF: {result_tf} | Fix-SL: {chk['fixed_sl']} | Fix-TP: {chk['fixed_tp']} | Exit-Klassifikation: {exit_class}")

            summary_rows.append({
                "symbol": sym, "entry_time": entry_time, "side": side, "pnl": pnl,
                "match_type": match_type, "ref_tf": result_tf, "exit_class": exit_class,
            })

    sdf = pd.DataFrame(summary_rows)
    print(f"\n\n{'='*100}\nZUSAMMENFASSUNG\n{'='*100}")
    print(sdf["match_type"].apply(lambda x: x.split(" (")[0].split(" auf")[0]).value_counts())
    print()
    print(sdf["exit_class"].value_counts())
    sdf.to_csv(os.path.join(os.path.dirname(__file__), "trade_forensics_summary.csv"), index=False)
    print("\nDetails gespeichert in daten/trade_forensics_summary.csv")


if __name__ == "__main__":
    main()
