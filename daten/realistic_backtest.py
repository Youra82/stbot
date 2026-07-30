import sys, os, json
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
import pandas as pd
import numpy as np
import ta

from stbot.analysis.backtester import load_data
from stbot.strategy.sr_engine import SREngine
from stbot.strategy.trade_logic import get_titan_signal

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')

START_DATE = "2026-05-01"
END_DATE = "2026-07-30"

CURRENT_TF = {
    "BTC": "4h", "XRP": "6h", "ADA": "6h", "ETH": "2h",
    "AAVE": "2h", "DOGE": "1d", "SOL": "4h",
}


class Bias:
    NEUTRAL = "NEUTRAL"


def simulate_symbol(sym, tf, cfg, start_capital=100):
    symbol_ccxt = cfg["market"]["symbol"]

    data = load_data(symbol_ccxt, tf, START_DATE, END_DATE)
    if data.empty or len(data) < 60:
        return None
    atr_ind = ta.volatility.AverageTrueRange(data['high'], data['low'], data['close'], window=14)
    data['atr'] = atr_ind.average_true_range()
    data = data.dropna(subset=['atr'])
    engine = SREngine(settings=cfg['strategy'])
    processed = engine.process_dataframe(data)

    print(f"  {sym}: lade 1m-Referenzdaten fuer realen Trailing-Stop-Verlauf ...")
    fine = load_data(symbol_ccxt, '1m', START_DATE, END_DATE)
    if fine.empty:
        print(f"  {sym}: keine 1m-Daten verfuegbar, ueberspringe.")
        return None
    f_idx = fine.index.values
    f_open = fine['open'].values
    f_high = fine['high'].values
    f_low = fine['low'].values
    f_close = fine['close'].values

    risk = cfg['risk']
    risk_per_trade_pct = risk.get('risk_per_trade_pct', 1.0) / 100
    leverage = risk.get('leverage', 10)
    fee_pct = 0.06 / 100
    atr_multiplier_sl = risk.get('atr_multiplier_sl', 2.0)
    min_sl_pct = risk.get('min_sl_pct', 0.3) / 100.0
    act_rr = risk.get('trailing_stop_activation_rr', 1.5)
    callback_rate = risk.get('trailing_stop_callback_rate_pct', 0.5) / 100.0

    params_for_logic = {"strategy": cfg['strategy'], "risk": risk}

    current_capital = start_capital
    blocked_until = None
    trades = []

    rows = list(processed.iterrows())
    for timestamp, current_candle in rows:
        if current_capital <= 0:
            break
        if blocked_until is not None and timestamp <= blocked_until:
            continue

        side, price = get_titan_signal(processed, current_candle, params_for_logic, Bias.NEUTRAL)
        if not side:
            continue

        entry_price = current_candle['close']
        current_atr = current_candle.get('atr', 0)
        if current_atr <= 0:
            continue
        sl_dist = max(current_atr * atr_multiplier_sl, entry_price * min_sl_pct)
        risk_amount_usd = current_capital * risk_per_trade_pct
        sl_pct = sl_dist / entry_price
        if sl_pct <= 0:
            continue
        calc_notional = risk_amount_usd / sl_pct
        final_notional = min(calc_notional, current_capital * 10, 1000000)
        margin_needed = final_notional / leverage
        if margin_needed > current_capital:
            continue

        pos_side = 'long' if side == 'buy' else 'short'
        if pos_side == 'long':
            sl = entry_price - sl_dist
            act_price = entry_price + sl_dist * act_rr
        else:
            sl = entry_price + sl_dist
            act_price = entry_price - sl_dist * act_rr

        # --- 1m-Simulation ab Entry-Zeitpunkt: NUR harter SL + echter Trailing-Stop (kein reales TP-Order-Objekt) ---
        start_pos = np.searchsorted(f_idx, np.datetime64(timestamp.tz_convert('UTC').tz_localize(None)))
        exit_price = None
        exit_ts = None
        exit_reason = None
        activated = False
        peak = entry_price

        n = len(f_idx)
        i = start_pos
        while i < n:
            hi, lo = f_high[i], f_low[i]
            if pos_side == 'long':
                if lo <= sl:
                    exit_price, exit_reason = sl, "SL"
                    exit_ts = f_idx[i]
                    break
                if not activated and hi >= act_price:
                    activated = True
                    peak = hi
                if activated:
                    peak = max(peak, hi)
                    trail_level = peak * (1 - callback_rate)
                    if lo <= trail_level:
                        exit_price, exit_reason = trail_level, "TRAILING"
                        exit_ts = f_idx[i]
                        break
            else:
                if hi >= sl:
                    exit_price, exit_reason = sl, "SL"
                    exit_ts = f_idx[i]
                    break
                if not activated and lo <= act_price:
                    activated = True
                    peak = lo
                if activated:
                    peak = min(peak, lo)
                    trail_level = peak * (1 + callback_rate)
                    if hi >= trail_level:
                        exit_price, exit_reason = trail_level, "TRAILING"
                        exit_ts = f_idx[i]
                        break
            i += 1

        if exit_price is None:
            # Kein Exit innerhalb der verfuegbaren 1m-Historie (Positionsende ausserhalb Datenfenster)
            exit_price = f_close[-1] if n > 0 else entry_price
            exit_ts = f_idx[-1] if n > 0 else timestamp
            exit_reason = "HORIZON_END"

        pnl_pct = (exit_price / entry_price - 1) if pos_side == 'long' else (1 - exit_price / entry_price)
        pnl_usd = final_notional * pnl_pct
        total_fees = final_notional * fee_pct * 2
        net_pnl = pnl_usd - total_fees
        current_capital += net_pnl

        exit_ts_final = pd.Timestamp(exit_ts, tz='UTC') if pd.Timestamp(exit_ts).tzinfo is None else pd.Timestamp(exit_ts)
        trades.append({
            "entry_ts": timestamp, "exit_ts": exit_ts_final, "side": pos_side,
            "entry_price": entry_price, "sl": sl, "act_price": act_price,
            "exit_price": exit_price, "reason": exit_reason, "net_pnl": net_pnl,
            "activated_trailing": activated,
        })
        blocked_until = exit_ts_final

    return {"trades": trades, "final_capital": current_capital, "start_capital": start_capital}


def main():
    all_results = {}
    for sym, tf in CURRENT_TF.items():
        cfg = json.load(open(os.path.join(CONFIG_DIR, f"config_{sym}USDTUSDT_{tf}.json")))
        print(f"\n{sym} ({tf}) — realistischer Backtest (echter SL + echter Trailing-Stop via 1m-Daten) ...")
        result = simulate_symbol(sym, tf, cfg)
        if result is None:
            continue
        all_results[sym] = result
        n = len(result["trades"])
        wins = sum(1 for t in result["trades"] if t["net_pnl"] > 0)
        pnl_pct = (result["final_capital"] - result["start_capital"]) / result["start_capital"] * 100
        trailing_hits = sum(1 for t in result["trades"] if t["reason"] == "TRAILING")
        sl_hits = sum(1 for t in result["trades"] if t["reason"] == "SL")
        print(f"  {n} Trades | Winrate {wins/n*100:.1f}% | PnL {pnl_pct:+.1f}% | SL-Exits: {sl_hits} | Trailing-Exits: {trailing_hits}")
        for t in result["trades"]:
            print(f"    [{t['entry_ts']}] {t['side']:5s} @ {t['entry_price']:<12.6g} -> [{t['exit_ts']}] @ {t['exit_price']:<12.6g} "
                  f"| {t['reason']:10s} | PnL {t['net_pnl']:+.3f} USDT | TSL-aktiviert: {t['activated_trailing']}")

    print(f"\n\n{'='*100}\nZUSAMMENFASSUNG — Realistischer Backtest (echter SL/Trailing) vs. urspruenglicher Backtest (statisches Fix-TP)\n{'='*100}")
    rows = []
    for sym, result in all_results.items():
        n = len(result["trades"])
        wins = sum(1 for t in result["trades"] if t["net_pnl"] > 0)
        pnl_pct = (result["final_capital"] - result["start_capital"]) / result["start_capital"] * 100
        rows.append({
            "symbol": sym, "trades": n,
            "winrate_realistic": round(wins / n * 100, 1) if n else 0,
            "pnl_pct_realistic": round(pnl_pct, 1),
        })
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    df.to_csv(os.path.join(os.path.dirname(__file__), "realistic_backtest_summary.csv"), index=False)


if __name__ == "__main__":
    main()
