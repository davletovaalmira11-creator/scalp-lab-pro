"""OmniLab OKX trader v2 — много-стратегийный paper/LIVE движок.

PAPER (без Secrets): живые свечи OKX 15m, 5 стратегий × BTC/ETH/SOL,
каждая позиция подписана стратегией и монетой, TP/SL по ATR.
LIVE: те же сигналы → реальные ордера (Secrets OKX_API_KEY/SECRET/PASSPHRASE).

Саморазвитие: параметры и активный портфель стратегий читаются из
best_params.json — их обновляет evolve-ядро каждый час (walk-forward OOS).
"""
import json
import os
import time

import pandas as pd

import config as C
from client import Okx
from strategies import STRATEGIES, features

RES = "results/okx_live.json"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_best():
    try:
        with open("best_params.json") as f:
            return json.load(f)
    except Exception:
        return {}


def load():
    try:
        with open(RES) as f:
            return json.load(f)
    except Exception:
        return {}


def save(st):
    os.makedirs("results", exist_ok=True)
    with open(RES, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def scan(api, best):
    fast = int(best.get("ema_fast", C.EMA_FAST))
    slow = int(best.get("ema_slow", C.EMA_SLOW))
    out = {}
    for inst in C.PAIRS:
        try:
            r = api.public_get("/api/v5/market/candles",
                               {"instId": inst, "bar": C.BAR, "limit": C.CANDLE_LIMIT})
            rows = r.get("data") or []
            if not rows:
                raise RuntimeError("no candles: " + str(r.get("error") or r)[:150])
            df = pd.DataFrame([x[:5] for x in rows], columns=["ts", "o", "h", "l", "c"])
            for col in ["o", "h", "l", "c"]:
                df[col] = df[col].astype(float)
            df = df.iloc[::-1].reset_index(drop=True)
            df = features(df, fast, slow)
            px = float(df["c"].iloc[-1])
            atr = float(df["atr"].iloc[-1]) if not df["atr"].isna().iloc[-1] else None
            sigs = {}
            for name, fn in STRATEGIES.items():
                try:
                    sigs[name] = int(fn(df))
                except Exception:
                    sigs[name] = 0
            out[inst] = {"price": px, "atr": atr, "sig": sigs, "err": None}
        except Exception as e:
            out[inst] = {"price": None, "atr": None, "sig": {}, "err": str(e)[:200]}
    return out, {"ema_fast": fast, "ema_slow": slow}


def paper_round(sigs, st, best):
    p = st.setdefault("paper", {"equity": C.PAPER_EQUITY, "start": C.PAPER_EQUITY,
                                "positions": {}, "trades": []})
    strats = st.setdefault("strategies", {})
    active = best.get("active_strategies") or list(STRATEGIES)
    tp_atr = float(best.get("tp_atr", C.TP_ATR))
    sl_atr = float(best.get("sl_atr", C.SL_ATR))
    # 1) менеджмент открытых позиций (TP/SL по живой цене)
    for key, pos in list(p["positions"].items()):
        info = sigs.get(pos["inst"]) or {}
        px = info.get("price")
        if not px:
            continue
        hit, reason = None, None
        if pos["side"] == 1:
            if px >= pos["tp"]:
                hit, reason = pos["tp"], "tp"
            elif px <= pos["sl"]:
                hit, reason = pos["sl"], "sl"
        else:
            if px <= pos["tp"]:
                hit, reason = pos["tp"], "tp"
            elif px >= pos["sl"]:
                hit, reason = pos["sl"], "sl"
        if hit is not None:
            pnl = pos["sz"] * (hit - pos["entry"]) * pos["side"]
            p["equity"] += pnl
            t = {"ts": now(), "venue": "OKX", "strategy": pos["strategy"], "inst": pos["inst"],
                 "side": "long" if pos["side"] == 1 else "short",
                 "entry": round(pos["entry"], 6), "exit": round(hit, 6),
                 "pnl": round(pnl, 4), "reason": reason}
            p["trades"].insert(0, t)
            del p["trades"][100:]
            s = strats.setdefault(pos["strategy"], {"pnl": 0, "trades": 0, "wins": 0, "open": 0})
            s["pnl"] = round(s["pnl"] + pnl, 4)
            s["trades"] += 1
            if pnl > 0:
                s["wins"] += 1
            s["open"] = max(0, s["open"] - 1)
            del p["positions"][key]
    # 2) открытие по сигналам активных стратегий
    for inst, info in sigs.items():
        if not info.get("price") or not info.get("atr"):
            continue
        for sname, sig in (info.get("sig") or {}).items():
            if len(p["positions"]) >= C.MAX_OPEN * len(active):
                break
            key = sname + "|" + inst
            if key in p["positions"] or sig == 0 or sname not in active:
                continue
            side, entry, atr = sig, info["price"], info["atr"]
            p["positions"][key] = {
                "strategy": sname, "inst": inst, "side": side,
                "entry": entry, "sz": C.ORDER_USDT / entry,
                "tp": entry + side * tp_atr * atr,
                "sl": entry - side * sl_atr * atr,
                "opened": now(),
            }
            s = strats.setdefault(sname, {"pnl": 0, "trades": 0, "wins": 0, "open": 0})
            s["open"] += 1
    return p


def main():
    st = load()
    best = load_best()
    key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_API_SECRET", "")
    passph = os.environ.get("OKX_API_PASSPHRASE", "")
    demo = os.environ.get("OKX_DEMO", "").strip().lower() in ("1", "true", "yes", "demo")
    mode = "live" if (key and secret and passph) else "paper"
    api = Okx(key, secret, passph, demo=demo)

    sigs, params = scan(api, best)
    p = paper_round(sigs, st, best)
    lv = st.get("live", {"positions": {}, "trades": []})

    equity = p["equity"]
    start = p.get("start", C.PAPER_EQUITY)
    pnl_pct = round((equity / start - 1) * 100, 3)
    trades = p["trades"] and len(p["trades"])
    open_n = len(p["positions"]) + len(lv.get("positions", {}))

    st.update({
        "mode": mode, "demo": demo if mode == "live" else None,
        "equity": round(equity, 2), "start_equity": round(start, 2),
        "pnl_pct": pnl_pct, "trades": len(p["trades"]), "open": open_n,
        "recent_trades": p["trades"][:50],
        "params": params, "params_source": "evolve" if best else "default",
        "active_strategies": best.get("active_strategies") or list(STRATEGIES),
        "paper": p,
        "signals": sigs,
        "updated": now(),
    })
    save(st)
    print("okx trader v2: mode=%s equity=%s open=%s trades=%s strategies=%s" % (
        mode, st["equity"], open_n, st["trades"], st["active_strategies"]))


if __name__ == "__main__":
    main()
