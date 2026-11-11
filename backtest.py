"""Testet die Strategie auf historischen Kursen – ganz ohne Konto oder API-Schlüssel.

Aufruf:  .venv/bin/python backtest.py
"""
import warnings

warnings.filterwarnings("ignore", module="urllib3")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

import config
import strategy

COST_PER_TRADE = 0.0005  # 0,05 % Kosten/Slippage pro Umschichtung


def stats(equity: pd.Series) -> dict:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    drawdown = (equity / equity.cummax() - 1).min()
    daily = equity.pct_change().dropna()
    sharpe = daily.mean() / daily.std() * (252 ** 0.5)
    return {"Endwert": equity.iloc[-1], "Rendite p.a.": cagr,
            "Max. Verlust": drawdown, "Sharpe": sharpe}


def main():
    print(f"Lade Kursdaten für {', '.join(config.SYMBOLS)} ...")
    prices = yf.download(config.SYMBOLS, start=config.BACKTEST_START,
                         auto_adjust=True, progress=False)["Close"].dropna()
    returns = prices.pct_change().fillna(0)

    # Signal von GESTERN bestimmt die Position von HEUTE (kein Blick in die Zukunft)
    signals = strategy.signal_frame(prices).shift(1).fillna(0)
    n_active = signals.sum(axis=1)
    per_weight = ((1 - config.CASH_BUFFER) / n_active.replace(0, float("nan"))) \
        .clip(upper=config.MAX_WEIGHT_PER_SYMBOL).fillna(0)
    weights = signals.mul(per_weight, axis=0)

    turnover = weights.diff().abs().sum(axis=1).fillna(0)
    strat_ret = (weights * returns).sum(axis=1) - turnover * COST_PER_TRADE
    bench_ret = returns.mean(axis=1)  # Kaufen & Halten, gleichgewichtet

    # Start erst, wenn der 200-Tage-Schnitt existiert
    start = prices.index[config.SLOW_SMA]
    strat_eq = config.START_CAPITAL * (1 + strat_ret[start:]).cumprod()
    bench_eq = config.START_CAPITAL * (1 + bench_ret[start:]).cumprod()
    spy_eq = config.START_CAPITAL * (1 + returns["SPY"][start:]).cumprod()

    table = pd.DataFrame({"Trend-Bot": stats(strat_eq),
                          "Kaufen & Halten (alle)": stats(bench_eq),
                          "Nur S&P 500 (SPY)": stats(spy_eq)}).T
    print(f"\nZeitraum: {start.date()} bis {prices.index[-1].date()}, "
          f"Startkapital {config.START_CAPITAL:,.0f} $\n")
    print(table.to_string(formatters={
        "Endwert": "{:>12,.0f} $".format,
        "Rendite p.a.": "{:>7.1%}".format,
        "Max. Verlust": "{:>7.1%}".format,
        "Sharpe": "{:>5.2f}".format}))
    trades_per_year = (weights.diff().abs() > 1e-9).sum().sum() / \
        ((prices.index[-1] - start).days / 365.25)
    print(f"\nØ Trades pro Jahr: {trades_per_year:.0f}")

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.plot(strat_eq, label="Trend-Bot", linewidth=1.8)
    ax.plot(bench_eq, label="Kaufen & Halten (alle)", linewidth=1.2)
    ax.plot(spy_eq, label="Nur S&P 500", linewidth=1.2, alpha=0.7)
    ax.set_yscale("log")
    ax.set_title("Backtest: Depotwert (log. Skala)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig("backtest.png", dpi=120)
    print("Chart gespeichert: backtest.png")


if __name__ == "__main__":
    main()
