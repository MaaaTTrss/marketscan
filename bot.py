"""Der eigentliche Bot: handelt mit Spielgeld auf deinem Alpaca-Paper-Konto.

Aufruf:
  .venv/bin/python bot.py            # zeigt nur an, was er tun WÜRDE (Standard)
  .venv/bin/python bot.py --trade    # sendet die Orders wirklich (Paper-Konto)

Einmal pro Handelstag laufen lassen reicht (Strategie nutzt Tageskurse).
"""
import warnings

warnings.filterwarnings("ignore", module="urllib3")
import argparse
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

import config
import strategy

LOG_FILE = "trades.csv"


def load_keys():
    load_dotenv()
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret or "DEIN" in key:
        sys.exit("Fehlende API-Schlüssel. Kopiere .env.example nach .env und trage "
                 "deine Alpaca-PAPER-Schlüssel ein (siehe README).")
    return key, secret


def fetch_closes(data_client):
    start = datetime.now(timezone.utc) - timedelta(days=int(config.SLOW_SMA * 1.6))
    req = StockBarsRequest(symbol_or_symbols=config.SYMBOLS, timeframe=TimeFrame.Day,
                           start=start, feed=DataFeed.IEX)
    df = data_client.get_stock_bars(req).df
    return {s: df.xs(s, level="symbol")["close"] for s in config.SYMBOLS
            if s in df.index.get_level_values("symbol")}


def log_trade(row):
    new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["zeit", "symbol", "seite", "betrag_usd", "status"])
        w.writerow(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade", action="store_true",
                        help="Orders wirklich senden (sonst nur Vorschau)")
    args = parser.parse_args()

    key, secret = load_keys()
    # paper=True ist fest eingebaut: Dieser Bot kann kein echtes Geld bewegen.
    trading = TradingClient(key, secret, paper=True)
    data = StockHistoricalDataClient(key, secret)

    account = trading.get_account()
    equity = float(account.equity)
    print(f"Paper-Konto: Depotwert {equity:,.2f} $, Cash {float(account.cash):,.2f} $")

    clock = trading.get_clock()
    if args.trade and not clock.is_open:
        print(f"Markt ist geschlossen. Orders werden zur Eröffnung "
              f"({clock.next_open:%d.%m. %H:%M} UTC) ausgeführt.")

    closes = fetch_closes(data)
    targets = strategy.target_weights(closes)
    positions = {p.symbol: float(p.market_value) for p in trading.get_all_positions()}

    print(f"\n{'Symbol':<6} {'Trend':<6} {'Ist':>11} {'Ziel':>11} {'Aktion':>14}")
    orders = []
    for sym in config.SYMBOLS:
        current = positions.get(sym, 0.0)
        target = targets.get(sym, 0.0) * equity
        diff = target - current
        trend = "ja" if targets.get(sym, 0) > 0 else "nein"
        action = "-"
        if target == 0 and current > 0:
            action = "alles verk."
            orders.append((sym, "close", current))
        elif abs(diff) >= config.MIN_TRADE_USD and abs(diff) / max(target, 1) > 0.05:
            action = f"{'kauf' if diff > 0 else 'verk'} {abs(diff):,.0f}$"
            orders.append((sym, OrderSide.BUY if diff > 0 else OrderSide.SELL, abs(diff)))
        print(f"{sym:<6} {trend:<6} {current:>10,.0f}$ {target:>10,.0f}$ {action:>14}")

    # Werte im Depot, die nicht (mehr) in der Liste stehen, ignoriert der Bot bewusst.
    if not orders:
        print("\nNichts zu tun – Depot entspricht der Strategie.")
        return
    if not args.trade:
        print("\nVorschau-Modus. Mit --trade werden diese Orders gesendet.")
        return

    # Erst verkaufen (macht Cash frei), dann kaufen
    orders.sort(key=lambda o: 0 if o[1] in ("close", OrderSide.SELL) else 1)
    now = datetime.now().isoformat(timespec="seconds")
    for sym, side, amount in orders:
        try:
            if side == "close":
                trading.close_position(sym)
                log_trade([now, sym, "close", round(amount, 2), "gesendet"])
            else:
                trading.submit_order(MarketOrderRequest(
                    symbol=sym, notional=round(amount, 2), side=side,
                    time_in_force=TimeInForce.DAY))
                log_trade([now, sym, side.value, round(amount, 2), "gesendet"])
            print(f"OK  {sym} {side if side == 'close' else side.value} {amount:,.0f}$")
        except Exception as e:  # z. B. Markt zu, Symbol nicht handelbar
            log_trade([now, sym, str(side), round(amount, 2), f"fehler: {e}"])
            print(f"FEHLER {sym}: {e}")


if __name__ == "__main__":
    main()
