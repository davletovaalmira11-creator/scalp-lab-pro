"""Пул стратегий OKX-движка — лаборатория разработки.

Каждая стратегия — именованный сигнал на закрытом баре. Эволюционное ядро
(evolve) оценивает их walk-forward (IS/OOS) на живых свечах и включает в
портфель только прошедшие отбор — trader торгует активными сам, без человека.
Новая стратегия: добавь sig_* и она автоматически попадёт в эволюцию.
"""
import pandas as pd


def features(df, fast=9, slow=21):
    df = df.copy()
    df["ema_f"] = df["c"].ewm(span=fast, adjust=False).mean()
    df["ema_s"] = df["c"].ewm(span=slow, adjust=False).mean()
    prev = df["c"].shift()
    tr = pd.concat([df["h"] - df["l"], (df["h"] - prev).abs(), (df["l"] - prev).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    delta = df["c"].diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    rs = up / dn.replace(0, 1e-9)
    df["rsi"] = 100 - 100 / (1 + rs)
    df["roc"] = df["c"].pct_change(10)
    df["don_hi"] = df["h"].rolling(20).max().shift(1)
    df["don_lo"] = df["l"].rolling(20).min().shift(1)
    df["sma"] = df["c"].rolling(48).mean()
    return df


def _cross(a, b):
    dp = a.iloc[-2] - b.iloc[-2]
    d = a.iloc[-1] - b.iloc[-1]
    if dp <= 0 < d:
        return 1
    if dp >= 0 > d:
        return -1
    return 0


def sig_ema_cross(df):
    return _cross(df["ema_f"], df["ema_s"])


def sig_momentum(df):
    rp, rc = df["roc"].iloc[-2], df["roc"].iloc[-1]
    if rp <= 0.004 < rc:
        return 1
    if rp >= -0.004 > rc:
        return -1
    return 0


def sig_rsi_revert(df):
    r = df["rsi"]
    if r.iloc[-2] < 32 <= r.iloc[-1]:
        return 1
    if r.iloc[-2] > 68 >= r.iloc[-1]:
        return -1
    return 0


def sig_donchian_break(df):
    c = df["c"]
    if c.iloc[-2] <= df["don_hi"].iloc[-2] and c.iloc[-1] > df["don_hi"].iloc[-1]:
        return 1
    if c.iloc[-2] >= df["don_lo"].iloc[-2] and c.iloc[-1] < df["don_lo"].iloc[-1]:
        return -1
    return 0


def sig_sma_revert(df):
    dev = (df["c"] - df["sma"]) / df["atr"].replace(0, 1e-9)
    dp, dc = dev.iloc[-2], dev.iloc[-1]
    if dp >= 2 > dc:
        return 1
    if dp <= -2 < dc:
        return -1
    return 0


STRATEGIES = {
    "ema_cross": sig_ema_cross,
    "momentum": sig_momentum,
    "rsi_revert": sig_rsi_revert,
    "donchian_break": sig_donchian_break,
    "sma_revert": sig_sma_revert,
}
