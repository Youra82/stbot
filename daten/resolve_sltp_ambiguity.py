import sys, os, json
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
import pandas as pd
import numpy as np
import ta

from stbot.analysis.backtester import load_data
from stbot.strategy.sr_engine import SREngine
from stbot.strategy.trade_logic import get_titan_signal
from stbot.utils.exchange import Exchange

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')

START_DATE = "2026-05-01"
END_DATE = "2026-07-30"

CURRENT_TF = {
    "BTC": "4h", "XRP": "6h", "ADA": "6h", "ETH": "2h",
    "AAVE": "2h", "DOGE": "1d", "SOL": "4h",
}

TF_MINUTES = {'5m': 5, '15m': 15, '30m': 30, '1h': 60, '2h': 120, '4h': 240, '6h': 360, '1d': 1440}

_exchange = None
def get_exchange():
    global _exchange
    if _exchange is None:
        secrets = json.load(open(os.path.join(PROJECT_ROOT, 'secret.json')))
        _exchange = Exchange(secrets['stbot'][0])
    return _exchange


class Bias:
    NEUTRAL = "NEUTRAL"


def resolve_order_1m(symbol_ccxt, candle_start, candle_end, sl, tp, side):
    """Holt 1m-Daten fuer das Fenster der Kerze und bestimmt, was zuerst beruehrt wurde."""
    ex = get_exchange()
    start_str = candle_start.strftime('%Y-%m-%d')
    end_str = (candle_end + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    fine = ex.fetch_historical_ohlcv(symbol_ccxt, '1m', start_str, end_str)
    if fine.empty:
        return None
    fine = fine.loc[(fine.index >= candle_start) & (fine.index < candle_end)]
    if fine.empty:
        return None

    for ts, row in fine.iterrows():
        if side == 'long':
            hit_sl = row['low'] <= sl
            hit_tp = row['high'] >= tp
        else:
            hit_sl = row['high'] >= sl
            hit_tp = row['low'] <= tp
        if hit_sl and hit_tp:
            # Innerhalb derselben 1m-Kerze -> nutze Open/Close Richtung als Tie-Breaker
            return 'sl' if abs(row['open'] - sl) < abs(row['open'] - tp) else 'tp'
        if hit_sl:
            return 'sl'
        if hit_tp:
            return 'tp'
    return None  # keiner der beiden Level in den 1m-Daten getroffen (Rand-Ungenauigkeit)


def run_instrumented_backtest(symbol_ccxt, timeframe, cfg, start_capital=100):
    data = load_data(symbol_ccxt, timeframe, START_DATE, END_DATE)
    if data.empty or len(data) < 60:
        return None

    atr_ind = ta.volatility.AverageTrueRange(data['high'], data['low'], data['close'], window=14)
    data['atr'] = atr_ind.average_true_range()
    data = data.dropna(subset=['atr'])

    engine = SREngine(settings=cfg['strategy'])
    processed = engine.process_dataframe(data)

    risk = cfg['risk']
    rr = risk.get('risk_reward_ratio', 2.0)
    risk_per_trade_pct = risk.get('risk_per_trade_pct', 1.0) / 100
    leverage = risk.get('leverage', 10)
    fee_pct = 0.06 / 100
    atr_multiplier_sl = risk.get('atr_multiplier_sl', 2.0)
    min_sl_pct = risk.get('min_sl_pct', 0.3) / 100.0
    tf_dur = pd.Timedelta(minutes=TF_MINUTES[timeframe])

    params_for_logic = {"strategy": cfg['strategy'], "risk": risk}

    current_capital = start_capital
    position = None
    trades = []  # (entry_ts, exit_ts, side, entry_price, sl, tp, exit_reason_original)
    ambiguous_candles = []

    rows = list(processed.iterrows())
    for i, (timestamp, current_candle) in enumerate(rows):
        if current_capital <= 0:
            break

        if position:
            low, high = current_candle['low'], current_candle['high']
            hit_sl = (low <= position['stop_loss']) if position['side'] == 'long' else (high >= position['stop_loss'])
            hit_tp = (high >= position['take_profit']) if position['side'] == 'long' else (low <= position['take_profit'])

            exit_price = None
            reason = None
            if hit_sl and hit_tp:
                ambiguous_candles.append({
                    "symbol": symbol_ccxt, "tf": timeframe, "candle_start": timestamp,
                    "candle_end": timestamp + tf_dur, "side": position['side'],
                    "sl": position['stop_loss'], "tp": position['take_profit'],
                    "entry_price": position['entry_price'],
                })
                exit_price = position['stop_loss']  # Default wie Original-Backtester
                reason = "AMBIGUOUS_DEFAULT_SL"
            elif hit_sl:
                exit_price = position['stop_loss']
                reason = "SL"
            elif hit_tp:
                exit_price = position['take_profit']
                reason = "TP"

            if exit_price:
                pnl_pct = (exit_price / position['entry_price'] - 1) if position['side'] == 'long' else (1 - exit_price / position['entry_price'])
                notional_value = position['notional_value']
                pnl_usd = notional_value * pnl_pct
                total_fees = notional_value * fee_pct * 2
                current_capital += (pnl_usd - total_fees)
                trades.append({
                    "entry_ts": position['entry_ts'], "exit_ts": timestamp, "side": position['side'],
                    "entry_price": position['entry_price'], "sl": position['stop_loss'], "tp": position['take_profit'],
                    "exit_price": exit_price, "reason": reason, "pnl_usd": pnl_usd - total_fees,
                    "notional_value": notional_value, "fee_pct": fee_pct,
                    "candle_end": timestamp + tf_dur,
                })
                position = None
                continue

        if not position and current_capital > 0:
            side, price = get_titan_signal(processed, current_candle, params_for_logic, Bias.NEUTRAL)
            if side:
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
                max_notional = current_capital * 10
                final_notional = min(calc_notional, max_notional, 1000000)
                margin_needed = final_notional / leverage
                if margin_needed > current_capital:
                    continue

                if side == 'buy':
                    sl = entry_price - sl_dist
                    tp = entry_price + sl_dist * rr
                else:
                    sl = entry_price + sl_dist
                    tp = entry_price - sl_dist * rr

                position = {
                    'side': 'long' if side == 'buy' else 'short',
                    'entry_price': entry_price, 'stop_loss': sl, 'take_profit': tp,
                    'notional_value': final_notional, 'entry_ts': timestamp,
                }

    return {"trades": trades, "ambiguous": ambiguous_candles, "final_capital": current_capital, "start_capital": start_capital}


def main():
    all_ambiguous = []
    all_trades = {}

    for sym, tf in CURRENT_TF.items():
        cfg = json.load(open(os.path.join(CONFIG_DIR, f"config_{sym}USDTUSDT_{tf}.json")))
        symbol_ccxt = cfg["market"]["symbol"]
        print(f"\n{sym} ({tf}) — simuliere Backtest & suche mehrdeutige Kerzen...")
        result = run_instrumented_backtest(symbol_ccxt, tf, cfg)
        if result is None:
            print("  keine Daten")
            continue
        all_trades[sym] = result
        n_amb = len(result["ambiguous"])
        n_trades = len(result["trades"])
        print(f"  {n_trades} simulierte Trades, davon {n_amb} mit SL+TP gleichzeitig in einer Kerze (mehrdeutig)")
        for amb in result["ambiguous"]:
            amb["ref_symbol"] = sym
            all_ambiguous.append(amb)

    print(f"\n\n{'='*100}\nAufloesung der {len(all_ambiguous)} mehrdeutigen Kerzen via 1m-Daten\n{'='*100}")

    corrections = {}  # (sym, entry_ts) -> 'sl' or 'tp'
    for amb in all_ambiguous:
        sym = amb["ref_symbol"]
        cfg = json.load(open(os.path.join(CONFIG_DIR, f"config_{sym}USDTUSDT_{CURRENT_TF[sym]}.json")))
        symbol_ccxt = cfg["market"]["symbol"]
        true_order = resolve_order_1m(symbol_ccxt, amb["candle_start"], amb["candle_end"], amb["sl"], amb["tp"], amb["side"])
        print(f"{sym} | Kerze {amb['candle_start']} .. {amb['candle_end']} | {amb['side']} | SL={amb['sl']:.6g} TP={amb['tp']:.6g} -> 1m-Aufloesung: {true_order}")
        corrections[(sym, amb["candle_start"])] = true_order

    print(f"\n\n{'='*100}\nKORRIGIERTE BACKTEST-ERGEBNISSE (aktuelle Configs, {START_DATE}..{END_DATE})\n{'='*100}")

    summary = []
    for sym, result in all_trades.items():
        start_capital = result["start_capital"]
        cap_original = start_capital
        cap_corrected = start_capital
        wins_orig = wins_corr = 0
        n = len(result["trades"])
        flips = 0
        for tr in result["trades"]:
            fee_dummy = 0
            if tr["reason"] == "AMBIGUOUS_DEFAULT_SL":
                true_order = corrections.get((sym, tr["entry_ts"]))
                orig_pnl = tr["pnl_usd"]  # bereits mit SL berechnet (Default)
                cap_original += orig_pnl
                if orig_pnl > 0:
                    wins_orig += 1

                if true_order == 'tp':
                    # Neu berechnen mit TP statt SL (exakt, ueber gespeicherten notional_value)
                    exit_price = tr["tp"]
                    pnl_pct = (exit_price / tr["entry_price"] - 1) if tr["side"] == 'long' else (1 - exit_price / tr["entry_price"])
                    pnl_usd = tr["notional_value"] * pnl_pct
                    total_fees = tr["notional_value"] * tr["fee_pct"] * 2
                    corr_pnl = pnl_usd - total_fees
                    cap_corrected += corr_pnl
                    if corr_pnl > 0:
                        wins_corr += 1
                    flips += 1
                else:
                    cap_corrected += orig_pnl
                    if orig_pnl > 0:
                        wins_corr += 1
            else:
                cap_original += tr["pnl_usd"]
                cap_corrected += tr["pnl_usd"]
                if tr["pnl_usd"] > 0:
                    wins_orig += 1
                    wins_corr += 1

        summary.append({
            "symbol": sym, "trades": n, "ambiguous_flips_to_TP": flips,
            "orig_pnl_pct": round((cap_original - start_capital) / start_capital * 100, 1),
            "corr_pnl_pct": round((cap_corrected - start_capital) / start_capital * 100, 1),
            "orig_winrate": round(wins_orig / n * 100, 1) if n else 0,
            "corr_winrate": round(wins_corr / n * 100, 1) if n else 0,
        })

    sdf = pd.DataFrame(summary)
    print(sdf.to_string(index=False))
    sdf.to_csv(os.path.join(os.path.dirname(__file__), "sltp_ambiguity_correction.csv"), index=False)


if __name__ == "__main__":
    main()
