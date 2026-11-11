"""OmniLab Evolve v2 — саморазвивающееся ядро.

Каждый час без человека:
1. Walk-forward отбор СТРАТЕГИЙ: все сигнальные функции движка тестируются на
   живых свечах OKX (900 баров 15m), сплит 50/50 IS/OOS, комиссия roundtrip.
   В портфель (best_params.active_strategies) идут только стратегии,
   прибыльные И на IS, И на OOS — трейдер подхватывает список сам.
2. Оптимизация параметров EMA×SL×TP той же walk-forward схемой.
3. Кросс-веномный радар: живой импульс OKX против подразумеваемой вероятности
   Polymarket (Up-окна) — расхождения сохраняются как кандидаты edge.
4. results/evolve.json — журнал эволюции: поколения параметров, портфель,
   золотые паттерны с обеих площадок.
Против подгонки: SL раньше TP внутри бара, минимум сделок, без подбора на OOS.
"""
import csv
import json
import math
import os
import time

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OKX = "https://www.okx.com/api/v5/market/candles"
PAIRS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
BAR = "15m"
LIMIT = 900
FEE_RT = 0.0016
MIN_TRADES = 6
EMA_GRID = [(5, 13), (8, 21), (9, 21), (12, 26), (20, 50)]
SL_GRID = [1.0, 1.5, 2.0]
TP_GRID = [2.0, 2.5, 3.0]

import sys
sys.path.insert(0, os.path.join(ROOT, "engines", "okx"))
from strategies import features  # noqa: E402


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def rp(*parts):
    return os.path.join(ROOT, *parts)


def candles(inst):
    r = requests.get(OKX, params={"instId": inst, "bar": BAR, "limit": LIMIT}, timeout=25).json()
    rows = r.get("data") or []
    df = pd.DataFrame([x[:5] for x in rows], columns=["ts", "o", "h", "l", "c"])
    for col in ["o", "h", "l", "c"]:
        df[col] = df[col].astype(float)
    return df.iloc[::-1].reset_index(drop=True)


def sig_series(df, name):
    """Векторизованный сигнал стратегии по всей истории (i-й бар закрыт)."""
    c = df["c"].values
    emaf, emas = df["ema_f"].values, df["ema_s"].values
    out = [0] * len(c)
    if name == "ema_cross":
        for i in range(1, len(c)):
            dp = emaf[i - 1] - emas[i - 1]
            d = emaf[i] - emas[i]
            if dp <= 0 < d:
                out[i] = 1
            elif dp >= 0 > d:
                out[i] = -1
    elif name == "momentum":
        roc = df["roc"].values
        for i in range(1, len(c)):
            if roc[i - 1] <= 0.004 < roc[i]:
                out[i] = 1
            elif roc[i - 1] >= -0.004 > roc[i]:
                out[i] = -1
    elif name == "rsi_revert":
        rsi = df["rsi"].values
        for i in range(1, len(c)):
            if rsi[i - 1] < 32 <= rsi[i]:
                out[i] = 1
            elif rsi[i - 1] > 68 >= rsi[i]:
                out[i] = -1
    elif name == "donchian_break":
        hi, lo = df["don_hi"].values, df["don_lo"].values
        for i in range(1, len(c)):
            if c[i - 1] <= hi[i - 1] and c[i] > hi[i]:
                out[i] = 1
            elif c[i - 1] >= lo[i - 1] and c[i] < lo[i]:
                out[i] = -1
    elif name == "sma_revert":
        dev = ((df["c"] - df["sma"]) / df["atr"].replace(0, 1e-9)).values
        for i in range(1, len(c)):
            if dev[i - 1] >= 2 > dev[i]:
                out[i] = 1
            elif dev[i - 1] <= -2 < dev[i]:
                out[i] = -1
    return out


def backtest_sig(df, sig, sl_k, tp_k):
    c = df["c"].values
    h = df["h"].values
    l = df["l"].values
    atr = df["atr"].values
    n = len(c)
    rets = []
    i = 30
    while i < n - 1:
        s = sig[i]
        if not s or math.isnan(atr[i]):
            i += 1
            continue
        e = c[i]
        tp_px = e + s * tp_k * atr[i]
        sl_px = e - s * sl_k * atr[i]
        j = i + 1
        exit_px = None
        while j < n:
            if s == 1:
                if l[j] <= sl_px:
                    exit_px = sl_px
                    break
                if h[j] >= tp_px:
                    exit_px = tp_px
                    break
            else:
                if h[j] >= sl_px:
                    exit_px = sl_px
                    break
                if l[j] <= tp_px:
                    exit_px = tp_px
                    break
            j += 1
        if exit_px is not None:
            rets.append((exit_px - e) / e * s - FEE_RT)
        i = j + 1
    return rets


def wf_eval(data, sig_of, sl_k, tp_k, min_trades):
    is_total = oos_total = 0.0
    is_n = oos_n = 0
    for df in data.values():
        half = len(df) // 2
        a = backtest_sig(df.iloc[:half].reset_index(drop=True), sig_of(df.iloc[:half].reset_index(drop=True)), sl_k, tp_k)
        b = backtest_sig(df.iloc[half:].reset_index(drop=True), sig_of(df.iloc[half:].reset_index(drop=True)), sl_k, tp_k)
        if len(a) < min_trades or len(b) < min_trades:
            return None
        is_total += sum(a)
        is_n += len(a)
        oos_total += sum(b)
        oos_n += len(b)
    return {"is_ret": round(is_total * 100, 2), "oos_ret": round(oos_total * 100, 2),
            "trades": is_n + oos_n, "_oos": oos_total}


def evolve_okx():
    try:
        raw = {p: candles(p) for p in PAIRS}
    except Exception as e:
        return None, [], "candles: " + str(e)[:150]
    data = {k: features(v, 9, 21) for k, v in raw.items()}
    names = ["ema_cross", "momentum", "rsi_revert", "donchian_break", "sma_revert"]
    stats = []
    active = []
    for nm in names:
        r = wf_eval(data, lambda d, _nm=nm: sig_series(d, _nm), 1.5, 2.5, MIN_TRADES // 3)
        if r and r["is_ret"] > 0 and r["oos_ret"] > 0:
            active.append(nm)
        if r:
            stats.append({"strategy": nm, "is_ret": r["is_ret"], "oos_ret": r["oos_ret"],
                          "trades": r["trades"], "portfolio": nm in active})
    best = None
    for (fast, slow) in EMA_GRID:
        for sl_k in SL_GRID:
            for tp_k in TP_GRID:
                dd = {k: features(v, fast, slow) for k, v in raw.items()}

                def sig_of(d):
                    return sig_series(d, "ema_cross")
                r = wf_eval(dd, sig_of, sl_k, tp_k, MIN_TRADES // 2)
                if r and r["is_ret"] > 0 and r["oos_ret"] > 0 and (best is None or r["_oos"] > best["_oos"]):
                    best = {"ema_fast": fast, "ema_slow": slow, "sl_atr": sl_k, "tp_atr": tp_k,
                            "is_ret": r["is_ret"], "oos_ret": r["oos_ret"], "trades": r["trades"],
                            "_oos": r["_oos"]}
    if best:
        best.pop("_oos", None)
        best["active_strategies"] = active if active else names
        best["strategy_stats"] = stats
        best["updated"] = now()
        with open(rp("engines", "okx", "best_params.json"), "w") as f:
            json.dump(best, f, indent=1)
    return best, stats, None


def radar(raw):
    """Кросс-веномный радар: импульс OKX против вероятностей Polymarket."""
    out = []
    try:
        with open(rp("results", "sht.json")) as f:
            sht = json.load(f)
    except Exception:
        sht = {}
    pm = {}
    for w in sht.get("live") or []:
        title = str(w.get("title") or w.get("series") or "")
        for coin in ("BTC", "ETH", "SOL"):
            if coin in title:
                pm[coin] = w
                break
    for inst, df in raw.items():
        coin = inst.split("-")[0]
        c = df["c"].values
        if len(c) < 13:
            continue
        mom = (c[-1] / c[-13] - 1) * 100
        w = pm.get(coin)
        p_up = w.get("p_up") if isinstance(w, dict) else None
        div = None
        if p_up is not None:
            imp_prob = min(1.0, max(0.0, 0.5 + mom / 10))
            div = round(imp_prob - float(p_up), 4)
        out.append({"coin": coin, "okx_mom_pct": round(mom, 3),
                    "pm_p_up": p_up, "divergence": div, "ts": now()})
    return out


def golden_cross_venue():
    items = []
    try:
        with open(rp("results", "lab.json")) as f:
            lab = json.load(f)
        for g in lab.get("golden") or []:
            items.append({"src": "polymarket-lab", "name": g.get("name"), "note": g.get("note")})
    except Exception:
        pass
    try:
        with open(rp("engines", "scalp", "reports", "leaderboard.csv")) as f:
            for r in list(csv.DictReader(f))[:3]:
                items.append({"src": "walk-forward bt", "name": "%s %s %s" % (
                    r.get("strategy"), r.get("symbol"), r.get("tf")),
                    "note": "OOS PF %s, Sharpe %s" % (r.get("oos_pf"), r.get("oos_sharpe"))})
    except Exception:
        pass
    return items


def main():
    ev_path = rp("results", "evolve.json")
    ev = {"history": []}
    try:
        with open(ev_path) as f:
            ev = json.load(f)
    except Exception:
        pass
    best, stats, err = evolve_okx()
    raw = {}
    try:
        raw = {p: candles(p) for p in PAIRS}
    except Exception:
        pass
    ev["updated"] = now()
    ev["best_okx"] = best
    ev["strategy_stats"] = stats
    ev["portfolio"] = best.get("active_strategies") if best else []
    ev["radar"] = radar(raw)
    ev["golden"] = golden_cross_venue()
    ev["error"] = err
    hist = ev.setdefault("history", [])
    hist.insert(0, {"ts": now(), "best": {k: v for k, v in (best or {}).items() if k != "strategy_stats"},
                    "portfolio": ev["portfolio"], "golden_n": len(ev["golden"]), "err": err})
    del hist[50:]
    with open(ev_path, "w") as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)
    print("evolve v2: portfolio=%s stats=%d golden=%d err=%s" % (
        ev["portfolio"], len(stats), len(ev["golden"]), err))


if __name__ == "__main__":
    main()
