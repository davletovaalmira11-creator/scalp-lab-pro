"""OmniLab Universe v1 — авто-поиск ликвидной вселенной.
Сканирует ВСЕ USDT-свопы OKX, ранжирует по обороту, отбирает топ-15.
Выход: results/universe_okx.json. Только stdlib, только публичные API."""
import json, os, time, urllib.request

BASE = "https://www.okx.com"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results", "universe_okx.json")

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

def main():
    data = api("/api/v5/market/tickers?instType=SWAP")
    if not data:
        print("universe: FAIL - нет ответа OKX tickers")
        return
    rows = []
    for t in data:
        inst = t.get("instId", "")
        if not inst.endswith("-USDT-SWAP"):
            continue
        try:
            last = float(t["last"])
            vol = float(t.get("volCcy24h") or 0) * last
            ask = float(t.get("askPx") or 0)
            bid = float(t.get("bidPx") or 0)
            o24 = float(t.get("open24h") or 0)
            rows.append({
                "instId": inst,
                "coin": inst.split("-")[0],
                "last": last,
                "vol24h_usd": round(vol),
                "spread_pct": round((ask - bid) / last * 100, 4) if ask > 0 and bid > 0 else None,
                "open24h_pct": round((last / o24 - 1) * 100, 2) if o24 > 0 else None,
            })
        except Exception:
            continue
    rows.sort(key=lambda r: r["vol24h_usd"], reverse=True)
    top = rows[:15]
    out = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_scanned": len(rows),
        "top": top,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f)
    print("universe: scanned=%d top15=%s" % (len(rows), ",".join(r["coin"] for r in top)))

if __name__ == "__main__":
    main()
