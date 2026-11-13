"""Deine Beobachtungsliste für den MarketScan. Einfach Einträge hinzufügen/löschen.

Krypto-Kürzel wie bei Yahoo Finance: <COIN>-USD
"""

CRYPTO = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
    "XRP-USD": "XRP",
    "BNB-USD": "BNB",
}

STOCKS = {
    "NVDA": "Nvidia",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "TSLA": "Tesla",
}

# Indizes/ETFs: bekommen Trend & RSI, aber keine Firmen-Kennzahlen
INDICES = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
}

# Bitcoin-Halvings (alle ~4 Jahre halbiert sich die Belohnung für Miner)
HALVINGS = ["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"]
NEXT_HALVING_ESTIMATE = "ca. April 2028"
