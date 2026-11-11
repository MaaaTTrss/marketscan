"""Zentrale Einstellungen des Bots. Hier kannst du alles anpassen."""

# Welche Werte der Bot handeln darf. Breite ETFs sind für den Anfang deutlich
# robuster als Einzelaktien.
#   SPY = S&P 500, QQQ = Nasdaq 100, IWM = US-Nebenwerte,
#   EFA = Industrieländer ohne USA, TLT = lange US-Staatsanleihen, GLD = Gold
SYMBOLS = ["SPY", "QQQ", "IWM", "EFA", "TLT", "GLD"]

# Trendfilter: Ein Wert gilt als "im Aufwärtstrend", wenn
#   Schlusskurs > gleitender Durchschnitt (SLOW) UND  SMA(FAST) > SMA(SLOW)
FAST_SMA = 50
SLOW_SMA = 200

# Risiko-Regeln
MAX_WEIGHT_PER_SYMBOL = 0.25   # höchstens 25 % des Depots in einem Wert
CASH_BUFFER = 0.02             # immer 2 % Cash als Puffer behalten
MIN_TRADE_USD = 50             # kleinere Umschichtungen ignorieren (spart Gebühren/Rauschen)

# Backtest
BACKTEST_START = "2010-01-01"
START_CAPITAL = 100_000
