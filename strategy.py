"""Die Handelsstrategie: Trendfolge mit Gleichgewichtung.

Idee: Nur in Werte investiert sein, die sich im Aufwärtstrend befinden.
Fällt ein Wert unter seinen 200-Tage-Durchschnitt, wird er verkauft und das
Geld bleibt in Cash. Das verdient in Crashs (2008, 2020, 2022) weniger Verluste,
kostet aber in Seitwärtsphasen Rendite durch Fehlsignale.
"""
import pandas as pd

import config


def in_uptrend(closes: pd.Series) -> bool:
    """True, wenn der Wert laut Trendfilter gehalten werden soll."""
    closes = closes.dropna()
    if len(closes) < config.SLOW_SMA:
        return False
    fast = closes.rolling(config.FAST_SMA).mean().iloc[-1]
    slow = closes.rolling(config.SLOW_SMA).mean().iloc[-1]
    return bool(closes.iloc[-1] > slow and fast > slow)


def target_weights(closes_by_symbol: dict) -> dict:
    """Zielgewichte je Symbol (0.0 = nicht halten)."""
    active = [s for s, c in closes_by_symbol.items() if in_uptrend(c)]
    weights = {s: 0.0 for s in closes_by_symbol}
    if not active:
        return weights
    w = min((1 - config.CASH_BUFFER) / len(active), config.MAX_WEIGHT_PER_SYMBOL)
    for s in active:
        weights[s] = w
    return weights


def signal_frame(prices: pd.DataFrame) -> pd.DataFrame:
    """Vektorisierte Version für den Backtest: 1 = im Trend, 0 = nicht."""
    fast = prices.rolling(config.FAST_SMA).mean()
    slow = prices.rolling(config.SLOW_SMA).mean()
    return ((prices > slow) & (fast > slow)).astype(float)
