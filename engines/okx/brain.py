"""OmniLab Brain v2 - адаптивный онлайн-мозг OKX.
v2 - два критических исправления:
  1) ИНКРЕМЕНТАЛЬНОСТЬ: каждый бар обрабатывается ровно один раз (watermark
     по времени свечи) - история больше не пересчитывается каждый прогон.
  2) ПОРОГИ 0.60/0.40 вместо 0.56/0.44 - меньше сделок, издержки не сжигают PnL.
Walk-forward честно: прогноз ВСЕГДА до обновления весов. Веса персистентны.
Вход: results/feed_okx.json. Выход: results/brain_okx.json. Только PAPER."""
import json, math, os, time

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "results")
FEED = os.path.join(R, "feed_okx.json")
OUT = os.path.join(R, "brain_okx.json")
WTS = os.path.join(R, "brain_weights.json")
STATE = os.path.join(R, "brain_state.json")

LR = 0.05
L2 = 1e-4
LONG_P = 0.60
SHORT_P = 0.40
COST_PCT = 0.10
MIN_BARS = 40
STATE_V = 2

def clip(x, lim):
    return max(-lim, min(lim, x))

def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

def ema(vals, n):
    if not vals:
        return 0.0
    k = 2.0 / (n + 1)
    e = float(vals[0])
    for v in vals[1:]:
        e = float(v) * k + e * (1 - k)
    return e

def rsi14(vals):
    if len(vals) < 15:
        return 50.0
    g = l = 0.0
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    if g + l == 0:
        return 50.0
    return 100.0 * g / (g + l)

def features(c):
    if len(c) < 25:
        return None
    r1 = (c[-1] / c[-2] - 1) * 100
    r3 = (c[-1] / c[-4] - 1) * 100
    r6 = (c[-1] / c[-7] - 1) * 100
    r12 = (c[-1] / c[-13] - 1) * 100
    e9 = ema(c[-30:], 9)
    e21 = ema(c[-30:], 21)
    edist = (e9 / e21 - 1) * 100 if e21 else 0.0
    vola = sum(abs(c[-i] / c[-i - 1] - 1) for i in range(1, 4)) / 3 * 100
    return [1.0, clip(r1, 3), clip(r3, 5), clip(r6, 7), clip(r12, 9),
            clip(edist, 5), rsi14(c[-15:]) / 100.0, clip(vola, 6)]

def load_json(p, default):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default

def save_json(p, obj):
    with open(p, "w") as f:
        json.dump(obj, f)

def main():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    feed = load_json(FEED, None)
    if not feed or not feed.get("coins"):
        print("brain: FAIL - feed_okx.json пуст, запусти feed.py первым")
        return
    w = load_json(WTS, {}).get("w") or [0.0] * 8
    st = load_json(STATE, {})
    if st.get("v") != STATE_V:
        st = {"v": STATE_V, "equity_pct": 0.0, "trades": 0, "wins": 0, "curve": [],
              "watermarks": {}}
    if "watermarks" not in st:
        st["watermarks"] = {}
    wm = st["watermarks"]

    preds = correct = 0
    trades_run = 0
    wins_run = 0
    pnl_run = 0.0
    per_coin = []
    signals = []

    for coin in feed["coins"]:
        inst = coin.get("instId")
        tf = (coin.get("tf") or {}).get("15m", {})
        c = tf.get("closes") or []
        ts = tf.get("ts") or []
        imb = (coin.get("book") or {}).get("imbalance") or 0.0
        if len(c) < MIN_BARS:
            continue
        last_wm = wm.get(inst)
        start = 25
        if last_wm is not None and ts:
            start = 25
            while start < len(ts) and ts[start] <= last_wm:
                start += 1
        cc = cp = ct = cw = 0
        cpnl = 0.0
        new_bars = 0
        for i in range(start, len(c) - 1):
            x = features(c[:i + 1])
            if x is None:
                continue
            z = sum(w[j] * x[j] for j in range(len(x)))
            p = sigmoid(z)
            nxt = (c[i + 1] / c[i] - 1) * 100
            y = 1 if nxt > 0.03 else 0
            preds += 1
            cc += 1
            new_bars += 1
            if (p >= 0.5) == (nxt > 0):
                correct += 1
                cp += 1
            if p >= LONG_P or p <= SHORT_P:
                trades_run += 1
                ct += 1
                pnl = nxt - COST_PCT if p >= LONG_P else -nxt - COST_PCT
                pnl_run += pnl
                cpnl += pnl
                if pnl > 0:
                    wins_run += 1
                    cw += 1
            err = y - p
            for j in range(len(x)):
                w[j] += LR * err * x[j] - L2 * w[j]
        if ts:
            wm[inst] = ts[-1]
        x = features(c)
        if x:
            z = sum(w[j] * x[j] for j in range(len(x)))
            p = sigmoid(z)
            side = "long" if p >= LONG_P else ("short" if p <= SHORT_P else "wait")
            signals.append({"instId": inst, "p": round(p, 3), "side": side,
                            "imbalance": round(imb, 3)})
        per_coin.append({"instId": inst, "new_bars": new_bars,
                         "preds": cc, "acc": round(100.0 * cp / cc, 1) if cc else None,
                         "trades": ct, "pnl_pct": round(cpnl, 2)})

    st["equity_pct"] = round(st.get("equity_pct", 0.0) + pnl_run, 3)
    st["trades"] = st.get("trades", 0) + trades_run
    st["wins"] = st.get("wins", 0) + wins_run
    curve = (st.get("curve") or [])[-499:]
    curve.append({"t": now, "pct": st["equity_pct"]})
    st["curve"] = curve
    save_json(WTS, {"w": w, "updated": now})
    save_json(STATE, st)
    out = {
        "updated": now, "v": STATE_V, "mode": "paper-online-ml",
        "engine": "logreg-sgd-incremental-walkforward",
        "equity_pct": st["equity_pct"], "trades": st["trades"],
        "winrate": round(100.0 * st["wins"] / st["trades"], 1) if st["trades"] else None,
        "accuracy": round(100.0 * correct / preds, 2) if preds else None,
        "preds_total": preds, "trades_run": trades_run, "pnl_run_pct": round(pnl_run, 3),
        "long_p": LONG_P, "short_p": SHORT_P, "cost_pct": COST_PCT,
        "signals": signals, "per_coin": per_coin, "curve": curve,
    }
    save_json(OUT, out)
    print("brain v2: new_bars_preds=%d acc=%s%% trades_total=%d equity=%+.2f%% signals=%d" % (
        preds, out["accuracy"], st["trades"], st["equity_pct"], len(signals)))

if __name__ == "__main__":
    main()
