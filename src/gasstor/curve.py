"""Seasonal shape of TTF prices and an illustrative monthly forward curve.

No free archive of the full TTF forward curve exists, so the curve is built from the front-month
series: the price observed on a trade date belongs to the next delivery month, its log deviation
from a centred one-year rolling mean measures how that delivery month trades relative to the
annual level, and the average deviation by delivery month is the seasonal factor. A forward curve
for a storage year is then the anchor price (the front month on the valuation date) scaled by the
seasonal factors. A real settlement curve can be passed instead as a CSV (month, EUR/MWh).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# Gas crisis window excluded from the seasonal fit by default: prices tripled within weeks and the
# rolling mean cannot separate level from season.
CRISIS = (pd.Timestamp("2021-09-01"), pd.Timestamp("2023-03-31"))


def delivery_month(trade_dates: pd.DatetimeIndex) -> pd.PeriodIndex:
    return trade_dates.to_period("M") + 1


def seasonal_factors(px: pd.Series, exclude_crisis: bool = True, window_days: int = 365
                     ) -> pd.Series:
    """Mean log deviation from the centred rolling mean, by delivery month (1-12), mean zero."""
    lp = np.log(px)
    trend = lp.rolling(f"{window_days}D", min_periods=window_days // 2).mean()
    # the time-based rolling window is trailing; shift it back by half a window to centre it
    trend = trend.shift(-(window_days // 2), freq="D").reindex(lp.index)
    dev = (lp - trend).dropna()
    if exclude_crisis:
        dev = dev[(dev.index < CRISIS[0]) | (dev.index > CRISIS[1])]
    s = dev.groupby(delivery_month(dev.index).month).mean()
    s = s.reindex(range(1, 13))
    s = s.fillna(s.mean())  # a delivery month without observations gets no seasonal premium
    return (s - s.mean()).rename("log_factor")


def forward_curve(anchor_price: float, anchor_month: pd.Period, months: pd.PeriodIndex,
                  factors: pd.Series) -> pd.Series:
    """Monthly forward prices: anchor scaled by exp(factor(month) - factor(anchor month))."""
    f = factors.reindex(months.month).to_numpy() - factors[anchor_month.month]
    return pd.Series(anchor_price * np.exp(f), index=months, name="forward_eur_mwh")


def read_curve_csv(path: Path) -> pd.Series:
    df = pd.read_csv(path)
    return pd.Series(df.iloc[:, 1].to_numpy(float), index=pd.PeriodIndex(df.iloc[:, 0], freq="M"),
                     name="forward_eur_mwh")


def daily_from_monthly(curve: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Daily forward (each day carries its delivery month's price), start inclusive, end exclusive."""
    days = pd.date_range(start, end, freq="D", inclusive="left")
    return pd.Series(curve.reindex(days.to_period("M")).to_numpy(), index=days,
                     name="forward_eur_mwh")


def realised_seasonal_spread(px: pd.Series) -> pd.DataFrame:
    """For each gas year (April to March), mean front-month price for summer (Apr-Sep) and winter
    (Oct-Mar) delivery, and the winter minus summer spread a storage owner faced in hindsight."""
    dm = delivery_month(px.index)
    gas_year = np.where(dm.month >= 4, dm.year, dm.year - 1)
    season = np.where((dm.month >= 4) & (dm.month <= 9), "summer", "winter")
    df = pd.DataFrame({"px": px.to_numpy(), "gas_year": gas_year, "season": season})
    t = df.pivot_table(index="gas_year", columns="season", values="px", aggfunc="mean")
    n = df.groupby(["gas_year", "season"]).size().unstack()
    t = t[(n["summer"] > 100) & (n["winter"] > 100)]  # complete gas years only
    t["spread"] = t["winter"] - t["summer"]
    return t
