"""OmniLab Feed v2 - мультитаймфрейм-фид OKX: свечи 5m/15m/1H/4H + стаканы.
v2: теперь сохраняет и время каждой свечи (ts) - мозг учится инкрементально,
каждый бар ровно один раз, без повторной обработки истории.
Выход: results/feed_okx.json. Только stdlib, только публичные API."""
import json, os, time, urllib.request

BASE = "https://www.okx.com"
HERE = os.path.dirname(os.path.abspath(__file__))
TFS = ["5m", "15m", "1H", "4H"]
N_COINS = 10
KEEP = 280

def api(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "omnilab/2.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode())
            if d.get("code") == "0" and d.get("data"):
                return d["data"]
            return None
        except Exception:
            time.sleep(1.0 + attempt)
    return None

def ema(vals, n):
    if not vals:
        return 0.0
    k = 2.0 / (n + 1)
    e = float(vals[0])
    for v in vals[1:]:
        e = float(v) * k + e * (1 - k)
    return e

def candles(inst, bar):
    rows = api("/api/v5/market/candles?instId=" + inst + "&bar=" + bar + "&limit=300")
    if not rows:
        return [], []
    ts = []
    closes = []
    for r in rows:
        try:
            ts.append(int(r[0]))
            closes.append(float(r[4]))
        except Exception:
            continue
    ts.reverse()
    closes.reverse()
    return ts, closes

def book(inst):
    d = api("/api/v5/market/books?instId=" + inst + "&sz=50")
    if not d:
        return None
    b = d[0]
    try:
        bids = sum(float(x[1]) * float(x[0]) for x in b.get("bids", []))
        asks = sum(float(x[1]) * float(x[0]) for x in b.get("asks", []))
        bb = float(b["bids"][0][0])
        ba = float(b["asks"][0][0])
        mid = (bb + ba) / 2 or 1.0
        return {
            "imbalance": round((bids - asks) / (bids + asks), 3) if (bids + asks) > 0 else 0.0,
            "spread_pct": round((ba - bb) / mid * 100, 4),
            "bid_usd": round(bids),
            "ask_usd": round(asks),
        }
    except Exception:
        return None

def tf_block(ts, c):
    n = len(c)
    if n < 25:
        return {"bars": n, "trend": "n/a", "ret12": None, "ts": ts, "closes": c}
    e9 = ema(c[-30:], 9)
    e21 = ema(c[-30:], 21)
    trend = "up" if e9 > e21 else "down"
    ret12 = round((c[-1] / c[-13] - 1) * 100, 2) if n >= 13 else None
    return {"bars": n, "trend": trend, "ret12": ret12,
            "ts": ts[-KEEP:], "closes": c[-KEEP:]}

def main():
    upath = os.path.join(HERE, "results", "universe_okx.json")
    try:
        with open(upath) as f:
            uni = json.load(f)
    except Exception:
        print("feed: FAIL - нет universe_okx.json, запусти universe.py первым")
        return
    coins = []
    for u in (uni.get("top") or [])[:N_COINS]:
        inst = u["instId"]
        entry = {"instId": inst, "coin": u["coin"], "vol24h_usd": u["vol24h_usd"], "tf": {}}
        for tf in TFS:
            ts, c = candles(inst, tf)
            entry["tf"][tf] = tf_block(ts, c)
            time.sleep(0.12)
        entry["book"] = book(inst)
        entry["last"] = u["last"]
        coins.append(entry)
        print("feed: %s ok (15m bars=%s)" % (inst, entry["tf"]["15m"]["bars"]))
        time.sleep(0.12)
    out = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base": "OKX",
        "tfs": TFS,
        "coins": coins,
    }
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "feed_okx.json"), "w") as f:
        json.dump(out, f)
    print("feed: done coins=%d tfs=%s" % (len(coins), "/".join(TFS)))

if __name__ == "__main__":
    main()
