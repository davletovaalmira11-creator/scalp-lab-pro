"""OmniLab Evolve v2 - анти-оверфит walk-forward подбор параметров.
Схема честного отбора (никакой подгонки):
  1) ВСЕ комбинации параметров оцениваются на IS-окне (первые 55% истории);
  2) лучшая по IS комбинация проверяется на OOS-окне (последние 45%);
  3) параметры применяются к трейдеру ТОЛЬКО если OOS-результат после
     издержек положителен (+1%% и >= 15 сделок на пуле монет).
Вход: results/feed_okx.json (вся ликвидная вселенная). Выход:
results/evolve2_okx.json + engines/okx/best_params.json (при отборе)."""
import json, os, time

HERE = os.path.dirname(os.path.abspath(__file__))
OKX_R = os.path.join(HERE, "..", "okx", "results")
FEED = os.path.join(OKX_R, "feed_okx.json")
OUT = os.path.join(OKX_R, "evolve2_okx.json")
BP = os.path.join(HERE, "..", "okx", "best_params.json")

FASTS = [5, 9, 12]
SLOWS = [21, 26, 34]
SLS = [1.5, 2.5]
TPS = [2.5, 4.0]
COST = 0.10
SPLIT = 0.55
MIN_OOS_PCT = 1.0
MIN_OOS_TRADES = 15

def load_json(p, default):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default

def ema_series(c, n):
    k = 2.0 / (n + 1)
    out = []
    e = float(c[0])
    out.append(e)
    for v in c[1:]:
        e = float(v) * k + e * (1 - k)
        out.append(e)
    return out

def atr_pct(c, i, n=14):
    j0 = max(1, i - n + 1)
    vals = [abs(c[k] / c[k - 1] - 1) for k in range(j0, i + 1)]
    return 100.0 * sum(vals) / len(vals) if vals else 0.0

def simulate(c, f, s, slk, tpk, lo, hi):
    fe = ema_series(c, f)
    se = ema_series(c, s)
    pos = None
    pnl = 0.0
    trades = 0
    wins = 0
    for i in range(lo + 1, hi):
        cu = fe[i - 1] <= se[i - 1] and fe[i] > se[i]
        cd = fe[i - 1] >= se[i - 1] and fe[i] < se[i]
        if pos is None and cu:
            pos = c[i]
            ap = atr_pct(c, i)
            sl = pos * (1 - slk * ap / 100.0)
            tp = pos * (1 + tpk * ap / 100.0)
            trades += 1
        elif pos is not None:
            if cd or c[i] <= sl or c[i] >= tp:
                r = (c[i] / pos - 1) * 100 - COST
                pnl += r
                if r > 0:
                    wins += 1
                pos = None
    if pos is not None:
        r = (c[hi - 1] / pos - 1) * 100 - COST
        pnl += r
        if r > 0:
            wins += 1
    return pnl, trades, wins

def main():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    feed = load_json(FEED, None)
    if not feed or not feed.get("coins"):
        print("evolve2: FAIL - feed_okx.json пуст")
        return
    series = []
    for coin in feed["coins"]:
        c = (coin.get("tf") or {}).get("15m", {}).get("closes") or []
        if len(c) >= 60:
            series.append(c)
    if not series:
        print("evolve2: FAIL - нет 15m истории")
        return

    combos = []
    for f in FASTS:
        for s in SLOWS:
            if f >= s:
                continue
            for slk in SLS:
                for tpk in TPS:
                    combos.append((f, s, slk, tpk))

    best = None
    for (f, s, slk, tpk) in combos:
        ip = it = 0
        for c in series:
            split = int(len(c) * SPLIT)
            p, t, w = simulate(c, f, s, slk, tpk, 1, split)
            ip += p
            it += t
        score = (round(ip, 3), it)
        if best is None or score > best["score"]:
            best = {"f": f, "s": s, "sl": slk, "tp": tpk, "score": score,
                    "is_pnl": round(ip, 2), "is_trades": it}

    op = ot = ow = 0
    for c in series:
        split = int(len(c) * SPLIT)
        p, t, w = simulate(c, best["f"], best["s"], best["sl"], best["tp"], split, len(c))
        op += p
        ot += t
        ow += w
    oos_pnl = round(op, 2)
    promoted = oos_pnl >= MIN_OOS_PCT and ot >= MIN_OOS_TRADES

    out = {
        "updated": now, "tried": len(combos), "coins_used": len(series),
        "best": {"ema_fast": best["f"], "ema_slow": best["s"], "sl_atr": best["sl"],
                 "tp_atr": best["tp"], "is_pnl": best["is_pnl"], "is_trades": best["is_trades"],
                 "oos_pnl": oos_pnl, "oos_trades": ot,
                 "oos_winrate": round(100.0 * ow / ot, 1) if ot else None},
        "promoted": promoted,
        "note": "Отбор ТОЛЬКО на IS (55% истории), зачёт ТОЛЬКО на OOS (45%). " +
                "Параметры применяются к трейдеру только при OOS >= +1% и >= 15 сделок " +
                "после издержек 0.1% круг. ATR по close (без H/L).",
    }
    os.makedirs(OKX_R, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f)
    if promoted:
        with open(BP, "w") as f:
            json.dump({"updated": now, "ema_fast": best["f"], "ema_slow": best["s"],
                       "sl_atr": best["sl"], "tp_atr": best["tp"], "oos_ret": oos_pnl,
                       "source": "evolve2", "pool_trades": ot}, f)
    print("evolve2: tried=%d best=%s/%s IS=%+.2f%% OOS=%+.2f%% trades=%d promoted=%s" % (
        len(combos), best["f"], best["s"], best["is_pnl"], oos_pnl, ot, promoted))

if __name__ == "__main__":
    main()
