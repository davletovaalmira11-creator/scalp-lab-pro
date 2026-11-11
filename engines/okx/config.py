# OmniLab OKX live-трейдинг — настройки и риски (v1.0.2)
import os


def _num(name, default, cast):
    v = (os.environ.get(name) or "").strip()
    try:
        return cast(v)
    except Exception:
        return cast(default)


# Фьючерсы USDT (SWAP). Для спота замени на "BTC-USDT" и tdMode="cash" в trader.py
PAIRS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
BAR = "15m"
CANDLE_LIMIT = 300

# Сигнал: пересечение EMA(9/21), волатильность ATR(14)
EMA_FAST = 9
EMA_SLOW = 21
ATR_PERIOD = 14
SL_ATR = 1.5     # стоп = 1.5 * ATR
TP_ATR = 2.5     # тейк = 2.5 * ATR (R:R ~ 1.67)

# Риск-менеджмент (консервативный старт — увеличь после обкатки)
ORDER_USDT = _num("OKX_ORDER_USDT", 5.0, float)   # $ на позицию
LEVERAGE = int(_num("OKX_LEVERAGE", 2, float))    # плечо
MAX_OPEN = int(_num("OKX_MAX_OPEN", 2, float))    # максимум одновременных позиций
MGN_MODE = "cross"
PAPER_EQUITY = 1000.0
